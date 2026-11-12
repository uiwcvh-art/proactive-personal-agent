import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest
from fastapi.testclient import TestClient
from main import create_app


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_TOKEN", "test-private-token")
    app = create_app(tmp_path, scheduler=False)
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer test-private-token"
        yield app, client


def _wait_bot_idle(brain, bot_id="companion", timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        t_set = brain.queued_tasks_by_bot.get(bot_id) or set()
        act = brain.active_tasks_by_bot.get(bot_id)
        if not any(not t.done() for t in t_set) and (act is None or act.done()):
            break
        time.sleep(0.05)


def test_task1_in_place_edit_reroll_dedup_and_messages_epoch(env):
    app, c = env
    brain = app.state.brain

    # Initial epoch is 1
    st0 = c.get("/api/status").json()
    assert st0["messages_epoch"] >= 1
    epoch0 = st0["messages_epoch"]

    # Send 2 phone turns and 1 PC turn
    c.post(
        "/api/chat",
        json={"text": "第一句原话", "bot_id": "companion", "mode": "chat", "source": "phone", "client_msg_id": "c-1"},
    )
    c.post(
        "/api/chat",
        json={"text": "第二句原话", "bot_id": "companion", "mode": "chat", "source": "phone", "client_msg_id": "c-2"},
    )
    c.post(
        "/api/chat",
        json={"text": "PC端独立对话", "bot_id": "companion", "mode": "chat", "source": "pc", "client_msg_id": "c-pc-1"},
    )

    phone_msgs_before = [
        m for m in app.state.store.state["messages"]
        if m.get("bot_id") == "companion" and m.get("source", "phone") == "phone"
    ]
    user1 = next(m for m in phone_msgs_before if m["role"] == "user" and m["text"] == "第一句原话")
    user1_id = user1["id"]

    # 1. Edit user1 in-place via /api/messages/edit
    edit_res = c.post(
        "/api/messages/edit",
        json={
            "message_id": user1_id,
            "text": "第一句原位修改后的话",
            "bot_id": "companion",
            "source": "phone",
        },
    ).json()
    assert edit_res["ok"] is True
    assert edit_res["edited_message"]["id"] == user1_id
    assert edit_res["edited_message"]["text"] == "第一句原位修改后的话"
    assert edit_res["messages_epoch"] > epoch0
    epoch1 = edit_res["messages_epoch"]

    _wait_bot_idle(brain, "companion")

    phone_msgs_after_edit = [
        m for m in app.state.store.state["messages"]
        if m.get("bot_id") == "companion" and m.get("source", "phone") == "phone"
    ]
    # Must NOT have "第二句原话" anymore, and must NOT have two user messages!
    user_msgs_after_edit = [m for m in phone_msgs_after_edit if m["role"] == "user"]
    assert len(user_msgs_after_edit) == 1
    assert user_msgs_after_edit[0]["id"] == user1_id
    assert user_msgs_after_edit[0]["text"] == "第一句原位修改后的话"
    assert user_msgs_after_edit[0].get("edited_at")
    # Assistant reply must follow user1 in-place
    assert len(phone_msgs_after_edit) == 2
    assert phone_msgs_after_edit[1]["role"] == "assistant"

    # PC messages must remain completely untouched
    pc_msgs = [
        m for m in app.state.store.state["messages"]
        if m.get("bot_id") == "companion" and m.get("source") == "pc"
    ]
    assert any(m["text"] == "PC端独立对话" for m in pc_msgs)

    # 2. Verify /api/messages and /api/status with stale messages_epoch force fullReplace (delta=False, epoch_reset=True)
    stale_msgs = c.get(f"/api/messages?after_id={user1_id}&messages_epoch={epoch0}&source=phone").json()
    assert stale_msgs["delta"] is False
    assert stale_msgs["epoch_reset"] is True
    assert stale_msgs["messages_epoch"] == epoch1

    stale_status = c.get(f"/api/status?after_id={user1_id}&messages_epoch={epoch0}").json()
    assert stale_status["epoch_reset"] is True
    assert stale_status["messages_epoch"] == epoch1
    assert "messages" in stale_status

    # 3. Reroll in-place via /api/messages/reroll
    old_assistant_id = phone_msgs_after_edit[1]["id"]
    reroll_res = c.post(
        "/api/messages/reroll",
        json={"bot_id": "companion", "message_id": old_assistant_id, "source": "phone"},
    ).json()
    assert reroll_res["ok"] is True
    assert reroll_res["rerolled_user_message_id"] == user1_id
    assert old_assistant_id in reroll_res["removed_message_ids"]
    assert reroll_res["messages_epoch"] > epoch1

    _wait_bot_idle(brain, "companion")

    phone_msgs_after_reroll = [
        m for m in app.state.store.state["messages"]
        if m.get("bot_id") == "companion" and m.get("source", "phone") == "phone"
    ]
    user_msgs_after_reroll = [m for m in phone_msgs_after_reroll if m["role"] == "user"]
    assert len(user_msgs_after_reroll) == 1
    assert user_msgs_after_reroll[0]["id"] == user1_id
    assert len(phone_msgs_after_reroll) == 2
    assert phone_msgs_after_reroll[1]["role"] == "assistant"
    assert phone_msgs_after_reroll[1]["id"] != old_assistant_id

    # 4. Resend deduplication when last user message was stopped_early or unanswered
    phone_msgs_after_reroll[1]["stopped_early"] = True
    app.state.store.save()
    dup_res = c.post(
        "/api/chat",
        json={
            "text": "第一句原位修改后的话",
            "bot_id": "companion",
            "mode": "chat",
            "source": "phone",
            "async_ack": True,
        },
    ).json()
    assert dup_res["reused_existing_user_message"] is True
    assert dup_res["user_message"]["id"] == user1_id

    _wait_bot_idle(brain, "companion")

    phone_msgs_after_dedup = [
        m for m in app.state.store.state["messages"]
        if m.get("bot_id") == "companion" and m.get("source", "phone") == "phone"
    ]
    assert len([m for m in phone_msgs_after_dedup if m["role"] == "user"]) == 1


def test_task2_stop_pure_text_chat_never_sends_phone_release(env):
    app, c = env
    brain = app.state.brain

    sent_events = []
    orig_publish = brain.publish

    async def spy_publish(event):
        sent_events.append(event)
        return await orig_publish(event)

    brain.publish = spy_publish

    # Even if _used_phone_in_turn was left True from an old turn, as long as _vdisplay_currently_open is False:
    brain._used_phone_in_turn = True
    brain._vdisplay_currently_open = False
    brain.phone_pending.clear()

    res = asyncio.run(brain.stop_immediately(bot_id="companion", reason="user_stop"))
    assert res["released_phone"] is False
    assert not any(e.get("type") == "phone_workspace_command" for e in sent_events)

    # Now if virtual display IS currently open (_vdisplay_currently_open = True), stop_immediately MUST release it once
    brain._vdisplay_currently_open = True
    res_open = asyncio.run(brain.stop_immediately(bot_id="companion", reason="user_stop"))
    assert res_open["released_phone"] is True
    assert brain._vdisplay_currently_open is False
    assert any(
        e.get("type") == "phone_workspace_command" and e.get("op") == "release"
        for e in sent_events
    )


def test_task3_sleep_consolidate_180s_and_tail_appended_memory_deltas(env):
    app, c = env
    brain = app.state.brain
    mem = brain.memory_engine

    # 1. Idle threshold must be 180 seconds
    assert mem.SLEEP_CONSOLIDATE_IDLE_SECONDS == 180

    # Add an outdated pin about Tencent Video auto-renewal and 2 new messages
    mem.add_pin("提醒主人解除腾讯视频自动续订", source="phone")
    now_ts = time.time()
    app.state.store.state["messages"].extend(
        [
            {
                "id": "m-u1",
                "bot_id": "companion",
                "source": "phone",
                "role": "user",
                "text": "我已经把腾讯视频自动续订解除掉啦！",
                "created_at": now_ts - 200,
            },
            {
                "id": "m-a1",
                "bot_id": "companion",
                "source": "phone",
                "role": "assistant",
                "text": "太好啦主人，腾讯视频自动续订已解除喵！",
                "created_at": now_ts - 195,
            },
        ]
    )
    app.state.store.state["last_user"] = now_ts - 190
    app.state.store.state["last_sleep_consolidate"] = 0.0

    # 2. If LLM fails, last_sleep_consolidate MUST NOT be updated
    async def failing_llm(messages):
        raise RuntimeError("transient network error")

    asyncio.run(mem.sleep_time_consolidate(failing_llm))
    assert app.state.store.state["last_sleep_consolidate"] == 0.0

    # 3. When LLM succeeds, last_sleep_consolidate updates AFTER vault writes and outdated pin is updated
    async def mock_llm(messages):
        return {
            "content": json.dumps(
                {
                    "new_traits": ["已解除腾讯视频自动续订，不再扣费"],
                    "completed_or_updated_pins": [
                        {
                            "match": "腾讯视频自动续订",
                            "replacement": "腾讯视频自动续订已解除（已完成，无需再提醒退订）",
                        }
                    ],
                    "updated_impression_markdown": "",
                    "note_slug": "tencent-video-renewal-cancelled",
                    "note_title": "腾讯视频自动续订已解除",
                    "note_markdown": "# 腾讯视频自动续订已解除\n- 主人已解除腾讯视频自动续订 [[profile/pinned_memories]]",
                },
                ensure_ascii=False,
            )
        }

    asyncio.run(mem.sleep_time_consolidate(mock_llm))
    assert app.state.store.state["last_sleep_consolidate"] > 0.0
    pins_now = mem.get_pins()
    assert any("腾讯视频自动续订已解除" in p["text"] for p in pins_now)

    # 4. Verify Tail-Appended Memory Delta ("放最底下") preserves historical positions and appends new delta to tail user message
    recent1 = [
        {"id": "m-u1", "role": "user", "content": "第一轮问题"},
        {"id": "m-a1", "role": "assistant", "content": "第一轮回答"},
    ]
    injected1 = mem.inject_memory_deltas(recent1, bot_id="companion", source="phone")
    assert "最新记忆同步" in injected1[0]["content"]

    # Add a second turn and a new memory delta: first delta stays locked in m-u1, new delta goes to the tail user message (m-u2)!
    recent2 = [
        {"id": "m-u1", "role": "user", "content": "第一轮问题"},
        {"id": "m-a1", "role": "assistant", "content": "第一轮回答"},
        {"id": "m-u2", "role": "user", "content": "第二轮问题"},
    ]
    mem.record_memory_delta("新增第二条尾部记忆增量", source="phone")
    injected2 = mem.inject_memory_deltas(recent2, bot_id="companion", source="phone")

    assert "腾讯视频自动续订已解除" in injected2[0]["content"]
    assert "新增第二条尾部记忆增量" not in injected2[0]["content"]
    assert "新增第二条尾部记忆增量" in injected2[-1]["content"]


def test_multimodal_llm_text_list_regression_and_public_msg_filtering(env):
    app, c = env
    brain = app.state.brain
    mem = brain.memory_engine

    # 1. estimate_tokens handles list[dict] multimodal content without TypeError
    mm_content = [
        {"type": "text", "text": "主人发来一张截图喵"},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}},
    ]
    tokens = mem.estimate_tokens(mm_content)
    assert tokens > 1100

    # 2. Store a message with multimodal llm_text (list[dict]) and verify /api/sync, /api/messages, /api/status strip llm_text and return 200 OK
    app.state.store.state["messages"].append(
        {
            "id": "mm-user-1",
            "bot_id": "companion",
            "source": "phone",
            "role": "user",
            "text": "请看这张图片",
            "llm_text": mm_content,
            "created_at": time.time(),
        }
    )
    app.state.store.save()

    r_sync = c.get("/api/sync?since_seq=0&source=phone")
    assert r_sync.status_code == 200
    assert all("llm_text" not in m for m in r_sync.json()["messages"])

    r_msgs = c.get("/api/messages?source=phone")
    assert r_msgs.status_code == 200
    assert all("llm_text" not in m for m in r_msgs.json()["messages"])

    r_status = c.get("/api/status")
    assert r_status.status_code == 200
    assert all("llm_text" not in m for m in r_status.json().get("recent_tail", []))

    # 3. Multi-turn chat after a multimodal message in history must NOT raise 'list' object has no attribute 'strip'
    r_next = c.post(
        "/api/chat",
        json={"text": "图片后面继续聊第二句", "bot_id": "companion", "mode": "chat", "source": "phone"},
    )
    assert r_next.status_code == 200
    assert r_next.json()["role"] == "assistant"


