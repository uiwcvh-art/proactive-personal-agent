import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest
from fastapi.testclient import TestClient
from main import create_app
from assistants import DEFAULT_ASSISTANTS, ensure_assistants


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_TOKEN", "test-private-token")
    app = create_app(tmp_path, scheduler=False)
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer test-private-token"
        yield app, client


def test_whalechan_default_persona_and_baike_preservation(env):
    app, c = env
    # 1. Default install includes 白珂 (companion) and authentic community Whale-chan (鲸鱼娘)
    assert DEFAULT_ASSISTANTS[0]["name"] == "白珂"
    whale_bot = next((b for b in DEFAULT_ASSISTANTS if b["name"] == "鲸鱼娘"), None)
    assert whale_bot is not None
    assert "白米饭" in whale_bot["instructions"]
    assert "大肥鱼" in whale_bot["instructions"]

    bots = app.state.store.state["assistants"]
    assert any(b["name"] == "鲸鱼娘" for b in bots)

    # 2. If user customized companion to 白珂, ensure_assistants NEVER overwrites it
    app.state.store.state["assistants"][0]["name"] = "白珂"
    app.state.store.state["assistants"][0]["instructions"] = "你是白珂（Baike），白发红瞳狐狸娘喵～"
    ensure_assistants(app.state.store)
    assert app.state.store.state["assistants"][0]["name"] == "白珂"
    assert "白发红瞳" in app.state.store.state["assistants"][0]["instructions"]


def test_work_log_and_pairing_info(env):
    app, c = env
    line = app.state.vault.append_work_log("[PC -> Phone] 鲸鱼娘请求查询手机剩余电量")
    assert "鲸鱼娘请求查询手机剩余电量" in line

    res = c.get("/api/work-log")
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert any("鲸鱼娘请求查询手机剩余电量" in x for x in data["entries"])

    pair = c.get("/api/pairing-info")
    assert pair.status_code == 200
    pdata = pair.json()
    assert pdata["ok"] is True
    assert pdata["pair_code"].startswith("DSCLAW1:")


def test_dsh_bridge_ws_and_bidirectional_colleague_messaging(env):
    app, c = env
    with c.websocket_connect("/ws/dsh-bridge") as ws:
        ws.send_json(
            {
                "type": "hello",
                "token": "test-private-token",
                "status": {
                    "dsh_running": True,
                    "sessions": [{"id": "sess-1", "title": "DeepSeek Claw Test"}],
                    "workspaces": ["D:/bubuki/proactive-personal-agent"],
                },
            }
        )
        ack = ws.receive_json()
        assert ack["type"] == "connected"

        # Verify /api/dsh/status reports PC online
        st = c.get("/api/dsh/status").json()
        assert st["pc_online"] is True
        assert st["pc_sentinel_connected"] is True

        # Verify PC Bot -> Phone Bot colleague messaging (/api/dsh/message-phone-bot)
        msg_res = c.post(
            "/api/dsh/message-phone-bot",
            json={
                "bot_id": "companion",
                "message": "请汇报手机当前状态并记录到跨端日志",
                "mode": "chat",
                "from_dsh_session": "sess-1",
            },
        )
        assert msg_res.status_code == 200
        mjson = msg_res.json()
        assert mjson["ok"] is True
        assert mjson["bot_id"] == "companion"
        assert "DSH" in mjson["work_log_line"] and "sess-1" in mjson["work_log_line"]


