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
    assert ("多端对称" in arch_doc or "双端对称" in arch_doc)
    assert "dsh_workspace" in arch_doc
    assert "send_message" in arch_doc
    assert "/opt/proactive-agent/data/memory_vault" in arch_doc
    assert "sleep_time_consolidate" in arch_doc
    assert "放最底下" in arch_doc

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

    async def mock_complete(messages, tools=None, big=False):
        cur_src = brain.source_context.get()
        cur_depth = brain.hop_depth.get()
        if cur_src == "pc" and cur_depth > 0:
            tool_names = [t["function"]["name"] for t in (tools or [])]
            pc_received_tools.extend(tool_names)
            return {
                "role": "assistant",
                "content": "电脑端白珂收到！已用 dsh_workspace 检查 QQ 消息并回复。",
            }
        # Phone bot accidentally writes plain-text @Windows一:白珂 without tool_calls
        return {
            "role": "assistant",
            "content": "@Windows一:白珂 电脑端，主人让余转一句：你好，帮他看看QQ信息",
            "tool_calls": [],
        }

    brain.complete = mock_complete
    r = c.post(
        "/api/chat",
        json={"text": "给电脑发个你好让他看看QQ信息", "bot_id": "companion", "source": "phone"},
    )
    assert r.status_code == 200
    body = r.json()
    assert "电脑端白珂收到" in body.get("text", "")
    assert "dsh_workspace" in pc_received_tools
    assert "send_message" not in pc_received_tools


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

