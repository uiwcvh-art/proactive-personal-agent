from __future__ import annotations

import asyncio
from pathlib import Path
import pytest

from main import create_app
from core.vfs_router import apply_authoritative_tree


def _build_test_app(tmp_path: Path):
    app = create_app(tmp_path, scheduler=False)
    store = app.state.store
    store.update_config({
        'api_key': 'sk-test-key',
        'default_model': 'deepseek-reasoner',
        'enabled': True,
    })
    return app, app.state.brain, store


@pytest.mark.asyncio
async def test_blackboard_no_self_echo_and_reply_sender_attribution(tmp_path: Path):
    _, brain, store = _build_test_app(tmp_path)
    woken_calls: list[dict] = []

    async def fake_chat(text: str, *, bot_id: str | None = None, source: str = 'phone', **kw):
        pre_msg = kw.get('pre_recorded_user_msg') or {}
        woken_calls.append({
            'bot_id': str(bot_id),
            'source': str(source),
            'text': text,
            'from_bot_id': pre_msg.get('from_bot_id'),
            'from_bot_name': pre_msg.get('from_bot_name'),
            'kind': pre_msg.get('kind'),
        })
        return {'ok': True, 'text': 'ack'}

    brain.chat = fake_chat  # type: ignore[method-assign]

    # 1. Companion posts "@司工 ..." and also mentions its own name + ssh email + @你:
    #    - Must wake ONLY pc_engineer ("司工")
    #    - Must NEVER wake companion itself with "@司工 ..."
    #    - Must ignore root@216.23.82.196 and @你
    post_res = await brain.blackboard_engine.execute({
        'action': 'post',
        'title': '@司工 紧急排查服务链路',
        'content': '机器 root@216.23.82.196 端口检查一下，@你 结论写完 @companion 一句即可。',
        '_caller_bot_id': 'companion',
        '_caller_source': 'phone',
    })
    assert post_res['ok'] is True
    topic = post_res['topic']
    topic_id = topic['post_id']
    assert '216' not in ' '.join(topic.get('mentions') or [])
    assert '你' not in (topic.get('mentions') or [])
    assert post_res['woken_bots'] == ['pc_engineer']

    await asyncio.sleep(0.08)
    assert len(woken_calls) == 1
    assert woken_calls[0]['bot_id'] == 'pc_engineer'
    assert woken_calls[0]['from_bot_id'] == 'companion'
    companion_msgs = [
        m for m in store.state['messages']
        if m.get('bot_id') == 'companion' and m.get('kind') == 'bot_message'
    ]
    assert companion_msgs == []

    # 2. pc_engineer ("司工") claims, replies, and immediately completes within 1s:
    #    - Companion must receive wakeup attributed to pc_engineer ("司工"), NOT companion
    #    - Snippet must NOT start with "@司工" from original post title
    #    - Back-to-back reply + complete within 6s must deduplicate to 1 wakeup
    woken_calls.clear()
    await brain.blackboard_engine.execute({
        'action': 'claim',
        'topic_id': topic_id,
        '_caller_bot_id': 'pc_engineer',
        '_caller_source': 'pc',
    })
    reply_res = await brain.blackboard_engine.execute({
        'action': 'reply',
        'topic_id': topic_id,
        'content': '@companion 链路已经恢复正常，服务监听 8188 绿灯。',
        '_caller_bot_id': 'pc_engineer',
        '_caller_source': 'pc',
    })
    assert reply_res['ok'] is True
    done_res = await brain.blackboard_engine.execute({
        'action': 'complete',
        'topic_id': topic_id,
        'summary': '链路已恢复并自测通过',
        '_caller_bot_id': 'pc_engineer',
        '_caller_source': 'pc',
    })
    assert done_res['ok'] is True

    await asyncio.sleep(0.08)
    companion_wakes = [c for c in woken_calls if c['bot_id'] == 'companion']
    assert len(companion_wakes) == 1
    wake = companion_wakes[0]
    assert wake['from_bot_id'] == 'pc_engineer'
    assert wake['from_bot_name'] == '司工'
    assert '@司工' not in wake['text']
    assert '链路已经恢复正常' in wake['text']
    companion_msgs_after = [
        m for m in store.state['messages']
        if m.get('bot_id') == 'companion' and m.get('kind') == 'bot_message'
    ]
    assert len(companion_msgs_after) == 1
    assert companion_msgs_after[0]['from_bot_id'] == 'pc_engineer'
    assert '@司工' not in companion_msgs_after[0]['text']


def test_apply_authoritative_tree_syncs_image_and_dpi():
    res = {
        'ok': True,
        'ui_tree': 'old-tree',
        'image': 'stale-b64-frame',
        'frameHash': 'old-hash',
        'dpi': 560,
        'swDp': 361,
    }
    fresh_tree = {
        'ui_tree': 'new-post-tap-tree',
        'nodeCount': 42,
        'package': 'com.sankuai.meituan',
        'treeHash': 'tree-hash-2',
        'image': 'fresh-b64-frame',
        'frameHash': 'frame-hash-2',
        'dpi': 481,
        'swDp': 420,
    }
    apply_authoritative_tree(res, fresh_tree)
    assert res['ui_tree'] == 'new-post-tap-tree'
    assert res['image'] == 'fresh-b64-frame'
    assert res['frameHash'] == 'frame-hash-2'
    assert res['dpi'] == 481
    assert res['swDp'] == 420


def test_pc_uploads_routing_checks_cloud_existence(tmp_path: Path):
    _, brain, _ = _build_test_app(tmp_path)
    brain.is_dsh_online = lambda: True  # type: ignore[method-assign]
    # File not in cloud uploads -> when DSH is online, should route to PC fs
    assert brain._should_use_pc_fs('read_file', {'path': 'uploads/pc_generated.png'}) is True

    # Once staged into cloud uploads -> should read directly from cloud harness
    staged = brain.harness.root / 'uploads' / 'cloud_staged.png'
    staged.parent.mkdir(parents=True, exist_ok=True)
    staged.write_bytes(b'png-bytes')
    assert brain._should_use_pc_fs('read_file', {'path': 'uploads/cloud_staged.png'}) is False

    # When DSH is offline -> never route to PC fs
    brain.is_dsh_online = lambda: False  # type: ignore[method-assign]
    assert brain._should_use_pc_fs('read_file', {'path': 'uploads/pc_generated.png'}) is False