def test_task4_self_architecture_skill_and_index_registration(env):
    app, c = env
    vault = app.state.vault

    arch_doc = vault.read("skills/self_architecture.md")
    assert "分端独立 Bot 花名册" in arch_doc
    assert "dsh_workspace" in arch_doc
    assert "cloud_desktop" in arch_doc
    assert "blackboard" in arch_doc
    assert "peek_bots" in arch_doc
    assert "/opt/proactive-agent/data" in arch_doc
    assert "sleep_time_consolidate" in arch_doc
    assert "100K" in arch_doc

    root_idx = vault.read("INDEX.md")
    skills_idx = vault.read("skills/INDEX.md")
    assert "skills/self_architecture" in root_idx
    assert "skills/self_architecture.md" in skills_idx


def test_plain_at_mention_auto_dispatches_send_message_and_pc_retains_dsh_workspace(env):
    app, c = env
    brain = app.state.brain
    c.post(
        "/api/config",
        json={
            "api_base_url": "https://api.openai.com/v1",
            "api_key": "sk-test-key",
            "default_model": "gpt-4.1-mini",
        },
    )
    pc_received_tools = []
    phone_step = {"n": 0}

    async def mock_complete(messages, tools=None, big=False):
        cur_src = brain.source_context.get()
        cur_bot = brain.bot_context.get()
        if cur_src == "pc" and cur_bot == "pc_engineer":
            tool_names = [t["function"]["name"] for t in (tools or [])]
            pc_received_tools.extend(tool_names)
            return {
                "role": "assistant",
                "content": "电脑端司工收到黑板提醒！已用 dsh_workspace 检查 QQ 消息并回复。",
            }
        phone_step["n"] += 1
        if phone_step["n"] == 1:
            return {
                "role": "assistant",
                "content": "@Windows一:司工 电脑端，主人让余转一句：你好，帮他看看QQ信息",
                "tool_calls": [],
            }
        assert any(m.get("role") == "tool" and "post_id" in str(m.get("content", "")) for m in messages)
        return {
            "role": "assistant",
            "content": "已经在黑板 @ 电脑端司工去检查 QQ 消息啦！",
            "tool_calls": [],
        }

    brain.complete = mock_complete
    r = c.post(
        "/api/chat",
        json={"text": "给电脑发个你好让他看看QQ信息", "bot_id": "companion", "source": "phone"},
    )
    assert r.status_code == 200
    body = r.json()
    assert "电脑端司工" in body.get("text", "")
    assert body.get("from_bot_id") == "companion"
    for _ in range(30):
        if pc_received_tools:
            break
        import time as _t
        _t.sleep(0.02)
    assert "dsh_workspace" in pc_received_tools
    assert "cloud_desktop" in pc_received_tools
    assert "blackboard" in pc_received_tools
    assert "peek_bots" in pc_received_tools
    assert "send_message" not in pc_received_tools
    assert brain._is_current_bot_vision_capable() is True


