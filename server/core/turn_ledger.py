"""
[Module Responsibility]
多步任务事务账本、中断恢复提示、运行可观测日志与占位符防污染守卫 (`server/core/turn_ledger.py`)。
负责：
1. `TurnLedgerManager`: 按 `(source, bot_id)` 持久化当前回合的 `completed_steps` / `pending_step` 与终态 (`completed` / `interrupted` / `truncated` / `error`)，在异常中断后的下一轮注入精简 `<previous_turn_interrupted>` 恢复提示（修复 34 / 36-2）。
2. `task_run_logs` 统一可观测账本：记录最近 80 条对话回合与定时任务（含正常触发、延迟补跑 `late_ok`、静默跳过、过期推进、异常告警）的耗时、步数与状态，供 `system_status` 直接查询（修复 35 / 36-3 / 36-4）。
3. 流式 UI 占位符识别与英文/中文思维链断流检测 (`is_ui_placeholder_text`, `strip_leaked_placeholders_for_history`, `has_unfulfilled_cot_or_placeholder_intent`)：根除 `"正在执行 N 项任务…"` 被写入历史后遭模型模仿并配合英文思维链 (`"Let me tap @e17."`) 提前断流的 Bug。
"""
from __future__ import annotations

import re
import time
from typing import Any

from storage import normalize_node_source
from vault_manager import now_iso

MAX_TASK_RUN_LOGS = 80

_UI_PLACEHOLDER_HEAD_RX = re.compile(
    r'^(?:'
    r'正在(?:执行\s*\d+\s*项任务|并行执行|并行调度|调用\s+\S+|生成\s+\S+|准备调用\s+\S+|等待「[^」]+」|汇总「[^」]+」|思考|整理回复|重新生成)'
    r'|⏰\s*正在执行'
    r'|⏳\s*'
    r'|📦\s*'
    r'|🎯\s*正在'
    r'|🎯\s*目标模式持续推进中'
    r')',
    flags=re.IGNORECASE,
)

_UI_PLACEHOLDER_FULL_LINE_RX = re.compile(
    r'^\s*(?:'
    r'正在(?:执行\s*\d+\s*项任务|并行执行[^\n]{0,40}|调用\s+[^\n]{1,50}|思考(?:与执行任务)?|整理回复|重新生成)[….。…\s]*'
    r')\s*$',
    flags=re.MULTILINE,
)

_COT_TAIL_ACTION_INTENT_RX = re.compile(
    r'(?:'
    r'\blet\s+me\s+(?:tap|click|open|launch|check|run|call|search|try|inspect|wait|scroll|swipe|type|press|read|write|edit|fetch|use|select|tick)\b'
    r'|\blet\'s\s+(?:tap|click|open|launch|check|run|call|search|try|inspect|scroll|swipe|type|press|select)\b'
    r'|\bi\s+(?:will|should|need\s+to|am\s+going\s+to)\s+(?:tap|click|open|launch|check|run|call|inspect|scroll|type|press)\b'
    r'|\bi\'ll\s+(?:tap|click|open|launch|check|run|call|inspect|scroll|type|press)\b'
    r'|\bnow\s+(?:let\'s|let\s+me|i\s+will|i\'ll|tap|click)\b'
    r'|(?:让我|我先|余先|先实际|先查看|先打开|先看|先调|现在调|去看看|点一下|调用|试试|查一下|接下来(?:点|调|打开|看))'
    r')',
    flags=re.IGNORECASE,
)

_HUMAN_GATE_RX = re.compile(
    r'(?:付款|支付密码|确认支付|扣款|验证码|人脸识别|指纹识别|指纹验证|扫码登录|请主人接手|交给主人|需要主人亲自)'
)


def is_ui_placeholder_text(t: str) -> bool:
    s = (t or '').strip()
    if not s:
        return True
    if re.fullmatch(r'[.。…·,，:：;；!！?？\-_~\s]+', s):
        return True
    if s in ('正在思考…', '正在思考与执行任务…', '正在整理回复…', '正在重新生成…', '🎯 正在规划并深度执行目标…'):
        return True
    return bool(_UI_PLACEHOLDER_HEAD_RX.match(s))


def strip_leaked_placeholders_for_history(text: str) -> str:
    s = (text or '').strip()
    if not s:
        return ''
    s = _UI_PLACEHOLDER_FULL_LINE_RX.sub('', s)
    s = re.sub(r'\n{3,}', '\n\n', s).strip()
    if is_ui_placeholder_text(s):
        return ''
    return s


def has_unfulfilled_cot_or_placeholder_intent(text: str, thought: str) -> bool:
    s = (text or '').strip()
    if s and is_ui_placeholder_text(s):
        return True
    if s and _HUMAN_GATE_RX.search(s):
        return False
    if len(s) >= 90:
        return False
    th_tail = (thought or '').strip()[-300:]
    if not th_tail:
        return False
    last_th_clause = th_tail[-180:]
    return bool(_COT_TAIL_ACTION_INTENT_RX.search(last_th_clause))