def test_zero_config_dsh_heartbeat_inbox_and_grok_whale_avatars(env):
    app, c = env
    # 1. Verify initial state has dsh_online=False and dsh_peers included in /api/state
    st0 = c.get("/api/state").json()
    assert st0["product_abbr"] == "DSB"
    assert st0["dsh_online"] is False
    assert isinstance(st0["dsh_peers"], list) and any(p["id"] == "pc_dsh" for p in st0["dsh_peers"])

    # 2. PC dsh sends heartbeat to /api/dsh/heartbeat -> flips dsh_online to True
    hb = c.post(
        "/api/dsh/heartbeat",
        json={"peer_id": "pc_dsh", "status": {"dsh_running": True, "sessions": []}},
    ).json()
    assert hb["ok"] is True and hb["dsh_online"] is True and hb["flipped"] is True

    # 3. /api/state, /api/status, /api/sync, /api/dsh/inbox now all report dsh_online=True
    st1 = c.get("/api/state").json()
    assert st1["dsh_online"] is True
    assert any(p["id"] == "pc_dsh" and p["online"] is True for p in st1["dsh_peers"])

    inbox = c.get("/api/dsh/inbox").json()
    assert inbox["ok"] is True and inbox["dsh_online"] is True

    # 4. Verify whale and whalechan shapes + Grok Bot static avatars work
    created = c.post(
        "/api/bots",
        json={"name": "深海大鲸鱼", "shape": "whale", "color": "#38BDF8"},
    )
    assert created.status_code == 200 and created.json()["shape"] == "whale"
    presets = c.get("/api/avatars/presets").json()
    assert presets["ok"] is True and len(presets["presets"]) == 2
    assert c.get("/static/avatars/whalechan_grok.png").status_code == 200
    assert c.get("/static/avatars/whale_grok.png").status_code == 200


def test_multi_pc_registration_and_device_id_routing(env):
    app, c = env
    # Register PC 1: pc-workstation
    hb1 = c.post(
        "/api/dsh/inbox?drain=0",
        json={
            "peer_id": "pc-workstation",
            "device_id": "pc-workstation",
            "device_name": "主力台式机 (DSH)",
            "device_type": "pc",
            "status": {
                "dsh_running": True,
                "sessions_count": 2,
                "running_count": 1,
                "sessions": [
                    {"id": "sess-ws-1", "title": "台式机重构任务"},
                    {"id": "sess-ws-2", "title": "台式机测试任务"},
                ],
            },
        },
    ).json()
    assert hb1["ok"] is True

    # Register PC 2: pc-macbook
    hb2 = c.post(
        "/api/dsh/inbox?drain=0",
        json={
            "peer_id": "pc-macbook",
            "device_id": "pc-macbook",
            "device_name": "随身笔记本 (DSH)",
            "device_type": "pc",
            "status": {
                "dsh_running": True,
                "sessions_count": 1,
                "running_count": 0,
                "sessions": [
                    {"id": "sess-mb-1", "title": "笔记本写文档"},
                ],
            },
        },
    ).json()
    assert hb2["ok"] is True

    # Both PCs must coexist in dsh_peers with independent sessions
    peers_by_id = {p["id"]: p for p in hb2["dsh_peers"]}
    assert "pc-workstation" in peers_by_id
    assert "pc-macbook" in peers_by_id
    assert len(peers_by_id["pc-workstation"]["status"]["sessions"]) == 2
    assert len(peers_by_id["pc-macbook"]["status"]["sessions"]) == 1

    # dsh_workspace tool routing with explicit device_id vs default active PC
    brain = app.state.brain
    res_ws = asyncio.run(brain._tool_dsh_workspace({"op": "status", "device_id": "pc-workstation"}))
    assert res_ws["ok"] is True
    assert res_ws["device_id"] == "pc-workstation"
    assert len(res_ws["sessions"]) == 2
    assert res_ws["sessions"][0]["id"] == "sess-ws-1"

    res_mb = asyncio.run(brain._tool_dsh_workspace({"op": "status", "device_id": "pc-macbook"}))
    assert res_mb["ok"] is True
    assert res_mb["device_id"] == "pc-macbook"
    assert len(res_mb["sessions"]) == 1
    assert res_mb["sessions"][0]["id"] == "sess-mb-1"

    # Default routing goes to most recently active PC (pc-macbook)
    res_default = asyncio.run(brain._tool_dsh_workspace({"op": "status"}))
    assert res_default["device_id"] == "pc-macbook"