def test_action_promise_and_fake_write_interception_plus_scroll_loop_tolerance(env):
    app, c = env
    brain = app.state.brain
    c.post(
        "/api/config",
        json={
            "api_base_url": "https://api.openai.com/v1",
            "api_key": "sk-test-key",
            "default_model": "gpt-4.1-mini",
        },
    )

    # 1. Verify stage-direction / action-promise hallucination at step 0 is intercepted and retried until tool is called,
    #    and fake write claim without write_file is also intercepted and forces a real write_file call.
    call_step = {"n": 0}

    async def mock_complete(messages, tools=None, big=False):
        call_step["n"] += 1
        n = call_step["n"]
        if n == 1:
            # Step 1: Model hallucinates a stage direction with zero tool_calls
            return {
                "role": "assistant",
                "content": "余先打开 QQ，找到「六分仪」这个会话。\n\n（正在打开 QQ 并搜索「六分仪」）",
                "tool_calls": [],
            }
        if n == 2:
            # Step 2: After system interception, model claims it wrote to notes/ without calling write_file
            return {
                "role": "assistant",
                "content": "这段聊天记录已经记进 notes/qq-liufenyi-test.md 了喵！",
                "tool_calls": [],
            }
        if n == 3:
            # Step 3: Model actually calls write_file
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_wf_1",
                        "type": "function",
                        "function": {
                            "name": "write_file",
                            "arguments": json.dumps(
                                {
                                    "path": "memory_vault/notes/qq-liufenyi-test.md",
                                    "content": "# 六分仪聊天记录\n- 实测落盘成功",
                                },
                                ensure_ascii=False,
                            ),
                        },
                    }
                ],
            }
        return {
            "role": "assistant",
            "content": "已真实读取并写入 notes/qq-liufenyi-test.md 喵！",
            "tool_calls": [],
        }

    brain.complete = mock_complete
    r = c.post(
        "/api/chat",
        json={"text": "打开QQ看我和六分仪的聊天，记录一下", "bot_id": "companion", "source": "phone"},
    )
    assert r.status_code == 200
    msg_out = r.json()
    assert call_step["n"] == 4
    assert "正在打开 QQ" not in msg_out["text"]
    assert any(t["tool"] == "write_file" for t in (msg_out.get("trace") or []))
    assert "实测落盘成功" in app.state.vault.read("notes/qq-liufenyi-test.md")

    # 2. Verify scroll signatures include treeHash so changing screens are never blocked, while unchanged boundary is detected
    brain._last_phone_tree_hash = "com.tencent.mobileqq:hash1"
    sig1, is_scr1 = brain._build_tool_call_sig("phone_workspace", {"op": "scroll", "direction": "up", "amount": 2})
    brain._last_phone_tree_hash = "com.tencent.mobileqq:hash2"
    sig2, _ = brain._build_tool_call_sig("phone_workspace", {"op": "scroll", "direction": "up", "amount": 4})
    assert is_scr1 is True
    assert sig1 != sig2

    # Same treeHash across dummy amount changes produces identical signature so boundary is cleanly detected
    sig2_dup, _ = brain._build_tool_call_sig("phone_workspace", {"op": "scroll", "direction": "up", "amount": 9})
    assert sig2 == sig2_dup
    assert brain._matches_requested_app_package("QQ", "com.tencent.mobileqq") is True
    assert brain._matches_requested_app_package("微信", "com.tencent.mm") is True


