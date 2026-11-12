from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from main import create_app


def _build_test_brain(tmp_path: Path):
    app = create_app(tmp_path, scheduler=False)
    store = app.state.store
    store.update_config({
        'api_key': 'sk-test-key',
        'default_model': 'deepseek-reasoner',
        'enabled': True,
    })
    brain = app.state.brain
    return brain, store, brain.vault, brain.harness, []


@pytest.mark.asyncio
async def test_exact_autostop_placeholder_and_english_cot_recovery(tmp_path: Path):
    """
    Reproduces live incident 69fa966a3a7fc82662eeda66 ("快点快点退了学生票"):
    - History contains a previously leaked assistant message "正在执行 2 项任务…".
    - Step 1 calls a tool (`list_dir`).
    - Step 2 returns literal `content="正在执行 2 项任务…"` + English CoT `"Let me tap the checkbox @e17."` with `tool_calls=[]`.
    - Brain MUST strip the leaked history placeholder, catch the mid-loop dropout on Step 2, execute Step 3 (`list_dir`), and output the real final summary!
    """
    brain, store, _, _, _ = _build_test_brain(tmp_path)
    leaked_msg = store.message('assistant', '正在执行 2 项任务…', 'chat', bot_id='companion', source='phone')
    assert brain._get_llm_history_text(leaked_msg) == ''

    seen_histories: list[list[dict]] = []
    call_idx = 0

    async def fake_complete(messages, tools=None, big=False):
        nonlocal call_idx
        call_idx += 1
        seen_histories.append(list(messages))
        if call_idx == 1:
            return {
                'role': 'assistant',
                'content': '',
                'thought': 'First open 12306 query screen.',
                'tool_calls': [{'id': 'c1', 'type': 'function', 'function': {'name': 'list_dir', 'arguments': '{"path": "."}'}}],
            }
        if call_idx == 2:
            # Exact bug: model imitates UI placeholder + finishes English CoT intending to tap @e17, but omits tool_calls!
            return {
                'role': 'assistant',
                'content': '正在执行 2 项任务…',
                'thought': 'Fastest safe path: tap 学生票 -> 查询 -> see price. Let me tap the checkbox @e17.',
                'tool_calls': [],
            }
        if call_idx == 3:
            return {
                'role': 'assistant',
                'content': '',
                'thought': 'Now actually invoking the tool.',
                'tool_calls': [{'id': 'c2', 'type': 'function', 'function': {'name': 'list_dir', 'arguments': '{"path": "."}'}}],
            }
        return {
            'role': 'assistant',
            'content': '已查清去程学生票二等座票价并核对退差方案，主人确认即可退旧买新。',
            'thought': 'All steps verified.',
            'tool_calls': [],
        }

    brain.complete = fake_complete  # type: ignore[method-assign]
    out = await brain.chat('快点快点退了学生票', bot_id='companion', source='phone')
    assert call_idx == 4
    assert '正在执行 2 项任务' not in out['text']
    assert '已查清去程学生票' in out['text']
    assert len(out.get('trace') or []) == 2
    # Leaked placeholder from history must NEVER appear in prompt sent to LLM
    for m in seen_histories[0]:
        if m.get('role') == 'assistant':
            assert '正在执行 2 项任务' not in str(m.get('content') or '')


@pytest.mark.asyncio
async def test_cross_node_stop_does_not_abort_phone_turn_and_records_ledger(tmp_path: Path):
    """Stopping `pc:companion` must NEVER abort an in-flight `phone:companion` turn."""
    brain, store, _, _, _ = _build_test_brain(tmp_path)
    step1_entered = asyncio.Event()
    allow_finish = asyncio.Event()

    async def slow_phone_complete(messages, tools=None, big=False):
        step1_entered.set()
        await allow_finish.wait()
        return {'role': 'assistant', 'content': '手机端任务顺利完成，未被电脑端打断！', 'tool_calls': []}

    brain.complete = slow_phone_complete  # type: ignore[method-assign]
    phone_task = asyncio.create_task(brain.chat('手机端独立任务', bot_id='companion', source='phone'))
    await asyncio.wait_for(step1_entered.wait(), timeout=3.0)

    # Stop PC companion while Phone companion is mid-flight!
    await brain.stop_immediately(bot_id='companion', reason='user_stop', source='pc')
    allow_finish.set()
    res = await asyncio.wait_for(phone_task, timeout=3.0)
    assert res.get('text') == '手机端任务顺利完成，未被电脑端打断！'
    assert store.state['turn_ledgers']['phone:companion']['status'] == 'completed'


