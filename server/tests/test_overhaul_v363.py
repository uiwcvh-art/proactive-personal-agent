"""End-to-end automated regression suite for v3.6.3 Overhaul:
1. Persistent Engine-Level `/goal` Mode + `complete_goal` Tool:
   - AI stopping mid-way with text only is automatically pulled back up by the engine.
   - Loop terminates ONLY when `complete_goal(summary=..., verified_evidence=...)` is invoked (or `/goal stop` / `stop_immediately`).
2. Agent Loop Premature-Stop Guard vs Post-Summary Non-Trigger:
   - Catches `"现在余去查虚拟屏为什么销不掉。"` (0 tool calls) and forces immediate continuation.
   - Does NOT false-trigger after a completed substantive summary (`len >= 220` with completed `trace`), and preserves full summary across trailing cleanup tool calls (`phone_workspace(op="close")`).
3. Virtual Display Controller (`phone_workspace`):
   - `op="release" | "close" | "destroy"` sends `destroy=True, user_close=True` by default and returns `released=True, display_active=False`.
   - `op="press", key="back"` fires `key` RPC strictly ONCE even when `treeHash` is unchanged (`nodeCount=0` on WeChat).
   - `op="shell"` hard-blocks `cmd uimode night no`, `settings put system ui_night_mode`, `monkey`, and `am start --display 0`.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
import pytest

SERVER_DIR = Path(__file__).resolve().parents[1]
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

from brain import Brain
from main import create_app


def _make_brain(tmp_path: Path) -> Brain:
    app = create_app(tmp_path / 'data', scheduler=False)
    app.state.store.config.small_brain.api_key = 'sk-test-key-12345678'
    app.state.store.config.small_brain.base_url = 'https://api.example.com/v1'
    app.state.store.config.small_brain.model = 'deepseek-reasoner'
    app.state.store.config.big_brain.api_key = 'sk-test-key-12345678'
    app.state.store.config.big_brain.base_url = 'https://api.example.com/v1'
    app.state.store.config.big_brain.model = 'deepseek-reasoner'
    return app.state.brain


def test_unfulfilled_action_promise_catches_premature_stop_and_spares_summary(tmp_path: Path):
    brain = _make_brain(tmp_path)
    # 1. Exact user-reported failure case ("现在余去查虚拟屏为什么销不掉。") MUST be caught!
    assert brain._is_unfulfilled_action_promise(
        '刚才那条已经核实完了。\n现在余去查虚拟屏为什么销不掉。',
        user_text='查一下',
        trace=[],
        thought='',
    ) is True
    assert brain._is_unfulfilled_action_promise(
        '让余先去打开支付宝看看积分情况。',
        user_text='看看支付宝',
        trace=[],
        thought='',
    ) is True

    # 2. Completed substantive summary (>= 220 chars) with internal `thought` mentioning "下一步" MUST NOT false-trigger!
    completed_report = (
        '主人，铁路 12306 的车次余票已经全部核实完毕啦喵～\n\n'
        '1. **G1032 次**：09:15 宜昌北发车 → 11:12 抵达汉口，二等座有票（¥112）。\n'
        '2. **D5816 次**：10:40 宜昌北发车 → 12:45 抵达武昌，二等座候补。\n'
        '3. **G802 次**：13:20 宜昌北发车 → 15:10 抵达武汉站，二等座充足（¥128）。\n\n'
        '手机独立虚拟屏也已经为你干净销毁收起，主屏应用完全不受影响。主人想选哪一趟告诉余就好～'
    )
    assert len(completed_report) >= 220
    assert brain._is_unfulfilled_action_promise(
        completed_report,
        user_text='查票',
        trace=[{'tool': 'phone_workspace', 'arguments': {'op': 'open'}, 'result': {'ok': True}}],
        thought='如果主人下一步想买票，接下来余准备帮主人选乘车人。',
    ) is False


@pytest.mark.asyncio
async def test_persistent_goal_mode_repulls_until_complete_goal_called(tmp_path: Path):
    brain = _make_brain(tmp_path)
    step_counter = 0

    async def fake_complete(messages, tools=None, big=False, tool_choice=None):
        nonlocal step_counter
        step_counter += 1
        # Step 1: Model runs one tool (`list_dir`)
        if step_counter == 1:
            return {
                'content': '余先查看工作区目录。',
                'tool_calls': [{
                    'id': 'call_1',
                    'type': 'function',
                    'function': {'name': 'list_dir', 'arguments': json.dumps({'path': '.'})},
                }],
            }
        # Step 2: Model prematurely stops with text ONLY (did NOT call complete_goal!) -> Engine MUST pull it back up!
        if step_counter == 2:
            return {
                'content': '第一阶段目录已经看完了，暂时汇报到这里。',
                'tool_calls': [],
            }
        # Step 3: After engine wakeup #1, model stops AGAIN without complete_goal -> Engine MUST pull it back up #2!
        if step_counter == 3:
            return {
                'content': '第二阶段核对完毕。',
                'tool_calls': [],
            }
        # Step 4: Model finally calls `complete_goal`!
        if step_counter == 4:
            return {
                'content': '',
                'tool_calls': [{
                    'id': 'call_goal_done',
                    'type': 'function',
                    'function': {
                        'name': 'complete_goal',
                        'arguments': json.dumps({
                            'summary': '已完整核查工作区目录并完成全部目标交付。',
                            'verified_evidence': 'list_dir 返回根目录列表正常',
                        }, ensure_ascii=False),
                    },
                }],
            }
        # Step 5: Final clean reply after `complete_goal` -> loop terminates!
        return {
            'content': '主人，持久目标已 100% 完成并验证闭环啦喵～工作区目录一切正常！',
            'tool_calls': [],
        }

    brain.complete = fake_complete
    res = await brain.chat('/goal 检查工作区根目录并完成闭环验收', bot_id='companion', source='phone')
    assert step_counter == 5
    assert '100% 完成' in res['text']
    # Active goal must be cleared once `complete_goal` succeeded
    assert 'phone:companion' not in (brain.store.state.get('active_goals') or {})
    last_goal = brain.store.state.get('last_goal_run') or {}
    assert last_goal.get('status') == 'completed'
    assert last_goal.get('wakeups') == 2

    # Also verify `/goal stop` cleanly cancels an active goal
    brain.store.state.setdefault('active_goals', {})['phone:companion'] = {
        'id': 'goal-test', 'goal': '测试取消目标', 'status': 'active', 'wakeups': 1, 'total_steps': 3,
    }
    stop_res = await brain.chat('/goal stop', bot_id='companion', source='phone')
    assert '已停止持久目标模式' in stop_res['text']
    assert 'phone:companion' not in (brain.store.state.get('active_goals') or {})


@pytest.mark.asyncio
async def test_substantive_summary_preserved_across_trailing_cleanup_tool(tmp_path: Path):
    brain = _make_brain(tmp_path)
    rpc_calls = []

    async def fake_phone_rpc(op: str, args: dict, timeout: float = 32.0):
        rpc_calls.append((op, dict(args)))
        return {'ok': True, 'released': True, 'destroyed': True, 'created': False, 'display_active': False}

    brain._raw_phone_rpc = fake_phone_rpc
    long_summary = (
        '主人，余已经帮你在独立虚拟屏里查清楚支付宝积分和账单明细啦喵～\n\n'
        '- 当前可用积分：**3,280 分**（月底无即将过期积分，可随时兑换权益）\n'
        '- 最近一笔消费：瑞幸咖啡 ¥9.90，支付状态正常且已入账\n'
        '- 所有核对均在后台独立虚拟屏完成，全程完全未触碰手机主屏幕应用。'
    )
    assert len(long_summary) >= 120
    step_idx = 0

    async def fake_complete(messages, tools=None, big=False, tool_choice=None):
        nonlocal step_idx
        step_idx += 1
        if step_idx == 1:
            # Model writes full summary alongside final cleanup tool call `phone_workspace(op="close")`
            return {
                'content': long_summary,
                'tool_calls': [{
                    'id': 'call_close_vd',
                    'type': 'function',
                    'function': {'name': 'phone_workspace', 'arguments': json.dumps({'op': 'close'})},
                }],
            }
        # Step 2: Model emits short closing line (or empty) — long_summary MUST NOT vanish!
        return {'content': '虚拟屏也已经为你彻底销毁关闭啦喵～', 'tool_calls': []}

    brain.complete = fake_complete
    out = await brain.chat('查一下支付宝积分然后关掉虚拟屏', bot_id='companion', source='phone')
    assert '3,280 分' in out['text']
    assert '彻底销毁关闭' in out['text']
    assert len(rpc_calls) == 1
    assert rpc_calls[0][0] == 'release'
    assert rpc_calls[0][1]['destroy'] is True
    assert rpc_calls[0][1]['user_close'] is True


@pytest.mark.asyncio
async def test_phone_workspace_single_back_key_and_shell_sandbox(tmp_path: Path):
    brain = _make_brain(tmp_path)
    brain.source_context.set('phone')
    rpc_log = []

    async def fake_phone_rpc(op: str, args: dict, timeout: float = 32.0):
        rpc_log.append((op, dict(args)))
        if op == 'ui_tree':
            # Simulate WeChat self-drawn screen where treeHash stays constant!
            return {
                'ok': True,
                'package': 'com.tencent.mm',
                'nodeCount': 0,
                'treeHash': 'wx_const_hash',
                'width': 1080,
                'height': 2400,
                'summary': '',
            }
        if op == 'key':
            return {
                'ok': True,
                'package': 'com.tencent.mm',
                'nodeCount': 0,
                'treeHash': 'wx_const_hash',
                'width': 1080,
                'height': 2400,
                'summary': '',
            }
        if op == 'release':
            return {'ok': True, 'released': True, 'destroyed': True, 'created': False}
        return {'ok': True}

    brain._raw_phone_rpc = fake_phone_rpc

    # 1. `press(key="back")` MUST fire `key` RPC strictly ONCE even when `treeHash` does not change!
    res_back = await brain._tool_phone_workspace({'op': 'press', 'key': 'back', 'include_image': False})
    assert res_back.get('ok') is True
    key_rpcs = [x for x in rpc_log if x[0] == 'key']
    assert len(key_rpcs) == 1

    # 2. `op="release"` destroys by default and reports `display_active: False`
    res_rel = await brain._tool_phone_workspace({'op': 'release'})
    assert res_rel['released'] is True
    assert res_rel['destroyed'] is True
    assert res_rel['display_active'] is False
    assert res_rel['created'] is False

    # 3. `op="shell"` blocks `cmd uimode`, `settings put`, `monkey`, and `--display 0`
    for bad_cmd in (
        'cmd uimode night no',
        'settings put system screen_brightness 255',
        'am start --display 0 -n com.tencent.mm/.ui.LauncherUI',
        'input -d 0 tap 500 500',
        'monkey -p com.tencent.mm 1',
    ):
        sh_res = await brain._tool_phone_workspace({'op': 'shell', 'command': bad_cmd})
        assert sh_res.get('ok') is False
        assert '拦截' in str(sh_res.get('error') or '')