def test_phone_workspace_b64_strip_and_settle_and_vault_history_purge(env):
    import asyncio
    app, c = env
    brain = app.state.brain
    vault = app.state.vault
    harness = brain.harness

    # 1. (16-2) Verify delete_file with purge=True deletes both the note and all .history backups
    vault.write("notes/temp-sensitive-test.md", "# 初始内容 v1\n测试段落1")
    vault.write("notes/temp-sensitive-test.md", "# 修改内容 v2\n测试段落2")
    hist_before = vault.history("notes/temp-sensitive-test.md")
    assert len(hist_before) >= 1
    del_res = asyncio.run(harness.execute("delete_file", {"path": "memory_vault/notes/temp-sensitive-test.md", "purge": True}))
    assert del_res["ok"] is True
    assert del_res["purged"] is True
    assert len(del_res.get("purged_history_backups") or []) >= 1
    assert len(vault.history("notes/temp-sensitive-test.md")) == 0

    # 2. (14-6, 17-1, 18-1, 18-3, 18-4, 18-5) Verify phone_workspace shell slimming, key retry, tree settle, and zero base64 bloat
    rpc_step = {"key_calls": 0, "tap_settled": False}

    async def fake_phone_rpc(op, args, timeout=32.0):
        if op == "shell":
            return {
                "ok": True,
                "output": "uid=0(root)",
                "exit_code": 0,
                "dpi": 293,
                "swDp": 384,
                "rotation": 0,
                "tier": "compact",
                "isolatedFocus": True,
                "allocated": True,
                "created": True,
                "displayId": 20,
            }
        if op == "tap_node":
            # First frame returns transient empty tree (18-3)
            rpc_step["tap_settled"] = True
            return {
                "ok": True,
                "displayId": 20,
                "package": "unknown",
                "node_count": 0,
                "summary": "[虚拟屏 #20 704x1552 package=unknown count=0 treeHash=811c9dc5]",
            }
        if op == "key":
            rpc_step["key_calls"] += 1
            return {
                "ok": True,
                "displayId": 20,
                "package": "com.android.launcher",
                "node_count": 12,
                "ui_tree": "[虚拟屏 #20 704x1552 package=com.android.launcher count=12 treeHash=same111]",
            }
        if op == "ui_tree":
            if rpc_step["key_calls"] >= 2:
                return {
                    "ok": True,
                    "displayId": 20,
                    "package": "com.android.settings",
                    "node_count": 18,
                    "ui_tree": "[虚拟屏 #20 704x1552 package=com.android.settings count=18 treeHash=changed222]\n@e1 设置",
                }
            if rpc_step["tap_settled"]:
                return {
                    "ok": True,
                    "displayId": 20,
                    "package": "com.android.launcher",
                    "node_count": 45,
                    "ui_tree": "[虚拟屏 #20 704x1552 package=com.android.launcher count=45 treeHash=e27b2fc1]\n@e18 速览",
                }
            return {
                "ok": True,
                "displayId": 20,
                "package": "com.android.launcher",
                "node_count": 12,
                "ui_tree": "[虚拟屏 #20 704x1552 package=com.android.launcher count=12 treeHash=same111]",
            }
        return {"ok": True, "displayId": 20}

    brain._raw_phone_rpc = fake_phone_rpc

    # 2a. Shell output must not contain virtual display metadata (14-6)
    sh_out = asyncio.run(brain._tool_phone_workspace({"op": "shell", "command": "id"}))
    assert sh_out == {"ok": True, "op": "shell", "output": "uid=0(root)", "exit_code": 0}
    for bad_k in ("dpi", "swDp", "rotation", "tier", "isolatedFocus", "allocated", "created", "displayId"):
        assert bad_k not in sh_out

    # 2b. Tap with transient empty tree must settle and overwrite top-level package/node_count/ui_tree without _image_b64 (17-1, 18-3, 18-4)
    tap_out = asyncio.run(brain._tool_phone_workspace({"op": "tap", "target": "@e18"}))
    assert tap_out["package"] == "com.android.launcher"
    assert tap_out["node_count"] == 45
    assert "treeHash=e27b2fc1" in tap_out["ui_tree"]
    assert "summary" not in tap_out
    assert tap_out["before_action"]["package"] == "unknown"
    assert "_image_b64" not in tap_out
    assert "_multimodal_b64" not in tap_out
    assert len(json.dumps(tap_out, ensure_ascii=False)) < 3000

    # 2c. Press back fires strictly ONCE even when treeHash does not change (never multi-pops back stack!)
    rpc_step["tap_settled"] = False
    brain._last_phone_tree_hash = "com.android.launcher:same111"
    key_out = asyncio.run(brain._tool_phone_workspace({"op": "press", "key": "back"}))
    assert rpc_step["key_calls"] == 1
    assert key_out["key_consumed"] is False
    assert key_out["tree_hash_before"] == "com.android.launcher:same111"
    assert key_out["tree_hash_after"] == "com.android.launcher:same111"

    # 2d. maybe_offload_tool_output strips any residual _image_b64 so ctx_offloaded is never triggered by images (17-1 / 18-4)
    fake_heavy = {"ok": True, "op": "ui_tree", "ui_tree": "@e1 按钮", "_image_b64": "A" * 160000}
    serialized_tool = brain.context_search.maybe_offload_tool_output("phone_workspace", {"op": "ui_tree"}, fake_heavy)
    parsed_tool = json.loads(serialized_tool)
    assert "_image_b64" not in parsed_tool
    assert parsed_tool.get("ctx_offloaded") is not True
    assert parsed_tool.get("_image_attached") is True
    assert parsed_tool.get("_image_chars_stripped") == 160000
    assert len(serialized_tool) < 1000

    # 3. (19-1, 19-2, 19-3, 19-5) Verify WebView/EditText type matching (@eN / id / hint), error screenshot stripping, and single tree normalization
    brain._parse_and_cache_phone_nodes({
        "width": 1080,
        "height": 2400,
        "ui_tree": (
            "[虚拟屏 #20 1080x2400 package=com.android.chrome count=3 treeHash=bing123]\n"
            "@e1 [View] \"Bing\" click norm=(500,120)\n"
            "@e2 [EditText] \"Search the web\" id=sb_form_q click,edit norm=(480,260)\n"
            "@e3 [Button] \"Search\" id=sb_form_go click norm=(880,260)"
        ),
    })
    m_by_ref = brain._match_phone_node(target_q="@e2", for_type=True)
    m_by_id = brain._match_phone_node(target_q="sb_form_q", for_type=True)
    m_by_hint = brain._match_phone_node(target_q="Search the web", for_type=True)
    assert m_by_ref and m_by_ref["id"] == "sb_form_q"
    assert m_by_id and m_by_id["ref"] == "@e2"
    assert m_by_hint and m_by_hint["ref"] == "@e2"

    # 4. Verify treeHash == tree_hash, change_summary consistency after retry_tree, and tap_and_type steps_executed
    assert tap_out["treeHash"] == "e27b2fc1"
    assert tap_out["tree_hash"] == "e27b2fc1"
    assert "com.android.launcher" in tap_out["change_summary"]
    assert "unknown" not in tap_out["change_summary"]
    assert tap_out["densityDpi"] == 320
    assert tap_out["screenWidthDp"] == 360
    assert tap_out["fontScale"] == 1.0

    type_out = asyncio.run(brain._tool_phone_workspace({"op": "type", "target": "@e2", "text": "WeChat", "submit": True}))
    assert type_out["ok"] is True
    assert isinstance(type_out.get("steps_executed"), list) and len(type_out["steps_executed"]) == 3
    assert [s["op"] for s in type_out["steps_executed"]] == ["tap", "text", "key"]
    assert type_out["treeHash"] == type_out["tree_hash"]


