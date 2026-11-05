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

    def is_quiet_hour(self, now_ts: float | None = None) -> bool:
        dt = datetime.fromtimestamp(now_ts if now_ts is not None else time.time(), TZ)
        return 0 <= dt.hour < 8

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
            target = now_dt.replace(hour=hh, minute=mm, second=ss, microsecond=0)
            if target.timestamp() <= base_ts:
                target += timedelta(days=1)
            return target.timestamp()
        s_iso = s.replace(' ', 'T') if re.match(r'^\d{4}-\d{2}-\d{2}\s+\d{1,2}:\d{2}', s) else s
        dt = datetime.fromisoformat(s_iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ)
        return dt.timestamp()

    @classmethod
    def extract_clock_from_goal(cls, goal: str) -> str:
        m = re.search(r'(?:每天|每早|每晚|早上|上午|中午|下午|晚上|凌晨|定点|北京时间)?\s*(\d{1,2}):(\d{2})', goal)
        if m:
            hh, mm = int(m.group(1)), int(m.group(2))
            if 0 <= hh <= 23 and 0 <= mm <= 59:
                if any(k in goal for k in ('下午', '晚上', '每晚')) and 1 <= hh <= 11:
                    hh += 12
                return f'{hh:02d}:{mm:02d}'
        return ''

    @classmethod
    def compute_next_routine_run(cls, r: dict, now_ts: float | None = None) -> float:
        base_ts = now_ts if now_ts is not None else time.time()
        daily_raw = str(r.get('daily_time') or '').strip()
        if not daily_raw:
            inferred = cls.extract_clock_from_goal(str(r.get('goal') or ''))
            if inferred:
                daily_raw = inferred
                r['daily_time'] = inferred
        if daily_raw:
            slots = []
            for part in daily_raw.split(','):
                part = part.strip()
                m = re.fullmatch(r'(\d{1,2}):(\d{2})(?::(\d{2}))?', part)
                if m:
                    slots.append((int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)))
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
        for r in routines:
            if not r.get('next_run') or (r.get('daily_time') and r.get('next_run', 0) < time.time() - 86400):
                r['next_run'] = self.compute_next_routine_run(r)
                changed = True
            elif not r.get('daily_time') and self.extract_clock_from_goal(str(r.get('goal') or '')):
                r['next_run'] = self.compute_next_routine_run(r)
                changed = True
            mode_desc = f"每天 {r['daily_time']} (北京时间)" if r.get('daily_time') else f"每 {r.get('interval_hours', 24)} 小时"
            if r.get('weekdays') and str(r.get('weekdays')) not in ('', 'all', '*'):
                mode_desc += f" [星期 {r['weekdays']}]"
            r_view.append({
                **r,
                'schedule_mode': mode_desc,
                'next_run_bj': self.fmt_bj(r.get('next_run')),
                'last_run_bj': self.fmt_bj(r.get('last_run')),
            })
        if changed:
            self.store.save()
        return {'now_bj': self.fmt_bj(time.time()), 'timers': t_view, 'routines': r_view}

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
            if not daily_time:
                daily_time = self.extract_clock_from_goal(goal)
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
                        t['at_time'] = raw_at
                        t['due_at'] = self.parse_wall_time(raw_at)
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
