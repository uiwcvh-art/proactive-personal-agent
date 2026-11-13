"""Beijing Time (UTC+8) Scheduler & Deduplication Engine.

Handles:
- Wall-clock timers (`add_timer`) and recurring routines (`add_routine`)
- Automatic deduplication of simultaneous timer + routine triggers (prevents double-sending at the same minute!)
- Unanswered-message backoff (prevents the agent from talking to itself when the user hasn't replied)
"""
from __future__ import annotations
import re, secrets, time
from datetime import datetime, timedelta
from vault_manager import TZ


class SchedulerEngine:
    def __init__(self, store, wake_event):
        self.store = store
        self.wake = wake_event
        self.last_scheduled_fire_by_bot: dict[str, float] = {}

    def get_quiet_window(self) -> tuple[int, int]:
        cfg = getattr(self.store, 'config', None)
        raw_s = getattr(cfg, 'quiet_start_hour', None) if cfg is not None else None
        raw_e = getattr(cfg, 'quiet_end_hour', None) if cfg is not None else None
        start_h = 0 if raw_s is None else int(raw_s) % 24
        end_h = 8 if raw_e is None else int(raw_e) % 24
        return start_h, end_h

    @staticmethod
    def is_hour_in_quiet_window(ts: float, start_h: int, end_h: int) -> bool:
        if start_h == end_h:
            return False
        dt = datetime.fromtimestamp(ts, TZ)
        if start_h < end_h:
            return start_h <= dt.hour < end_h
        return dt.hour >= start_h or dt.hour < end_h

    def is_quiet_hour(self, now_ts: float | None = None) -> bool:
        start_h, end_h = self.get_quiet_window()
        ts = now_ts if now_ts is not None else time.time()
        return self.is_hour_in_quiet_window(ts, start_h, end_h)

    @staticmethod
    def defer_ts_out_of_quiet_window(ts: float, start_h: int, end_h: int, jitter_sec: float = 0.0) -> tuple[float, bool]:
        if start_h == end_h or not SchedulerEngine.is_hour_in_quiet_window(ts, start_h, end_h):
            return ts, False
        dt = datetime.fromtimestamp(ts, TZ)
        if start_h < end_h or dt.hour < end_h:
            target = dt.replace(hour=end_h, minute=0, second=0, microsecond=0)
        else:
            target = (dt + timedelta(days=1)).replace(hour=end_h, minute=0, second=0, microsecond=0)
        out = target.timestamp() + max(0.0, float(jitter_sec))
        if out <= ts:
            out = (target + timedelta(days=1)).timestamp() + max(0.0, float(jitter_sec))
        return out, True

    def defer_out_of_quiet_hours(self, ts: float, jitter_sec: float = 0.0) -> tuple[float, bool]:
        start_h, end_h = self.get_quiet_window()
        return self.defer_ts_out_of_quiet_window(ts, start_h, end_h, jitter_sec=jitter_sec)

    @staticmethod
    def validate_daily_time_slots(daily_raw: str) -> list[tuple[int, int, int]]:
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

    @staticmethod
    def fmt_bj(ts: float | None) -> str:
        if not ts:
            return ''
        dt = datetime.fromtimestamp(float(ts), TZ)
        wk = ('周一', '周二', '周三', '周四', '周五', '周六', '周日')[dt.weekday()]
        return f"{dt.strftime('%Y-%m-%d %H:%M:%S')} ({wk})"

    @staticmethod
    def parse_wall_time(raw: str, now_ts: float | None = None) -> float:
        s = raw.strip()
        base_ts = now_ts if now_ts is not None else time.time()
        now_dt = datetime.fromtimestamp(base_ts, TZ)
        m_hm = re.fullmatch(r'(\d{1,2}):(\d{2})(?::(\d{2}))?', s)
        if m_hm:
            hh, mm, ss = int(m_hm.group(1)), int(m_hm.group(2)), int(m_hm.group(3) or 0)
            if hh == 24 and mm == 0 and ss == 0:
                hh = 0
            if not (0 <= hh <= 23 and 0 <= mm <= 59 and 0 <= ss <= 59):
                raise ValueError(f'时间范围无效: {s}')
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
    def _compute_next_routine_ts(
        r: dict,
        now_ts: float | None = None,
        quiet_window: tuple[int, int] = (0, 8),
    ) -> float:
        base_ts = float(now_ts if now_ts is not None else time.time())
        daily_raw = str(r.get('daily_time') or '').strip()
        if daily_raw:
            try:
                slots = SchedulerEngine.validate_daily_time_slots(daily_raw)
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
        last_run = float(r.get('last_run') or 0)
        hours = max(0.05, float(r.get('interval_hours') or 24))
        step = hours * 3600
        cand = (last_run + step) if last_run > 0 else (base_ts + step)
        if cand <= base_ts + 1:
            steps = int((base_ts + 1 - cand) // step) + 1
            cand += steps * step
        if cand <= base_ts + 1:
            cand += step
        if not r.get('allow_quiet') and not r.get('quiet_ok'):
            cand, _ = SchedulerEngine.defer_ts_out_of_quiet_window(cand, quiet_window[0], quiet_window[1])
        return cand

    def compute_next_routine_run(self, r: dict, now_ts: float | None = None) -> float:
        return self._compute_next_routine_ts(r, now_ts, quiet_window=self.get_quiet_window())

    def build_curiosity_poll_view(self, now_t: float | None = None) -> dict:
        now_val = float(now_t if now_t is not None else time.time())
        cfg = getattr(self.store, 'config', None)
        cur_min = max(1, int(getattr(cfg, 'curiosity_interval_minutes', 30) or 30))
        assistants = list(self.store.state.get('assistants') or [])
        cur_bots: list[str] = []
        for b in assistants:
            if not isinstance(b, dict) or not b.get('enabled', True):
                continue
            src_map = b.get('sources') or {}
            enabled = bool(
                b.get('curiosity_enabled')
                or (isinstance(src_map.get('pc'), dict) and src_map['pc'].get('curiosity_enabled'))
                or (isinstance(src_map.get('phone'), dict) and src_map['phone'].get('curiosity_enabled'))
            )
            if enabled:
                cur_bots.append(str(b.get('id') or ''))
        last_cur = float(self.store.state.get('last_curiosity') or 0)
        cur_next = float(self.store.state.get('curiosity_next_run') or 0)
        eff_next = cur_next if cur_next > 0 else ((last_cur + cur_min * 60) if last_cur > 0 else (now_val + cur_min * 60))
        in_quiet = self.is_quiet_hour(now_val)
        last_user = float(self.store.state.get('last_user') or 0)
        quiet_idle_wait = max(0, round(300 - (now_val - last_user))) if last_user > 0 and (now_val - last_user) < 300 else 0
        if not cur_bots:
            state_tag = 'disabled'
        elif in_quiet:
            state_tag = 'deferred_by_quiet'
            eff_next, _ = self.defer_out_of_quiet_hours(max(eff_next, now_val))
        elif eff_next > now_val + 1:
            state_tag = 'snoozed' if cur_next > last_cur + cur_min * 60 + 5 else 'scheduled'
        elif quiet_idle_wait > 0:
            state_tag = 'waiting_user_idle'
        else:
            state_tag = 'due_now'
        last_run_log = next(
            (e for e in reversed(list(self.store.state.get('task_run_logs') or [])) if str(e.get('task_id') or '') == 'curiosity_poll'),
            None,
        )
        return {
            'id': 'curiosity_poll',
            'name': '好奇心自主巡检 (Curiosity)',
            'enabled': bool(cur_bots),
            'enabled_bots': cur_bots,
            'interval_minutes': cur_min,
            'last_run': last_cur,
            'last_run_bj': self.fmt_bj(last_cur),
            'next_run': eff_next,
            'next_run_bj': self.fmt_bj(eff_next),
            'overdue_seconds': max(0, round(now_val - eff_next)) if cur_bots and not in_quiet else 0,
            'in_quiet_hours': in_quiet,
            'schedule_state': state_tag,
            'last_outcome': (last_run_log or {}).get('status', ''),
            'last_outcome_detail': (last_run_log or {}).get('detail', ''),
        }

    def inspect_scheduler_health(self, now_t: float | None = None) -> dict:
        now_val = float(now_t if now_t is not None else time.time())
        routines = list(self.store.state.get('routines') or [])
        stale_routines: list[dict] = []
        circuit_broken: list[dict] = []
        warnings: list[str] = []
        for r in routines:
            if not isinstance(r, dict) or not r.get('enabled', True):
                continue
            skips = int(r.get('consecutive_skips') or 0)
            cb_until = float(r.get('circuit_broken_until') or 0)
            nxt = float(r.get('next_run') or 0)
            if cb_until > now_val:
                circuit_broken.append({'id': r.get('id'), 'goal': str(r.get('goal') or '')[:60], 'until_bj': self.fmt_bj(cb_until)})
                warnings.append(f"routine {r.get('id')} 已触发熔断至 {self.fmt_bj(cb_until)}")
            elif skips >= 3 or (nxt > 0 and nxt < now_val - 1800 and not r.get('daily_time')):
                stale_routines.append({'id': r.get('id'), 'goal': str(r.get('goal') or '')[:60], 'consecutive_skips': skips, 'next_run_bj': self.fmt_bj(nxt)})
                warnings.append(f"routine {r.get('id')} 连续跳过 {skips} 次或过期未推进 (next={self.fmt_bj(nxt)})")
        cur_view = self.build_curiosity_poll_view(now_val)
        cur_min = int(cur_view.get('interval_minutes') or 30)
        last_cur = float(cur_view.get('last_run') or 0)
        starved = False
        if cur_view.get('enabled') and not cur_view.get('in_quiet_hours'):
            if last_cur > 0 and (now_val - last_cur) > max(cur_min * 60 * 3, 5400):
                starved = True
                gap_min = round((now_val - last_cur) / 60)
                warnings.append(f"好奇心巡检已超过 {gap_min} 分钟未触发（配置间隔 {cur_min} 分钟）")
        start_h, end_h = self.get_quiet_window()
        return {
            'healthy': not warnings,
            'warnings': warnings,
            'stale_routines': stale_routines,
            'circuit_broken_routines': circuit_broken,
            'curiosity_starved': starved,
            'curiosity_status': cur_view,
            'quiet_window': {'start_hour': start_h, 'end_hour': end_h, 'in_quiet_hours': self.is_quiet_hour(now_val)},
        }

    def record_curiosity_cycle(self, status: str, bot_id: str, detail: str = '', snooze_min: int | None = None) -> None:
        from core.turn_ledger import TurnLedgerManager
        from vault_manager import now_iso
        now_ts = time.time()
        cur_min = max(1, int(getattr(self.store.config, 'curiosity_interval_minutes', 30) or 30))
        eff_min = cur_min if snooze_min is None else max(1, int(snooze_min))
        if snooze_min is None:
            existing_next = float(self.store.state.get('curiosity_next_run') or 0)
            self.store.state['curiosity_next_run'] = max(existing_next, now_ts + eff_min * 60)
        else:
            self.store.state['curiosity_next_run'] = now_ts + eff_min * 60
        self.store.state['last_curiosity'] = now_ts
        TurnLedgerManager.append_run_log(
            self.store,
            {
                'kind': 'curiosity',
                'task_id': 'curiosity_poll',
                'bot_id': bot_id,
                'goal': '好奇心自主巡检',
                'status': status,
                'detail': str(detail or '')[:160],
                'next_run_bj': self.fmt_bj(self.store.state['curiosity_next_run']),
                'ts': now_iso(),
            },
        )
        self.store.save()

    def build_system_status_extras(self, cur_src: str, bot_id: str, last_pt: int, last_ct: int, hit_pct: int) -> dict:
        from core.turn_ledger import TurnLedgerManager
        cfg = self.store.config
        if last_pt <= 0:
            cache_reason = '当前轮尚无服务端返回的 prompt_tokens 记录（可能在首轮前或仅统计客户端估值）。'
        elif last_pt < 1024:
            cache_reason = f'Prompt 长度 {last_pt} tokens < 1024，前缀未达到模型厂商 KV-Cache 最小分块阈值（1024 tok），命中率为 0% 属正常机制。'
        elif hit_pct == 0:
            cache_reason = f'Prompt {last_pt} >= 1024 tok 但缓存命中 0%，通常为本会话首次冷启动建缓存，或本轮动态变更了系统前缀/模型导致缓存未命中；后续连续轮次应升至 80%~99%。'
        else:
            cache_reason = f'KV-Cache 工作正常：最近一轮命中 {last_ct}/{last_pt} tokens ({hit_pct}%)。'
        return {
            'cache_diagnostic': {
                'last_prompt_tokens': last_pt,
                'last_cached_tokens': last_ct,
                'cache_hit_pct': hit_pct,
                'kv_cache_min_tokens_hint': 1024,
                'explanation': cache_reason,
            },
            'config_summary': {
                'default_model': cfg.effective_model(),
                'auto_compact_tokens': int(getattr(cfg, 'auto_compact_tokens', 200000) or 200000),
                'curiosity_interval_minutes': int(getattr(cfg, 'curiosity_interval_minutes', 30) or 30),
                'idle_research_minutes': int(getattr(cfg, 'idle_research_minutes', 30) or 30),
                'quiet_start_hour': int(getattr(cfg, 'quiet_start_hour', 0) or 0),
                'quiet_end_hour': int(getattr(cfg, 'quiet_end_hour', 8) or 8),
            },
            'scheduler_health': self.inspect_scheduler_health(),
            'observability': {
                'turn_ledger': (self.store.state.get('turn_ledgers') or {}).get(f'{cur_src}:{bot_id}'),
                'recent_task_runs': TurnLedgerManager.get_folded_recent_runs(self.store, limit=16),
            },
        }

    def decorate_schedule_view(self) -> dict:
        timers = self.store.state.setdefault('timers', [])
        routines = self.store.state.setdefault('routines', [])
        t_view = []
        for t in timers:
            t_view.append({
                **t,
                'due_at_bj': self.fmt_bj(t.get('due_at')),
                'remaining_seconds': max(0, round(float(t.get('due_at', 0)) - time.time())) if not t.get('done') else 0,
            })
        r_view = []
        changed = False
        now_t = time.time()
        for r in routines:
            nxt = float(r.get('next_run') or 0)
            if not nxt or (r.get('daily_time') and nxt < now_t - 14400) or (not r.get('daily_time') and nxt < now_t - 1800 and int(r.get('consecutive_skips') or 0) > 0):
                r['next_run'] = self.compute_next_routine_run(r, now_t)
                r['next_run_bj'] = self.fmt_bj(r['next_run'])
                changed = True
            mode_desc = f"每天 {r['daily_time']} (北京时间)" if r.get('daily_time') else f"每 {r.get('interval_hours', 24)} 小时"
            if r.get('weekdays') and str(r.get('weekdays')) not in ('', 'all', '*'):
                mode_desc += f" [星期 {r['weekdays']}]"
            overdue_s = max(0, round(now_t - float(r.get('next_run') or now_t)))
            cb_until = float(r.get('circuit_broken_until') or 0)
            if not r.get('enabled', True):
                sched_state = 'disabled'
            elif cb_until > now_t:
                sched_state = 'circuit_broken'
            elif (
                (not r.get('daily_time') and not r.get('allow_quiet') and not r.get('quiet_ok') and self.is_quiet_hour(float(r.get('next_run') or now_t)))
                or (r.get('deferred_reason') == 'quiet_hours' and float(r.get('next_run') or 0) > now_t)
            ):
                sched_state = 'deferred_by_quiet'
            elif overdue_s > 0:
                sched_state = 'queued_catchup'
            else:
                sched_state = 'scheduled'
            r_view.append({
                **r,
                'schedule_mode': mode_desc,
                'schedule_state': sched_state,
                'overdue_seconds': overdue_s,
                'next_run_bj': self.fmt_bj(r.get('next_run')),
                'last_run_bj': self.fmt_bj(r.get('last_run')),
            })
        if changed:
            self.store.save()
        start_h, end_h = self.get_quiet_window()
        return {
            'now_bj': self.fmt_bj(now_t),
            'quiet_window': {'start_hour': start_h, 'end_hour': end_h, 'in_quiet_hours': self.is_quiet_hour(now_t)},
            'builtin_polls': [self.build_curiosity_poll_view(now_t)],
            'timers': t_view,
            'routines': r_view,
        }

    async def handle_tool(self, args: dict, default_bot_id: str) -> dict:
        action = str(args.get('action') or 'list').strip().lower()
        timers = self.store.state.setdefault('timers', [])
        routines = self.store.state.setdefault('routines', [])
        if action == 'list':
            return self.decorate_schedule_view()
        if action == 'add_timer':
            goal = str(args.get('goal') or '').strip()
            if not goal:
                return {'error': 'goal 不能为空'}
            bot_id = str(args.get('bot_id') or default_bot_id).strip()
            at_time = str(args.get('at_time') or args.get('daily_time') or '').strip()
            if at_time:
                try:
                    due_at = self.parse_wall_time(at_time)
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
                'due_at_bj': self.fmt_bj(due_at),
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
                return {'error': 'goal 不能为空'}
            hours = max(1, min(720, int(args.get('interval_hours') or 24)))
            bot_id = str(args.get('bot_id') or default_bot_id).strip()
            daily_time = str(args.get('daily_time') or args.get('at_time') or '').strip()
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
            item['next_run'] = self.compute_next_routine_run(item)
            item['next_run_bj'] = self.fmt_bj(item['next_run'])
            routines.append(item)
            self.store.save()
            self.wake.set()
            return {'scheduled': True, 'routine': item}
        if action == 'update':
            tid = str(args.get('task_id') or '').strip()
            for r in routines:
                if r['id'] == tid or (not tid and len(routines) == 1):
                    if 'goal' in args and str(args['goal']).strip():
                        r['goal'] = str(args['goal']).strip()[:2000]
                    if 'bot_id' in args and str(args['bot_id']).strip():
                        r['bot_id'] = str(args['bot_id']).strip()
                    if 'daily_time' in args or 'at_time' in args:
                        r['daily_time'] = str(args.get('daily_time') or args.get('at_time') or '').strip()
                    if 'weekdays' in args:
                        r['weekdays'] = str(args['weekdays'] or '').strip()
                    if 'interval_hours' in args and args['interval_hours'] is not None:
                        r['interval_hours'] = max(1, min(720, int(args['interval_hours'])))
                    if 'enabled' in args and args['enabled'] is not None:
                        r['enabled'] = bool(args['enabled'])
                    r['next_run'] = self.compute_next_routine_run(r)
                    r['next_run_bj'] = self.fmt_bj(r['next_run'])
                    self.store.save()
                    self.wake.set()
                    return {'updated': True, 'routine': r}
            for t in timers:
                if t['id'] == tid:
                    if 'goal' in args and str(args['goal']).strip():
                        t['goal'] = str(args['goal']).strip()[:2000]
                    if 'at_time' in args or 'daily_time' in args:
                        raw_at = str(args.get('at_time') or args.get('daily_time') or '').strip()
                        try:
                            t['due_at'] = self.parse_wall_time(raw_at)
                        except Exception as e:
                            return {'error': f'无法解析 at_time "{raw_at}"：{e}'}
                        t['at_time'] = raw_at
                        t['due_at_bj'] = self.fmt_bj(t['due_at'])
                    elif 'delay_minutes' in args and args['delay_minutes'] is not None:
                        t['due_at'] = time.time() + max(1, int(args['delay_minutes'])) * 60
                        t['due_at_bj'] = self.fmt_bj(t['due_at'])
                    t['done'] = False
                    self.store.save()
                    self.wake.set()
                    return {'updated': True, 'timer': t}
            return {'error': f'未找到定时任务 "{tid}"'}
        if action == 'remove':
            tid = str(args.get('task_id') or '').strip()
            self.store.state['timers'] = [t for t in timers if t['id'] != tid]
            self.store.state['routines'] = [r for r in routines if r['id'] != tid]
            self.store.save()
            return {'removed': True, 'task_id': tid}
        return {'error': f'未知 schedule_task action: {action}'}