def test_context_window_adjustment_and_hard_enforcement(env):
    """
    Verify:
    1. Updating `auto_compact_tokens` via `POST /api/config` persists cleanly to workspace/settings.json without being overwritten by stale workspace mtimes.
    2. When `reported_tokens` (e.g., 68000) exceeds a newly lowered `auto_compact_tokens` (e.g., 32000), `POST /api/config` triggers auto-compaction immediately without being blocked by the old 1.35x cap.
    3. `_build_turn_messages_and_tools` enforces the hard Context Window budget on `clean_recent`.
    """
    app, client = env
    store = app.state.store
    brain = app.state.brain

    # Touch workspace/bots.json with a newer mtime to simulate active workspace edits
    bots_ws = store.workspace_dir / "bots.json"
    if bots_ws.exists():
        import os
        st = bots_ws.stat()
        os.utime(bots_ws, (st.st_atime + 5, st.st_mtime + 5))

    # Add 10 messages (~1000 tokens each) and set last_prompt_tokens to 68000 (simulating tool-heavy turn)
    for i in range(5):
        store.message("user", f"用户长消息 {i}: " + ("测试内容" * 220), bot_id="companion", source="phone")
        store.message("assistant", f"助手长回复 {i}: " + ("回复内容" * 220), bot_id="companion", source="phone")
    brain.memory_engine.last_prompt_tokens["phone:companion"] = 68000
    brain.memory_engine.last_prompt_tokens["companion"] = 68000

    res = client.post("/api/config?source=phone", json={"auto_compact_tokens": 32000})
    assert res.status_code == 200
    body = res.json()
    assert body["auto_compact_tokens"] == 32000
    assert store.config.auto_compact_tokens == 32000
    assert "phone:companion" in body.get("compacted_bots", [])
    assert body["context_by_bot"]["companion"]["current_tokens"] < 32000

    # Verify hard context window slicing in _build_turn_messages_and_tools when window is 18800 (history budget 4000)
    store.update_config({"auto_compact_tokens": 18800})
    for i in range(12):
        store.message("user", f"窗口裁剪测试 {i}: " + ("窗口受限内容" * 200), bot_id="companion", source="phone")
        store.message("assistant", f"窗口裁剪回复 {i}: " + ("窗口受限回复" * 200), bot_id="companion", source="phone")
    store.message("user", "最新一条用户问题", bot_id="companion", source="phone")
    bot_obj = store.state["assistants"][0]
    turn_msgs, _, _, _ = asyncio.run(brain._build_turn_messages_and_tools("companion", "phone", bot_obj))
    hist_msgs = turn_msgs[1:]
    total_hist_tok = sum(brain.memory_engine.estimate_tokens(m.get("content", "")) + 12 for m in hist_msgs)
    assert total_hist_tok <= 4500
    assert "最新一条用户问题" in str(hist_msgs[-1].get("content", ""))


