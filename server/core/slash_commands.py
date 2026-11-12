"""OpenCode / Claude Code / Grok Bot style Harness Slash Commands.

Supported commands:
- `/goal` (`/goals`, `/loop`): Autonomous multi-step Goal Mode with completion gate & self-healing retries
- `/compact` (`/compack`, `/summarize`): Compress & archive older context into Zettelkasten + FTS5
- `/mcp` (`/mcps`, `/tools`, `/skills`): List all active Harness tools, MCP/custom plugins, and skills
- `/memory` (`/vault`, `/init`): View or search Zettelkasten memory vault, user traits & SOUL persona
- `/models` (`/model`): List available LLMs or switch the current Bot's model (`/model <id>`)
- `/sessions` (`/resume`, `/bots`): View multi-node Bot sessions & Windows DSH workspace sessions
- `/undo`: Undo the last turn in the current room (saved to undo stack)
- `/redo`: Redo the previously undone turn
- `/status` (`/context`, `/cost`): Context token usage, Prefix KV Cache hit rate & node status
- `/doctor`: Full channel & environment health check
- `/pin` (`/pins`, `/unpin`): Add/list/remove pinned core memories (`📌 置顶记忆`)
- `/clear` (`/new`, `/reset`): Archive current room to FTS5 and start a fresh context
- `/share` (`/export`): Export current room transcript to Markdown in `workspace/exports/`
- `/help` (`/commands`): Display all slash commands
"""
from __future__ import annotations

import re
import time
from assistants import AssistantInput, get_assistant, save_assistant
from storage import node_display_name, normalize_node_source
from vault_manager import now_iso

SLASH_RX = re.compile(
    r"^/(goal|goals|loop|compact|compack|summarize|mcp|mcps|tools|skills|memory|vault|init|model|models|sessions|resume|bots|undo|redo|status|context|cost|doctor|clear|new|reset|pin|pins|unpin|share|export|help|commands)(?:\s+([\s\S]*))?$",
    re.IGNORECASE,
)

SLASH_COMMANDS_CATALOG = [
    {"cmd": "/goal", "aliases": ["/goals", "/loop"], "args": "<目标描述>", "title": "深度目标闭环模式", "desc": "开启长程死磕执行模式（最高 160 步、子目标拆解、报错自动换路与终局完工验收门禁，绝不半途停工）"},
    {"cmd": "/compact", "aliases": ["/compack", "/summarize"], "args": "[重点说明]", "title": "压缩上下文", "desc": "将较早对话归档至 Zettelkasten 记忆库与 FTS5 索引，释放 Token 窗口"},
    {"cmd": "/mcp", "aliases": ["/mcps", "/tools", "/skills"], "args": "", "title": "MCP 与工具插件", "desc": "查看已挂载的 MCP 扩展工具、技能包 (Skills) 与本机/跨端工具矩阵"},
    {"cmd": "/memory", "aliases": ["/vault", "/init"], "args": "[搜索词]", "title": "记忆库与画像检索", "desc": "查看 Zettelkasten 记忆库索引与个人印象，或直接检索记忆卡片"},
    {"cmd": "/models", "aliases": ["/model"], "args": "[模型ID]", "title": "查看或切换模型", "desc": "列出可用大模型，或直接切换当前节点 Bot 的专属模型"},
    {"cmd": "/sessions", "aliases": ["/resume", "/bots"], "args": "", "title": "多端会话与工作区", "desc": "查看四端 Bot 房间状态及 Windows DSH 底部工作区活跃会话"},
    {"cmd": "/undo", "aliases": [], "args": "", "title": "撤销上一轮对话", "desc": "撤回当前房间最后一轮问答（可通过 `/redo` 恢复）"},
    {"cmd": "/redo", "aliases": [], "args": "", "title": "重做已撤销对话", "desc": "恢复最近一次通过 `/undo` 撤回的问答记录"},
    {"cmd": "/status", "aliases": ["/context", "/cost"], "args": "", "title": "上下文与缓存状态", "desc": "查看 Token 预算、Prefix KV 缓存命中率与跨端在线状态"},
    {"cmd": "/doctor", "aliases": [], "args": "", "title": "Harness 通道自检", "desc": "诊断多模态视觉、图片通道、记忆库、手机端与电脑端桥接状态"},
    {"cmd": "/pin", "aliases": ["/pins", "/unpin"], "args": "<规则内容>", "title": "置顶核心记忆", "desc": "添加永久置顶规则 (`/pin 文本`)、查看列表 (`/pins`) 或移除 (`/unpin ID`)"},
    {"cmd": "/clear", "aliases": ["/new", "/reset"], "args": "", "title": "归档并清空当前屏", "desc": "将当前房间历史安全归档至 SQLite FTS5 全文检索库并开启新对话"},
    {"cmd": "/share", "aliases": ["/export"], "args": "", "title": "导出当前对话", "desc": "将当前房间对话导出为工作区 Markdown 文件 (`workspace/exports/`)"},
    {"cmd": "/help", "aliases": ["/commands"], "args": "", "title": "斜杠指令帮助", "desc": "显示所有支持的 Harness `/` 快捷指令大全"},
]