@pytest.mark.asyncio
async def test_interrupted_turn_injects_recovery_hint_on_next_turn(tmp_path: Path):
    """Section 34 & 36-2: Interrupted turn persists ledger and injects `<previous_turn_interrupted>` on the next turn."""
    brain, store, _, _, _ = _build_test_brain(tmp_path)
    in_flight = asyncio.Event()

    async def hang_complete(messages, tools=None, big=False):
        in_flight.set()
        await asyncio.sleep(10)
        return {'role': 'assistant', 'content': 'never'}

    brain.complete = hang_complete  # type: ignore[method-assign]
    t1 = asyncio.create_task(brain.chat('帮我查12306车票', bot_id='companion', source='phone'))
    await asyncio.wait_for(in_flight.wait(), timeout=3.0)
    await brain.stop_immediately(bot_id='companion', reason='user_preempt', source='phone')
    await asyncio.wait_for(t1, timeout=3.0)
    assert store.state['turn_ledgers']['phone:companion']['status'] == 'interrupted'

    captured_last_user = ''

    async def next_complete(messages, tools=None, big=False):
        nonlocal captured_last_user
        captured_last_user = str(messages[-1].get('content') or '')
        return {'role': 'assistant', 'content': '收到，我先刷新最新画面再继续。', 'tool_calls': []}

    brain.complete = next_complete  # type: ignore[method-assign]
    await brain.chat('继续刚才的车票操作', bot_id='companion', source='phone')
    assert '<previous_turn_interrupted>' in captured_last_user
    assert '帮我查12306车票' in captured_last_user


@pytest.mark.asyncio
async def test_scheduler_catchup_no_55s_drop_and_failure_alert(tmp_path: Path):
    """Section 35 & 36-3 & 36-4: Two overdue routines execute sequentially without 55s silent drop, advance next_run, and emit alert on error."""
    brain, store, _, _, _ = _build_test_brain(tmp_path)
    now = time.time()
    store.state['routines'] = [
        {
            'id': 'rt-news',
            'goal': '整理今日新闻简报',
            'bot_id': 'companion',
            'daily_time': '08:00',
            'interval_hours': 24,
            'enabled': True,
            'next_run': now - 600,  # 10 min overdue -> late_ok catch-up
        },
        {
            'id': 'rt-fail',
            'goal': '测试报错告警任务',
            'bot_id': 'companion',
            'daily_time': '08:05',
            'interval_hours': 24,
            'enabled': True,
            'next_run': now - 300,
        },
    ]
    executed_goals: list[str] = []

    async def fake_run_scheduled(goal: str, bot_id: str | None = None, label: str = ''):
        executed_goals.append(goal)
        if '测试报错告警任务' in goal:
            raise RuntimeError('模拟上游网络异常')
        return {'ok': True, 'trace': []}

    brain.run_scheduled = fake_run_scheduled  # type: ignore[method-assign]
    sched_task = asyncio.create_task(brain.scheduler())
    try:
        brain.wake.set()
        for _ in range(30):
            if len(executed_goals) >= 2:
                break
            brain.wake.set()
            await asyncio.sleep(0.05)
    finally:
        sched_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await sched_task

    assert len(executed_goals) == 2
    assert '延迟补跑说明' in executed_goals[0]
    r0, r1 = store.state['routines'][0], store.state['routines'][1]
    assert r0['last_status'] == 'late_ok'
    assert float(r0['next_run']) > now
    assert r1['last_status'] == 'error'
    assert float(r1['next_run']) > now
    assert any('定时例行执行异常告警' in m.get('text', '') for m in store.state['messages'])
    st_doc = await brain._tool_system_status({'op': 'status'})
    assert len(st_doc['observability']['recent_task_runs']) >= 2


@pytest.mark.asyncio
async def test_phone_ui_readable_hint_session_handle_and_root_reconcile(tmp_path: Path):
    """Sections 28/29/30, 36-0②, 36-1: Verify ui_readable=False hint, display_session_id, and device_info root_degraded."""
    brain, _, _, _, _ = _build_test_brain(tmp_path)

    async def fake_phone_rpc(op: str, args: dict, timeout: float = 32.0):
        if op == 'device_info':
            return {'ok': True, 'root': True, 'battery': 90}
        if op == 'shell':
            return {'ok': False, 'exit_code': 0, 'output': '', 'error': 'Root 工具进程握手未完成: ok'}
        if op == 'ui_tree':
            return {'ok': True, 'package': 'com.tencent.mm', 'node_count': 0, 'ui_tree': '', 'displayId': 26, 'width': 720, 'height': 1600}
        return {'ok': True}

    brain._raw_phone_rpc = fake_phone_rpc  # type: ignore[method-assign]
    dev_res = await brain._tool_phone_workspace({'op': 'device_info'})
    assert dev_res['root_present'] is True
    assert dev_res['root_functional'] is False
    assert dev_res['root'] is False
    assert dev_res['root_degraded'] is True

    sh_res = await brain._tool_phone_workspace({'op': 'shell', 'command': 'id'})
    assert sh_res['ok'] is False
    assert sh_res['exit_code'] == -1
    assert sh_res['root_state'] == 'handshake_failed'
    assert not sh_res['error'].endswith(': ok')

    insp_res = await brain._tool_phone_workspace({'op': 'inspect', 'include_image': False})
    assert insp_res['ui_readable'] is False
    assert '0..1000' in insp_res['ui_hint']
    assert insp_res['display_session_id'] == 'vd-phone'
    assert insp_res['display_id'] == 26