def test_cross_bot_reply_send_message_and_shell_error_exit_code(env):
    """
    Verify:
    1. When Bot A sends a `send_message` to Bot B (e.g., `research` or `contrarian`), Bot B at `hop_depth == 1`
       CAN call `send_message` back to Bot A without getting `此助手未启用该工具`, and its reply is delivered via `_reply_box_by_sig`.
    2. When `phone_workspace(op="shell")` fails with an error and no `ok` field, `exit_code` is `-1` (never `0` alongside `ok: false`).
    """
    app, client = env
    brain = app.state.brain
    client.post(
        "/api/config",
        json={
            "api_base_url": "https://api.openai.com/v1",
            "api_key": "sk-test-key",
            "default_model": "gpt-4.1-mini",
        },
    )

    step = {"n": 0}
    res_done = {"ok": False}

    async def mock_complete(messages, tools=None, big=False):
        cur_bot = brain.bot_context.get()
        if cur_bot == "research":
            res_step = sum(1 for m in messages if m.get("role") == "tool")
            if res_step == 0:
                return {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_bb_reply",
                            "type": "function",
                            "function": {
                                "name": "blackboard",
                                "arguments": json.dumps({"action": "complete", "post_id": "bb-1", "summary": "研究助手已在黑板正式结项回执！"}, ensure_ascii=False),
                            },
                        }
                    ],
                }
            res_done["ok"] = True
            return {"role": "assistant", "content": "研究助手已在黑板正式结项回执！"}
        step["n"] += 1
        if step["n"] == 1:
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_to_research",
                        "type": "function",
                        "function": {
                            "name": "blackboard",
                            "arguments": json.dumps({"action": "post", "mentions": ["@research"], "text": "请检查并在黑板结项回执"}, ensure_ascii=False),
                        },
                    }
                ],
            }
        return {"role": "assistant", "content": "已在黑板派发给研究助手处理。"}

    brain.complete = mock_complete
    r = client.post("/api/chat", json={"text": "让研究助手回信", "bot_id": "companion", "source": "phone"})
    assert r.status_code == 200
    body = r.json()
    assert "黑板派发给研究助手" in body.get("text", "")
    assert body.get("from_bot_id") == "companion"
    for _ in range(30):
        if res_done["ok"]:
            break
        import time as _t
        _t.sleep(0.02)
    assert res_done["ok"] is True
    comp_msgs = [m for m in app.state.store.state["messages"] if m.get("bot_id") == "companion"]
    assert not any(m.get("kind") == "bot_reply" for m in comp_msgs)

    # Verify phone_workspace(op="shell") error exit_code is -1, never 0
    async def mock_failed_rpc(op, args, timeout=32.0):
        return {"error": "Root 工具进程握手未完成 (daemon_handshake_timeout)"}

    brain._raw_phone_rpc = mock_failed_rpc
    sh_res = asyncio.run(brain._tool_phone_workspace({"op": "shell", "command": "id"}))
    assert sh_res["ok"] is False
    assert sh_res["exit_code"] == -1