def test_source_isolation_zero_prompt_pollution_and_on_demand_limit(env):
    app, c = env
    brain = app.state.brain

    # 1. Verify _build_ephemeral_pc_peer_brief is strictly empty (0 prompt suffix pollution)
    c.post(
        "/api/dsh/heartbeat",
        json={
            "peer_id": "pc-alpha",
            "device_id": "pc-alpha",
            "status": {"dsh_running": True, "sessions": [{"id": "s1", "title": "不应被自动塞进Prompt"}]},
        },
    )
    assert brain._build_ephemeral_pc_peer_brief() == ""

    # 2. Send 6 phone dialogs (source="phone") and 5 PC dialogs (source="pc")
    for i in range(1, 7):
        r_phone = c.post(
            "/api/chat",
            json={"text": f"手机端专属消息-{i}", "bot_id": "companion", "mode": "chat", "source": "phone"},
        )
        assert r_phone.status_code == 200
        assert r_phone.json()["source"] == "phone"

    for i in range(1, 6):
        r_pc = c.post(
            "/api/chat",
            json={"text": f"电脑端专属消息-{i}", "bot_id": "companion", "mode": "chat", "source": "pc"},
        )
        assert r_pc.status_code == 200
        assert r_pc.json()["source"] == "pc"

    # 3. Verify strict isolation in /api/sync and /api/messages
    phone_sync = c.get("/api/sync?since_seq=0&source=phone").json()
    assert all(m.get("source", "phone") == "phone" for m in phone_sync["messages"])
    assert any("手机端专属消息-6" in m.get("text", "") for m in phone_sync["messages"])
    assert not any("电脑端专属消息" in m.get("text", "") for m in phone_sync["messages"])

    pc_sync = c.get("/api/sync?since_seq=0&source=pc").json()
    assert all(m.get("source") == "pc" for m in pc_sync["messages"])
    assert any("电脑端专属消息-5" in m.get("text", "") for m in pc_sync["messages"])
    assert not any("手机端专属消息" in m.get("text", "") for m in pc_sync["messages"])

    # 4. Verify on-demand cross-device peek_dialogs with custom limit (e.g. limit=5, limit=4, limit=2)
    peek_phone_5 = asyncio.run(
        brain._tool_dsh_workspace({"op": "peek_dialogs", "source": "phone", "limit": 5})
    )
    assert peek_phone_5["ok"] is True
    assert peek_phone_5["source"] == "phone"
    assert peek_phone_5["limit"] == 5
    assert len(peek_phone_5["dialogs"]) == 5
    assert peek_phone_5["dialogs"][-1]["user"] == "手机端专属消息-6"
    assert all(d["source"] == "phone" for d in peek_phone_5["dialogs"])

    peek_pc_4 = asyncio.run(
        brain._tool_dsh_workspace({"op": "peek_dialogs", "source": "pc", "limit": 4})
    )
    assert peek_pc_4["ok"] is True
    assert peek_pc_4["source"] == "pc"
    assert peek_pc_4["limit"] == 4
    assert len(peek_pc_4["dialogs"]) == 4
    assert peek_pc_4["dialogs"][-1]["user"] == "电脑端专属消息-5"
    assert all(d["source"] == "pc" for d in peek_pc_4["dialogs"])

    # Also verify /api/dsh/status?limit=5&source=phone and /api/dsh/message-phone-bot (op="peek_dialogs", limit=5)
    st_5 = c.get("/api/dsh/status?limit=5&source=phone").json()
    assert len(st_5["dialogs"]) == 5
    assert st_5["limit"] == 5

    mpb_peek = c.post(
        "/api/dsh/message-phone-bot",
        json={"op": "peek_dialogs", "source": "phone", "limit": 4},
    ).json()
    assert mpb_peek["ok"] is True
    assert len(mpb_peek["dialogs"]) == 4


def test_shared_memory_source_tagging_pc_vs_phone(env):
    app, c = env

    # 1. Phone adds a pinned memory (default or explicit source="phone")
    pin_phone = c.post(
        "/api/memories/pins",
        json={"action": "remember", "text": "主人出门喜欢带降噪耳机", "source": "phone"},
    ).json()
    assert pin_phone["ok"] is True
    assert any(
        p["source"] == "phone" and "[来源: 手机端]" in p["text"] and "降噪耳机" in p["text"]
        for p in pin_phone["pins"]
    )

    # 2. PC adds a pinned memory via /api/dsh/shared-memory
    pin_pc = c.post(
        "/api/dsh/shared-memory",
        json={"op": "remember", "text": "主人的 DSH 插件目录在 D:\\dsh-baike-plugin", "source": "pc"},
    ).json()
    assert pin_pc["ok"] is True
    pins = pin_pc["shared_memories"]["pins"]
    assert any(
        p["source"] == "pc" and "[来源: PC端]" in p["text"] and "dsh-baike-plugin" in p["text"]
        for p in pins
    )
    assert any(
        p["source"] == "phone" and "[来源: 手机端]" in p["text"] and "降噪耳机" in p["text"]
        for p in pins
    )


