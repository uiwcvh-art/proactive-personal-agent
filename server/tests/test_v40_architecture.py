from __future__ import annotations

import asyncio
import time
from pathlib import Path
from fastapi.testclient import TestClient
import pytest

from main import create_app
from core.tool_schemas import TOOLS
from core.memory_engine import MemoryEngine


def _build_test_app(tmp_path: Path, monkeypatch=None):
    if monkeypatch is not None:
        monkeypatch.setenv('AGENT_TOKEN', 'test')
    app = create_app(tmp_path, scheduler=False)
    store = app.state.store
    store.update_config({
        'api_key': 'sk-test-key',
        'default_model': 'deepseek-reasoner',
        'enabled': True,
    })
    return app, app.state.brain, store


@pytest.mark.asyncio
async def test_v40_tools_and_pc_cloud_desktop_exposure(tmp_path: Path):
    _, brain, store = _build_test_app(tmp_path)
    tool_names = {t['function']['name'] for t in TOOLS}
    assert 'send_message' not in tool_names
    assert 'blackboard' in tool_names
    assert 'peek_bots' in tool_names

    pc_bot = next(b for b in store.state['assistants'] if b['id'] == 'pc_engineer')
    store.message('user', '打开云电脑看看', 'chat', bot_id='pc_engineer', source='pc')
    _, pc_tools, pc_allowed, _ = await brain._build_turn_messages_and_tools('pc_engineer', 'pc', pc_bot)
    pc_tool_names = {t['function']['name'] for t in pc_tools}
    assert 'cloud_desktop' in pc_tool_names
    assert 'cloud_desktop' in pc_allowed
    assert 'blackboard' in pc_tool_names
    assert 'peek_bots' in pc_tool_names
    assert 'send_message' not in pc_tool_names


@pytest.mark.asyncio
async def test_blackboard_mentions_kanban_review_timer_and_watch(tmp_path: Path):
    _, brain, store = _build_test_app(tmp_path)
    woken: list[tuple[str, str, str]] = []

    async def fake_chat(text: str, *, bot_id: str | None = None, source: str = 'phone', **kw):
        woken.append((str(bot_id), str(source), text))
        await asyncio.sleep(0.4)
        return {'ok': True, 'text': 'ack'}

    brain.chat = fake_chat  # type: ignore[method-assign]

    # 1. Non-blocking (<250ms even when target turn takes 400ms+) post + @mention async wakeup
    t0 = time.perf_counter()
    res = await brain.blackboard_engine.execute({
        'action': 'post',
        'title': '排查日志',
        'content': '@司工 帮忙在电脑端跑一下测试',
        'steer': True,
        '_caller_bot_id': 'companion',
        '_caller_source': 'phone',
    })
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert res['ok'] is True
    assert elapsed_ms < 250
    topic_id = res['topic']['post_id']
    assert 'pc_engineer' in res['woken_bots']
    await asyncio.sleep(0.05)
    assert any(b == 'pc_engineer' and src == 'pc' for b, src, _ in woken)

    # 2. Kanban claim lease & contention rejection
    c1 = await brain.blackboard_engine.execute({'action': 'claim', 'topic_id': topic_id, '_caller_bot_id': 'pc_engineer', '_caller_source': 'pc'})
    assert c1['ok'] is True
    assert c1['topic']['claimed_by'] == 'pc_engineer'
    c2 = await brain.blackboard_engine.execute({'action': 'claim', 'topic_id': topic_id, '_caller_bot_id': 'pc_research', '_caller_source': 'pc'})
    assert c2['ok'] is False

    # Normal completion (no forced review when review_by is not requested)
    done_normal = await brain.blackboard_engine.execute({
        'action': 'complete',
        'topic_id': topic_id,
        'artifact_path': 'workspace/report.md',
        'next_action': '主人可查阅报告',
        'content': '已跑完',
        '_caller_bot_id': 'pc_engineer',
        '_caller_source': 'pc',
    })
    assert done_normal['ok'] is True
    assert done_normal['topic']['status'] == 'done'
    assert done_normal['topic']['artifact_path'] == 'workspace/report.md'

    # 3. Opt-in Maker-Checker review gate (only when review_by / require_review is explicitly set)
    r_post = await brain.blackboard_engine.execute({
        'action': 'post',
        'title': '核心重构',
        'content': '@司工 完成后请审校复核',
        'review_by': '审校',
        '_caller_bot_id': 'companion',
        '_caller_source': 'phone',
    })
    rtid = r_post['topic']['post_id']
    c_gate = await brain.blackboard_engine.execute({
        'action': 'complete',
        'topic_id': rtid,
        'artifact_path': 'workspace/patch.diff',
        'content': '已提交补丁',
        '_caller_bot_id': 'pc_engineer',
        '_caller_source': 'pc',
    })
    assert c_gate['topic']['status'] == 'review_required'
    rev_ok = await brain.blackboard_engine.execute({
        'action': 'review',
        'topic_id': rtid,
        'verdict': 'approved',
        'content': '边界测试通过',
        '_caller_bot_id': 'pc_reviewer',
        '_caller_source': 'pc',
    })
    assert rev_ok['ok'] is True
    assert rev_ok['topic']['status'] == 'done'

    # 4. Alarm/Timer + 0-token File Watchpoint
    watch_target = store.root / 'workspace' / 'build_ready.flag'
    watch_target.parent.mkdir(parents=True, exist_ok=True)
    w_post = await brain.blackboard_engine.execute({
        'action': 'post',
        'title': '等待构建产物',
        'content': '构建完成后唤醒 @self',
        'mentions': ['self'],
        'watch': {'kind': 'file_exists', 'target': str(watch_target)},
        '_caller_bot_id': 'pc_engineer',
        '_caller_source': 'pc',
    })
    wtid = w_post['topic']['post_id']
    t_post = await brain.blackboard_engine.execute({
        'action': 'post',
        'title': '喝水闹钟',
        'content': '提醒主人喝水 @self',
        'delay_seconds': 0.02,
        'exec_action': 'notify_user',
        '_caller_bot_id': 'companion',
        '_caller_source': 'phone',
    })
    ttid = t_post['topic']['post_id']

    await asyncio.sleep(0.05)
    watch_target.write_text('ok', encoding='utf-8')
    fired = await brain.blackboard_engine.tick()
    assert fired >= 2

    bb_read = await brain.blackboard_engine.execute({'action': 'read', 'topic_id': wtid, '_caller_bot_id': 'pc_engineer', '_caller_source': 'pc'})
    assert bb_read['topic']['watch']['triggered'] is True
    t_read = await brain.blackboard_engine.execute({'action': 'read', 'topic_id': ttid, '_caller_bot_id': 'companion', '_caller_source': 'phone'})
    assert t_read['topic']['fired_at'] > 0