def test_dot_filter_goal_mode_and_stay_silent_tool(env):
    app, client = env
    brain = app.state.brain
    client.post(
        "/api/config",
        json={
            "api_base_url": "https://api.openai.com/v1",
            "api_key": "fixture",
            "default_model": "gpt-4.1-mini",
        },
    )

    # 1. Single-dot and leading-dot filter
    assert brain._clean_and_filter_dot_text(".") == ""
    assert brain._clean_and_filter_dot_text("  。  ") == ""
    assert brain._clean_and_filter_dot_text("...") == ""
    assert brain._clean_and_filter_dot_text(".喵，主人好！") == "喵，主人好！"

    # 2. Mid-loop unfulfilled action promise & thought-only dropout detection
    assert brain._is_unfulfilled_action_promise(
        "日期选择器已打开，25 号在第三行、第四列，余按坐标点它喵。",
        user_text="查票",
        trace=[{"tool": "phone_workspace"}],
    ) is True
    assert brain._is_unfulfilled_action_promise(
        ".",
        user_text="打开必应搜索",
        trace=[{"tool": "phone_workspace"}],
        thought="Let me open https://www.bing.com directly. Let me do that.",
    ) is True

    # 3. /goal help card when called with no args, and full Goal Mode loop with self-healing + complete_goal when called with args
    r_help = client.post("/api/chat", json={"text": "/goal", "bot_id": "companion", "source": "phone"})
    assert r_help.status_code == 200
    assert "(`/goal`" in r_help.json().get("text", "")

    step_g = {"n": 0}

    async def mock_goal_complete(messages, tools=None, big=False, tool_choice=None):
        step_g["n"] += 1
        if step_g["n"] == 1:
            # Simulate upstream emitting a single dot "." with thought intending to act
            return {
                "role": "assistant",
                "content": ".",
                "thought": "让我先查看状态。",
                "tool_calls": [],
            }
        if step_g["n"] == 2:
            # Self-healed retry executes system_status
            return {
                "role": "assistant",
                "content": ".余先检查一下系统状态喵。",
                "tool_calls": [
                    {
                        "id": "call_goal_st",
                        "type": "function",
                        "function": {"name": "system_status", "arguments": "{}"},
                    }
                ],
            }
        if step_g["n"] == 3:
            # Explicit complete_goal invocation required by persistent Goal Mode engine
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_goal_done",
                        "type": "function",
                        "function": {
                            "name": "complete_goal",
                            "arguments": json.dumps(
                                {"summary": "系统通道一切正常", "verified_evidence": "system_status=ok"},
                                ensure_ascii=False,
                            ),
                        },
                    }
                ],
            }
        return {
            "role": "assistant",
            "content": ".喵，目标已全部完成并核验无误，系统通道一切正常！",
            "tool_calls": [],
        }

    brain.complete = mock_goal_complete
    r_goal = client.post("/api/chat", json={"text": "/goal 检查系统状态并汇总汇报", "bot_id": "companion", "source": "phone"})
    assert r_goal.status_code == 200
    g_body = r_goal.json()
    assert "喵，目标已全部完成" in g_body["text"]
    assert not g_body["text"].startswith(".")
    assert app.state.store.state.get("last_goal_run", {}).get("status") == "completed"
    assert app.state.store.state.get("last_goal_run", {}).get("steps") == 2

    # 4. stay_silent tool in curiosity and run_scheduled suppresses bubbles and notifications
    before_count = len(app.state.store.state["messages"])

    async def mock_silent_curiosity(messages, tools=None, big=False, bot_id="companion", tool_choice=None):
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_silent_1",
                    "type": "function",
                    "function": {
                        "name": "stay_silent",
                        "arguments": json.dumps({"reason": "主人刚忙完，当前无紧急事项，保持安静", "snooze_minutes": 30}, ensure_ascii=False),
                    },
                }
            ],
        }

    brain.llm.complete_silent = mock_silent_curiosity
    cur_res = asyncio.run(brain.curiosity(manual=True, bot_id="companion", source="phone"))
    assert cur_res.get("silent") is True
    assert cur_res.get("status") == "silent"
    assert len(app.state.store.state["messages"]) == before_count






