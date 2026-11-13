"""Regression tests for bug-collection Sections 38-41 & production log fixes."""
from __future__ import annotations

import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

SERVER_DIR = Path(__file__).resolve().parents[1]
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

import main
from core.llm_client import LLMClient
from core.scheduler_engine import SchedulerEngine
from core.turn_ledger import TurnLedgerManager
from vault_manager import TZ


def _make_app(tmp_path: Path):
    (tmp_path / 'device-token').write_text('test-tok', encoding='utf-8')
    return main.create_app(data_dir=tmp_path, scheduler=False)


def test_scheduler_quiet_hours_and_interval_deferral(tmp_path: Path):
    app = _make_app(tmp_path)
    store = app.state.store
    brain = app.state.brain
    store.update_config({'quiet_start_hour': 0, 'quiet_end_hour': 8}, source_tag='test')
    eng = brain.scheduler_engine

    ts_0200 = datetime(2026, 11, 13, 2, 0, 0, tzinfo=TZ).timestamp()
    ts_0900 = datetime(2026, 11, 13, 9, 0, 0, tzinfo=TZ).timestamp()
    assert eng.is_quiet_hour(ts_0200) is True
    assert eng.is_quiet_hour(ts_0900) is False

    # Stale interval routine must advance strictly past now_ts AND defer out of quiet hours
    stale_r = {
        'id': 'rt-stale',
        'goal': 'interval check',
        'interval_hours': 2,
        'last_run': ts_0200 - 10 * 3600,
    }
    nxt = eng.compute_next_routine_run(stale_r, ts_0200)
    assert nxt > ts_0200
    assert not eng.is_quiet_hour(nxt)
    assert datetime.fromtimestamp(nxt, TZ).hour == 8

    # Explicit daily_time inside quiet hours stays at exact clock
    clock_r = {'id': 'rt-clk', 'goal': 'alarm', 'daily_time': '06:30'}
    nxt_clk = eng.compute_next_routine_run(clock_r, ts_0200)
    dt_clk = datetime.fromtimestamp(nxt_clk, TZ)
    assert (dt_clk.hour, dt_clk.minute) == (6, 30)

    # Custom wrap-around quiet hours (23..7)
    store.update_config({'quiet_start_hour': 23, 'quiet_end_hour': 7}, source_tag='test')
    ts_2330 = datetime(2026, 11, 13, 23, 30, 0, tzinfo=TZ).timestamp()
    assert eng.is_quiet_hour(ts_2330) is True
    nxt_wrap = eng.compute_next_routine_run({'id': 'w', 'goal': 'x', 'interval_hours': 1}, ts_2330)
    dt_wrap = datetime.fromtimestamp(nxt_wrap, TZ)
    assert (dt_wrap.day, dt_wrap.hour) == (14, 7)

    # Disable quiet hours with 0..0
    store.update_config({'quiet_start_hour': 0, 'quiet_end_hour': 0}, source_tag='test')
    assert eng.is_quiet_hour(ts_0200) is False


def test_run_log_folding_and_schedule_view_curiosity_poll(tmp_path: Path):
    app = _make_app(tmp_path)
    store = app.state.store
    brain = app.state.brain

    for i in range(5):
        TurnLedgerManager.append_run_log(
            store,
            {'kind': 'routine', 'task_id': 'rt-1', 'status': 'expired_skip', 'ts': f'2026-11-13T08:00:0{i}+08:00'},
        )
    logs = store.state['task_run_logs']
    assert len(logs) == 1
    assert logs[0]['repeat_count'] == 5

    folded = TurnLedgerManager.get_folded_recent_runs(store, limit=10)
    assert len(folded) == 1 and folded[0]['repeat_count'] == 5

    view = brain.scheduler_engine.decorate_schedule_view()
    assert 'builtin_polls' in view
    poll_ids = [p['id'] for p in view['builtin_polls']]
    assert 'curiosity_poll' in poll_ids
    assert 'quiet_window' in view

    health = brain.scheduler_engine.inspect_scheduler_health()
    assert 'curiosity_status' in health and 'warnings' in health


def test_routine_does_not_starve_curiosity_and_stay_silent_scopes(tmp_path: Path):
    app = _make_app(tmp_path)
    store = app.state.store
    brain = app.state.brain

    async def _run():
        t0 = time.time() - 5000
        store.state['last_curiosity'] = t0
        store.state['curiosity_next_run'] = t0 + 1800

        # Non-curiosity activity stay_silent must NOT reset last_curiosity
        brain.activity = 'running_routine'
        res_routine_silent = await brain._tool_stay_silent({'reason': 'routine ok', 'snooze_minutes': 30})
        assert res_routine_silent['_stay_silent'] is True
        assert abs(store.state['last_curiosity'] - t0) < 0.01

        # Curiosity activity stay_silent DOES update last_curiosity and curiosity_next_run
        brain.activity = 'curiosity'
        res_cur_silent = await brain._tool_stay_silent({'reason': 'quiet', 'snooze_minutes': 0})
        assert res_cur_silent['_stay_silent'] is True
        assert store.state['last_curiosity'] > t0
        assert store.state['curiosity_next_run'] > time.time() + 1700

        # record_curiosity_cycle logs curiosity_poll
        brain.scheduler_engine.record_curiosity_cycle('emitted', 'companion', 'hi')
        assert any(e.get('task_id') == 'curiosity_poll' for e in store.state.get('task_run_logs', []))

    asyncio.run(_run())