def test_bot_deletion_persists_across_sync_and_ensure_assistants(env):
    from assistants import ensure_assistants, get_assistant
    import pytest

    app, c = env
    store = app.state.store

    # 1. Delete built-in non-primary bot 'whalechan'
    del_res = c.delete("/api/bots/whalechan")
    assert del_res.status_code == 200
    assert del_res.json()["deleted"] is True
    assert "whalechan" in store.state.get("deleted_bot_ids", [])

    # 2. Verify ensure_assistants() and get_assistant() do NOT resurrect 'whalechan'
    ensure_assistants(store)
    with pytest.raises(ValueError):
        get_assistant(store, "whalechan")

    bots_api = c.get("/api/bots").json()["bots"]
    assert all(b["id"] != "whalechan" for b in bots_api)

    sync_pc = c.get("/api/sync?since_seq=0&source=pc").json()["bots"]
    assert all(b["id"] != "whalechan" for b in sync_pc)

    sync_phone = c.get("/api/sync?since_seq=0&source=phone").json()["bots"]
    assert all(b["id"] != "whalechan" for b in sync_phone)

    # 3. Re-creating '鲸鱼娘' explicitly removes it from deleted_bot_ids and restores it
    recreate = c.post(
        "/api/bots",
        json={"name": "鲸鱼娘", "description": "重新添加的鲸鱼娘", "instructions": "你是鲸鱼娘"},
    )
    assert recreate.status_code == 200
    assert recreate.json()["id"] == "whalechan"
    assert "whalechan" not in store.state.get("deleted_bot_ids", [])


def test_cross_node_same_bot_send_message_and_clean_thought(env):
    import json

    app, c = env
    brain = app.state.brain
    c.post("/api/config", json={"api_key": "sk-test-key", "default_model": "gpt-4.1"})

    captured_target_msgs = []
    call_step = {"n": 0}

    async def fake_complete(messages, tools=None, big=False, **kw):
        call_step["n"] += 1
        if call_step["n"] == 1:
            await brain._set_stream(
                text="",
                thought="正在生成 send_message · 安卓一:白珂 (42 字符)\n下午好喵，单纯打个招呼",
                tool="send_message",
            )
            return {
                "role": "assistant",
                "content": "",
                "reasoning_content": "正在生成 send_message · 安卓一:白珂 (42 字符)\n下午好喵，单纯打个招呼",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "send_message",
                            "arguments": json.dumps(
                                {"to_bot_id": "安卓一:白珂", "text": "下午好喵，单纯打个招呼，没事随便聊一句也行喵。"},
                                ensure_ascii=False,
                            ),
                        },
                    }
                ],
            }
        if call_step["n"] == 2:
            captured_target_msgs.extend(messages)
            return {
                "role": "assistant",
                "content": "下午好喵！手机端这边一切正常，余在这边守着通知栏喵～",
                "reasoning_content": "收到电脑端白珂的打招呼，简短回复她喵。",
            }
        return {
            "role": "assistant",
            "content": "已经跟手机端的白珂打过招呼啦喵！",
            "reasoning_content": "",
        }

    brain.complete = fake_complete
    r = c.post(
        "/api/chat",
        json={"text": "给安卓一的白珂发条新消息打个招呼", "bot_id": "companion", "mode": "chat", "source": "pc"},
    )
    assert r.status_code == 200
    assert captured_target_msgs, "Target bot on phone was not invoked"
    # The incoming cross-node message from pc:companion to phone:companion MUST be role=='user', NOT 'assistant'!
    user_msgs_in_target = [m for m in captured_target_msgs if m.get("role") == "user"]
    assert any("跨端/同事 Bot 协作来信" in str(m.get("content", "")) and "下午好喵，单纯打个招呼" in str(m.get("content", "")) for m in user_msgs_in_target)

    # Verify the outbound bot_message in PC chat has NO '正在生成 send_message' leaked in `thought`
    pc_msgs = [m for m in app.state.store.state["messages"] if m.get("source") == "pc"]
    outbound_bm = [m for m in pc_msgs if m.get("kind") == "bot_message"]
    assert outbound_bm
    assert not str(outbound_bm[-1].get("thought") or "").startswith("正在生成 ")