@pytest.mark.asyncio
async def test_native_turn_steps_replay_and_peek_bots(tmp_path: Path):
    _, brain, _ = _build_test_app(tmp_path)
    seen_second_turn_msgs: list[dict] = []
    step_no = 0

    async def fake_complete(messages, tools=None, big=False):
        nonlocal step_no
        step_no += 1
        if step_no == 1:
            return {
                'role': 'assistant',
                'content': '',
                'thought': 'Check workspace directory first.',
                'tool_calls': [{'id': 'call_native_1', 'type': 'function', 'function': {'name': 'list_dir', 'arguments': '{"path": "."}'}}],
            }
        if step_no == 2:
            return {
                'role': 'assistant',
                'content': '目录已检查完毕。',
                'thought': 'Done.',
                'tool_calls': [],
            }
        seen_second_turn_msgs.extend(messages)
        return {
            'role': 'assistant',
            'content': '收到第二轮指令，已衔接上一轮工具结果。',
            'thought': 'Replay verified.',
            'tool_calls': [],
        }

    brain.complete = fake_complete  # type: ignore[method-assign]
    out1 = await brain.chat('看看当前目录', bot_id='pc_engineer', source='pc')
    assert '目录已检查完毕' in out1['text']

    out2 = await brain.chat('刚才列出了什么？', bot_id='pc_engineer', source='pc')
    assert '收到第二轮指令' in out2['text']

    has_native_assistant_tc = any(
        m.get('role') == 'assistant' and any(tc.get('id') == 'call_native_1' for tc in (m.get('tool_calls') or []))
        for m in seen_second_turn_msgs
    )
    has_native_tool_role = any(
        m.get('role') == 'tool' and m.get('tool_call_id') == 'call_native_1'
        for m in seen_second_turn_msgs
    )
    assert has_native_assistant_tc is True
    assert has_native_tool_role is True

    peek = await brain._tool_peek_bots({'target': 'pc_engineer', 'limit': 3})
    assert peek['ok'] is True
    assert len(peek['bots']) == 1
    assert peek['bots'][0]['id'] == 'pc_engineer'
    assert len(peek['bots'][0]['recent_messages']) >= 2


