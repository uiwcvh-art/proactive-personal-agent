from __future__ import annotations

import asyncio
import contextlib
import re
import secrets
import time
from typing import Any

from assistants import get_assistant, resolve_bot_for_source
from harness import TOOLS
from storage import compact_trace_list, node_display_name, normalize_node_source


class InterconnectRouterMixin:
    """Domain Controller for Cross-Node (`Windows一`/`安卓一`/`安卓二`) & Multi-Bot Interconnect Routing."""

    def _resolve_bot(self, query: str | dict | None):
        if isinstance(query, dict):
            return query
        q = (query or '').strip()
        if not q:
            return None
        bots = self.store.state.get('assistants', [])
        for b in bots:
            if b['id'] == q or b['name'] == q:
                return b
        q_low = q.lower()
        alias_map = {
            '白珂': 'companion',
            'baike': 'companion',
            '主助理': 'companion',
            '研究助手': 'research',
            '研究员': 'research',
            '杠精': 'contrarian',
            '反方': 'contrarian',
            '鲸鱼娘': 'whalechan',
            '小鲸鱼': 'whalechan',
            '大鲸鱼': 'whalechan',
            'whale': 'whalechan',
        }
        if q_low in alias_map:
            mapped_id = alias_map[q_low]
            for b in bots:
                if b['id'] == mapped_id:
                    return b
        for b in bots:
            if b['id'].lower() == q_low or b['name'].lower() == q_low or q_low in b['name'].lower() or b['name'].lower() in q_low:
                return b
        if q_low in ('b', 'bot b', 'bot_b', '助手b', '老二', '2') and len(bots) >= 2:
            primary = self.store.state.get('primary_bot_id', 'companion')
            return next((b for b in bots if b['id'] != primary), bots[1])
        return None

    def _parse_cross_node_target(self, raw_target: str, explicit_node: str = '', cur_src: str = 'phone') -> tuple[str, dict | None]:
        """
        Parse any target specifier into `(target_node_source, target_bot_dict)`.
        Examples supported:
        - "Windows一:研究助手", "Windows一/杠精", "Windows一·鲸鱼娘", "pc:research", "电脑端:白珂"
        - "安卓二:白珂", "安卓一:鲸鱼娘", "phone2:contrarian", "平板:研究助手"
        - "Windows一", "pc", "电脑端", "安卓一", "安卓二" (defaults to same bot_id on target node)
        - "研究助手" with explicit_node="Windows一"
        - "研究助手" without node (same-node bot: target_src == normalize_node_source(cur_src))
        """
        norm_cur = normalize_node_source(cur_src)
        from_id = self.bot_context.get() or self.store.state.get('primary_bot_id', 'companion')
        default_bot = get_assistant(self.store, from_id)

        node_tokens = {
            'windows一': 'pc', 'windows1': 'pc', 'windows': 'pc', 'win1': 'pc', 'pc': 'pc',
            'pc_dsh': 'pc', 'dsh': 'pc', 'dsb': 'pc', '电脑': 'pc', '电脑端': 'pc', '桌面端': 'pc',
            'deepseek-harness': 'pc', 'harness': 'pc',
            'windows二': 'pc2', 'windows2': 'pc2', 'win2': 'pc2', 'pc2': 'pc2', '电脑二': 'pc2',
            '安卓一': 'phone', '安卓1': 'phone', 'android1': 'phone', 'phone': 'phone',
            'phone1': 'phone', 'phone_dsb': 'phone', '手机': 'phone', '手机端': 'phone', '安卓': 'phone', 'android': 'phone',
            '安卓二': 'phone2', '安卓2': 'phone2', 'android2': 'phone2', 'phone2': 'phone2',
            'tablet': 'phone2', 'pad': 'phone2', '平板': 'phone2', '平板端': 'phone2',
        }

        target_src = normalize_node_source(explicit_node) if explicit_node.strip() else ''
        bot_query = (raw_target or '').strip()

        if '@' in bot_query and not bot_query.startswith('@'):
            left_p, right_p = [x.strip() for x in bot_query.split('@', 1)]
            if right_p.lower() in node_tokens:
                target_src = node_tokens[right_p.lower()]
                bot_query = left_p
            elif left_p.lower() in node_tokens:
                target_src = node_tokens[left_p.lower()]
                bot_query = right_p

        for sep in (':', '：', '/', '·', '|', ' '):
            if sep in bot_query:
                p1, p2 = [x.strip() for x in bot_query.split(sep, 1)]
                if p1.lower() in node_tokens:
                    target_src = node_tokens[p1.lower()]
                    bot_query = p2
                    break
                if p2.lower() in node_tokens:
                    target_src = node_tokens[p2.lower()]
                    bot_query = p1
                    break

        low_q = bot_query.lower()
        if low_q in node_tokens:
            target_src = node_tokens[low_q]
            bot_query = ''
        elif not target_src:
            for nk, nv in sorted(node_tokens.items(), key=lambda x: len(x[0]), reverse=True):
                if low_q.startswith(nk):
                    rest = bot_query[len(nk):].lstrip('的:：/·-— ')
                    target_src = nv
                    bot_query = rest
                    break

        if not target_src:
            target_src = norm_cur

        if not bot_query or bot_query.lower() in ('permanent', 'default', 'auto', 'main', '主会话', '默认'):
            return target_src, default_bot

        resolved = self._resolve_bot(bot_query)
        if resolved is None and target_src != norm_cur:
            return target_src, default_bot
        return target_src, resolved

    async def _tool_manage_bot(self, args: dict):
        action = str(args.get('action') or 'list').strip().lower()
        self.store.sync_from_workspace()
        bots = self.store.state.get('assistants', [])
        if action == 'list':
            return {
                'primary_bot_id': self.store.state.get('primary_bot_id', 'companion'),
                'bots': [
                    {
                        'id': b['id'],
                        'name': b['name'],
                        'model': b.get('model') or self.store.config.effective_model(),
                        'raw_model_field': b.get('model', ''),
                        'description': b.get('description', ''),
                        'curiosity_enabled': b.get('curiosity_enabled', False),
                    }
                    for b in bots
                ]
            }
        if action == 'create':
            name = str(args.get('name') or args.get('bot_id') or '').strip()
            if not name:
                return {'error': 'create 需要提供 name'}
            if len(bots) >= 30:
                return {'error': '最多创建 30 位 Bot'}
            requested_id = re.sub(r'[^a-zA-Z0-9_-]', '', str(args.get('bot_id') or '')).lower()
            existing_ids = {b['id'] for b in bots}
            new_id = requested_id if (requested_id and requested_id not in existing_ids) else secrets.token_hex(6)
            new_bot = {
                'id': new_id,
                'name': name[:40],
                'title': '',
                'description': str(args.get('description') or f'负责专项协作的 {name}')[:160],
                'instructions': str(args.get('instructions') or f'你是 {name}，专业、直接、善用工具完成任务并汇报结果。')[:12000],
                'model': str(args.get('model') or '').strip()[:200],
                'curiosity_enabled': bool(args.get('curiosity_enabled', False)),
                'engine': 'small',
                'color': '#28B9D1',
                'shape': 'circle',
                'notify': True,
                'tools': [t['function']['name'] for t in TOOLS],
                'pinned': False,
                'hidden': False,
                'pinned_at': 0,
                'avatar_version': '',
                'created_at': time.time(),
            }
            bots.append(new_bot)
            deleted_list = self.store.state.get('deleted_bot_ids')
            if isinstance(deleted_list, list) and new_id in deleted_list:
                self.store.state['deleted_bot_ids'] = [x for x in deleted_list if x != new_id]
            self.store.save()
            await self.publish({'type': 'bots_changed', 'bot_id': new_id, 'bots': self.store.state['assistants'], 'primary_bot_id': self.store.state.get('primary_bot_id', 'companion')})
            return {'created': True, 'bot': new_bot}
        if action in ('update', 'update_model', 'update_prompt'):
            target_q = str(args.get('bot_id') or args.get('name') or '').strip()
            target = self._resolve_bot(target_q)
            if not target:
                return {'error': f'未找到 Bot "{target_q}"'}
            cur_src = normalize_node_source(self.source_context.get() or 'phone')
            ns_map = target.setdefault('node_settings', {})
            cur_ns = ns_map.setdefault(cur_src, {
                'curiosity_enabled': bool(target.get('curiosity_enabled', target.get('id') == 'companion')) if cur_src == 'phone' else False,
                'notify': bool(target.get('notify', True)),
                'model': str(target.get('model') or ''),
                'pinned': bool(target.get('pinned', False)),
                'pinned_at': float(target.get('pinned_at') or 0),
                'hidden': bool(target.get('hidden', False)),
            })
            if 'name' in args and str(args['name']).strip():
                target['name'] = str(args['name']).strip()[:40]
            if 'model' in args:
                m_val = str(args['model']).strip()[:200]
                cur_ns['model'] = m_val
                if cur_src == 'phone':
                    target['model'] = m_val
            if 'description' in args:
                target['description'] = str(args['description']).strip()[:160]
            if 'instructions' in args and str(args['instructions']).strip():
                target['instructions'] = str(args['instructions']).strip()[:12000]
            if 'curiosity_enabled' in args:
                c_val = bool(args['curiosity_enabled'])
                cur_ns['curiosity_enabled'] = c_val
                if cur_src == 'phone':
                    target['curiosity_enabled'] = c_val
            target['updated_at'] = time.time()
            self.store.save()
            await self.publish({'type': 'bots_changed', 'source': cur_src, 'bot_id': target['id'], 'bots': self.store.state['assistants'], 'primary_bot_id': self.store.state.get('primary_bot_id', 'companion')})
            return {'updated': True, 'bot': resolve_bot_for_source(target, cur_src)}
        if action == 'delete':
            target_q = str(args.get('bot_id') or args.get('name') or '').strip()
            target = self._resolve_bot(target_q)
            if not target:
                return {'error': f'未找到 Bot "{target_q}"'}
            if target['id'] == self.store.state.get('primary_bot_id'):
                return {'error': '不能删除当前主助理'}
            del_id = target['id']
            deleted_list = self.store.state.setdefault('deleted_bot_ids', [])
            if del_id not in deleted_list:
                deleted_list.append(del_id)
            self.store.state['assistants'] = [b for b in bots if b['id'] != del_id]
            self.store.state['routines'] = [r for r in self.store.state.get('routines', []) if r.get('bot_id') != del_id]
            self.store.state['timers'] = [t for t in self.store.state.get('timers', []) if t.get('bot_id') != del_id]
            self.store.state['messages_epoch'] = int(self.store.state.get('messages_epoch', 1)) + 1
            self.store.save()
            await self.publish({'type': 'bots_changed', 'deleted_bot_id': del_id, 'bots': self.store.state['assistants'], 'primary_bot_id': self.store.state.get('primary_bot_id', 'companion')})
            return {'deleted': True, 'bot_id': del_id}
        return {'error': f'未知 manage_bot action: {action}'}

    async def _tool_send_message(self, args: dict):
        raw_target = str(args.get('to_bot_id') or args.get('target') or args.get('colleague') or args.get('bot_id') or '').strip()
        explicit_node = str(args.get('to_node') or args.get('target_node') or args.get('node') or args.get('source') or '').strip()
        text = str(args.get('text') or args.get('message') or '').strip()
        if (not raw_target and not explicit_node) or not text:
            return {'error': 'to_bot_id 和 text 不能为空'}
        self.store.sync_from_workspace()

        cur_src = normalize_node_source(self.source_context.get() or 'phone')
        cur_node_label = node_display_name(cur_src)
        from_id = self.bot_context.get()
        room_id = self.room_context.get() or from_id
        from_bot = get_assistant(self.store, from_id)
        parent_turn_id = self.turn_id_var.get() or ('turn-' + secrets.token_hex(6))

        # 1. Check if the target is a bottom DeepSeek Harness Workspace Session (e.g. "会话:好饿", "会话:1", "工作区:1", or direct title/short_id match)
        sess_prefixes = ('会话:', '会话：', 'session:', '工作区:', '工作区：', '任务:', '任务：', 'dsh:')
        is_explicit_session = any(raw_target.lower().startswith(p) for p in sess_prefixes)
        sess_query = raw_target
        for p in sess_prefixes:
            if raw_target.lower().startswith(p):
                sess_query = raw_target[len(p):].strip()
                break

        dsh_sessions = self._get_dsh_sessions_list()
        matched_session = None
        if is_explicit_session or raw_target:
            q_low = sess_query.lower()
            for s in dsh_sessions:
                stitle = str(s.get('title') or '').strip()
                ssid = str(s.get('short_id') or '').strip()
                sfid = str(s.get('session_id') or '').strip()
                if q_low and (stitle.lower() == q_low or ssid.lower() == q_low or sfid.lower() == q_low):
                    matched_session = s
                    break
            if matched_session is None and is_explicit_session and q_low:
                for s in dsh_sessions:
                    stitle = str(s.get('title') or '').strip()
                    ssid = str(s.get('short_id') or '').strip()
                    sfid = str(s.get('session_id') or '').strip()
                    if q_low in stitle.lower() or sfid.lower().startswith(q_low):
                        matched_session = s
                        break

        target_src, target_bot = (None, None) if is_explicit_session else self._parse_cross_node_target(raw_target, explicit_node=explicit_node, cur_src=cur_src)

        caller_exec = self.executions_by_node_bot.get(f'{cur_src}:{room_id}') or self.executions_by_bot.get(room_id) or {}
        caller_st = self.streams_by_node_bot.get(f'{cur_src}:{room_id}') or self.streams_by_bot.get(room_id) or {}
        caller_thought = str(caller_st.get('thought') or '').strip()
        if caller_thought.startswith(('正在生成 ', '正在准备调用 ')):
            caller_thought = ''
        outbound_trace = compact_trace_list([
            {**t, 'status': 'done', 'duration_ms': max(1, int(t.get('duration_ms') or 1))}
            for t in (caller_exec.get('trace') or [])
            if isinstance(t, dict)
        ])
        if not any(t.get('tool') in ('send_message', 'message_colleague') for t in outbound_trace):
            outbound_trace.append({
                'tool': 'send_message',
                'arguments': {'to_bot_id': raw_target, 'text': text[:240]},
                'duration_ms': 1,
                'status': 'done',
            })

        if is_explicit_session or (target_bot is None and matched_session is not None):
            sess_id_to_send = str((matched_session or {}).get('session_id') or (matched_session or {}).get('short_id') or sess_query).strip()
            sess_title_label = str((matched_session or {}).get('title') or sess_query).strip()
            if not sess_id_to_send:
                return {'error': '请指定要发送的工作区会话标题或 ID，例如 send_message(to_bot_id="会话:1", text="...")'}
            target_sess_label = f'工作区会话 · {sess_title_label}'
            await self._set_stream(
                text=f'正在等待「{target_sess_label}」执行并回复…',
                tool=f'send_message → {sess_title_label}',
                speaker_id=from_id,
                speaker_name=from_bot['name'],
                thought=None,
            )
            res = await self._raw_dsh_rpc(
                'dispatch_session',
                {'session_id': sess_id_to_send, 'message': text, 'wait': True, 'timeout_sec': 90},
                timeout=95.0,
            )
            reply_str = str(res.get('assistant_reply') or res.get('message') or res.get('error') or '（工作区会话已接收并执行完毕）').strip()
            final_sess_title = str(res.get('title') or sess_title_label).strip()
            return {
                'ok': bool(res.get('ok')),
                'status': 'session_dispatched' if res.get('ok') else 'error',
                'session_id': res.get('session_id') or sess_id_to_send,
                'session_title': final_sess_title,
                'sent_text': text,
                'reply': reply_str,
                'detail': res,
            }

        if target_bot is None:
            available = [f"{b['id']} ({b['name']})" for b in self.store.state.get('assistants', [])]
            sess_hints = [f"会话:{s.get('title')} ({s.get('short_id')})" for s in dsh_sessions[:8]]
            return {
                'error': (
                    f'未找到目标 Bot 或工作区会话 "{raw_target}"。'
                    f'可用 Bot: {", ".join(available)}（跨端可写 "Windows一:companion"、"Windows一:研究助手"、"安卓一:companion" 等）；'
                    + (f'可用电脑端工作区会话: {", ".join(sess_hints)}。' if sess_hints else '也可写 "会话:<工作区会话名>" 发给电脑端底部工作区会话。')
                )
            }

        target_id = target_bot['id']
        target_node_label = node_display_name(target_src)
        if target_src == cur_src and target_id == from_id:
            return {'error': '不能给自己发送 send_message（如需发给其他端的同名 Bot，请在 to_bot_id 中加上节点前缀，如 "Windows一:companion" 或 "安卓一:companion"）'}

        cur_sig = f'{cur_src}:{from_id}'
        target_sig = f'{target_src}:{target_id}'
        sender_label = from_bot['name'] if target_src == cur_src else f'{from_bot["name"]} · {cur_node_label}'
        target_label = target_bot['name'] if target_src == cur_src else f'{target_bot["name"]} · {target_node_label}'

        # 2. If target_sig is ALREADY waiting for our reply in an active turn (i.e. we are calling send_message to reply back to the caller),
        # deliver the reply into target_sig's reply box immediately without emitting any trace=[] intermediate bubble!
        if target_sig in self._reply_box_by_sig:
            self._reply_box_by_sig[target_sig].append({
                'from_bot_id': from_id,
                'from_bot_name': sender_label,
                'text': text,
            })
            return {
                'ok': True,
                'status': 'delivered_reply',
                'to_bot_id': target_id,
                'to_bot_name': target_label,
                'delivered_text': text,
                'note': f'已通过 send_message 将回复送达「{target_label}」。',
            }

        # 3. Strict 1-on-1 Room & CoT Isolation:
        # Never emit `bot_message` or `bot_reply` bubbles into Caller's room (`room_id`),
        # and never mirror Target Bot's live stream or CoT into Caller's room!
        depth = self.hop_depth.get()
        if depth >= 4:
            return {'error': 'Bot 间连续转发已达单次上限 (4 跳)，请先总结当前进展'}
        chain = list(self.call_chain_var.get() or (cur_sig,))
        if target_sig in chain:
            return {'error': f'检测到 Bot 循环调用链路 ({" -> ".join(chain + [target_sig])})，已自动拦截以防死锁；请直接汇总当前信息。'}

        incoming_prompt = (
            f'【跨端/同事 Bot 协作来信 · 来自「{sender_label}」的同级触发委托】（请站在你自己的专业职责视角独立完成任务并直接输出完整汇报；系统会将你的正文回传给「{sender_label}」，你的思考过程与会话空间严格独立）：\n\n'
            f'{text}\n\n'
            f'[协作来信 · 来自「{sender_label}」—— 处理完毕后可直接输出回复正文，或调用 `send_message(to_bot_id="{cur_node_label}:{from_bot["name"]}", text="...")` 回信]'
        )
        deferred_incoming_spec = {
            'role': 'user',
            'text': text,
            'kind': 'bot_message',
            'bot_id': target_id,
            'from_bot_id': from_id,
            'from_bot_name': sender_label,
            'from_node': cur_src,
            'to_bot_id': target_id,
            'to_bot_name': target_label,
            'to_node': target_src,
            'direction': 'inbound',
            'trace': [],
            'notify': True,
            'source': target_src,
            'llm_text': incoming_prompt,
            'turn_id': parent_turn_id,
            'is_sub_turn': True,
        }
        inbound_msg = self.store.message(
            'user',
            text,
            kind='bot_message',
            bot_id=target_id,
            from_bot_id=from_id,
            from_bot_name=sender_label,
            from_node=cur_src,
            to_bot_id=target_id,
            to_bot_name=target_label,
            to_node=target_src,
            direction='inbound',
            trace=[],
            notify=True,
            source=target_src,
            llm_text=incoming_prompt,
        )
        self.stream_seq += 1
        inbound_msg['seq'] = self.stream_seq
        inbound_msg['turn_id'] = parent_turn_id
        inbound_msg['is_sub_turn'] = True
        self.store.save()
        await self.publish({'type': 'message', 'source': target_src, 'message': inbound_msg, 'seq': self.stream_seq})

        if cur_src != target_src and target_src in ('phone', 'phone2'):
            with contextlib.suppress(Exception):
                await self.publish({
                    'type': 'user_notification',
                    'id': 'xnode-' + secrets.token_hex(4),
                    'bot_id': target_id,
                    'bot_name': sender_label,
                    'title': f'{sender_label} 发来跨端协作消息',
                    'body': text[:240],
                    'urgent': False,
                })

        caller_user_txt = str(self._current_user_text_by_bot.get(from_id) or '').strip()
        auto_async_delegate = bool(
            depth == 0
            and caller_user_txt
            and re.search(r'(?:派发?给|交给|丢给|甩给|转给|分给).*(?:跟(?:我|余)|陪(?:我|余)|继续.*聊)', caller_user_txt)
        )
        raw_wait = args.get('wait')
        should_wait = False if (raw_wait is False or str(raw_wait).lower() == 'false' or auto_async_delegate) else True

        if should_wait:
            wait_hint = (
                f'⏳ 「{target_label}」正在处理上一条消息，排队等待中…'
                if self._get_bot_lock(target_id, target_src).locked()
                else f'正在等待「{target_label}」协作处理…'
            )
            await self._set_stream(
                text=wait_hint,
                tool=f'send_message → {target_label}',
                speaker_id=from_id,
                speaker_name=from_bot['name'],
                thought=None,
            )

        reply_box_list: list[dict] = []
        reply_box_key = f'{cur_sig}:{parent_turn_id}'
        self._reply_box_by_sig[reply_box_key] = reply_box_list
        self._reply_box_by_sig[cur_sig] = reply_box_list

        async def _run_peer_turn() -> dict:
            parent_stream_token = self.parent_stream_room_var.set(None)
            depth_token = self.hop_depth.set(depth + 1)
            chain_token = self.call_chain_var.set(tuple(chain + [target_sig]))
            bot_token = self.bot_context.set(target_id)
            room_token = self.room_context.set(target_id)
            src_token = self.source_context.set(target_src)
            sub_turn_token = self.turn_id_var.set('turn-' + secrets.token_hex(6))
            sub_prefix_token = self.stream_prefix_var.set('')
            sub_thought_token = self.thought_prefix_var.set('') if hasattr(self, 'thought_prefix_var') else None
            sub_choices_token = self.choices_var.set(None)
            sub_cp_token = self.choices_prompt_var.set('')
            sub_stk_token = self.pending_sticker_var.set('')
            sub_stks_token = self.pending_stickers_var.set(())
            cur_sub_task = asyncio.current_task()
            if cur_sub_task:
                self.queued_tasks_by_bot.setdefault(target_id, set()).add(cur_sub_task)
            peer_res = None
            try:
                peer_res = await self._chat(
                    incoming_prompt,
                    pre_recorded_user_msg=inbound_msg,
                    deferred_incoming_spec=deferred_incoming_spec,
                    source=target_src,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                err_text = f'调用 {target_label} 失败: {exc}'
                err_msg_in_target = self.store.message(
                    'assistant',
                    err_text,
                    kind='error',
                    bot_id=target_id,
                    from_bot_id=target_id,
                    from_bot_name=target_label,
                    to_bot_id=from_id,
                    to_bot_name=sender_label,
                    notify=False,
                    source=target_src,
                )
                self.stream_seq += 1
                err_msg_in_target['seq'] = self.stream_seq
                await self.publish({'type': 'message', 'source': target_src, 'message': err_msg_in_target, 'seq': self.stream_seq})
                peer_res = err_msg_in_target
            finally:
                if cur_sub_task:
                    self.queued_tasks_by_bot.get(target_id, set()).discard(cur_sub_task)
                self.pending_stickers_var.reset(sub_stks_token)
                self.pending_sticker_var.reset(sub_stk_token)
                self.choices_prompt_var.reset(sub_cp_token)
                self.choices_var.reset(sub_choices_token)
                if sub_thought_token is not None and hasattr(self, 'thought_prefix_var'):
                    self.thought_prefix_var.reset(sub_thought_token)
                self.stream_prefix_var.reset(sub_prefix_token)
                self.turn_id_var.reset(sub_turn_token)
                self.source_context.reset(src_token)
                self.room_context.reset(room_token)
                self.bot_context.reset(bot_token)
                self.call_chain_var.reset(chain_token)
                self.hop_depth.reset(depth_token)
                self.parent_stream_room_var.reset(parent_stream_token)
                self._reply_box_by_sig.pop(reply_box_key, None)
                if self._reply_box_by_sig.get(cur_sig) is reply_box_list:
                    self._reply_box_by_sig.pop(cur_sig, None)
                replies = list(reply_box_list)
                if not replies and isinstance(peer_res, dict) and str(peer_res.get('text') or '').strip():
                    replies.append({
                        'from_bot_id': target_id,
                        'from_bot_name': target_label,
                        'text': str(peer_res.get('text') or '').strip(),
                    })

            trace_items = list((peer_res or {}).get('trace') or [])
            with contextlib.suppress(Exception):
                ts_str = time.strftime('%Y-%m-%d %H:%M:%S')
                tools_str = ', '.join(str(t.get('tool') or '') for t in trace_items if isinstance(t, dict)) if trace_items else '直接处理'
                reply_summary = (' / '.join(r['text'] for r in replies) if replies else '（未回发消息，已在自身会话内处理）')[:200]
                digest_line = (
                    f"- **{ts_str}** [{cur_node_label}·{from_bot['name']} -> {target_node_label}·{target_bot['name']}] "
                    f"指令: `{text[:120]}` | 工具: `{tools_str}` | 回发: {reply_summary}"
                )
                self.vault.append_work_log(digest_line)

            if replies:
                combined_reply = '\n\n'.join(str(r.get('text') or '') for r in replies if r.get('text')).strip()
                peer_is_error = str((peer_res or {}).get('kind') or '') == 'error'
                if depth == 0 and not should_wait and combined_reply:
                    asyncio.create_task(
                        self._deliver_async_peer_callback(
                            caller_bot_id=from_id,
                            caller_room_id=room_id,
                            caller_src=cur_src,
                            peer_id=target_id,
                            peer_label=target_label,
                            sent_text=text,
                            peer_reply=combined_reply,
                            is_error=peer_is_error,
                        )
                    )
                if peer_is_error:
                    return {
                        'ok': False,
                        'status': 'error',
                        'delivery_state': 'delivered_but_errored',
                        'error': combined_reply,
                        'target_node': target_node_label,
                        'target_source': target_src,
                        'from_bot_id': target_id,
                        'from_bot_name': target_label,
                        'sent_text': text,
                    }
                return {
                    'ok': True,
                    'status': 'replied',
                    'delivery_state': 'delivered_and_replied',
                    'target_node': target_node_label,
                    'target_source': target_src,
                    'pc_sentinel_online': bool(self.is_dsh_online()) if target_src == 'pc' and hasattr(self, 'is_dsh_online') else None,
                    'from_bot_id': target_id,
                    'from_bot_name': target_label,
                    'sent_text': text,
                    'reply': combined_reply,
                    'replies': replies,
                    'target_trace_count': len(trace_items),
                }
            return {
                'ok': True,
                'status': 'delivered_no_reply',
                'delivery_state': 'delivered_and_processed',
                'target_node': target_node_label,
                'target_source': target_src,
                'to_bot_id': target_id,
                'to_bot_name': target_label,
                'sent_text': text,
                'target_trace_count': len(trace_items),
                'note': f'消息已送达「{target_label}」并在其独立会话中执行完毕（对方处理了 {len(trace_items)} 步工具）。',
            }

        peer_task = asyncio.create_task(_run_peer_turn())
        if not should_wait:
            await asyncio.sleep(0)
            return {
                'ok': True,
                'status': 'dispatched_async',
                'delivery_state': 'queued_async',
                'target_node': target_node_label,
                'target_source': target_src,
                'to_bot_id': target_id,
                'to_bot_name': target_label,
                'sent_text': text,
                'inbound_message_id': inbound_msg.get('id', ''),
                'note': (
                    f'已将任务投递至「{target_label}」独立会话并在后台启动执行；'
                    f'对方完成后系统会唤醒你向主人转达结果，你现在可以立即以自己的身份回复主人继续聊天！'
                ),
            }
        return await asyncio.shield(peer_task)

    async def _deliver_async_peer_callback(
        self,
        caller_bot_id: str,
        caller_room_id: str,
        caller_src: str,
        peer_id: str,
        peer_label: str,
        sent_text: str,
        peer_reply: str,
        is_error: bool = False,
    ) -> None:
        """
        Deliver an async background delegation completion strictly via the Caller Bot's own voice & room,
        NEVER injecting the Peer Bot's bubble or CoT into the Caller Bot's conversation!
        """
        src = normalize_node_source(caller_src)
        async with self._get_bot_lock(caller_bot_id, src):
            caller_bot = get_assistant(self.store, caller_bot_id, source=src)
            cb_turn_id = 'turn-' + secrets.token_hex(6)
            b_tok = self.bot_context.set(caller_bot_id)
            r_tok = self.room_context.set(caller_room_id)
            s_tok = self.source_context.set(src)
            t_tok = self.turn_id_var.set(cb_turn_id)
            p_tok = self.stream_prefix_var.set('')
            th_tok = self.thought_prefix_var.set('') if hasattr(self, 'thought_prefix_var') else None
            cb_trace = [{
                'tool': 'send_message_callback',
                'arguments': {'from_bot_id': peer_id, 'from_bot_name': peer_label, 'task': sent_text[:160]},
                'result': {'ok': not is_error, 'reply': peer_reply[:600]},
                'duration_ms': 1,
                'status': 'error' if is_error else 'done',
            }]
            my_exec = {'bot_id': caller_room_id, 'source': src, 'goal': f'汇总「{peer_label}」后台任务结果', 'trace': cb_trace, 'tool': f'收到「{peer_label}」回执', 'started_at': time.time()}
            self.executions_by_node_bot[f'{src}:{caller_room_id}'] = my_exec
            try:
                api_key = self.store.config.effective_api_key(big=False) or self.store.config.effective_api_key(big=True)
                model = self.store.config.effective_model(bot_model=caller_bot.get('model', ''), big=False) or self.store.config.effective_model(bot_model=caller_bot.get('model', ''), big=True)
                report_text = ''
                caller_thought_out = ''
                if api_key and model:
                    await self._set_stream(
                        text=f'正在汇总「{peer_label}」后台完成结果…',
                        tool=f'收到「{peer_label}」回执',
                        speaker_id=caller_bot_id,
                        speaker_name=caller_bot['name'],
                        thought=None,
                    )
                    sys_p = (
                        '【角色设定与最高响应准则】\n' + self.persona(caller_bot_id) + '\n\n'
                        '【后台协作回执转达规则】：你此前派发给同事 Bot 的异步后台任务刚刚完成。请严格站在你自己的身份与口吻下，把完成结果简明自然地汇报给主人；严禁冒充同事 Bot 的名字自称。'
                    )
                    cb_prompt = (
                        f'【系统后台协作回执 · 来自「{peer_label}」】\n'
                        f'- 原派发指令：{sent_text[:400]}\n'
                        f'- 对方执行状态：{"报错" if is_error else "已完成"}\n'
                        f'- 对方回传结果：\n{peer_reply[:3000]}\n\n'
                        f'请直接以「{caller_bot["name"]}」自己的身份向主人汇报这项后台任务的完成结果。'
                    )
                    with contextlib.suppress(Exception):
                        resp = await self.complete(
                            [{'role': 'system', 'content': sys_p}, {'role': 'user', 'content': cb_prompt}],
                            tools=None,
                            big=False,
                        )
                        raw_c = self._clean_and_filter_dot_text(resp.get('content') or '')
                        clean_c, _ = self._extract_reply_piece(raw_c)
                        clean_c, _, tag_th, _ = self._extract_all_tags(clean_c or raw_c)
                        report_text = self._clean_and_filter_dot_text(clean_c)
                        caller_thought_out = str(resp.get('thought') or resp.get('reasoning_content') or tag_th or '').strip()
                if not report_text:
                    report_text = (
                        f'主人，刚才派给「{peer_label}」的后台任务执行出错了：{peer_reply}'
                        if is_error
                        else f'主人，刚才派给「{peer_label}」在后台处理的任务已经完成啦，结果如下：\n\n{peer_reply}'
                    )
                await self.emit(
                    report_text,
                    'error' if is_error else 'chat',
                    track=False,
                    bot_id=caller_room_id,
                    from_bot_id=caller_bot_id,
                    from_bot_name=caller_bot['name'],
                    from_node=src,
                    thought=caller_thought_out,
                    trace=cb_trace,
                    source=src,
                )
            finally:
                self._clear_bot_stream(caller_room_id, source=src)
                with contextlib.suppress(Exception):
                    await self.publish({'type': 'stream', 'source': src, 'bot_id': caller_room_id, 'turn_id': cb_turn_id, 'stream': None, 'seq': self.stream_seq})
                    await self.publish({'type': 'execution', 'source': src, 'bot_id': caller_room_id, 'execution': None, 'seq': self.stream_seq})
                if th_tok is not None and hasattr(self, 'thought_prefix_var'):
                    self.thought_prefix_var.reset(th_tok)
                self.stream_prefix_var.reset(p_tok)
                self.turn_id_var.reset(t_tok)
                self.source_context.reset(s_tok)
                self.room_context.reset(r_tok)
                self.bot_context.reset(b_tok)
