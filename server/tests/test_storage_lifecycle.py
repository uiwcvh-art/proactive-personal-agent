from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
from core.context_search import ContextSearchEngine
from main import create_app
from storage import Store, run_storage_maintenance
from vault_manager import Vault


def test_vault_snapshot_dedup_and_history_pruning(tmp_path):
    vault_dir = tmp_path / 'memory_vault'
    v = Vault(vault_dir)
    hist_dir = vault_dir / '.history'
    hist_dir.mkdir(parents=True, exist_ok=True)

    # 1. Rapidly calling vault.log() 10 times on today's timeline should NOT flood .history with 10 snapshots
    for i in range(10):
        v.log(f'日常流水记录第 {i} 条')
    today_snaps = list(hist_dir.glob('timeline__*.md'))
    assert len(today_snaps) <= 2

    # 2. Appending to work_log or logs/notifications_inbox.md should NEVER create .history snapshots
    v.append_work_log('测试跨端日志 1')
    v.append_work_log('测试跨端日志 2')
    v.append('logs/notifications_inbox.md', '- 测试通知 1\n- 测试通知 2')
    assert list(hist_dir.glob('timeline__work_log*.md')) == []
    assert list(hist_dir.glob('logs__*.md')) == []

    # 3. Core memory edits DO create snapshots (capped at 8) and preserve content
    v.write('profile/personal_impression.md', '# 主人立体个人印象档案\n- 版本 1\n')
    for idx in range(2, 14):
        v.write('profile/personal_impression.md', f'# 主人立体个人印象档案\n- 版本 {idx}\n')
    imp_snaps = list(hist_dir.glob('profile__personal_impression.md.*.md'))
    assert 1 <= len(imp_snaps) <= 8
    assert '版本 13' in v.read('profile/personal_impression.md')

    # 4. Seed legacy bloated snapshots (past day timelines, logs, URL-encoded names) and verify prune_storage cleans them
    (hist_dir / 'timeline__2026-11-01.md.20261101-120000.md').write_text('old timeline snap ' * 500, encoding='utf-8')
    (hist_dir / 'logs__notifications_inbox.md.20261105-120000.md').write_text('old log snap ' * 500, encoding='utf-8')
    (hist_dir / 'profile__user%5Ftraits.md.20261106-173144.md').write_text('escaped snap', encoding='utf-8')

    res = v.prune_storage()
    assert res['removed_snapshots'] >= 3
    assert not (hist_dir / 'timeline__2026-11-01.md.20261101-120000.md').exists()
    assert not (hist_dir / 'logs__notifications_inbox.md.20261105-120000.md').exists()
    assert '版本 13' in v.read('profile/personal_impression.md')


def test_context_search_dedup_ttl_and_compaction_preservation(tmp_path):
    db_path = tmp_path / 'context_index.db'
    engine = ContextSearchEngine(db_path)

    # 1. Repeated identical transient tool calls deduplicate in-place
    big_output = '\n'.join(f'Line {i}: some tool output payload data {i}' for i in range(300))
    for _ in range(5):
        cid = engine.store_tool_context('read_file', {'path': 'memory_vault/timeline/2026-11-06.md'}, big_output)

    conn = engine._connect()
    try:
        rows = conn.execute("SELECT ctx_id, length(full_text) AS flen, total_chars FROM tool_contexts WHERE tool_name = 'read_file'").fetchall()
        assert len(rows) == 1
        assert rows[0]['flen'] <= 1200
        assert rows[0]['total_chars'] == len(big_output)
    finally:
        conn.close()

    # Verify ctx_search still finds content from the tail chunk!
    hit = engine.ctx_search('payload data 295', ctx_id=cid)
    assert hit['count'] >= 1
    assert 'Line 295' in hit['chunks'][0]['content']

    # 2. Expired transient tool context (>48h) is pruned, while compaction_archive is preserved!
    old_transient_id = engine.store_tool_context('phone_workspace', {'op': 'ui_tree'}, 'old ui tree ' * 600)
    archive_id = engine.store_tool_context('compaction_archive', {'bot_id': 'companion'}, 'precious archived history ' * 600)

    conn = engine._connect()
    try:
        conn.execute('UPDATE tool_contexts SET created_at = ? WHERE ctx_id = ?', (time.time() - 72 * 3600, old_transient_id))
        conn.execute('UPDATE tool_contexts SET created_at = ? WHERE ctx_id = ?', (time.time() - 72 * 3600, archive_id))
        conn.commit()
    finally:
        conn.close()

    prune_res = engine.prune_and_optimize(force_vacuum=True)
    assert prune_res['deleted_contexts'] >= 1
    assert engine.ctx_search('old ui tree', ctx_id=old_transient_id)['count'] == 0
    assert engine.ctx_search('precious archived history', ctx_id=archive_id)['count'] >= 1


def test_unified_storage_maintenance_and_gc_endpoint(tmp_path, monkeypatch):
    monkeypatch.setenv('AGENT_TOKEN', 'test-gc-token')
    app = create_app(tmp_path, scheduler=False)
    store: Store = app.state.store

    # Seed expired done timers, old notified deadlines, old notifications, and orphan 0-byte DB files
    now = time.time()
    store.state['timers'] = [
        {'id': 't-old-done', 'goal': 'old timer', 'due_at': now - 7200, 'done': True},
        {'id': 't-active', 'goal': 'active timer', 'due_at': now + 3600, 'done': False},
    ]
    store.state['deadlines'] = [
        {'id': 'd-old', 'title': 'old deadline', 'due_at': now - 10 * 86400, 'notified': True},
        {'id': 'd-keep', 'title': 'upcoming deadline', 'due_at': now + 86400, 'notified': False},
    ]
    store.state['cached_vault_context'] = 'legacy bloat ' * 200
    (tmp_path / 'agent.db').write_bytes(b'')
    (tmp_path / 'state.sqlite3').write_bytes(b'')

    # Seed duplicate uploads in workspace/uploads
    up_dir = app.state.brain.harness.root / 'uploads'
    up_dir.mkdir(parents=True, exist_ok=True)
    dup_bytes = b'duplicate screenshot payload ' * 1000
    (up_dir / '111-screen.png').write_bytes(dup_bytes)
    (up_dir / '222-screen.png').write_bytes(dup_bytes)

    with TestClient(app, headers={'Authorization': 'Bearer test-gc-token'}) as client:
        resp = client.post('/api/storage/gc', json={'force_vacuum': True})
        assert resp.status_code == 200
        data = resp.json()
        assert data['ok'] is True
        rep = data['report']
        assert rep['files_removed'] >= 3

    # Verify core state & active items are intact while bloat is gone
    assert [t['id'] for t in store.state['timers']] == ['t-active']
    assert [d['id'] for d in store.state['deadlines']] == ['d-keep']
    assert 'cached_vault_context' not in store.state
    assert not (tmp_path / 'agent.db').exists()
    assert len(list(up_dir.glob('*-screen.png'))) == 1
    assert len(store.state['assistants']) >= 1