@pytest.mark.asyncio
async def test_100k_threshold_and_topic_boundary_partial_compaction(tmp_path: Path):
    _, brain, store = _build_test_app(tmp_path)
    assert MemoryEngine.AUTO_COMPACT_TOKENS == 100_000
    assert store.config.auto_compact_tokens == 100_000

    # Below 100K (~12K tokens): must NOT trigger auto-compaction
    for i in range(6):
        store.message('user', f'早期话题 {i}: ' + ('测试内容' * 300), 'chat', bot_id='companion', source='phone')
        store.message('assistant', f'早期回复 {i}: ' + ('结论归档' * 300), 'chat', bot_id='companion', source='phone')

    under_res = await brain._auto_compact_if_needed('companion', force=False, source='phone')
    assert under_res is None

    # Push over 100K with a clear completed episode gap (>30 min) followed by active recent chat
    now = time.time()
    all_msgs = store.state['messages']
    for idx, m in enumerate(all_msgs):
        m['created_at'] = now - 7200 + idx * 10

    for i in range(36):
        all_msgs.append({
            'id': f'old_u_{i}',
            'role': 'user',
            'kind': 'chat',
            'bot_id': 'companion',
            'source': 'phone',
            'text': f'大段历史调研 {i}: ' + ('架构细节与实验日志' * 850),
            'created': now - 5400 + i * 20,
            'created_at': now - 5400 + i * 20,
        })
        all_msgs.append({
            'id': f'old_a_{i}',
            'role': 'assistant',
            'kind': 'chat',
            'bot_id': 'companion',
            'source': 'phone',
            'text': f'大段历史结论 {i}: ' + ('已完成阶段归档完毕' * 850),
            'created': now - 5390 + i * 20,
            'created_at': now - 5390 + i * 20,
        })

    recent_u = store.message('user', '【当前正在进行的活跃新话题】请保留原文不要压缩我', 'chat', bot_id='companion', source='phone')
    recent_a = store.message('assistant', '【当前活跃回复】明白，这段正在进行中的对话会完整保留。', 'chat', bot_id='companion', source='phone')
    recent_u['created_at'] = now - 120
    recent_a['created_at'] = now - 110

    async def fake_stream_with_tools(msgs, sys_p='', on_delta=None, **kw):
        return {
            'role': 'assistant',
            'content': '### 📋 核心进展与上下文摘要\n\n- 完成了早期话题与 36 轮架构调研归档，全部结论已沉淀。',
            'thought': '',
            'tool_calls': [],
        }

    brain.llm.stream_with_tools = fake_stream_with_tools  # type: ignore[method-assign]
    brain.llm.complete_silent = fake_stream_with_tools  # type: ignore[method-assign]
    over_res = await brain._auto_compact_if_needed('companion', force=False, source='phone')
    assert over_res is not None
    assert over_res.get('compacted') is True
    assert over_res['before_tokens'] >= 100_000
    assert over_res['after_tokens'] < over_res['before_tokens']

    remaining = [m for m in store.state['messages'] if m.get('bot_id') == 'companion' and m.get('source') == 'phone']
    assert remaining[0]['kind'] == 'compact_summary'
    texts = [m.get('text', '') for m in remaining]
    assert any('【当前正在进行的活跃新话题】' in t for t in texts)
    assert any('【当前活跃回复】' in t for t in texts)


def test_per_end_bot_rosters_and_archive_export_import_api(tmp_path: Path, monkeypatch):
    app, _, store = _build_test_app(tmp_path, monkeypatch=monkeypatch)
    headers = {'Authorization': 'Bearer test'}
    with TestClient(app, headers=headers) as client:
        phone_bots = client.get('/api/bots?source=phone').json()['bots']
        pc_bots = client.get('/api/bots?source=pc').json()['bots']
        phone_ids = {b['id'] for b in phone_bots}
        pc_ids = {b['id'] for b in pc_bots}

        assert {'companion', 'research', 'contrarian', 'whalechan'}.issubset(phone_ids)
        assert {'pc_engineer', 'pc_research', 'pc_reviewer', 'pc_companion'}.issubset(pc_ids)
        assert 'pc_engineer' not in phone_ids
        assert 'companion' not in pc_ids

        store.message('user', '测试导出档案消息', 'chat', bot_id='pc_engineer', source='pc')
        store.message('assistant', '档案回复已记录', 'chat', bot_id='pc_engineer', source='pc')

        exp = client.post('/api/bots/export', json={'bot_id': 'pc_engineer', 'include_messages': True})
        assert exp.status_code == 200
        bundle = exp.json()['bundle']
        assert bundle['format'] == 'dsb-bot-archive'
        assert bundle['bot']['id'] == 'pc_engineer'
        assert len(bundle['messages']) == 2

        imp = client.post('/api/bots/import', json={
            'bundle': bundle,
            'mode': 'clone',
            'target_bot_id': 'pc_engineer_backup',
            'home_node': 'pc',
            'source': 'pc',
            'import_messages': True,
        })
        assert imp.status_code == 200
        imp_data = imp.json()
        assert imp_data['ok'] is True
        assert imp_data['bot_id'] == 'pc_engineer_backup'
        assert imp_data['imported_messages'] == 2
        assert any(b['id'] == 'pc_engineer_backup' for b in imp_data['bots'])