def test_compact_guard_workspace_sync_and_config_audit(tmp_path: Path):
    app = _make_app(tmp_path)
    store = app.state.store
    events: list[tuple[str, str, list[str]]] = []
    store.on_config_changed = lambda cfg, tag, keys: events.append((tag, str(cfg.get('auto_compact_tokens')), keys))

    store.update_config({'auto_compact_tokens': 200000, 'curiosity_interval_minutes': 30}, source_tag='test_init')
    client = TestClient(app)
    headers = {'Authorization': 'Bearer test-tok'}

    # Clicking compact without persist_threshold MUST NOT clobber 200000!
    r = client.post('/api/context/compact', headers=headers, json={'bot_id': 'companion', 'auto_compact_tokens': 120000})
    assert r.status_code == 200
    assert store.config.auto_compact_tokens == 200000

    # Explicit persist_threshold=True updates config & disk readback passes
    r2 = client.post('/api/context/compact', headers=headers, json={'bot_id': 'companion', 'auto_compact_tokens': 160000, 'persist_threshold': True})
    assert r2.status_code == 200
    assert store.config.auto_compact_tokens == 160000

    # Same-second raw text race check on workspace/settings.json
    settings_p = store.settings_ws_path
    payload = json.loads(settings_p.read_text('utf-8'))
    payload['auto_compact_tokens'] = 200000
    settings_p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), 'utf-8')
    assert store.sync_from_workspace() is True
    assert store.config.auto_compact_tokens == 200000

    # Audit log & callback recorded
    audit_p = settings_p.parent / 'logs' / 'config_audit.md'
    assert audit_p.exists()
    assert 'workspace_sync' in audit_p.read_text('utf-8')
    assert any(tag == 'workspace_sync' for tag, _, _ in events)

    # /api/status exposes curiosity_interval_minutes alongside auto_compact_tokens
    st = client.get('/api/status', headers=headers)
    assert st.status_code == 200
    assert st.json()['auto_compact_tokens'] == 200000
    assert st.json()['curiosity_interval_minutes'] == 30


def test_prompts_system_status_and_llm_cloudflare_retry(tmp_path: Path, monkeypatch):
    app = _make_app(tmp_path)
    brain = app.state.brain

    # Remove whalechan from roster to verify static_comm_guide itself has zero hardcoded whalechan/send_message
    app.state.store.state['assistants'] = [
        b for b in app.state.store.state['assistants'] if b.get('id') != 'whalechan'
    ]
    p_txt = brain.persona('companion')
    assert 'send_message' not in p_txt
    assert 'whalechan' not in p_txt
    assert 'blackboard' in p_txt and 'peek_bots' in p_txt

    async def _check_status():
        brain.last_prompt_tokens['companion'] = 512
        brain.last_cached_tokens['companion'] = 0
        st = await brain._tool_system_status({'op': 'all'})
        assert st['ok'] is True
        assert 'cache_diagnostic' in st
        assert st['cache_diagnostic']['kv_cache_min_tokens_hint'] == 1024
        assert 'scheduler_health' in st
        assert 'config_summary' in st

    asyncio.run(_check_status())

    # LLMClient HTML stripping + 520 retry + source-aware model resolution
    cleaned = LLMClient._clean_error_detail('<html><head><title>520 Web server is returning an unknown error</title></head><body>Cloudflare Ray ID</body></html>')
    assert '<html' not in cleaned and '520 Web server' in cleaned

    calls: list[str] = []
    seen_sources: list[str] = []

    def _get_bot(st, bid, source='phone'):
        seen_sources.append(source)
        return {'model': 'pc-custom-model' if source == 'pc' else 'phone-custom-model'}

    llm = LLMClient(app.state.store, get_assistant_fn=_get_bot)
    cands = llm._resolve_endpoint_candidates('companion', source='pc')
    assert seen_sources == ['pc']
    if cands:
        assert cands[0][2] == 'pc-custom-model'

    class _FakeResp:
        def __init__(self, code: int, text: str, data: dict | None = None):
            self.status_code = code
            self.text = text
            self._data = data or {}
            self.headers = {'content-type': 'application/json'}

        def json(self):
            return self._data

    class _FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            calls.append(url)
            if len(calls) == 1:
                return _FakeResp(520, '<html><title>520 Edge Error</title></html>')
            return _FakeResp(
                200,
                '',
                {
                    'choices': [{'message': {'content': 'recovered'}}],
                    'usage': {'prompt_tokens': 100, 'completion_tokens': 5},
                },
            )

    app.state.store.update_config({'api_base_url': 'https://example.invalid/v1', 'api_key': 'k-test', 'default_model': 'deepseek-v4.1-flash'})
    monkeypatch.setattr(httpx, 'AsyncClient', _FakeClient)

    async def _no_sleep(_sec):
        return None

    monkeypatch.setattr(asyncio, 'sleep', _no_sleep)
    out = asyncio.run(llm.complete_silent([{'role': 'user', 'content': 'hi'}]))
    assert out['content'] == 'recovered'
    assert len(calls) == 2
