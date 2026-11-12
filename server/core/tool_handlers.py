from __future__ import annotations

import ast
import asyncio
import json
import os
import re
import secrets
import signal
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from assistants import get_assistant
from storage import normalize_node_source
from vault_manager import TZ, now_iso


class ToolHandlersMixin:
    """Domain Controller for Scheduling, Background Tasks, Watchdogs, Memory Search, Skills, Blackboard & Notifications."""

    async def _tool_list_models(self, args: dict):
        models = await self._fetch_available_models()
        return {
            'models': models[:100],
            'total': len(models),
            'default_model': self.store.config.effective_model(),
            'api_base_url': self.store.config.effective_base_url(),
        }

    @staticmethod
    def _fmt_bj(ts: float | None) -> str:
        if not ts:
            return ''
        dt = datetime.fromtimestamp(float(ts), TZ)
        wk = ('周一', '周二', '周三', '周四', '周五', '周六', '周日')[dt.weekday()]
        return f"{dt.strftime('%Y-%m-%d %H:%M:%S')} ({wk})"

    @staticmethod
    def _parse_wall_time(raw: str, now_ts: float | None = None) -> float:
        s = raw.strip()
        base_ts = now_ts if now_ts is not None else time.time()
        now_dt = datetime.fromtimestamp(base_ts, TZ)
        m_hm = re.fullmatch(r'(\d{1,2}):(\d{2})(?::(\d{2}))?', s)
        if m_hm:
            hh, mm, ss = int(m_hm.group(1)), int(m_hm.group(2)), int(m_hm.group(3) or 0)
            if hh == 24 and mm == 0 and ss == 0:
                hh = 0
            if not (0 <= hh <= 23 and 0 <= mm <= 59 and 0 <= ss <= 59):
                raise ValueError(f'时间格式非法: {raw}（小时须为 00-23，分钟/秒须为 00-59）')
            target = now_dt.replace(hour=hh, minute=mm, second=ss, microsecond=0)
            if target.timestamp() <= base_ts:
                target += timedelta(days=1)
            return target.timestamp()
        s_iso = s.replace(' ', 'T') if re.match(r'^\d{4}-\d{2}-\d{2}\s+\d{1,2}:\d{2}', s) else s
        dt = datetime.fromisoformat(s_iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ)
        return dt.timestamp()

    @staticmethod
    def _validate_daily_time_slots(daily_raw: str) -> list[tuple[int, int, int]]:
        s = (daily_raw or '').strip()
        if not s:
            return []
        slots: list[tuple[int, int, int]] = []
        for part in s.split(','):
            p = part.strip()
            if not p:
                continue
            m = re.fullmatch(r'(\d{1,2}):(\d{2})(?::(\d{2}))?', p)
            if not m:
                raise ValueError(f'daily_time 格式非法: {p}（请使用 HH:MM，如 08:30 或 08:30,21:00）')
            hh, mm, ss = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
            if hh == 24 and mm == 0 and ss == 0:
                hh = 0
            if not (0 <= hh <= 23 and 0 <= mm <= 59 and 0 <= ss <= 59):
                raise ValueError(f'daily_time 时间超出有效范围: {p}（小时须为 00-23，分钟/秒须为 00-59）')
            slots.append((hh, mm, ss))
        if not slots:
            raise ValueError(f'daily_time 格式非法: {daily_raw}')
        return slots

    @classmethod
    def _extract_clock_from_goal(cls, goal: str) -> str:
        return ''

    @classmethod
    def _compute_next_routine_run(cls, r: dict, now_ts: float | None = None) -> float:
        base_ts = now_ts if now_ts is not None else time.time()
        daily_raw = str(r.get('daily_time') or '').strip()
        if not daily_raw:
            inferred = cls._extract_clock_from_goal(str(r.get('goal') or ''))
            if inferred:
                daily_raw = inferred
                r['daily_time'] = inferred
        if daily_raw:
            try:
                slots = cls._validate_daily_time_slots(daily_raw)
            except ValueError:
                slots = []
            if slots:
                wk_raw = str(r.get('weekdays') or '').strip().lower()
                allowed_days = set(range(1, 8))
                if wk_raw and wk_raw not in ('all', '*', 'everyday', '每天'):
                    parsed_days = {int(x) for x in re.findall(r'[1-7]', wk_raw)}
                    if parsed_days:
                        allowed_days = parsed_days
                now_dt = datetime.fromtimestamp(base_ts, TZ)
                candidates = []
                for day_offset in range(0, 9):
                    day_dt = now_dt + timedelta(days=day_offset)
                    if day_dt.isoweekday() not in allowed_days:
                        continue
                    for hh, mm, ss in slots:
                        cand = day_dt.replace(hour=hh, minute=mm, second=ss, microsecond=0)
                        if cand.timestamp() > base_ts + 1:
                            candidates.append(cand.timestamp())
                if candidates:
                    return min(candidates)
        last_run = float(r.get('last_run') or base_ts)
        hours = max(0.05, float(r.get('interval_hours') or 24))
        return last_run + hours * 3600

    def _decorate_schedule_view(self) -> dict:
        return self.scheduler_engine.decorate_schedule_view()

    async def _tool_schedule_task(self, args: dict):
        action = str(args.get('action') or 'list').strip().lower()
        timers = self.store.state.setdefault('timers', [])
        routines = self.store.state.setdefault('routines', [])
        if action == 'list':
            return self._decorate_schedule_view()
        if action in ('snooze', 'dnd', 'quiet'):
            mins = max(0, min(1440, int(args.get('minutes') if args.get('minutes') is not None else 60)))
            await self.escalation.snooze(mins)
            return {'snoozed': mins > 0, 'minutes': mins, 'snooze_until': self.store.state.get('snooze_until', 0)}
        if action == 'add_timer':
            goal = str(args.get('goal') or '').strip()
            if not goal:
                return {'error': 'goal 不能为空'}
            bot_id = str(args.get('bot_id') or self.bot_context.get()).strip()
            at_time = str(args.get('at_time') or args.get('daily_time') or '').strip()
            if at_time:
                try:
                    due_at = self._parse_wall_time(at_time)
                except Exception as e:
                    return {'error': f'无法解析 at_time "{at_time}"（支持 "20:35" 或 "2026-11-05 07:30"）：{e}'}
            elif args.get('delay_seconds') is not None:
                due_at = time.time() + max(5, min(604800, int(args['delay_seconds'])))
            else:
                delay_min = max(1, min(10080, int(args.get('delay_minutes') or 5)))
                due_at = time.time() + delay_min * 60
            item = {
                'id': 'timer-' + secrets.token_hex(6),
                'goal': goal[:2000],
                'bot_id': bot_id,
                'at_time': at_time,
                'due_at': due_at,
                'due_at_bj': self._fmt_bj(due_at),
                'done': False,
                'created_at': time.time(),
            }
            timers.append(item)
            self.store.save()
            self.wake.set()
            return {'scheduled': True, 'timer': item}
        if action == 'add_routine':
            goal = str(args.get('goal') or '').strip()
            if not goal:
                return {'ok': False, 'error': 'goal 不能为空'}
            hours = max(1, min(720, int(args.get('interval_hours') or 24)))
            bot_id = str(args.get('bot_id') or self.bot_context.get()).strip()
            daily_time = str(args.get('daily_time') or args.get('at_time') or '').strip()
            if not daily_time:
                daily_time = self._extract_clock_from_goal(goal)
            if daily_time:
                try:
                    self._validate_daily_time_slots(daily_time)
                except ValueError as e:
                    return {'ok': False, 'error': str(e)}
            weekdays = str(args.get('weekdays') or '').strip()
            item = {
                'id': secrets.token_hex(8),
                'goal': goal[:2000],
                'bot_id': bot_id,
                'daily_time': daily_time,
                'weekdays': weekdays,
                'interval_hours': hours,
                'enabled': True,
                'last_run': time.time(),
                'created_at': time.time(),
            }
            item['next_run'] = self._compute_next_routine_run(item)
            item['next_run_bj'] = self._fmt_bj(item['next_run'])
            routines.append(item)
            self.store.save()
            self.wake.set()
            return {'ok': True, 'scheduled': True, 'routine': item}
        if action == 'update':
            tid = str(args.get('task_id') or '').strip()
            for r in routines:
                if r['id'] == tid or (not tid and len(routines) == 1):
                    if 'goal' in args and str(args['goal']).strip():
                        r['goal'] = str(args['goal']).strip()[:2000]
                    if 'bot_id' in args and str(args['bot_id']).strip():
                        r['bot_id'] = str(args['bot_id']).strip()
                    if 'daily_time' in args or 'at_time' in args:
                        new_dt = str(args.get('daily_time') or args.get('at_time') or '').strip()
                        if new_dt:
                            try:
                                self._validate_daily_time_slots(new_dt)
                            except ValueError as e:
                                return {'ok': False, 'error': str(e)}
                        r['daily_time'] = new_dt
                    if 'weekdays' in args:
                        r['weekdays'] = str(args['weekdays'] or '').strip()
                    if 'interval_hours' in args and args['interval_hours'] is not None:
                        r['interval_hours'] = max(1, min(720, int(args['interval_hours'])))
                    if 'enabled' in args and args['enabled'] is not None:
                        r['enabled'] = bool(args['enabled'])
                    r['next_run'] = self._compute_next_routine_run(r)
                    r['next_run_bj'] = self._fmt_bj(r['next_run'])
                    self.store.save()
                    self.wake.set()
                    return {'ok': True, 'updated': True, 'routine': r}
            for t in timers:
                if t['id'] == tid:
                    if 'goal' in args and str(args['goal']).strip():
                        t['goal'] = str(args['goal']).strip()[:2000]
                    if 'at_time' in args or 'daily_time' in args:
                        raw_at = str(args.get('at_time') or args.get('daily_time') or '').strip()
                        try:
                            due_val = self._parse_wall_time(raw_at)
                        except ValueError as e:
                            return {'ok': False, 'error': str(e)}
                        t['at_time'] = raw_at
                        t['due_at'] = due_val
                        t['due_at_bj'] = self._fmt_bj(t['due_at'])
                    elif 'delay_minutes' in args and args['delay_minutes'] is not None:
                        t['due_at'] = time.time() + max(1, int(args['delay_minutes'])) * 60
                        t['due_at_bj'] = self._fmt_bj(t['due_at'])
                    t['done'] = False
                    self.store.save()
                    self.wake.set()
                    return {'updated': True, 'timer': t}
            return {'error': f'未找到定时任务 "{tid}"'}
        if action == 'remove':
            tid = str(args.get('task_id') or '').strip()
            before_n = len(timers) + len(routines)
            self.store.state['timers'] = [t for t in timers if t['id'] != tid]
            self.store.state['routines'] = [r for r in routines if r['id'] != tid]
            existed = (len(self.store.state['timers']) + len(self.store.state['routines'])) < before_n
            if existed: self.store.save()
            return {'ok': existed, 'removed': existed, 'existed': existed, 'task_id': tid, **({'error': f'未找到定时任务 "{tid}"'} if not existed else {})}
        return {'error': f'未知 schedule_task action: {action}'}

    async def _tool_bg_task(self, args: dict):
        action = str(args.get('action') or 'list').strip().lower()
        if action == 'list':
            return {
                'tasks': [
                    {k: v for k, v in info.items() if k != 'proc'}
                    for info in self.bg_tasks.values()
                ],
                'watchdogs': [
                    {k: v for k, v in w.items() if k != 'task'}
                    for w in self.watchdogs.values()
                ],
            }
        if action == 'logs':
            tid = str(args.get('task_id') or '').strip()
            info = self.bg_tasks.get(tid)
            if not info:
                return {'error': f'未找到后台任务 {tid}'}
            log_path = Path(info['log_file'])
            return {
                'task_id': tid,
                'status': info['status'],
                'exit_code': info.get('exit_code'),
                'logs': log_path.read_text('utf-8', errors='replace')[-12000:] if log_path.exists() else '',
            }
        if action == 'send_stdin':
            tid = str(args.get('task_id') or '').strip()
            input_text = str(args.get('input_text') or args.get('command') or '')
            info = self.bg_tasks.get(tid)
            if not info or info.get('status') != 'running':
                return {'error': f'未找到正在运行的后台任务 {tid}'}
            proc = info.get('proc')
            if not proc or proc.stdin is None:
                return {'error': f'任务 {tid} 不支持 stdin 输入'}
            try:
                line_bytes = (input_text if input_text.endswith('\n') else input_text + '\n').encode('utf-8')
                proc.stdin.write(line_bytes)
                await proc.stdin.drain()
                return {'sent_stdin': True, 'task_id': tid, 'bytes': len(line_bytes)}
            except Exception as e:
                return {'error': f'写入 stdin 失败: {e}'}
        if action == 'kill':
            tid = str(args.get('task_id') or '').strip()
            info = self.bg_tasks.get(tid)
            if not info:
                return {'error': f'未找到后台任务 {tid}'}
            proc = info.get('proc')
            if proc and info['status'] == 'running':
                try:
                    if sys.platform == 'linux':
                        os.killpg(proc.pid, signal.SIGKILL)
                    else:
                        proc.kill()
                except Exception:
                    pass
                info['status'] = 'killed'
            return {'killed': True, 'task_id': tid}
        if action == 'start':
            cmd = str(args.get('command') or '').strip()
            if not cmd:
                return {'error': 'command 不能为空'}
            if normalize_node_source(self.source_context.get()) in ('pc', 'pc2') and self.is_dsh_online():
                pc_bg = await self._raw_dsh_rpc('pc_shell', {'command': cmd, 'background': True, 'cwd': args.get('cwd')}, timeout=15.0)
                if isinstance(pc_bg, dict) and pc_bg.get('ok'):
                    return {'started': True, 'node': 'pc', **pc_bg}
                return {'error': str((pc_bg or {}).get('error') or 'Windows 后台任务启动失败')}
            tid = 'bg-' + secrets.token_hex(4)
            log_dir = self.harness.root / '.bg_logs'
            log_dir.mkdir(parents=True, exist_ok=True)
            log_file = log_dir / f'{tid}.log'
            owner_bot = self.bot_context.get()
            notify_exit = bool(args.get('notify_on_exit', True))
            env = os.environ.copy()
            env['TZ'] = 'Asia/Shanghai'
            env['API_BASE_URL'] = self.store.config.effective_base_url()
            env['API_KEY'] = self.store.config.effective_api_key(big=True) or self.store.config.effective_api_key(big=False)
            env['DEFAULT_MODEL'] = self.store.config.effective_model()
            out_f = log_file.open('w', encoding='utf-8')
            proc = await asyncio.create_subprocess_shell(
                cmd, cwd=str(self.harness.root), stdin=asyncio.subprocess.PIPE, stdout=out_f, stderr=asyncio.subprocess.STDOUT,
                start_new_session=(sys.platform == 'linux'), env=env
            )
            info = {
                'task_id': tid,
                'command': cmd[:300],
                'bot_id': owner_bot,
                'status': 'running',
                'started_at': time.time(),
                'log_file': str(log_file),
                'proc': proc,
            }
            self.bg_tasks[tid] = info

            async def watch():
                try:
                    rc = await proc.wait()
                    info['status'] = 'done' if rc == 0 else 'failed'
                    info['exit_code'] = rc
                    info['finished_at'] = time.time()
                finally:
                    out_f.close()
                if notify_exit:
                    tail = log_file.read_text('utf-8', errors='replace')[-2000:] if log_file.exists() else ''
                    msg_text = f'⚙️ [后台任务 `{tid}` 已结束 (exit={info.get("exit_code")})]\n命令：`{cmd[:120]}`\n输出摘要：\n```\n{tail}\n```'
                    m = self.store.message('assistant', msg_text, kind='result', bot_id=owner_bot, notify=False)
                    self.stream_seq += 1
                    m['seq'] = self.stream_seq
                    self.context_search.index_message(m)
                    await self.publish({'type': 'message', 'message': m, 'seq': self.stream_seq})

            asyncio.create_task(watch())
            return {'started': True, 'task_id': tid, 'log_file': f'.bg_logs/{tid}.log'}
        return {'error': f'未知 bg_task action: {action}'}

    async def _tool_watchdog_task(self, args: dict):
        """Zero-token self-written watchdog scripts that silently monitor targets and wake the agent ONLY on TRIGGER:"""
        action = str(args.get('action') or 'list').strip().lower()
        if action == 'list':
            return {
                'watchdogs': [
                    {k: v for k, v in w.items() if k != 'task'}
                    for w in self.watchdogs.values()
                ]
            }
        if action in ('stop', 'kill', 'remove'):
            tid = str(args.get('task_id') or args.get('watchdog_id') or args.get('id') or args.get('name') or '').strip()
            w = self.watchdogs.get(tid)
            if not w:
                for k, cand in list(self.watchdogs.items()):
                    if cand.get('name') == tid:
                        w = cand
                        tid = k
                        break
            if not w:
                return {'error': f'未找到盯盘哨兵 {tid}'}
            w['status'] = 'stopped'
            t = w.get('task')
            if t and not t.done():
                t.cancel()
            self.watchdogs.pop(tid, None)
            return {'stopped': True, 'task_id': tid, 'watchdog_id': tid}

        if action == 'logs':
            tid = str(args.get('task_id') or args.get('watchdog_id') or args.get('id') or args.get('name') or '').strip()
            w = self.watchdogs.get(tid)
            if not w:
                for k, cand in list(self.watchdogs.items()):
                    if cand.get('name') == tid:
                        w = cand
                        break
            if not w:
                return {'error': f'未找到盯盘哨兵 {tid}'}
            return {k: v for k, v in w.items() if k != 'task'}

        if action == 'run_now':
            tid = str(args.get('task_id') or args.get('watchdog_id') or args.get('id') or args.get('name') or '').strip()
            w = self.watchdogs.get(tid)
            if not w:
                for k, cand in list(self.watchdogs.items()):
                    if cand.get('name') == tid:
                        w = cand
                        tid = k
                        break
            if not w:
                return {'error': f'未找到盯盘哨兵 {tid}'}
            out = await self._exec_watchdog_once(w)
            return {'ran': True, 'task_id': tid, 'watchdog_id': tid, 'output': out}

        if action == 'start':
            name = str(args.get('name') or '监控哨兵').strip()[:80]
            script = str(args.get('script') or '').strip()
            if not script:
                return {'error': 'script 不能为空（请提供 Python 或 Bash 探针脚本，当检测到目标变化时打印 "TRIGGER: 详情"）'}
            interval_sec = max(15, min(86400, int(args.get('interval_sec') or 120)))
            owner_bot = str(args.get('bot_id') or self.bot_context.get() or 'companion').strip()
            tid = 'wd-' + secrets.token_hex(4)
            wd_dir = self.harness.root / '.watchdogs'
            wd_dir.mkdir(parents=True, exist_ok=True)
            is_py = ('import ' in script or 'print(' in script or script.startswith('#!/usr/bin/env python'))
            ext = '.py' if is_py else '.sh'
            script_path = wd_dir / f'{tid}{ext}'
            script_path.write_text(script, encoding='utf-8')
            w_info: dict = {
                'task_id': tid,
                'name': name,
                'bot_id': owner_bot,
                'interval_sec': interval_sec,
                'script_path': str(script_path),
                'status': 'running',
                'runs': 0,
                'triggers': 0,
                'last_run_at': 0.0,
                'last_output': '',
                'created_at': time.time(),
            }

            async def _loop():
                while w_info.get('status') == 'running':
                    try:
                        await self._exec_watchdog_once(w_info)
                    except asyncio.CancelledError:
                        break
                    except Exception as e:
                        w_info['last_output'] = f'Error: {e}'
                    await asyncio.sleep(interval_sec)

            w_info['task'] = asyncio.create_task(_loop())
            self.watchdogs[tid] = w_info
            return {
                'started': True,
                'task_id': tid,
                'name': name,
                'interval_sec': interval_sec,
                'note': '哨兵已在后台以 0 Token 模式静默运行；当脚本输出包含 "TRIGGER:" 时将自动唤醒你向主人汇报。',
            }
        return {'error': f'未知 watchdog_task action: {action}'}

    async def _exec_watchdog_once(self, w_info: dict) -> str:
        script_path = Path(w_info['script_path'])
        cmd = f'{sys.executable} "{script_path}"' if script_path.suffix == '.py' else f'bash "{script_path}"'
        proc = await asyncio.create_subprocess_shell(
            cmd,
            cwd=str(self.harness.root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            raw_out, _ = await asyncio.wait_for(proc.communicate(), timeout=30.0)
        except asyncio.TimeoutError:
            proc.kill()
            raw_out = b'Timeout after 30s'
        out_str = raw_out.decode('utf-8', errors='replace').strip()
        w_info['runs'] = int(w_info.get('runs') or 0) + 1
        w_info['last_run_at'] = time.time()
        w_info['last_output'] = out_str[-800:]
        if 'TRIGGER:' in out_str:
            w_info['triggers'] = int(w_info.get('triggers') or 0) + 1
            trig_detail = out_str.split('TRIGGER:', 1)[1].strip()[:1500]
            asyncio.create_task(
                self.run_scheduled(
                    f'【盯盘哨兵「{w_info["name"]}」命中触发条件】\n探针输出：{trig_detail}\n请立即向主人汇报此变化并给出处理建议。',
                    bot_id=w_info.get('bot_id', 'companion'),
                    label=f'哨兵触发 ({w_info["name"]})',
                )
            )
        return out_str[-800:]

    async def _tool_conversation_search(self, args: dict):
        query = str(args.get('query') or '').strip()
        cur_bot = self.room_context.get() or self.bot_context.get()
        raw_bot_arg = str(args.get('bot_id') or '').strip()
        bot_id = raw_bot_arg if raw_bot_arg else cur_bot
        limit = int(args.get('limit') or 10)
        try:
            self.context_search.sync_store_messages(self.store.state.get('messages', []))
        except Exception:
            pass
        active_ids = {
            str(m.get('id'))
            for m in self.store.state.get('messages', [])
            if m.get('bot_id', 'companion') == cur_bot and m.get('kind') != 'compact_summary'
        }
        return self.context_search.conversation_search(
            query=query,
            bot_id=bot_id,
            limit=limit,
            exclude_msg_ids=active_ids if bot_id in ('all', cur_bot, '') else set(),
            vault=self.vault,
        )

    async def _tool_ctx_search(self, args: dict):
        query = str(args.get('query') or '').strip()
        ctx_id = str(args.get('ctx_id') or '').strip()
        limit = int(args.get('limit') or 8)
        cur_bot = self.room_context.get() or self.bot_context.get()
        bot_filter = str(args.get('bot_id') or cur_bot or '').strip()
        return self.context_search.ctx_search(query=query, ctx_id=ctx_id, limit=limit, bot_id=bot_filter)

    def _search_vault_files(self, query: str, limit: int = 8, bot_id: str = '') -> list[dict]:
        if not query or self.vault is None or limit <= 0:
            return []
        tokens = [t for t in re.split(r'\s+', query.strip()) if t]
        rx_full = re.compile(re.escape(query), re.IGNORECASE)
        hits = []
        eff_bot = str(bot_id or '').strip()
        for f in self.vault.files():
            path_str = f['path']
            if eff_bot and eff_bot != 'all':
                if path_str.startswith('timeline/bots/') and not path_str.startswith(f'timeline/bots/{eff_bot}/'):
                    continue
                if path_str.startswith('conversations/') and '_' in path_str[14:] and not path_str[14:].startswith(f'{eff_bot}_'):
                    continue
            content = self.vault.read(path_str)
            if rx_full.search(content) or rx_full.search(path_str) or (tokens and any(t.lower() in content.lower() or t.lower() in path_str.lower() for t in tokens)):
                matched_lines = [ln.strip() for ln in content.splitlines() if any(t.lower() in ln.lower() for t in (tokens or [query]))]
                snippet = ('\n'.join(matched_lines[:4])[:360] if matched_lines else content[:360])
                hits.append({'path': 'memory_vault/' + path_str, 'snippet': snippet})
                if len(hits) >= limit:
                    break
        return hits[:limit]

    async def _tool_memory_search(self, args: dict):
        query = str(args.get('query') or '').strip()
        cur_bot = self.room_context.get() or self.bot_context.get()
        bot_id = str(args.get('bot_id') or cur_bot or 'all').strip()
        ctx_id = str(args.get('ctx_id') or '').strip()
        raw_lim = args.get('limit')
        if raw_lim is not None and int(raw_lim) <= 0:
            return {'ok': False, 'query': query, 'hits': [], 'conversation_hits': [], 'ctx_hits': [], 'error': 'limit 必须大于 0'}
        if not query and not ctx_id:
            return {'ok': False, 'query': '', 'hits': [], 'conversation_hits': [], 'ctx_hits': [], 'error': '检索词 query 不能为空'}
        limit = max(1, min(20, int(raw_lim) if raw_lim is not None else 6))
        if ctx_id:
            ctx_res = self.context_search.ctx_search(query=query, ctx_id=ctx_id, limit=limit, bot_id=bot_id)
            if isinstance(ctx_res, dict) and ctx_res.get('ok') is False:
                return ctx_res
            vault_hits = self._search_vault_files(query, min(limit, max(1, limit // 2)), bot_id=bot_id) if query else []
            raw_ctx_hits = (ctx_res.get('chunks') or ctx_res.get('hits') or [])[:limit] if isinstance(ctx_res, dict) else []
            return {
                'ok': True,
                'query': query,
                'hits': vault_hits[:limit],
                'ctx_hits': [
                    {**c, 'content': str(c.get('content') or '')[:400]} if isinstance(c, dict) else c
                    for c in raw_ctx_hits
                ],
                'ctx_id': ctx_id,
            }
        vault_hits = self._search_vault_files(query, limit, bot_id=bot_id)
        conv_res = await self._tool_conversation_search({'query': query, 'bot_id': bot_id, 'limit': limit})
        ctx_res = self.context_search.ctx_search(query=query, ctx_id='', limit=limit, bot_id=bot_id)
        raw_conv_hits = (conv_res.get('results') or conv_res.get('hits') or [])[:limit] if isinstance(conv_res, dict) else []
        raw_ctx_hits = (ctx_res.get('chunks') or ctx_res.get('hits') or [])[:limit] if isinstance(ctx_res, dict) else []
        total_found = len(vault_hits) + len(raw_conv_hits) + len(raw_ctx_hits)
        out_ms: dict = {
            'ok': True,
            'query': query,
            'hits': vault_hits[:limit],
            'conversation_hits': [
                {**c, 'text': str(c.get('text') or '')[:400]} if isinstance(c, dict) else c
                for c in raw_conv_hits
            ],
            'ctx_hits': [
                {**c, 'content': str(c.get('content') or '')[:400]} if isinstance(c, dict) else c
                for c in raw_ctx_hits
            ],
        }
        if total_found > 0:
            out_ms['note'] = '已一次性检索长期记忆库 (memory_vault)、历史对话归档与工具大输出缓存，无需再重复调用其他搜索工具。'
        return out_ms

    async def _tool_present_choices(self, args: dict):
        raw_choices = args.get('choices') or args.get('options') or args.get('items')
        prompt_txt = str(args.get('prompt') or args.get('question') or args.get('title') or '').strip()
        if isinstance(raw_choices, str):
            raw_choices = [ln.strip() for ln in re.split(r'[\n;]+', raw_choices) if ln.strip()]
        if not isinstance(raw_choices, list):
            return {'error': 'choices 必须是列表（如 ["直接帮我填好 | 顺手把我的信息填进去提交", "先发截图看看 | 先截张图给我确认一下"]）'}
        cleaned = self._sanitize_choices_list(raw_choices)
        if not cleaned:
            return {'error': 'choices 不能为空，且必须是站在主人第一人称视角的简短可执行选项（禁止传入总结汇报条目）'}
        self.choices_var.set(cleaned)
        if prompt_txt:
            self.choices_prompt_var.set(prompt_txt)
        bot_id = self.room_context.get() or self.bot_context.get()
        cur_exec = self.executions_by_bot.get(bot_id) or self.execution
        if isinstance(cur_exec, dict):
            cur_exec['choices'] = cleaned
        cur_prefix = (self.stream_prefix_var.get() or '').strip()
        if cur_prefix and prompt_txt and prompt_txt not in cur_prefix:
            combined_stream_txt = cur_prefix + '\n\n' + prompt_txt
        else:
            combined_stream_txt = cur_prefix or prompt_txt or '正在思考…'
        await self._set_stream(
            text=combined_stream_txt,
            tool='present_choices',
        )
        return {
            'ok': True,
            'choices_attached': [item['label'] for item in cleaned],
            'note': '可点击选项按钮已附加到回复气泡底部。若前面已说明清楚，无需再重复生成多余总结。',
        }

    async def _tool_memory_pin(self, args: dict):
        action = str(args.get('action') or 'list').strip().lower()
        if action == 'list':
            return {'ok': True, 'pins': self.memory_engine.list_pins()}
        if action in ('pin', 'add', 'remember'):
            src = str(args.get('source') or self.source_context.get() or 'phone').strip().lower()
            return self.memory_engine.add_pin(str(args.get('text') or ''), source=('pc' if src == 'pc' else 'phone'))
        if action in ('unpin', 'remove', 'delete', 'forget'):
            return self.memory_engine.remove_pin(str(args.get('pin_id') or args.get('text') or ''))
        return {'error': f'未知 memory_pin action: {action}'}

    async def _tool_blackboard(self, args: dict):
        return await self.blackboard_engine.execute(args)

    async def _tool_peek_bots(self, args: dict):
        return self.blackboard_engine.peek_bots(args)

    async def _tool_custom_tool(self, args: dict):
        action = str(args.get('action') or 'list').strip().lower()
        plugins_dir = self.harness.root / 'plugins'
        plugins_dir.mkdir(parents=True, exist_ok=True)
        skills_dir = self.vault.root / 'skills'
        skills_dir.mkdir(parents=True, exist_ok=True)

        if action in ('list_skills', 'skills'):
            skills = []
            for sf in sorted(skills_dir.glob('*.md')):
                lines = [ln.strip() for ln in sf.read_text('utf-8', errors='replace').splitlines() if ln.strip()]
                title = lines[0].lstrip('#').strip() if lines else sf.stem
                desc = lines[1] if len(lines) > 1 else ''
                skills.append({'name': sf.stem, 'path': f'memory_vault/skills/{sf.name}', 'title': title[:80], 'summary': desc[:160]})
            return {'ok': True, 'skills': skills, 'note': '按需调用 custom_tool(action="open_skill", name="...") 或 read_file 打开指定 Skill，日常不占额外 Token'}
        if action in ('open_skill', 'read_skill', 'load_skill'):
            s_name = re.sub(r'[^a-zA-Z0-9_\-\u4e00-\u9fa5]', '-', str(args.get('name') or '')).strip('-').lower()
            if not s_name:
                return {'error': '请提供要打开的 skill name'}
            sf = skills_dir / f'{s_name}.md'
            if not sf.exists():
                return {'error': f'未找到技能 "{s_name}"，可先用 action="list_skills" 查看可用技能列表'}
            return {'ok': True, 'name': s_name, 'path': f'memory_vault/skills/{sf.name}', 'content': sf.read_text('utf-8', errors='replace')}
        if action in ('save_skill', 'create_skill', 'write_skill'):
            s_name = re.sub(r'[^a-zA-Z0-9_\-\u4e00-\u9fa5]', '-', str(args.get('name') or '')).strip('-').lower()
            desc = str(args.get('description') or '').strip()
            body = str(args.get('content') or args.get('code') or '').strip()
            if not s_name or not body:
                return {'error': 'save_skill 需要提供 name 与 content（技能步骤/经验总结 Markdown）'}
            md_text = body if body.startswith('#') else f'# {s_name}\n{desc}\n\n{body}\n'
            rel_p = f'skills/{s_name}.md'
            self.vault.write(rel_p, md_text)
            return {'ok': True, 'saved_skill': s_name, 'path': f'memory_vault/{rel_p}'}
        if action in ('delete_skill', 'remove_skill'):
            s_name = re.sub(r'[^a-zA-Z0-9_\-\u4e00-\u9fa5]', '-', str(args.get('name') or '')).strip('-').lower()
            sf = skills_dir / f'{s_name}.md'
            if sf.exists():
                sf.unlink()
            return {'ok': True, 'deleted_skill': s_name}

        if action == 'list':
            summary = self.harness.extension_registry.summary()
            skills_list = [sf.stem for sf in sorted(skills_dir.glob('*.md'))]
            return {**summary, 'available_skills': skills_list}
        if action == 'delete':
            raw_name = str(args.get('name') or '').strip()
            t_name = re.sub(r'[^a-zA-Z0-9_\u4e00-\u9fa5]', '_', raw_name).strip('_').lower()
            if not t_name:
                return {'ok': False, 'deleted': False, 'existed': False, 'error': '请提供要删除的自定义工具名称 name'}
            safe_t_name = self.harness.extension_registry._safe_openai_tool_name(t_name)
            p_file = plugins_dir / f'{t_name}.py'
            existed = p_file.exists() or (t_name in self.harness.extension_registry.tool_plugins) or (safe_t_name in self.harness.extension_registry.tool_plugins)
            if p_file.exists():
                p_file.unlink()
            self.harness.extension_registry.tool_plugins.pop(t_name, None)
            self.harness.extension_registry.tool_plugins.pop(safe_t_name, None)
            return {'ok': existed, 'deleted': existed, 'existed': existed, 'name': t_name, **({'error': f'未找到自定义工具 "{raw_name}"'} if not existed else {})}
        if action == 'create':
            raw_name = str(args.get('name') or '').strip()
            t_name = re.sub(r'[^a-zA-Z0-9_\u4e00-\u9fa5]', '_', raw_name).strip('_').lower()
            code = str(args.get('code') or '').strip()
            if not t_name or not code:
                return {
                    'ok': False,
                    'error': 'create 需要提供合法的 name（支持中英文、数字与下划线）和 code (支持 def run(args, context) / def run(args) / def run(param_name) / async def run(...))',
                }
            code_lines = code.splitlines()
            try:
                ast.parse(code)
            except SyntaxError as se:
                return {
                    'ok': False,
                    'total_lines': len(code_lines),
                    'error': f'Python 语法校验未通过 (line {se.lineno} / 共 {len(code_lines)} 行): {se.msg}',
                }
            desc = str(args.get('description') or f'Custom tool {t_name}').strip()
            cls_id = re.sub(r'[^a-zA-Z0-9_]', '_', t_name).strip('_')
            if not cls_id or not cls_id.isidentifier():
                import hashlib as _hl
                cls_id = 'u_' + _hl.md5(t_name.encode('utf-8')).hexdigest()[:10]
            try:
                params_schema = json.loads(str(args.get('parameters_json') or '{"type":"object","properties":{}}'))
            except Exception:
                params_schema = {'type': 'object', 'properties': {}}
            indented_code = '\n'.join('        ' + ln for ln in code_lines)
            plugin_src = (
                f'"""Dynamic Custom Tool: {t_name}"""\n'
                f'import asyncio, inspect, json, os, re, time, httpx\n'
                f'from pathlib import Path\n\n'
                f'class DynamicPlugin_{cls_id}:\n'
                f'    name = {json.dumps(t_name, ensure_ascii=False)}\n'
                f'    description = {json.dumps(desc, ensure_ascii=False)}\n'
                f'    parameters = {repr(params_schema)}\n\n'
                f'    async def execute(self, args: dict, context: dict) -> dict:\n'
                f'{indented_code}\n'
                f'        if "run" in locals() and callable(locals()["run"]):\n'
                f'            _fn = locals()["run"]\n'
                f'            _sig = inspect.signature(_fn)\n'
                f'            _params = list(_sig.parameters.values())\n'
                f'            _dict_arg_names = ("args", "params", "payload", "data", "input", "req", "request")\n'
                f'            _ctx_arg_names = ("context", "ctx", "env")\n'
                f'            if len(_params) == 0:\n'
                f'                res = _fn()\n'
                f'            elif any(p.kind == inspect.Parameter.VAR_POSITIONAL for p in _params):\n'
                f'                res = _fn(args, context)\n'
                f'            elif len(_params) == 1 and _params[0].kind != inspect.Parameter.VAR_KEYWORD:\n'
                f'                _p0 = _params[0].name\n'
                f'                if _p0 in _dict_arg_names or not isinstance(args, dict):\n'
                f'                    res = _fn(args)\n'
                f'                elif _p0 in _ctx_arg_names:\n'
                f'                    res = _fn(context)\n'
                f'                elif _p0 in args:\n'
                f'                    res = _fn(args[_p0])\n'
                f'                elif len(args) == 1 and _params[0].default is inspect.Parameter.empty:\n'
                f'                    res = _fn(next(iter(args.values())))\n'
                f'                else:\n'
                f'                    res = _fn(args)\n'
                f'            elif len(_params) == 2 and _params[0].name in _dict_arg_names and _params[1].name in _ctx_arg_names:\n'
                f'                res = _fn(args, context)\n'
                f'            elif isinstance(args, dict):\n'
                f'                if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in _params):\n'
                f'                    _kw = dict(args)\n'
                f'                    for _cn in _ctx_arg_names:\n'
                f'                        if _cn in _sig.parameters and _cn not in _kw:\n'
                f'                            _kw[_cn] = context\n'
                f'                    res = _fn(**_kw)\n'
                f'                else:\n'
                f'                    _kw = {{k: v for k, v in args.items() if k in _sig.parameters}}\n'
                f'                    for _cn in _ctx_arg_names:\n'
                f'                        if _cn in _sig.parameters and _cn not in _kw:\n'
                f'                            _kw[_cn] = context\n'
                f'                    for _an in _dict_arg_names:\n'
                f'                        if _an in _sig.parameters and _an not in _kw:\n'
                f'                            _kw[_an] = args\n'
                f'                    _missing = [p.name for p in _params if p.default is inspect.Parameter.empty and p.name not in _kw]\n'
                f'                    if not _missing:\n'
                f'                        res = _fn(**_kw)\n'
                f'                    else:\n'
                f'                        res = _fn(args, context)\n'
                f'            else:\n'
                f'                res = _fn(args, context)\n'
                f'            out = (await res) if asyncio.iscoroutine(res) else res\n'
                f'            if isinstance(out, dict):\n'
                f'                if "ok" not in out and "error" not in out:\n'
                f'                    out["ok"] = True\n'
                f'                return out\n'
                f'            return {{"ok": True, "result": out}}\n'
                f'        return {{"ok": True}}\n\n'
                f'def register(registry):\n'
                f'    registry.register_tool(DynamicPlugin_{cls_id}())\n'
            )
            try:
                ast.parse(plugin_src)
            except SyntaxError as se:
                adj_line = max(1, (se.lineno or 10) - 9)
                return {'ok': False, 'total_lines': len(code_lines), 'error': f'Python 语法校验未通过 (line {adj_line} / 共 {len(code_lines)} 行): {se.msg}'}
            p_file = plugins_dir / f'{t_name}.py'
            p_file.write_text(plugin_src, encoding='utf-8')
            loaded = self.harness.extension_registry.discover_plugins_dir(plugins_dir)
            return {
                'ok': True,
                'created': True,
                'name': t_name,
                'total_lines': len(code_lines),
                'loaded_plugins': loaded,
                'supported_signatures': [
                    'def run(args, context):',
                    'def run(args):',
                    'def run(named_param, ...):',
                    'def run(*args, **kwargs):',
                ],
            }
        return {'error': f'未知 custom_tool action: {action}'}

    async def _tool_notify_user(self, args: dict):
        title = str(args.get('title') or '助理提醒').strip()[:120]
        body = str(args.get('body') or '').strip()[:2000]
        urgent = bool(args.get('urgent', False))
        overlay = bool(args.get('overlay', False))
        if not body:
            return {'error': '通知内容不能为空'}
        bot_id = self.room_context.get() or self.bot_context.get()
        bot = get_assistant(self.store, self.bot_context.get())
        nid = 'notif-' + secrets.token_hex(6)
        await self.publish({
            'type': 'user_notification',
            'id': nid,
            'bot_id': bot_id,
            'bot_name': bot['name'],
            'title': title,
            'body': body,
            'urgent': urgent,
        })
        if overlay:
            overlay_text = f'【{title}】\n{body}' if title and title not in body else body
            with self.store.lock:
                self.store.state.setdefault('pending', {})[nid] = {
                    'id': nid,
                    'text': overlay_text[:3000],
                    'created_at': time.time(),
                    'stage': 3,
                    'last_email': 0,
                    'emails': 0,
                    'demo': False,
                    'email_status': 'overlay_tool',
                }
                self.store.save()
            await self.publish({
                'type': 'escalation',
                'stage': 3,
                'id': nid,
                'title': title,
                'text': overlay_text[:3000],
                'demo': False,
            })
        return {
            'notified': True,
            'overlay_popped': overlay,
            'id': nid,
            'title': title,
            'urgent': urgent,
            'note': '手机系统通知栏推送' + ('与可滚动确认悬浮窗' if overlay else '') + '已发出；请直接将提醒正文作为本轮最终回复输出即可，禁止再额外说“推送已经发了”。',
        }

    async def _tool_complete_goal(self, args: dict):
        summary = str(args.get('summary') or args.get('result') or args.get('text') or '').strip()
        verified_evidence = str(args.get('verified_evidence') or args.get('evidence') or '').strip()
        if not summary:
            return {'ok': False, 'error': '调用 complete_goal 必须提供具体的完成总结 summary'}
        norm_src = normalize_node_source(self.source_context.get())
        room_bot_id = self.room_context.get() or self.bot_context.get()
        nb_key = f'{norm_src}:{room_bot_id}'
        with self.store.lock:
            active_goals = self.store.state.setdefault('active_goals', {})
            cur_goal = active_goals.pop(nb_key, None) or {}
            cur_exec = self.executions_by_node_bot.get(nb_key) or self.execution or {}
            cur_trace_steps = len(cur_exec.get('trace') or [])
            goal_title = str(cur_goal.get('goal') or summary)[:200]
            self.store.state['last_goal_run'] = {
                'goal': goal_title,
                'status': 'completed',
                'summary': summary[:600],
                'verified_evidence': verified_evidence[:400],
                'wakeups': int(cur_goal.get('wakeups') or 0),
                'steps': int(cur_goal.get('total_steps') or 0) + max(1, cur_trace_steps),
                'updated_at': now_iso(),
            }
            self.store.save()
        await self.publish({'type': 'goal_status', 'source': norm_src, 'bot_id': room_bot_id, 'active': False, 'status': 'completed', 'goal': goal_title, 'summary': summary[:500]})
        return {'ok': True, 'goal_completed': True, 'goal': goal_title, 'summary': summary, 'verified_evidence': verified_evidence, 'note': '🎯 目标已完成状态已由引擎正式登记，自动拉起循环已解除。现在请向主人输出清晰、完整的最终交付总结！'}

    async def _tool_emergency_alarm(self, args: dict):
        title, reason = str(args.get('title') or '紧急呼叫').strip()[:120], str(args.get('reason') or '').strip()[:1000]
        duration_sec = max(5, min(45, int(args.get('duration_sec') or 18)))
        if not reason:
            return {'error': '必须提供触发紧急响铃的具体原因 reason'}
        now = time.time()
        if self.store.state.get('snooze_until', 0) > now:
            return {'suppressed': True, 'reason': '当前处于主人设定的免打扰时段，已拦截紧急闹钟'}
        if now - self._last_emergency_alarm_at < 180.0:
            return {'suppressed': True, 'reason': '3 分钟内已触发过一次紧急响铃，避免重复打扰'}
        self._last_emergency_alarm_at = now
        bot_id = self.room_context.get() or self.bot_context.get()
        bot = get_assistant(self.store, self.bot_context.get())
        aid = 'alarm-' + secrets.token_hex(6)
        await self.publish({'type': 'emergency_alarm', 'id': aid, 'bot_id': bot_id, 'bot_name': bot['name'], 'title': f'🚨 {title}', 'body': reason, 'duration_sec': duration_sec, 'urgent': True})
        return {'alarm_triggered': True, 'id': aid, 'duration_sec': duration_sec, 'title': title}
