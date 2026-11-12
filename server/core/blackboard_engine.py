"""
[Module Responsibility]
事件驱动共享黑板 (`BlackboardEngine`)、零算力条件监视点 (`watch`)、黑板闹钟/定时器及全端 Bot 状态窥视 (`peek_bots`) 模块 (`server/core/blackboard_engine.py`)。
取代阻塞式点对点 `send_message`：
- 发帖/回帖/认领/交付/评审 `< 50ms` 瞬时返回，后台异步唤醒被 `@` 的目标端 Bot（支持 `@Bot名`、`@self`、`@everyone` 及 `steer=True` 抢占插话）；
- 支持可选的 Maker-Checker 审校门禁（仅当显式指定 `review_by` 或 `require_review=True` 时启用）；
- 统一承载黑板闹钟/定时器 (`at_time`/`delay_minutes`/`daily_time`/`exec_action`) 与 0-Token 条件监视器 (`watch`)；
- 提供无锁只读 `peek_bots` 供任意端随时查看其他 Bot 干到哪了。
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import re
import secrets
import time
from pathlib import Path
from typing import Any

import httpx

from assistants import get_assistant, resolve_bot_for_source
from storage import node_display_name, normalize_node_source
from vault_manager import TZ, now_iso

MENTION_TOKEN_RE = re.compile(r'(?<![A-Za-z0-9_.\-/|>])@([A-Za-z0-9_\-\u4e00-\u9fa5:：]{1,32})')


class BlackboardEngine:
    MAX_POSTS = 200
    DEFAULT_LEASE_SEC = 900

    def __init__(self, brain: Any):
        self.brain = brain
        self.store = brain.store
        self.vault = brain.vault
        self._watch_last_check: dict[str, float] = {}
        self._recent_wakeups: dict[tuple[str, str, str], float] = {}

    def _posts(self) -> list[dict]:
        posts = self.store.state.setdefault('blackboard_posts', [])
        if not isinstance(posts, list):
            posts = []
            self.store.state['blackboard_posts'] = posts
        return posts

    def _next_post_id(self) -> str:
        seq = int(self.store.state.get('blackboard_seq') or 0) + 1
        self.store.state['blackboard_seq'] = seq
        return f'bb-{seq}'

    @staticmethod
    def _norm_post_id(raw: Any) -> str:
        s = str(raw or '').strip().lstrip('#')
        if not s:
            return ''
        if s.isdigit():
            return f'bb-{s}'
        return s

    def _find_post(self, post_id: Any) -> dict | None:
        pid = self._norm_post_id(post_id)
        if not pid:
            return None
        for p in reversed(self._posts()):
            if isinstance(p, dict) and str(p.get('post_id') or '') == pid:
                return p
        return None

    def _expire_stale_leases(self, now: float) -> bool:
        changed = False
        for p in self._posts():
            if not isinstance(p, dict):
                continue
            if p.get('status') == 'claimed' and float(p.get('lease_until') or 0) > 0 and now > float(p['lease_until']):
                p['status'] = 'open'
                p['expired_claim_by'] = p.get('claimed_by', '')
                p['claimed_by'] = ''
                p['claimed_name'] = ''
                p['lease_until'] = 0.0
                changed = True
        return changed

    def _extract_mentions(self, args: dict, text: str) -> list[str]:
        raw_list: list[str] = []
        m_arg = args.get('mentions') or args.get('at') or args.get('to')
        if isinstance(m_arg, list):
            raw_list.extend(str(x).strip() for x in m_arg if str(x).strip())
        elif isinstance(m_arg, str) and m_arg.strip():
            for part in re.split(r'[,，;\s]+', m_arg.strip()):
                if part.strip():
                    raw_list.append(part.strip())
        for m in MENTION_TOKEN_RE.finditer(text or ''):
            tok = m.group(1).strip()
            if not tok or tok.isdigit() or re.match(r'^[be]\d+$', tok, re.IGNORECASE):
                continue
            if tok in ('你', '您', '他', '她', '它', '大家'):
                continue
            raw_list.append(f'@{tok}')
        normed: list[str] = []
        for item in raw_list:
            clean = item if item.startswith('@') else f'@{item}'
            if clean not in normed:
                normed.append(clean)
        return normed

    def _resolve_targets(self, mentions: list[str], author_bot_id: str, author_src: str) -> list[tuple[str, str, str]]:
        """Resolve `@mention` tokens into `[(bot_id, bot_name, target_source)]`."""
        assistants = [b for b in self.store.state.get('assistants', []) if isinstance(b, dict) and b.get('id')]
        targets: list[tuple[str, str, str]] = []
        seen: set[tuple[str, str]] = set()
        norm_author_src = normalize_node_source(author_src)

        def _home_src(b: dict, explicit_src: str | None = None) -> str:
            if explicit_src:
                return normalize_node_source(explicit_src)
            hn = str(b.get('home_node') or '').strip().lower()
            if hn in ('pc', 'pc2'):
                return hn
            if hn in ('phone', 'phone2'):
                return hn
            return norm_author_src

        def _add_bot(b: dict, exp_src: str | None = None, allow_self: bool = False):
            tsrc = _home_src(b, exp_src)
            bid = str(b['id'])
            if not allow_self and bid == author_bot_id and tsrc == norm_author_src:
                return
            key = (bid, tsrc)
            if key not in seen:
                seen.add(key)
                targets.append((bid, str(b.get('name') or bid), tsrc))

        for raw in mentions:
            tok = raw.lstrip('@').strip()
            if not tok:
                continue
            low = tok.lower()
            if low in ('everyone', 'all', '全体', '所有人', '全员'):
                for b in assistants:
                    if b['id'] != author_bot_id and not b.get('hidden'):
                        _add_bot(b)
                continue
            if low in ('self', 'me', '自己', '我'):
                try:
                    me = get_assistant(self.store, author_bot_id)
                    _add_bot(me, norm_author_src, allow_self=True)
                except Exception:
                    pass
                continue
            exp_src: str | None = None
            bot_tok = tok
            if ':' in tok or '：' in tok:
                left, right = re.split(r'[:：]', tok, maxsplit=1)
                exp_src = normalize_node_source(left)
                bot_tok = right.strip()
            elif low in ('电脑端', '电脑', '桌面端', 'windows一', 'windows1', 'pc'):
                exp_src = 'pc'
                bot_tok = 'pc_engineer'
            elif low in ('手机端', '手机', '安卓一', '安卓1', 'phone'):
                exp_src = 'phone'
                bot_tok = str(self.store.state.get('primary_bot_id') or 'companion')
            matched = next(
                (
                    b for b in assistants
                    if str(b.get('id') or '').lower() == bot_tok.lower()
                    or str(b.get('name') or '').strip().lower() == bot_tok.lower()
                ),
                None,
            )
            if matched is None and exp_src == 'pc':
                matched = next((b for b in assistants if str(b.get('home_node') or '') == 'pc'), None) or (assistants[0] if assistants else None)
            elif matched is None and exp_src == 'phone':
                matched = next((b for b in assistants if str(b.get('home_node') or 'phone') == 'phone'), None)
            if matched is not None:
                _add_bot(matched, exp_src, allow_self=False)
        return targets

    def _parse_due_timestamp(self, args: dict, now: float) -> tuple[float, dict]:
        """Parse optional timer/alarm scheduling fields (`delay_seconds`, `delay_minutes`, `at_time`, `daily_time`, `interval_hours`)."""
        sched_meta: dict[str, Any] = {}
        delay_sec = float(args.get('delay_seconds') or 0)
        delay_min = float(args.get('delay_minutes') or 0)
        interval_h = float(args.get('interval_hours') or 0)
        at_time = str(args.get('at_time') or '').strip()
        daily_time = str(args.get('daily_time') or '').strip()
        weekdays = str(args.get('weekdays') or '').strip()

        if delay_sec > 0 or delay_min > 0:
            due = now + max(0.01, delay_sec + delay_min * 60.0)
            sched_meta['kind'] = 'timer'
            return due, sched_meta

        clock_str = daily_time or at_time
        if clock_str and re.match(r'^\d{1,2}:\d{2}$', clock_str):
            hh, mm = [int(x) for x in clock_str.split(':')]
            hh = max(0, min(23, hh))
            mm = max(0, min(59, mm))
            now_dt = dt.datetime.fromtimestamp(now, TZ)
            cand = now_dt.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if cand.timestamp() <= now + 1.0:
                cand += dt.timedelta(days=1)
            if weekdays:
                allowed_days = self._parse_weekdays(weekdays)
                for _ in range(8):
                    if cand.isoweekday() in allowed_days:
                        break
                    cand += dt.timedelta(days=1)
            sched_meta['kind'] = 'routine' if daily_time else 'alarm'
            if daily_time:
                sched_meta['daily_time'] = f'{hh:02d}:{mm:02d}'
            if weekdays:
                sched_meta['weekdays'] = weekdays
            return cand.timestamp(), sched_meta

        if interval_h > 0:
            sched_meta['kind'] = 'routine'
            sched_meta['interval_hours'] = interval_h
            return now + interval_h * 3600.0, sched_meta

        return 0.0, sched_meta

    @staticmethod
    def _parse_weekdays(spec: str) -> set[int]:
        days: set[int] = set()
        for part in re.split(r'[,，\s]+', spec.strip()):
            if '-' in part:
                a, b = part.split('-', 1)
                if a.isdigit() and b.isdigit():
                    for d in range(int(a), int(b) + 1):
                        if 1 <= d <= 7:
                            days.add(d)
            elif part.isdigit() and 1 <= int(part) <= 7:
                days.add(int(part))
        return days or {1, 2, 3, 4, 5, 6, 7}

    def _schedule_wakeup(
        self,
        post: dict,
        targets: list[tuple[str, str, str]],
        steer: bool = False,
        reason_label: str = '黑板提醒',
        sender_bot_id: str | None = None,
        sender_name: str | None = None,
        snippet_text: str | None = None,
    ) -> list[str]:
        woken: list[str] = []
        post_id = str(post.get('post_id') or '')
        eff_sender_id = str(sender_bot_id if sender_bot_id is not None else (post.get('author_bot_id') or ''))
        eff_sender_name = str(sender_name if sender_name is not None else (post.get('author_name') or eff_sender_id or '同事'))
        raw_hint = str(snippet_text if snippet_text is not None else (post.get('title') or post.get('text') or '')).strip()
        clean_hint = re.sub(r'^(?:@[A-Za-z0-9_\-\u4e00-\u9fa5:：]+\s*)+', '', raw_hint).replace('\n', ' ').strip()[:56]
        if not clean_hint:
            clean_hint = raw_hint.replace('\n', ' ')[:56]
        now_ts = time.time()
        for tid, tname, tsrc in targets:
            dedup_key = (post_id, tid, normalize_node_source(tsrc))
            last_w = self._recent_wakeups.get(dedup_key, 0.0)
            if not steer and (now_ts - last_w) < 6.0:
                continue
            self._recent_wakeups[dedup_key] = now_ts
            woken.append(f'{node_display_name(tsrc)}:{tname}({tid})')
            asyncio.create_task(
                self._async_wake_bot(
                    target_bot_id=tid,
                    target_name=tname,
                    target_src=tsrc,
                    post_id=post_id,
                    author_bot_id=eff_sender_id,
                    author_name=eff_sender_name,
                    title_hint=clean_hint,
                    steer=steer,
                    reason_label=reason_label,
                )
            )
        return woken

    async def _async_wake_bot(
        self,
        target_bot_id: str,
        target_name: str,
        target_src: str,
        post_id: str,
        author_bot_id: str = '',
        author_name: str = '同事',
        title_hint: str = '',
        steer: bool = False,
        reason_label: str = '黑板提醒',
    ) -> None:
        try:
            norm_src = normalize_node_source(target_src)
            if steer and self.brain.is_node_bot_busy(target_bot_id, norm_src):
                await self.brain.stop_immediately(
                    bot_id=target_bot_id,
                    reason='user_preempt',
                    commit_partial=True,
                    source=norm_src,
                )
                for _ in range(15):
                    if not self.brain._get_bot_lock(target_bot_id, norm_src).locked():
                        break
                    await asyncio.sleep(0.01)

            has_custom_complete = 'complete' in getattr(self.brain, '__dict__', {}) or 'chat' in getattr(self.brain, '__dict__', {})
            has_api = has_custom_complete or bool(
                (self.store.config.effective_api_key(big=False) or self.store.config.effective_api_key(big=True))
                and (self.store.config.effective_model(big=False) or self.store.config.effective_model(big=True))
            )
            wake_prompt = (
                f'[{reason_label}] 有人在黑板 @ 了你（#{post_id}，来自「{author_name}」：{title_hint}）。'
                f'请自行调用 `blackboard(action="read", post_id="{post_id}")` 扫一眼黑板并按需处理。'
            )
            await self.brain.publish({
                'type': 'blackboard_wake',
                'post_id': post_id,
                'target_bot_id': target_bot_id,
                'target_bot_name': target_name,
                'source': norm_src,
                'steer': steer,
            })
            if not has_api:
                return
            turn_id = 'turn-' + secrets.token_hex(6)
            m = self.store.message(
                'user',
                wake_prompt,
                kind='bot_message',
                bot_id=target_bot_id,
                from_bot_id=author_bot_id,
                from_bot_name=author_name,
                to_bot_id=target_bot_id,
                direction='inbound',
                source=norm_src,
            )
            self.brain.stream_seq += 1
            m['seq'] = self.brain.stream_seq
            m['turn_id'] = turn_id
            self.store.save()
            await self.brain.publish({'type': 'message', 'source': norm_src, 'message': m, 'seq': self.brain.stream_seq})
            hop_tok = self.brain.hop_depth.set(max(1, self.brain.hop_depth.get() + 1))
            try:
                await self.brain.chat(
                    wake_prompt,
                    bot_id=target_bot_id,
                    mode='auto',
                    pre_recorded_user_msg=m,
                    turn_id=turn_id,
                    source=norm_src,
                )
            finally:
                self.brain.hop_depth.reset(hop_tok)
        except Exception as exc:
            err_txt = str(exc)[:300]
            self.store.state['last_error'] = f'blackboard_wake({target_bot_id}): {err_txt[:180]}'
            try:
                self.store.message('assistant', f'执行出错：{err_txt}', kind='error', bot_id=target_bot_id, from_bot_id=target_bot_id, source=norm_src)
                p = self._find_post(post_id)
                if p is not None:
                    p.setdefault('replies', []).append({
                        'reply_id': 'r-' + secrets.token_hex(3),
                        'author_bot_id': target_bot_id,
                        'author_name': target_name,
                        'author_node': norm_src,
                        'text': f'⚠️ 唤醒执行异常：{err_txt}',
                        'artifact_path': '',
                        'mentions': [],
                        'created_at': time.time(),
                        'created_iso': now_iso(),
                    })
                self.store.save()
            except Exception:
                pass

    async def execute(self, args: dict) -> dict:
        t0 = time.perf_counter()
        action = str(args.get('action') or 'list').strip().lower()
        caller_bot_id = str(args.get('_caller_bot_id') or self.brain.bot_context.get() or 'companion').strip()
        caller_room_id = str(self.brain.room_context.get() or caller_bot_id).strip()
        caller_src = normalize_node_source(args.get('_caller_source') or self.brain.source_context.get())
        try:
            caller_bot = get_assistant(self.store, caller_bot_id)
            caller_name = str(caller_bot.get('name') or caller_bot_id)
        except Exception:
            caller_name = caller_bot_id

        now = time.time()
        if self._expire_stale_leases(now):
            self.store.save()

        # 1. Backward-compatible KV & Todo progress actions
        if action == 'write':
            return self.brain.context_search.blackboard_write(
                key=str(args.get('key') or ''),
                value=str(args.get('value') or args.get('text') or ''),
                author_bot=caller_bot_id,
                summary=str(args.get('summary') or args.get('title') or ''),
            )
        if action == 'delete':
            pid = self._norm_post_id(args.get('post_id') or args.get('topic_id') or '')
            if pid:
                post = self._find_post(pid)
                if not post:
                    return {'ok': False, 'error': f'未找到黑板条目 {pid}'}
                self.store.state['blackboard_posts'] = [p for p in self._posts() if p.get('post_id') != pid]
                self.store.save()
                return {'ok': True, 'deleted_post_id': pid}
            return self.brain.context_search.blackboard_delete(str(args.get('key') or ''))
        if action == 'set_todos':
            raw_steps = args.get('steps') if isinstance(args.get('steps'), list) else []
            if not raw_steps:
                return {'ok': False, 'error': 'steps 不能为空（请提供包含步骤标题的列表）'}
            todos = []
            for idx, st in enumerate(raw_steps[:12], 1):
                if isinstance(st, dict):
                    todos.append({
                        'title': str(st.get('title') or st.get('text') or f'步骤 {idx}')[:120],
                        'status': str(st.get('status') or ('done' if st.get('done') else 'pending')),
                    })
                elif isinstance(st, str) and st.strip():
                    todos.append({'title': st.strip()[:120], 'status': 'pending'})
            if not todos:
                return {'ok': False, 'error': 'steps 不能为空'}
            todo_key = str(args.get('key') or 'todos').strip() or 'todos'
            self.brain.context_search.blackboard_write(
                key=todo_key,
                value=json.dumps(todos, ensure_ascii=False),
                author_bot=caller_bot_id,
                summary='; '.join(f"{t['title']}({t['status']})" for t in todos[:5]),
            )
            cur_exec = self.brain.executions_by_bot.get(caller_room_id) or self.brain.execution
            if isinstance(cur_exec, dict):
                cur_exec['todos'] = todos
                await self.brain.publish({'type': 'execution', 'execution': cur_exec})
            return {'ok': True, 'key': todo_key, 'todos': todos}

        # 2. Event Blackboard `post` (tasks, @mentions, timers/alarms, zero-token watchpoints)
        if action in ('post', 'publish', 'schedule', 'watch'):
            text = str(args.get('text') or args.get('value') or args.get('content') or args.get('goal') or '').strip()
            title = str(args.get('title') or args.get('summary') or '').strip()[:140]
            if not text and not title:
                return {'ok': False, 'error': 'post 需要提供 text（或 title/value）'}
            if not text:
                text = title
            mentions = self._extract_mentions(args, f'{title} {text}')
            due_at, sched_meta = self._parse_due_timestamp(args, now)
            watch_cfg = args.get('watch') if isinstance(args.get('watch'), dict) else None
            if action == 'watch' and not watch_cfg and args.get('target'):
                watch_cfg = {
                    'kind': str(args.get('watch_kind') or 'file_exists'),
                    'target': str(args.get('target') or ''),
                    'expect': str(args.get('expect') or ''),
                    'interval_sec': max(3, int(args.get('interval_sec') or 10)),
                }
            exec_action = str(args.get('exec_action') or '').strip().lower()
            if not exec_action and args.get('ring_alarm'):
                exec_action = 'ring_alarm'
            elif not exec_action and args.get('notify_user'):
                exec_action = 'notify_user'

            is_deferred = bool(due_at > now or watch_cfg)
            if is_deferred and not mentions:
                mentions = ['@self']

            # Opt-In ONLY Maker-Checker Gate: active ONLY when caller explicitly sets `review_by` or `require_review=True`
            review_by = str(args.get('review_by') or '').strip()
            if not review_by and bool(args.get('require_review', False)):
                review_by = '@contrarian' if caller_src in ('phone', 'phone2') else '@pc_reviewer'

            targets = self._resolve_targets(mentions, caller_bot_id, caller_src)
            post_id = self._next_post_id()
            post: dict[str, Any] = {
                'post_id': post_id,
                'title': title or text.splitlines()[0][:72],
                'text': text[:8000],
                'author_bot_id': caller_bot_id,
                'author_name': caller_name,
                'author_node': caller_src,
                'mentions': mentions,
                'target_bot_ids': [t[0] for t in targets],
                'unread_by': [t[0] for t in targets],
                'status': 'scheduled' if is_deferred else 'open',
                'claimed_by': '',
                'claimed_name': '',
                'claimed_node': '',
                'lease_until': 0.0,
                'artifact_path': str(args.get('artifact_path') or '').strip(),
                'next_action': str(args.get('next_action') or '').strip()[:400],
                'review_by': review_by,
                'exec_action': exec_action,
                'due_at': round(due_at, 3) if due_at > 0 else 0.0,
                'due_iso': dt.datetime.fromtimestamp(due_at, TZ).isoformat(timespec='seconds') if due_at > 0 else '',
                'schedule': sched_meta,
                'watch': watch_cfg,
                'replies': [],
                'created_at': now,
                'created_iso': now_iso(),
                'updated_at': now,
            }
            posts = self._posts()
            posts.append(post)
            if len(posts) > self.MAX_POSTS:
                self.store.state['blackboard_posts'] = posts[-self.MAX_POSTS:]
            self.store.save()
            await self.brain.publish({'type': 'blackboard_updated', 'op': 'post', 'post': post})

            woken: list[str] = []
            steer_flag = bool(args.get('steer') or str(args.get('priority') or '').lower() in ('steer', 'interrupt', 'urgent'))
            if not is_deferred and targets:
                woken = self._schedule_wakeup(post, targets, steer=steer_flag, reason_label='黑板提醒')
            else:
                self.brain.wake.set()

            return {
                'ok': True,
                'post_id': post_id,
                'topic_id': post_id,
                'post': post,
                'topic': post,
                'status': post['status'],
                'mentions': mentions,
                'woken': woken,
                'woken_bots': [t[0] for t in targets] if not is_deferred else [],
                'scheduled_due_iso': post['due_iso'] or None,
                'watch_active': bool(watch_cfg),
                'review_by': review_by or None,
                'elapsed_ms': max(1, int(round((time.perf_counter() - t0) * 1000))),
            }

        # 3. `reply`: append progress/reply + optional `@mention` wakeup
        if action == 'reply':
            post = self._find_post(args.get('post_id') or args.get('topic_id') or args.get('key'))
            if not post:
                return {'ok': False, 'error': 'reply 需要有效的 post_id（如 "bb-1"）'}
            text = str(args.get('text') or args.get('value') or args.get('content') or '').strip()
            if not text:
                return {'ok': False, 'error': 'reply 内容 text 不能为空'}
            mentions = self._extract_mentions(args, text)
            targets = self._resolve_targets(mentions, caller_bot_id, caller_src)
            art = str(args.get('artifact_path') or '').strip()
            if art:
                post['artifact_path'] = art
            if args.get('next_action'):
                post['next_action'] = str(args.get('next_action')).strip()[:400]
            rep = {
                'reply_id': 'r-' + secrets.token_hex(3),
                'author_bot_id': caller_bot_id,
                'author_name': caller_name,
                'author_node': caller_src,
                'text': text[:6000],
                'artifact_path': art,
                'mentions': mentions,
                'created_at': now,
                'created_iso': now_iso(),
            }
            post.setdefault('replies', []).append(rep)
            unread = set(post.get('unread_by') or [])
            unread.discard(caller_bot_id)
            for tid, _, _ in targets:
                unread.add(tid)
            post['unread_by'] = list(unread)
            post['updated_at'] = now
            self.store.save()
            await self.brain.publish({'type': 'blackboard_updated', 'op': 'reply', 'post': post})
            steer_flag = bool(args.get('steer'))
            woken = (
                self._schedule_wakeup(
                    post,
                    targets,
                    steer=steer_flag,
                    reason_label='黑板回帖提醒',
                    sender_bot_id=caller_bot_id,
                    sender_name=caller_name,
                    snippet_text=text,
                )
                if targets else []
            )
            return {'ok': True, 'post_id': post['post_id'], 'reply': rep, 'woken': woken}

        # 4. `claim`: atomic Kanban claim with 15-min lease so `@everyone` tasks are never duplicated
        if action == 'claim':
            post = self._find_post(args.get('post_id') or args.get('topic_id') or args.get('key'))
            if not post:
                return {'ok': False, 'error': 'claim 需要有效的 post_id'}
            cur_claimed = str(post.get('claimed_by') or '')
            lease_until = float(post.get('lease_until') or 0)
            if post.get('status') == 'claimed' and cur_claimed and cur_claimed != caller_bot_id and lease_until > now:
                return {
                    'ok': False,
                    'already_claimed': True,
                    'post_id': post['post_id'],
                    'claimed_by': cur_claimed,
                    'claimed_name': post.get('claimed_name', ''),
                    'lease_remaining_sec': int(lease_until - now),
                }
            lease_sec = max(60, min(7200, int(args.get('lease_seconds') or self.DEFAULT_LEASE_SEC)))
            post['status'] = 'claimed'
            post['claimed_by'] = caller_bot_id
            post['claimed_name'] = caller_name
            post['claimed_node'] = caller_src
            post['lease_until'] = now + lease_sec
            post['unread_by'] = [u for u in (post.get('unread_by') or []) if u != caller_bot_id]
            post['updated_at'] = now
            self.store.save()
            await self.brain.publish({'type': 'blackboard_updated', 'op': 'claim', 'post': post})
            return {'ok': True, 'post_id': post['post_id'], 'post': post, 'topic': post, 'status': 'claimed', 'claimed_by': caller_bot_id, 'lease_seconds': lease_sec}

        # 5. `complete`: mark done OR enter opt-in Maker-Checker review if `review_by` was explicitly set
        if action in ('complete', 'done', 'resolve'):
            post = self._find_post(args.get('post_id') or args.get('topic_id') or args.get('key'))
            if not post:
                return {'ok': False, 'error': 'complete 需要有效的 post_id'}
            summary = str(args.get('summary') or args.get('text') or args.get('content') or args.get('value') or '').strip()
            art = str(args.get('artifact_path') or '').strip()
            if art:
                post['artifact_path'] = art
            if args.get('next_action'):
                post['next_action'] = str(args.get('next_action')).strip()[:400]
            if summary:
                post.setdefault('replies', []).append({
                    'reply_id': 'r-' + secrets.token_hex(3),
                    'author_bot_id': caller_bot_id,
                    'author_name': caller_name,
                    'author_node': caller_src,
                    'text': f'✅ 交付总结：{summary[:4000]}',
                    'artifact_path': art,
                    'mentions': [],
                    'created_at': now,
                    'created_iso': now_iso(),
                })
            review_by = str(post.get('review_by') or '').strip()
            woken: list[str] = []
            if review_by and caller_bot_id not in review_by:
                post['status'] = 'review_required'
                rev_targets = self._resolve_targets([review_by], caller_bot_id, caller_src)
                post['unread_by'] = [t[0] for t in rev_targets]
                woken = self._schedule_wakeup(
                    post,
                    rev_targets,
                    reason_label='黑板审校请求',
                    sender_bot_id=caller_bot_id,
                    sender_name=caller_name,
                    snippet_text=summary or post.get('title', ''),
                )
            else:
                post['status'] = 'done'
                post['completed_at'] = now
                post['unread_by'] = []
                # Optionally notify original author if another Bot completed their delegated post
                orig_author = str(post.get('author_bot_id') or '')
                orig_node = normalize_node_source(post.get('author_node') or caller_src)
                if orig_author and orig_author != caller_bot_id and bool(args.get('notify_author', True)):
                    try:
                        ob = get_assistant(self.store, orig_author)
                        woken = self._schedule_wakeup(
                            post,
                            [(orig_author, str(ob.get('name') or orig_author), orig_node)],
                            reason_label='黑板任务结项回执',
                            sender_bot_id=caller_bot_id,
                            sender_name=caller_name,
                            snippet_text=summary or '任务已结项',
                        )
                    except Exception:
                        pass
            post['lease_until'] = 0.0
            post['updated_at'] = now
            self.store.save()
            await self.brain.publish({'type': 'blackboard_updated', 'op': 'complete', 'post': post})
            return {'ok': True, 'post_id': post['post_id'], 'post': post, 'topic': post, 'status': post['status'], 'artifact_path': post.get('artifact_path', ''), 'woken': woken}

        # 6. `review`: Maker-Checker verdict (`approved` | `rejected`)
        if action == 'review':
            post = self._find_post(args.get('post_id') or args.get('topic_id') or args.get('key'))
            if not post:
                return {'ok': False, 'error': 'review 需要有效的 post_id'}
            verdict = str(args.get('verdict') or 'approved').strip().lower()
            feedback = str(args.get('text') or args.get('content') or args.get('summary') or args.get('feedback') or '').strip()
            approved = verdict in ('approved', 'approve', 'pass', 'ok', '通过')
            post.setdefault('replies', []).append({
                'reply_id': 'r-' + secrets.token_hex(3),
                'author_bot_id': caller_bot_id,
                'author_name': caller_name,
                'author_node': caller_src,
                'text': f"{'🟢 审校通过' if approved else '🟠 审校退回修改'}：{feedback or verdict}",
                'artifact_path': '',
                'mentions': [],
                'created_at': now,
                'created_iso': now_iso(),
            })
            woken: list[str] = []
            if approved:
                post['status'] = 'done'
                post['completed_at'] = now
                post['unread_by'] = []
            else:
                post['status'] = 'claimed' if post.get('claimed_by') else 'open'
                assignee = str(post.get('claimed_by') or post.get('author_bot_id') or '')
                assignee_node = normalize_node_source(post.get('claimed_node') or post.get('author_node') or caller_src)
                if assignee and assignee != caller_bot_id:
                    post['unread_by'] = [assignee]
                    try:
                        ab = get_assistant(self.store, assignee)
                        woken = self._schedule_wakeup(
                            post,
                            [(assignee, str(ab.get('name') or assignee), assignee_node)],
                            reason_label='黑板审校退回提醒',
                            sender_bot_id=caller_bot_id,
                            sender_name=caller_name,
                            snippet_text=feedback or verdict,
                        )
                    except Exception:
                        pass
            post['updated_at'] = now
            self.store.save()
            await self.brain.publish({'type': 'blackboard_updated', 'op': 'review', 'post': post})
            return {'ok': True, 'post_id': post['post_id'], 'post': post, 'topic': post, 'status': post['status'], 'verdict': 'approved' if approved else 'rejected', 'woken': woken}

        if action == 'cancel':
            post = self._find_post(args.get('post_id') or args.get('topic_id') or args.get('key'))
            if not post:
                return {'ok': False, 'error': 'cancel 需要有效的 post_id'}
            post['status'] = 'cancelled'
            post['unread_by'] = []
            post['updated_at'] = now
            self.store.save()
            return {'ok': True, 'post_id': post['post_id'], 'post': post, 'topic': post, 'status': 'cancelled'}

        # 7. `read`: read single post, legacy KV key, OR smart inbox digest for calling Bot when called with no args!
        if action == 'read':
            raw_pid = str(args.get('post_id') or args.get('topic_id') or '').strip()
            raw_key = str(args.get('key') or '').strip()
            lookup = raw_pid or (raw_key if raw_key.lstrip('#').startswith('bb-') or raw_key.lstrip('#').isdigit() else '')
            if lookup:
                post = self._find_post(lookup)
                if not post:
                    return {'ok': False, 'error': f'未找到黑板帖子 {lookup}'}
                if caller_bot_id in (post.get('unread_by') or []):
                    post['unread_by'] = [u for u in post['unread_by'] if u != caller_bot_id]
                    self.store.save()
                return {'ok': True, 'post': post, 'topic': post}
            if raw_key:
                return self.brain.context_search.blackboard_read(raw_key)
            # Smart scan: unread mentions for caller_bot_id + active open/claimed/scheduled posts
            posts = self._posts()
            unread_posts = []
            open_posts = []
            dirty = False
            for p in reversed(posts):
                if not isinstance(p, dict):
                    continue
                if caller_bot_id in (p.get('unread_by') or []):
                    unread_posts.append(dict(p))
                    p['unread_by'] = [u for u in p['unread_by'] if u != caller_bot_id]
                    dirty = True
                elif p.get('status') in ('open', 'claimed', 'review_required', 'scheduled') and len(open_posts) < 15:
                    open_posts.append(dict(p))
            if dirty:
                self.store.save()
            kv_res = self.brain.context_search.blackboard_list()
            return {
                'ok': True,
                'caller_bot_id': caller_bot_id,
                'unread_mentions': unread_posts,
                'active_posts': open_posts,
                'kv_entries': kv_res.get('entries', []),
            }

        # 8. Default `list`
        status_filter = str(args.get('status') or '').strip().lower()
        posts = [
            p for p in reversed(self._posts())
            if isinstance(p, dict) and (not status_filter or p.get('status') == status_filter)
        ][:30]
        kv_res = self.brain.context_search.blackboard_list()
        return {
            'ok': True,
            'posts': posts,
            'total_posts': len(self._posts()),
            'entries': kv_res.get('entries', []),
        }

    async def tick(self) -> int:
        """Zero-LLM-token background tick for Blackboard timers, alarms, and watchpoints."""
        now = time.time()
        fired_count = 0
        dirty = self._expire_stale_leases(now)
        posts = self._posts()
        for post in list(posts):
            if not isinstance(post, dict) or post.get('status') != 'scheduled':
                continue
            pid = str(post.get('post_id') or '')
            due_at = float(post.get('due_at') or 0.0)
            watch = post.get('watch') if isinstance(post.get('watch'), dict) else None
            matched_watch_evidence = ''
            is_ready = False

            if watch:
                interval = max(3.0, float(watch.get('interval_sec') or 10.0))
                last_chk = self._watch_last_check.get(pid, 0.0)
                if now - last_chk < interval:
                    continue
                self._watch_last_check[pid] = now
                ok_w, ev_w = await self._check_watchpoint(watch)
                if ok_w:
                    is_ready = True
                    matched_watch_evidence = ev_w
            elif due_at > 0 and now >= due_at:
                is_ready = True

            if not is_ready:
                continue

            fired_count += 1
            dirty = True
            if matched_watch_evidence:
                if isinstance(watch, dict):
                    watch['triggered'] = True
                post['watch_evidence'] = matched_watch_evidence[:600]
                post.setdefault('replies', []).append({
                    'reply_id': 'r-' + secrets.token_hex(3),
                    'author_bot_id': 'system',
                    'author_name': '黑板监视器',
                    'author_node': post.get('author_node', 'phone'),
                    'text': f'🎯 零算力条件监视命中：{matched_watch_evidence[:500]}',
                    'artifact_path': '',
                    'mentions': post.get('mentions', []),
                    'created_at': now,
                    'created_iso': now_iso(),
                })

            exec_act = str(post.get('exec_action') or '').strip().lower()
            if exec_act == 'ring_alarm':
                await self.brain._tool_emergency_alarm({
                    'title': str(post.get('title') or '黑板闹钟'),
                    'reason': str(post.get('text') or '黑板预定闹钟时间到'),
                })
            elif exec_act == 'notify_user':
                await self.brain._tool_notify_user({
                    'title': str(post.get('title') or '黑板提醒'),
                    'body': str(post.get('text') or ''),
                    'urgent': True,
                })

            sched = post.get('schedule') if isinstance(post.get('schedule'), dict) else {}
            if sched.get('daily_time') or sched.get('interval_hours'):
                next_due, _ = self._parse_due_timestamp(sched, now + 2.0)
                post['due_at'] = round(next_due, 3)
                post['due_iso'] = dt.datetime.fromtimestamp(next_due, TZ).isoformat(timespec='seconds')
                post['last_fired_at'] = now
            else:
                post['status'] = 'open'
                post['fired_at'] = now

            mentions = post.get('mentions') or ['@self']
            author_id = str(post.get('author_bot_id') or 'companion')
            author_src = normalize_node_source(post.get('author_node') or 'phone')
            targets = self._resolve_targets(mentions, author_id, author_src)
            post['unread_by'] = [t[0] for t in targets]
            label = '黑板监视命中' if matched_watch_evidence else '黑板闹钟到期'
            self._schedule_wakeup(post, targets, reason_label=label)

        if dirty:
            self.store.save()
        return fired_count

    async def _check_watchpoint(self, watch: dict) -> tuple[bool, str]:
        kind = str(watch.get('kind') or 'file_exists').strip().lower()
        target = str(watch.get('target') or '').strip()
        expect = str(watch.get('expect') or '').strip()
        if not target:
            return False, ''
        try:
            if kind in ('file_exists', 'file_contains'):
                try:
                    p = self.brain.harness.path(target)
                except Exception:
                    p = Path(target)
                if not p.exists():
                    return False, ''
                if kind == 'file_exists':
                    return True, f'文件已就绪: {target} ({p.stat().st_size} bytes)'
                body = p.read_text('utf-8', errors='replace')[:20000]
                if expect and expect in body:
                    return True, f'文件 {target} 已包含 "{expect}"'
                return False, ''
            if kind in ('cmd_exit', 'cmd_contains'):
                proc = await asyncio.create_subprocess_shell(
                    target,
                    cwd=str(self.brain.harness.root),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    env={**os.environ, 'LANG': 'C.UTF-8'},
                )
                out_b, _ = await asyncio.wait_for(proc.communicate(), timeout=8.0)
                out_s = (out_b or b'').decode('utf-8', errors='replace').strip()
                if kind == 'cmd_exit':
                    exp_code = int(expect) if expect.lstrip('-').isdigit() else 0
                    if proc.returncode == exp_code:
                        return True, f'命令退出码 {proc.returncode}: {out_s[:240]}'
                    return False, ''
                if expect and expect in out_s:
                    return True, f'命令输出命中 "{expect}": {out_s[:240]}'
                return False, ''
            if kind == 'http_status':
                async with httpx.AsyncClient(timeout=6.0, trust_env=False) as client:
                    r = await client.get(target)
                exp_st = int(expect) if expect.isdigit() else 200
                if r.status_code == exp_st:
                    return True, f'HTTP {target} 返回状态码 {r.status_code}'
        except Exception:
            return False, ''
        return False, ''

    def peek_bots(self, args: dict | None = None) -> dict:
        """Stateless, lock-free read-only snapshot of all Bots' live progress, recent messages, and active Blackboard items."""
        args = args or {}
        q = str(args.get('target') or args.get('bot_id') or 'all').strip().lstrip('@')
        limit = max(1, min(15, int(args.get('limit') or 5)))
        all_msgs = self.store.state.get('messages') or []
        posts = self._posts()
        assistants = [b for b in self.store.state.get('assistants', []) if isinstance(b, dict) and b.get('id')]

        if q and q.lower() not in ('all', 'everyone', '*', '全部', '全体'):
            q_low = q.lower()
            assistants = [
                b for b in assistants
                if str(b.get('id') or '').lower() == q_low
                or str(b.get('name') or '').strip().lower() == q_low
                or q_low in str(b.get('name') or '').strip().lower()
            ]

        snapshots: list[dict] = []
        for b in assistants:
            bid = str(b['id'])
            bname = str(b.get('name') or bid)
            home_node = str(b.get('home_node') or 'phone')
            running_nodes: list[str] = []
            cur_tool = ''
            cur_steps = 0
            stream_snippet = ''
            thought_snippet = ''
            for nk in ('phone', 'pc', 'phone2', 'pc2'):
                nb = f'{nk}:{bid}'
                ex = self.brain.executions_by_node_bot.get(nb)
                st = self.brain.streams_by_node_bot.get(nb)
                if ex or st or self.brain.is_node_bot_busy(bid, nk):
                    running_nodes.append(nk)
                    if isinstance(ex, dict):
                        cur_tool = cur_tool or str(ex.get('tool') or '')
                        cur_steps = max(cur_steps, len(ex.get('trace') or []))
                    if isinstance(st, dict):
                        stream_snippet = stream_snippet or str(st.get('text') or '')[:260]
                        thought_snippet = thought_snippet or str(st.get('thought') or '')[-220:]

            bot_msgs = [m for m in reversed(all_msgs) if isinstance(m, dict) and m.get('bot_id', 'companion') == bid and m.get('role') in ('user', 'assistant')][:limit]
            bot_msgs.reverse()
            recent_summary = [{
                'role': m.get('role'), 'source': normalize_node_source(m.get('source')), 'text': str(m.get('text') or '').strip()[:240],
                'tools_used': [t.get('tool') for t in (m.get('trace') or []) if isinstance(t, dict) and t.get('tool')][:6],
                'stopped_early': bool(m.get('stopped_early', False)), 'created_at': m.get('created_at') or m.get('created'),
            } for m in bot_msgs]
            open_items = [{
                'post_id': p.get('post_id'), 'title': p.get('title'), 'status': p.get('status'),
                'claimed_by': p.get('claimed_by'), 'artifact_path': p.get('artifact_path', ''),
            } for p in reversed(posts) if isinstance(p, dict)
                and p.get('status') in ('open', 'claimed', 'review_required', 'scheduled')
                and (p.get('claimed_by') == bid or bid in (p.get('target_bot_ids') or []) or p.get('author_bot_id') == bid)
            ][:8]
            eff_bot = resolve_bot_for_source(b, home_node if home_node in ('phone', 'pc') else 'phone')
            snapshots.append({
                'id': bid, 'bot_id': bid, 'name': bname, 'home_node': home_node,
                'home_node_label': node_display_name(home_node if home_node in ('phone', 'pc', 'phone2', 'pc2') else 'phone'),
                'status': 'running' if running_nodes else 'idle', 'running_nodes': running_nodes,
                'current_tool': cur_tool or None, 'steps_executed': cur_steps,
                'live_stream_snippet': stream_snippet, 'live_thought_snippet': thought_snippet,
                'model': eff_bot.get('model') or self.store.config.effective_model(big=b.get('engine') == 'big'),
                'recent_messages': recent_summary, 'open_blackboard_items': open_items,
            })
        return {
            'ok': True, 'count': len(snapshots), 'bots': snapshots,
            'pc_dsh_online': bool(self.brain.is_dsh_online()) if hasattr(self.brain, 'is_dsh_online') else False,
            'pc_dsh_sessions': self.brain._get_dsh_sessions_list()[:8] if hasattr(self.brain, '_get_dsh_sessions_list') else [],
        }