class TurnLedgerManager:
    """Persists bounded turn transaction state & observability run logs in `store.state`."""

    @staticmethod
    def _nb_key(src: str | None, bot_id: str | None) -> str:
        return f"{normalize_node_source(src)}:{(bot_id or 'companion').strip()}"

    @classmethod
    def begin_turn(cls, store, src: str, bot_id: str, turn_id: str, goal: str) -> dict | None:
        ledgers: dict[str, dict] = store.state.setdefault('turn_ledgers', {})
        key = cls._nb_key(src, bot_id)
        prev = ledgers.get(key)
        unacked_prev = None
        if isinstance(prev, dict) and prev.get('status') in ('interrupted', 'truncated') and not prev.get('ack_injected'):
            prev['ack_injected'] = True
            unacked_prev = dict(prev)
        ledgers[key] = {
            'turn_id': turn_id,
            'source': normalize_node_source(src),
            'bot_id': bot_id,
            'goal': str(goal or '')[:240],
            'status': 'running',
            'completed_steps': [],
            'pending_step': '',
            'started_at': time.time(),
            'updated_at': now_iso(),
            'ack_injected': False,
        }
        return unacked_prev

    @classmethod
    def record_pending_step(cls, store, src: str, bot_id: str, turn_id: str, pending_desc: str) -> None:
        ledgers: dict[str, dict] = store.state.setdefault('turn_ledgers', {})
        cur = ledgers.get(cls._nb_key(src, bot_id))
        if isinstance(cur, dict) and (not turn_id or cur.get('turn_id') == turn_id):
            cur['pending_step'] = str(pending_desc or '')[:200]
            cur['updated_at'] = now_iso()

    @classmethod
    def record_completed_steps(cls, store, src: str, bot_id: str, turn_id: str, step_descs: list[str]) -> None:
        ledgers: dict[str, dict] = store.state.setdefault('turn_ledgers', {})
        cur = ledgers.get(cls._nb_key(src, bot_id))
        if isinstance(cur, dict) and (not turn_id or cur.get('turn_id') == turn_id):
            steps = list(cur.get('completed_steps') or [])
            for d in step_descs:
                if d:
                    steps.append(str(d)[:160])
            cur['completed_steps'] = steps[-16:]
            cur['pending_step'] = ''
            cur['updated_at'] = now_iso()

    @classmethod
    def finish_turn(
        cls,
        store,
        src: str,
        bot_id: str,
        turn_id: str,
        status: str = 'completed',
        reason: str = '',
        api_calls: int = 0,
        steps_count: int = 0,
        duration_ms: int = 0,
        error: str = '',
    ) -> None:
        ledgers: dict[str, dict] = store.state.setdefault('turn_ledgers', {})
        key = cls._nb_key(src, bot_id)
        cur = ledgers.get(key)
        goal_txt = ''
        if isinstance(cur, dict) and (not turn_id or cur.get('turn_id') == turn_id or cur.get('status') == 'running'):
            cur['status'] = status
            if reason:
                cur['reason'] = str(reason)[:160]
            if error:
                cur['error'] = str(error)[:240]
            if status == 'completed':
                cur['pending_step'] = ''
            cur['updated_at'] = now_iso()
            goal_txt = str(cur.get('goal') or '')
            if not steps_count:
                steps_count = len(cur.get('completed_steps') or [])
        cls.append_run_log(
            store,
             {
                'kind': 'chat_turn',
                'turn_id': turn_id,
                'source': normalize_node_source(src),
                'bot_id': bot_id,
                'goal': goal_txt[:140],
                'status': status,
                'reason': str(reason or '')[:120],
                'api_calls': int(api_calls or 0),
                'steps': int(steps_count or 0),
                'duration_ms': int(duration_ms or 0),
                'error': str(error or '')[:200],
                'ts': now_iso(),
            },
        )

    @staticmethod
    def append_run_log(store, entry: dict[str, Any]) -> None:
        logs: list[dict] = store.state.setdefault('task_run_logs', [])
        logs.append(entry)
        if len(logs) > MAX_TASK_RUN_LOGS:
            del logs[:-MAX_TASK_RUN_LOGS]

    @staticmethod
    def format_interrupted_turn_hint(prev_ledger: dict | None) -> str:
        if not isinstance(prev_ledger, dict):
            return ''
        age_sec = time.time() - float(prev_ledger.get('started_at') or 0)
        if age_sec > 1800:
            return ''
        done_steps = prev_ledger.get('completed_steps') or []
        pending = str(prev_ledger.get('pending_step') or '').strip()
        prev_goal = str(prev_ledger.get('goal') or '').strip()
        done_s = ' -> '.join(done_steps[-4:]) if done_steps else '无已完成工具步'
        pend_s = f'；中断于待执行步：{pending}' if pending else ''
        return (
            f'\n\n<previous_turn_interrupted>\n'
            f'[上一回合未完成事务回执]：前序目标「{prev_goal[:80]}」因 {prev_ledger.get("reason") or prev_ledger.get("status")} 中断（已完成：{done_s}{pend_s}）。'
            f'若主人本条消息要求继续前序操作，请先用 `inspect` 核实当前最新画面再行动，勿盲信旧坐标。\n'
            f'</previous_turn_interrupted>'
        )