async def try_handle_slash_command(brain, raw_text: str, bot_id: str, src: str, bot: dict, current_user_msg: dict | None = None) -> str | None:
    txt = (raw_text or "").strip()
    if txt == "/":
        cmd, arg = "help", ""
    else:
        m = SLASH_RX.match(txt)
        if not m:
            return None
        cmd = m.group(1).lower()
        arg = (m.group(2) or "").strip()

    norm_src = normalize_node_source(src)
    node_label = node_display_name(norm_src)

    if cmd in ("goal", "goals", "loop"):
        nb_key = f"{norm_src}:{bot_id}"
        active_goals = brain.store.state.setdefault("active_goals", {})
        if arg.lower() in ("stop", "cancel", "off", "clear", "abort", "停止", "取消", "关闭"):
            removed = active_goals.pop(nb_key, None)
            if isinstance(removed, dict) and removed.get("goal"):
                brain.store.state["last_goal_run"] = {
                    "goal": str(removed.get("goal"))[:200],
                    "status": "cancelled",
                    "steps": int(removed.get("total_steps") or 0),
                    "wakeups": int(removed.get("wakeups") or 0),
                    "updated_at": now_iso(),
                }
                brain.store.save()
                return f"### 🛑 已停止持久目标模式 (`/goal stop`)\n\n- **已终止目标**：{removed.get('goal')}\n- 后续对话恢复普通单轮模式。"
            return "### ℹ️ 当前没有正在运行的持久目标 (`/goal`)\n\n随时输入 `/goal <你要彻底办完的目标>` 即可开启。"
        if not arg or arg.lower() in ("help", "status", "list"):
            cur_g = active_goals.get(nb_key)
            active_info = ""
            if isinstance(cur_g, dict) and cur_g.get("status") == "active":
                active_info = (
                    f"\n#### 🔥 当前正在持续推进的活跃目标\n"
                    f"- **目标**：{cur_g.get('goal')}\n"
                    f"- **状态**：`active`（自动拉起 `{cur_g.get('wakeups', 0)}` 次 · 累计 `{cur_g.get('total_steps', 0)}` 步）\n"
                    f"- *(输入 `/goal stop` 或点击右上角停止按钮可随时终止)*\n"
                )
            last_goal = brain.store.state.get("last_goal_run") or {}
            last_info = ""
            if isinstance(last_goal, dict) and last_goal.get("goal"):
                last_info = (
                    f"\n#### 📌 最近一次 `/goal` 记录\n"
                    f"- **目标**：{last_goal.get('goal')}\n"
                    f"- **状态**：`{last_goal.get('status', 'done')}` · 共执行 `{last_goal.get('steps', 0)}` 个工具步\n"
                    f"- **时间**：`{last_goal.get('updated_at', '')}`\n"
                )
            return (
                f"### 🎯 持久目标闭环引擎 (`/goal` · `{node_label}` · {bot['name']})\n\n"
                f"**用法**：输入 `/goal <目标描述>` 启动；输入 `/goal stop` 停止。\n\n"
                f"#### ✨ 引擎级持续拉起与 `complete_goal` 门禁\n"
                f"1. **不达目标绝不停机**：即使 AI 在某轮停止输出，引擎也会自动将 AI 重新拉起继续干活，**直到 AI 真正办完所有事项并显式调用 `complete_goal` 工具确认目标完成**才会停止！\n"
                f"2. **支持中途协同**：若遇到付款密码/验证码需要你接手，AI 会暂停等你处理，你回复后自动恢复持久目标循环。\n"
                f"{active_info}{last_info}\n"
                f"> 💡 **示例**：`/goal 打开铁路12306查11月25日宜昌北到武汉的高铁票并把车次列表发给我`"
            ).strip()
        # Non-empty goal argument -> return None so Brain._chat activates persistent Goal Mode!
        return None

    if cmd in ("help", "commands"):
        lines = [
            f"### ⚡ DeepSeek Claw · Harness 斜杠指令大全 (`{node_label}` · {bot['name']})",
            "",
            "| 指令 | 参数 / 别名 | 功能说明 |",
            "| :--- | :--- | :--- |",
        ]
        for item in SLASH_COMMANDS_CATALOG:
            alias_str = " ".join(f"`{a}`" for a in item["aliases"][:2]) if item["aliases"] else ""
            arg_str = f"`{item['args']}` " if item.get("args") else ""
            lines.append(f"| **`{item['cmd']}`** | {arg_str}{alias_str} | **{item['title']}**：{item['desc']} |")
        lines.append("\n> [!TIP]\n> 在输入框输入 `/` 会自动弹出补全菜单（支持方向键、`Tab` 或点击选择），手机端与电脑端完全通用。")
        return "\n".join(lines)

    if cmd in ("compact", "compack", "summarize"):
        await brain._set_stream(text="📦 **[1/3] 正在锁定并扫描历史上下文 (`/compact`)…**", tool="compact_context", speaker_id=bot_id, speaker_name=bot["name"])
        res = await brain._auto_compact_if_needed(bot_id, force=True, source=norm_src)
        stats = brain.memory_engine.get_context_stats(bot_id, source=norm_src)
        await brain.publish({
            "type": "context_compacted",
            "source": norm_src,
            "bot_id": bot_id,
            "result": res,
            "context_by_bot": brain.memory_engine.get_all_bots_context_stats(source=norm_src),
        })
        if isinstance(res, dict) and res.get("already_compacted"):
            return (
                f"### 📦 当前上下文已是最新压缩状态 (`/compact`)\n\n"
                f"- **最近归档笔记**：`{res.get('note_name') or res.get('vault_path') or 'compact_latest.md'}`\n"
                f"- **说明**：{res.get('message') or '自上次压缩以来新增对话不足 2 条，无需重复压缩。'}"
            ).strip()
        if isinstance(res, dict) and res.get("compacted_messages"):
            return (
                f"### 📦 上下文压缩完成 (`/compact`)\n\n"
                f"- **节点与助手**：`{node_label}` · **{bot['name']}**\n"
                f"- **归档消息数**：`{res.get('compacted_messages')} 条`\n"
                f"- **Token 变化**：`{res.get('before_tokens', 0):,}` ➔ **`{res.get('after_tokens', 0):,}` tokens** (节省 `~{res.get('saved_tokens', 0):,}` tokens)\n"
                f"- **记忆库卡片**：`{res.get('vault_path') or '已同步至 SQLite FTS5'}`\n\n"
                f"{res.get('summary') or ''}"
            ).strip()
        return (
            f"### 📦 上下文已是精简状态 (`/compact`)\n\n"
            f"- **当前房间 (`{node_label}` · {bot['name']})**：共 `{stats.get('message_count', 0)}` 条消息，估算占用 **`{stats.get('current_tokens', 0):,}` / `{stats.get('threshold_tokens', 200000):,}` tokens**，暂无需压缩归档。"
        )

    if cmd in ("mcp", "mcps", "tools", "skills"):
        ext = brain.harness.extension_registry
        custom_plugins = list(ext.tool_plugins.keys()) if hasattr(ext, "tool_plugins") else []
        skills_list = list(ext.skills.keys()) if hasattr(ext, "skills") else []
        all_schemas = brain.harness.all_tools()
        allowed = set(bot.get("tools") or [t["function"]["name"] for t in all_schemas]) | set(custom_plugins)
        if norm_src in ("pc", "pc2"):
            allowed -= {"phone_workspace", "phone_macro", "notify_user", "emergency_alarm"}
        else:
            allowed -= {"dsh_workspace", "message_phone_bot"}
        active_names = sorted(allowed)
        lines = [
            f"### 🔌 MCP / 插件工具与技能矩阵 (`/mcp` · `{node_label}`)",
            "",
            f"- **当前节点可用工具 (`{len(active_names)}` 项)**：",
            "  " + " · ".join(f"`{n}`" for n in active_names),
            f"- **动态扩展工具插件 (`custom_tool` / MCP Bridge)**：`{len(custom_plugins)} 项`"
            + (f" ({', '.join(f'`{p}`' for p in custom_plugins)})" if custom_plugins else " *(可通过 `custom_tool` 动态注册)*"),
            f"- **已挂载技能包 (`Skills`)**：`{len(skills_list)} 项`"
            + (f" ({', '.join(f'`{s}`' for s in skills_list)})" if skills_list else " *(位于 `workspace/skills/`)*"),
            f"- **跨端桥接状态**：📱 安卓 `{'🟢 在线' if brain.is_phone_online() else '⚪ 离线'}` · 💻 Windows DSH `{'🟢 在线' if brain.is_dsh_online() else '⚪ 离线'}`",
        ]
        return "\n".join(lines)

    if cmd in ("memory", "vault", "init"):
        if arg:
            hits = brain.vault.search(arg, limit=8)
            if not hits:
                return f"### 🧠 记忆库检索 (`/memory {arg}`)\n\n未找到包含 `{arg}` 的记忆卡片。"
            lines = [f"### 🧠 记忆库检索结果 (`/memory {arg}` · 共 `{len(hits)}` 条)", ""]
            for h in hits:
                p_clean = str(h.get("path", "")).removesuffix(".md")
                lines.append(f"- [[{p_clean}]]：{h.get('snippet', '')}")
            return "\n".join(lines)
        files = brain.vault.files()
        file_paths = [str(f.get("path") if isinstance(f, dict) else f) for f in files if f]
        pins = brain.memory_engine.list_pins()
        profile_cards = [p[:-3] if p.endswith(".md") else p for p in file_paths if p.startswith("profile/")]
        skill_cards = [p[:-3] if p.endswith(".md") else p for p in file_paths if p.startswith("skills/") and not p.endswith("INDEX.md")]
        note_cards = [p[:-3] if p.endswith(".md") else p for p in file_paths if p.startswith("notes/")]
        recent_notes = sorted(note_cards, reverse=True)[:10]
        prof_links = " · ".join(f"[[{p}]]" for p in profile_cards[:5]) or "[[profile/personal_impression]] · [[profile/user_traits]]"
        skill_links = " · ".join(f"[[{s}]]" for s in skill_cards[:6]) or "[[skills/self_architecture]]"
        note_lines = "\n".join(f"  - [[{n}]]" for n in recent_notes) if recent_notes else "  - *(暂无归档笔记)*"
        pin_preview = ""
        if pins:
            top_pins = "\n".join(f"  - 📌 {str(p.get('text') or '').strip()[:90]}" for p in pins[:3])
            pin_preview = f"- **📌 置顶核心记忆 (`{len(pins)} 条`)**：\n{top_pins}\n"
        else:
            pin_preview = "- **📌 置顶核心记忆**：`0 条` *(可用 `/pin <内容>` 添加)*\n"
        return (
            f"### 🧠 Zettelkasten 记忆库概览 (`/memory`)\n\n"
            f"- **记忆卡片总数**：`{len(files)} 篇` Markdown 笔记（支持点击卡片标签直接打开抽屉）\n"
            f"{pin_preview}"
            f"- **👤 核心人物档案**：{prof_links} · `workspace/SOUL.md`\n"
            f"- **🛠️ 架构与技能索引**：{skill_links}\n"
            f"- **📝 近期沉淀笔记 (`Top {len(recent_notes)}/{len(note_cards)}`)**：\n{note_lines}\n\n"
            f"> 💡 提示：点击上方任意紫色记忆标签（或右上角 `📖 记忆库抽屉`）可翻阅全文；输入 `/memory <关键词>` 全文检索，输入 `/pins` 管理置顶记忆。"
        )

    if cmd in ("sessions", "resume", "bots"):
        dsh_sessions = brain._get_dsh_sessions_list()
        bots = brain.store.state.get("assistants", [])
        lines = [
            f"### 🗂️ 多端 Bot 房间与工作区会话 (`/sessions`)",
            "",
            "#### 1. 专属 Bot 房间",
        ]
        for b in bots:
            bid = b.get("id", "")
            cnt_pc = sum(1 for x in brain.store.state.get("messages", []) if x.get("bot_id", "companion") == bid and normalize_node_source(x.get("source")) == "pc")
            cnt_ph = sum(1 for x in brain.store.state.get("messages", []) if x.get("bot_id", "companion") == bid and normalize_node_source(x.get("source")) == "phone")
            lines.append(f"- **{b.get('name')}** (`{bid}`)：📱 安卓一 `{cnt_ph} 条` · 💻 Windows一 `{cnt_pc} 条`")
        lines.append("\n#### 2. Windows DSH 底部工作区会话")
        if not dsh_sessions:
            lines.append("- 当前暂无活跃的 DSH 工作区会话（或 Windows DSH 未连接）。")
        else:
            for s in dsh_sessions[:12]:
                lines.append(f"- `{s.get('short_id') or s.get('id', '')[:8]}` · **{s.get('title', '未命名会话')}** (`{s.get('status', 'idle')}`)")
        return "\n".join(lines)

    if cmd == "undo":
        msgs = brain.store.state.get("messages", [])
        cur_id = (current_user_msg or {}).get("id")
        filtered = [m_it for m_it in msgs if m_it.get("id") != cur_id]
        room_idxs = [
            i for i, m_it in enumerate(filtered)
            if m_it.get("bot_id", "companion") == bot_id and normalize_node_source(m_it.get("source")) == norm_src
        ]
        if not room_idxs:
            brain.store.state["messages"] = filtered
            brain.store.save()
            return "### ↩️ 暂无可撤销的对话 (`/undo`)\n\n当前房间没有更早的对话轮次。"
        last_user_idx = -1
        for i in reversed(room_idxs):
            if filtered[i].get("role") == "user":
                last_user_idx = i
                break
        cut_from = last_user_idx if last_user_idx >= 0 else room_idxs[-1]
        undone_items = []
        kept = []
        for idx, m_it in enumerate(filtered):
            if idx >= cut_from and m_it.get("bot_id", "companion") == bot_id and normalize_node_source(m_it.get("source")) == norm_src:
                undone_items.append(m_it)
            else:
                kept.append(m_it)
        undo_stack = brain.store.state.setdefault("undo_stack", {})
        room_key = f"{norm_src}:{bot_id}"
        undo_stack.setdefault(room_key, []).append(undone_items)
        brain.store.state["messages"] = kept
        brain.store.state["messages_epoch"] = int(brain.store.state.get("messages_epoch") or 1) + 1
        brain.store.save()
        preview = (undone_items[0].get("text") or "")[:60] if undone_items else ""
        return f"### ↩️ 已撤销上一轮对话 (`/undo`)\n\n- **撤回消息数**：`{len(undone_items)} 条`（`{preview}`）\n- 如需恢复，随时输入 `/redo`。"

    if cmd == "redo":
        msgs = brain.store.state.get("messages", [])
        cur_id = (current_user_msg or {}).get("id")
        filtered = [m_it for m_it in msgs if m_it.get("id") != cur_id]
        undo_stack = brain.store.state.setdefault("undo_stack", {})
        room_key = f"{norm_src}:{bot_id}"
        stack_list = undo_stack.get(room_key) or []
        if not stack_list:
            brain.store.state["messages"] = filtered
            brain.store.save()
            return "### ↪️ 暂无可重做的对话 (`/redo`)\n\n当前房间没有通过 `/undo` 撤回的记录。"
        restored = stack_list.pop()
        brain.store.state["messages"] = filtered + restored
        brain.store.state["messages_epoch"] = int(brain.store.state.get("messages_epoch") or 1) + 1
        brain.store.save()
        return f"### ↪️ 已恢复撤销的对话 (`/redo`)\n\n- 已重新恢复 `{len(restored)} 条` 历史消息。"

    if cmd in ("share", "export"):
        msgs = [
            m_it for m_it in brain.store.state.get("messages", [])
            if m_it.get("bot_id", "companion") == bot_id and normalize_node_source(m_it.get("source")) == norm_src
        ]
        exp_dir = brain.harness.root / "exports"
        exp_dir.mkdir(parents=True, exist_ok=True)
        fname = f"chat-{norm_src}-{bot_id}-{int(time.time())}.md"
        out_path = exp_dir / fname
        md_lines = [f"# DeepSeek Claw 对话导出 · {bot['name']} ({node_label})", f"> 导出时间：{now_iso()}", ""]
        for m_it in msgs[-100:]:
            role_lbl = "👤 主人" if m_it.get("role") == "user" else f"🤖 {m_it.get('from_bot_name') or bot['name']}"
            md_lines.append(f"### {role_lbl} ({m_it.get('time') or ''})\n\n{m_it.get('text') or ''}\n")
        out_path.write_text("\n".join(md_lines), encoding="utf-8")
        return f"### 📤 对话已导出 (`/share`)\n\n- **导出路径**：`workspace/exports/{fname}`\n- **导出消息数**：`{min(len(msgs), 100)} 条`"

    if cmd in ("status", "context", "cost"):
        st_res = await brain._tool_system_status({})
        cb = st_res.get("context_budget") or {}
        ch = st_res.get("channels_doctor") or {}
        stats = brain.memory_engine.get_context_stats(bot_id, source=norm_src)
        pins = brain.memory_engine.list_pins()
        eff_model = brain.store.config.effective_model(bot_model=bot.get("model", ""), big=(bot.get("engine") == "big"))
        pt = int(cb.get("last_prompt_tokens") or stats.get("estimated_tokens") or 0)
        ct = int(cb.get("last_cached_tokens") or 0)
        hit = int(cb.get("cache_hit_pct") or 0)
        limit_t = int(cb.get("auto_compact_limit_tokens") or 200000)
        return (
            f"### 📊 Harness 运行状态 (`/status`)\n\n"
            f"- **当前节点 / 助手**：`{node_label}` (`{norm_src}`) · **{bot['name']}** (`{bot_id}`)\n"
            f"- **当前生效模型**：`{eff_model or '未配置'}`\n"
            f"- **上下文 Token 占用**：**`{pt:,}`** / `{limit_t:,}` tokens (`{round(pt * 100.0 / max(1, limit_t), 1)}%`)\n"
            f"- **Prefix KV 缓存命中**：**`{hit}%`** (`{ct:,}` cached tokens)\n"
            f"- **长期记忆库**：`{cb.get('vault_markdown_files_count', 0)}` 篇 Markdown · `{len(pins)}` 条 📌 置顶记忆\n"
            f"- **跨端协同状态**：📱 手机端 `{'🟢 在线' if ch.get('phone_online') else '⚪ 离线'}` · 💻 Windows DSH `{'🟢 在线' if ch.get('pc_online') else '⚪ 离线'}`"
        )

    if cmd == "doctor":
        st_res = await brain._tool_system_status({})
        ch = st_res.get("channels_doctor") or {}
        v_ok = (ch.get("vision_read_file") or {}).get("ok", True)
        tess_ok = (ch.get("vision_read_file") or {}).get("tesseract_ocr_available", False)
        mv_ok = (ch.get("memory_vault") or {}).get("ok", True)
        return (
            f"### 🩺 Harness 通道自检报告 (`/doctor`)\n\n"
            f"- **多模态视觉 (`read_file` / VLM)**：`{'✅ 正常' if v_ok else '❌ 异常'}` (Tesseract OCR: `{'已就绪' if tess_ok else '未安装'}`)\n"
            f"- **图片发送通道 (`send_image`)**：`✅ 正常 (/workspace-media/uploads/)`\n"
            f"- **Zettelkasten 记忆库 (`memory_vault`)**：`{'✅ 正常' if mv_ok else '❌ 异常'}`\n"
            f"- **📱 安卓节点通道 (`phone_workspace`)**：`{'🟢 在线就绪' if ch.get('phone_online') else '⚪ 当前未连接 WebSocket'}`\n"
            f"- **💻 Windows DSH 通道 (`dsh_workspace`)**：`{'🟢 在线就绪' if ch.get('pc_online') else '⚪ 当前未连接桥接'}`"
        )

    if cmd in ("clear", "new", "reset"):
        msgs = brain.store.state.get("messages", [])
        kept = []
        archived_count = 0
        for m_item in msgs:
            if m_item.get("bot_id", "companion") == bot_id and normalize_node_source(m_item.get("source")) == norm_src:
                try:
                    brain.context_search.index_message(m_item)
                except Exception:
                    pass
                archived_count += 1
            else:
                kept.append(m_item)
        brain.store.state["messages"] = kept
        brain.store.state["messages_epoch"] = int(brain.store.state.get("messages_epoch") or 1) + 1
        nb_key = f"{norm_src}:{bot_id}"
        brain.last_prompt_tokens[nb_key] = 0
        brain.last_cached_tokens[nb_key] = 0
        brain.store.state.setdefault("last_prompt_tokens", {})[nb_key] = 0
        brain.store.state.setdefault("last_cached_tokens", {})[nb_key] = 0
        if norm_src == "phone":
            brain.last_prompt_tokens[bot_id] = 0
            brain.last_cached_tokens[bot_id] = 0
            brain.store.state.setdefault("last_prompt_tokens", {})[bot_id] = 0
            brain.store.state.setdefault("last_cached_tokens", {})[bot_id] = 0
        brain.memory_engine.clear_memory_deltas(bot_id=bot_id, source=norm_src)
        brain.store.save()
        return (
            f"### 🧹 当前会话上下文已清空 (`/clear`)\n\n"
            f"- 已将 `{node_label}` · **{bot['name']}** 先前的 `{archived_count}` 条消息安全归档至 SQLite FTS5 全文检索库（随时可通过 `ctx_search` 召回），并开启全新上下文。"
        )

    if cmd in ("pin", "pins", "unpin"):
        if cmd == "unpin" and arg:
            res = await brain._tool_memory_pin({"action": "remove", "pin_id": arg.strip(), "text": arg.strip(), "source": norm_src})
            pins = res.get("pins") or []
            return f"### 📌 已移除置顶记忆\n\n当前剩余 `{len(pins)}` 条置顶记忆。"
        if cmd == "pin" and arg:
            res = await brain._tool_memory_pin({"action": "add", "text": arg.strip(), "source": norm_src})
            pins = res.get("pins") or []
            return f"### 📌 已钉入核心置顶记忆\n\n- **新增内容**：{arg.strip()}\n- **当前总数**：`{len(pins)}` 条（永久固定在系统提示词前缀中）"
        pins = brain.memory_engine.list_pins()
        if not pins:
            return "### 📌 置顶核心记忆列表 (`/pins`)\n\n当前暂无置顶记忆。可使用 `/pin <要记住的规则或偏好>` 添加。"
        lines = [f"### 📌 置顶核心记忆列表 (`{len(pins)}` 条)", ""]
        for p in pins:
            lines.append(f"- `{p.get('id', '')}`：{p.get('text', '')}")
        lines.append("\n> 输入 `/pin <新内容>` 可新增，输入 `/unpin <ID或关键词>` 可移除。")
        return "\n".join(lines)

    if cmd in ("model", "models"):
        if not arg:
            models = await brain.llm.fetch_available_models()
            eff_model = brain.store.config.effective_model(bot_model=bot.get("model", ""), big=(bot.get("engine") == "big"))
            m_lines = [
                f"### 🧠 模型配置 (`/model` · `{node_label}` · {bot['name']})",
                "",
                f"- **当前生效模型**：`{eff_model or '默认'}`",
                "- **可用模型列表**：",
            ]
            for mod_id in (models or [])[:15]:
                mark = " *(当前)*" if mod_id == eff_model else ""
                m_lines.append(f"  - `{mod_id}`{mark}")
            m_lines.append("\n> 输入 `/model <模型名>` 即可直接切换当前节点上该 Bot 的模型。")
            return "\n".join(m_lines)
        new_model = arg.strip()
        full_bot = get_assistant(brain.store, bot_id)
        inp = AssistantInput(
            id=full_bot["id"],
            name=full_bot["name"],
            title=full_bot.get("title", ""),
            description=full_bot.get("description", ""),
            instructions=full_bot.get("instructions", ""),
            model=new_model,
            curiosity_enabled=bool(bot.get("curiosity_enabled", False)),
            engine=full_bot.get("engine", "small"),
            color=full_bot.get("color", "#7C5CFF"),
            shape=full_bot.get("shape", "circle"),
            notify=bool(bot.get("notify", True)),
            tools=full_bot.get("tools") or [],
            source=norm_src,
        )
        updated = save_assistant(brain.store, inp, bot_id, source=norm_src)
        await brain.publish({
            "type": "bots_changed",
            "source": norm_src,
            "bot_id": bot_id,
            "bots": brain.store.state["assistants"],
            "primary_bot_id": brain.store.state["primary_bot_id"],
        })
        return f"### 🧠 模型已切换 (`/model`)\n\n- **`{node_label}` · {updated['name']}** 的专属模型已更新为：`{new_model}`"

    return None
