# DeepSeek-Claw (Proactive Personal Agent) — 超级总纲、全量端口路由与三端接口契约 (v4.0)

> **核心工程铁律（Engineering Mandates）**：
> 1. **微模块化（Micro-Modularization）**：新增或拆分的独立功能模块单文件严格控制在 **< 1,000 行**（新增微模块推荐 ≤ 500 行），职责单一，严禁向巨石文件堆砌数百行内联代码。
> 2. **契约先行（Contract-First）**：服务端 (`server/`)、Android 客户端 (`android/`)、Windows DSH 桌面插件 (`D:\dsh-baike-plugin/`) 三端的所有端口、鉴权 Header、HTTP 路由、WebSocket 帧、事件黑板 (`blackboard`) 与状态机必须 100% 遵循本总纲。
> 3. **多 Bot 调度唯一总线（Event Blackboard）**：彻底废除点对点同步 `send_message`；跨端/同端协同、看板认领 (`claim`)、产物交接 (`artifact_path`)、按需双盲验收 (`review_by`)、闹钟/定时自唤醒 (`ring_alarm`/`wake_bot`) 与 0-Token 事件探针 (`watch`) 统一走 `blackboard` + 只读窥视镜 `peek_bots`。

---

## 一、 全栈网络拓扑、端口矩阵与进程通信协议 (Ports, Sockets & IPC Topology)

| 端 / 节点 | 进程 / 服务名 | 绑定地址与端口 / 通道 | 协议与加密方式 | 核心职责与主备切换规则 |
| :--- | :--- | :--- | :--- | :--- |
| **云端主网关 (`vps-jp`)** | `proactive-agent.service` (`uvicorn main:app`) | `0.0.0.0:8000` (Nginx 反代 `HTTPS 443` / `HTTP 80`) | HTTP/1.1 + WebSocket + `/api/secure` (AES-256-GCM 端到端信封加密) | 全端状态中枢、DeepSeek LLM 推理编排、MemoryVault 记忆库、多端消息与工具路由 |
| **云端 Linux 桌面沙箱** | `proactive-desktop.service` (`computer_desktop:app`) | `127.0.0.1:18193` (仅限本机回环) | HTTP JSON RPC + Xvfb (`:99`) + Openbox | 云端隔离 Linux 虚拟桌面、浏览器自动化与受限命令执行沙箱 |
| **Windows DSH 主桥接服务** | `D:\dsh-baike-plugin\index.js` (`localHttpServer`) | `127.0.0.1:18195` (`DS_CLAW_LOCAL_PORT`) | HTTP JSON + CORS (`*`) | PC 端主桥接入口：向 `client.js` 提供 `/local/*` 接口，且**唯一**负责轮询云端 `/api/dsh/inbox?drain=1` |
| **Windows DSH 宿主 IPC 通道** | `D:\dsh-baike-plugin\index.js` (`dshHostIpcServer`) | `127.0.0.1:18196` (`DS_CLAW_HOST_IPC_PORT`) | HTTP JSON (Electron 宿主进程内) | 当 `18195` 被后台守护进程 `DeepSeekClawSentinel.exe` 暂持时，DSH GUI 启动后会 POST `/local/yield-port` 让守护进程让出 `18195`，或通过 `18196` 代理访问 Cordis `sessions` / `speechToText` 服务 |
| **Android 后台常驻服务** | `AgentForegroundService` | 主动长连接 `wss://<server>/ws/client` + 轮询 `/api/sync` | WebSocket + `SecureTransport` (AES-256-GCM) | 保持手机端在线心跳、接收云端 `phone_workspace` 操控指令、管理通知栏与语音播报 |
| **Android Root 虚拟屏服务** | `RootDisplayServer` (由 `app_process` 以 UID `0`/`2000` 启动) | 匿名管道 `stdin` / `stdout` (`PULSE_RPC:` 行协议) | 进程间标准流单行 JSON RPC (`_req_id` 异步匹配) | 彻底绕开 SELinux `unix_stream_socket` 拦截，在系统级创建/销毁虚拟屏 (`Display > 0`)、截图 (`capture`)、注入点击/滑动/按键与启动应用 |
| **Android Root 剪贴板代理** | `RootClipboardBroker` | `app_process` 管道 / `service call clipboard` | Binder / Shell 管道 | 跨 Android 10+ 后台剪贴板读取限制，供 Agent 读写系统剪贴板 |

---

## 二、 全栈完整目录树与模块职责清单 (Complete Directory & Module Map)

### 2.1 服务端 (`D:\bubuki\proactive-personal-agent\server\` — 全文件严格 ≤ 845 行)
```text
server/
├── main.py                           # [745行] FastAPI 核心网关：对话、记忆、Bot 花名册与设置 API 路由
├── brain.py                          # [835行] Agent 认知中枢宿主类 Brain(BrainPromptMixin, BrainToolsMixin, BrainProactiveMixin)
├── harness.py                        # [676行] ExtensionRegistry、Harness 工具路由与沙箱安全守卫（重导出 tool_schemas）
├── assistants.py                     # [391行] Bot 花名册定义：4 大默认助手与自定义 Bot 归一化
├── bot_identity.py                   # [233行] Bot 灵魂档案同步器 (IDENTITY.md / SOUL.md)
├── storage.py                        # [709行] 状态存储引擎：config.json、state.json 与 workspace/*.json 双向同步
├── vault_manager.py                  # [542行] MemoryVault 文件系统与 SQLite 数据库连接管理
├── computer_desktop.py               # [766行] 云端 Linux 虚拟桌面 X11 键鼠截图操控与 FastAPI 路由
├── computer_browser.py               # [685行] [领域模块] DesktopBrowserMixin：Playwright Chromium、DOM 快照与桌面环境渲染
├── core/
│   ├── interfaces.py                 # [52行] 跨模块协议与类型契约定义
│   ├── tool_schemas.py               # [416行] [领域模块] 全量 TOOLS OpenAI JSON Schema 契约、DEFAULT_SYSTEM_MD 与 SSRF 校验
│   ├── bridge_routes.py              # [564行] [领域模块] 跨端桥接网关路由 (/api/dsh/*, /api/phone/*, /ws/dsh-bridge, 静态资源)
│   ├── brain_prompt_mixin.py         # [778行] [领域模块] 人设加载、多模态提示词组装 (_build_turn_messages_and_tools)、伪动作拦截与自动压缩触发
│   ├── brain_tools_mixin.py          # [819行] [领域模块] 工具并发域调度 (_resolve_tool_concurrency_domain)、ReAct 多步工具循环 (_chat) 与 LLM 调用
│   ├── brain_proactive_mixin.py      # [674行] [领域模块] 主动关怀 (curiosity)、深度研究 (research)、定时任务 (run_scheduled) 与睡眠记忆巩固
│   ├── compaction_service.py         # [366行] [领域模块] 上下文流式压缩、按(bot_id,source)互斥锁、防连压幂等守卫与防回答跑偏校验
│   ├── xml_tool_parser.py            # [212行] [领域模块] 5 类内联伪 XML/DSML 工具调用解析器、标签剥离与静默输出拦截器
│   ├── memory_engine.py              # [622行] 长期记忆、Prefix Cache 静态前缀维护与 tool_contexts 存取
│   ├── llm_client.py                 # [625行] DeepSeek 流式客户端与缓存命中率统计
│   ├── phone_controller.py           # [713行] 手机虚拟屏控制器（wait 单步回传 ui_tree+截图、转场 550ms 重试、精简 nodes）
│   ├── desktop_controller.py         # [829行] Windows DSH 桌面与 CUA 跨端指令控制器
│   ├── interconnect_router.py        # [617行] 跨节点 Bot 间互相呼叫与消息路由
│   ├── notification_hub.py           # [431行] WebSocket 广播中心与设备心跳管理
│   ├── context_search.py             # [770行] FTS5 全文检索与历史归档按需召回引擎
│   ├── scheduler_engine.py           # [245行] 定时任务、闹钟与截止日期调度器
│   ├── search_engine.py              # [258行] 联网搜索与网页正文提取器
│   ├── slash_commands.py             # [436行] /compact, /pin, /bots 等斜杠指令处理器
│   ├── tool_handlers.py              # [845行] 内置文件/记忆库/系统状态工具分发器
│   └── vision_engine.py              # [356行] 视觉多模态图片压缩、四边标尺标注与 Base64 注入引擎
└── tests/
    └── test_ds_claw.py               # 自动化回归测试套件（71 项全绿）
```

### 2.2 Android 客户端 (`D:\bubuki\proactive-personal-agent\android\...\proactive\` — 全文件严格 ≤ 880 行)
```text
dev/bubuki/proactive/
├── NativePulseActivity.kt            # [880行] 宿主 Activity 生命周期、领域状态模型 (NativePulseState) 与核心对话发送/停止/重生成控制器
├── PulseActivitySync.kt              # [856行] [领域模块] WebSocket 实时流事件、/api/sync 增量同步、单调工具轨迹合并与本地缓存
├── PulseActivityBotOps.kt            # [733行] [领域模块] Bot CRUD、OTA APK 更新、Vault 读写、Routines/Pins 与上下文流式压缩 (compactBotContext)
├── PulseActivityMedia.kt             # [477行] [领域模块] 多媒体附件上传、语音识别、云端桌面帧流控制与系统权限引导
├── NativePulseScreen.kt              # [839行] 主界面 Scaffold 路由、首页总览 (NativeHome) 与聊天室手势视口 (NativeChatRoomViewport)
├── PulseScreenComposer.kt            # [805行] [领域模块] 聊天室顶栏、Bot 切换胶囊、上下文进度条与底部斜杠指令/附件输入栏 (NativeComposer)
├── PulseScreenChatItems.kt           # [853行] [领域模块] 消息气泡、思考折叠块、工具调用轨迹胶囊 (NativeToolTraceGroup) 与贴纸/图片组件
├── PulseScreenModals.kt              # [867行] [领域模块] 全局设置向导 (NativeSettingsPanel) 与 Bot 外观/档案编辑器 (NativeBotEditorPanel)
├── PulseScreenPanels.kt              # [864行] [领域模块] 透明记忆库面板 (NativeVaultPanel)、云端桌面触控面板与定时任务面板
├── PulseScreenHelpers.kt             # [818行] [领域模块] Apple HIG + Telegram Night 调色板、按压动效、通用列表行与双链格式化工具
├── NativePulseIcons.kt               # [240行] 矢量图标库与 PulseAvatar 入口（解除 companion 硬编码覆盖，委托 BotAvatarView）
├── BotAvatarView.kt                  # [192行] [领域模块] 11 种几何形状 Path、9 色预设色板选择器 BotColorSwatchRow 与 11 形状网格 BotShapeSelectorGrid
├── FloatingVirtualDisplay.kt         # [686行] 灵动岛悬浮虚拟屏宿主窗口生命周期与迷你/展开态布局切换
├── FloatingVirtualDisplayControls.kt # [865行] [领域模块] 悬浮窗弹簧物理引擎、矢量控制按钮、缩放手势与内嵌预览组件
├── VirtualDisplayAppDrawer.kt        # [260行] [领域模块] 虚拟屏原生 4 列竖屏应用桌面网格（LruCache 真实图标 + 搜索过滤，替代错位副屏九宫格）
├── RootPhoneHarness.kt               # [650行] 手机虚拟屏状态流、WakeLock 守护、tap_node 240ms 防连击与零拷贝截图回传
├── RootPhoneHarnessActions.kt        # [695行] [领域模块] 虚拟屏文件传输、Root Shell、无障碍树查询与应用列表检索
├── DisplayRuntime.java               # [751行] UID 0/2000 虚拟屏底层生命周期、ImageReader 帧捕获与任务栈守护
├── DisplayInputHelper.java           # [607行] [领域模块] InputManager 反射触摸/滑动/按键注入与任务栈迁移辅助器
├── PulseAccessibilityService.kt      # [563行] 无障碍节点采集（80ms 节流 + TYPE_WINDOWS_CHANGED 转场等待）
├── AgentForegroundService.java       # [394行] 常驻前台服务（使用 currentDisplayId() 上报虚拟屏状态）
├── SecurePrefs.java                  # [76行] 内存级 AES SecretKey + JSON 快照缓存 (readFast 消除 737K 缺页中断)
└── SecureTransport.java              # [146行] 端到端加密 HTTP 客户端
```

### 2.3 Windows DSH 桌面端插件 (`D:\dsh-baike-plugin\` — 全文件严格 ≤ 853 行)
```text
D:\dsh-baike-plugin\
├── index.js                          # [719行] 插件主入口 (端口 18195 / 18196)：云端双向桥接、本地 HTTP 网关与 Cordis 注入
├── lib-chat-cache.mjs                # [773行] [领域模块] Bot 花名册持久化、本地聊天缓存清洗与增量合并、多 Bot 云端对话轮次执行器
├── lib-dsh-sessions.mjs              # [848行] [领域模块] DSH 会话全景扫描、模糊搜索、详情读取、跨会话指令派发与本机 PowerShell/FS 工具
├── client.js                         # [853行] [领域模块] 右侧 400px 停靠式手机同款伴侣分栏（100% 不遮挡左侧 DSH 工作区，含 9色+11形状外观编辑器与流式压缩）
├── cua-bridge.mjs                    # [160行] Windows 原生桌面 CUA 键鼠/窗口操控桥接模块
└── setup.mjs                         # [162行] 插件安装与环境自检脚本
```

---

## 三、 全量 REST API、WebSocket 与本地桥接接口契约 (Full Endpoint & Schema Reference)

### 3.1 鉴权与加密传输契约
1. **明文鉴权头 (`Authorization` / `X-API-Token`)**：
   - 所有受保护路由支持 `Authorization: Bearer <token>` 或 `X-API-Token: <token>`，或 URL Query `?token=<token>`。
2. **端到端加密网关 (`POST /api/secure`)**：
   - Android `SecureTransport` 将任意请求封装为 `{ "method": "GET|POST|PUT|PATCH|DELETE", "path": "/api/...", "body": {...}, "ts": 1794240000 }`，使用派生的 AES-256-GCM 密钥加密后 POST 到 `/api/secure`，服务端解密后内部路由并返回加密响应。

### 3.2 云端核心 REST 路由表 (`server/main.py` — 端口 `8000`)

| 方法与路径 | 请求体 / Query 参数 | 响应 JSON 核心字段契约 | 说明与防错约束 |
| :--- | :--- | :--- | :--- |
| `GET /health` | 无 | `{"ok": true, "ts": 1794...}` | 负载均衡与存活探针 |
| `GET /api/sync` | `?since_seq=0&source=phone\|pc` | `{"ok": true, "seq": 1024, "messages_epoch": 38, "messages": [...], "bots": [...], "streams_by_bot": {...}, "executions_by_bot": {...}, "bot_context_by_id": {...}, "dsh_status": {...}}` | **三端核心增量同步接口**：返回当前节点的全部消息、Bot 花名册、实时流式输出缓冲、活跃工具轨迹及每个 Bot 的上下文 Token 占用 |
| `POST /api/chat` | `{"message": "...", "bot_id": "companion", "source": "phone\|pc", "client_msg_id": "...", "attachments": [...]}` | `{"ok": true, "reply": "...", "bot_id": "companion", "message_id": "...", "trace": [...], "context": {...}}` | 发起对话或执行斜杠指令（如 `/compact`）；执行期间通过 WebSocket `/ws/client` 及 `streams_by_bot` 实时推送流式进度 |
| `POST /api/stop` | `{"bot_id": "companion", "source": "phone\|pc"}` | `{"ok": true, "stopped": true, "bot_id": "companion"}` | 立即中断指定 Bot 在指定节点的流式生成与工具调用循环 |
| `POST /api/context/compact` | `{"bot_id": "companion", "source": "phone\|pc", "force": true}` | `{"ok": true, "compacted": true\|false, "already_compacted": false\|true, "note_name": "compact_YYYYMMDD_HHMMSS.md", "context_id": "ctx-...", "archived_count": 18, "saved_tokens": 14200, "summary": "### 📋 ...", "marker": {...}, "context": {...}}` | **统一委托 `brain._auto_compact_if_needed`**；内置按 `(bot_id, source)` 的异步锁与幂等守卫，压缩过程全程流式广播 `stream` 事件 |
| `GET /api/bots` | `?source=phone\|pc` | `{"bots": [{"id": "...", "name": "...", "home_node": "phone\|pc", ...}]}` | 按节点获取分端专属 Bot 花名册（手机与电脑默认采用不同名字，全局名字唯一） |
| `GET /api/bots/{bot_id}/export` | `?source=phone\|pc` | `{"schema_version": "dsb-bot-v1", "bot": {...}, "messages": [...], "pinned_memories": [...], "vault_notes": {...}}` | 一键导出指定 Bot 完整档案包 (`.dsb-bot.json`) |
| `POST /api/bots/import` | `{"bundle": {...}, "mode": "merge_or_create\|overwrite", "source": "phone\|pc"}` | `{"ok": true, "bot": {...}, "imported_messages": 42}` | 快速导入 `.dsb-bot.json` 档案，无损恢复老 Bot 人设与历史记录 |
| `POST /api/blackboard` | `{"action": "post\|reply\|read\|list\|claim\|complete\|review\|...", ...}` | `{"ok": true, "post": {...}, "woken": [...]}` | 统一事件黑板总线（`<50ms` 瞬时返回，异步 `@mention` 唤醒、闹钟定时与 0-Token `watch` 探针） |
| `GET /api/peek-bots` | `?target=all&limit=5` | `{"ok": true, "bots": [...]}` | 只读窥视全端各 Bot 实时状态、正在调用的工具、流式片段与最新消息 |
| `POST /api/bots` | `{"name": "...", "shape": "star", "color": "#0A84FF", "instructions": "...", "engine": "big"}` | `{"ok": true, "id": "bot_...", "bot": {...}, "bots": [...]}` | 创建新 Bot 并同步生成 `workspace/bots/{id}/IDENTITY.md` |
| `PUT /api/bots/{bot_id}` | `{"name": "...", "shape": "diamond", "color": "#2EC4B6", "instructions": "...", "model": "...", ...}` | `{"ok": true, "bot": {...}, "bots": [...]}` | 更新指定 Bot 的完整配置（含形状、颜色、人设）并写盘 |
| `PATCH /api/bots/{bot_id}/identity` | `{"name": "...", "shape": "...", "color": "...", "soul": "...", "avatar_b64": "..."}` | `{"ok": true, "bot": {...}, "bots": [...]}` | 增量更新 Bot 外观/灵魂档案并同步更新 `bots.json` 与 `state.json` |
| `DELETE /api/bots/{bot_id}` | 无 (`companion` 不可删除) | `{"ok": true, "bots": [...]}` | 删除自定义 Bot |
| `POST /api/messages/edit` | `{"message_id": "...", "text": "...", "bot_id": "...", "source": "phone\|pc"}` | `{"ok": true, "reply": "...", "messages_epoch": 39}` | 编辑历史用户消息并从该点截断重跑回复 |
| `POST /api/messages/reroll` | `{"message_id": "...", "bot_id": "...", "source": "phone\|pc"}` | `{"ok": true, "reply": "...", "messages_epoch": 40}` | 重新生成指定助手回复 |
| `POST /api/phone/command` | `{"op": "ui_tree\|tap_node\|wait\|...", ...}` | `{"ok": true, "result": {...}}` | 下发手机虚拟屏操控指令并等待 `AgentForegroundService` 回传 |
| `POST /api/phone/result` | `{"request_id": "...", "result": {...}}` | `{"ok": true}` | Android 端回传 `phone_workspace` 执行结果与截图 |
| `POST /api/dsh/inbox` | `?drain=1`, Body: `{"peer_id": "...", "status": {...}}` | `{"ok": true, "commands": [...], "phone_online": true, "shared_memories": {...}}` | PC 插件 `18195` 主进程长轮询心跳与拉取待执行的跨端指令 |
| `POST /api/dsh/result` | `{"request_id": "...", "peer_id": "...", "result": {...}}` | `{"ok": true}` | PC 插件回传 DSH 会话调度、本地 Shell/FS 或 CUA 桌面操作结果 |

### 3.3 WebSocket 实时帧契约 (`/ws/client`)
客户端连接 `wss://<server>/ws/client?token=<token>&source=phone|pc` 后，服务端推送的主要事件帧：
1. **流式生成 / 流式压缩帧 (`type: "stream"`)**：
   ```json
   {
     "type": "stream",
     "bot_id": "companion",
     "source": "phone",
     "text": "📦 **[2/3] 正在流式生成上下文归档摘要...**\n\n### 📋 核心进展与上下文摘要\n- 已完成...",
     "thought": "正在提炼核心进展与关键状态 (已生成 420 字)...",
     "tool": "context_compaction",
     "speaker_id": "companion",
     "speaker_name": "白珂"
   }
   ```
2. **新消息落地帧 (`type: "message"` / `type: "proactive"`)**：
   携带完整消息对象 `{"id": "...", "role": "assistant", "kind": "chat|compact_summary|bot_reply", "bot_id": "...", "source": "phone|pc", "text": "...", "created": 1794240123.456}`。

### 3.4 Windows DSH 插件本地桥接契约 (`127.0.0.1:18195`)
- `POST /local/bots`：
  - `op: "list"` -> 返回 `{ ok: true, bots, active_bot_id, messages_epoch }`
  - `op: "switch"` (`{ bot_id }`) -> 切换当前激活 Bot
  - `op: "create_bot"` (`{ name, shape, color, persona }`) -> 创建新 Bot 并同步云端 `POST /api/bots`
  - `op: "update_bot"` (`{ bot_id, name, shape, color, persona, instructions }`) -> **新增契约**：更新本地 `runtimeState.botsRoster` 并落盘，同时调用云端 `PUT /api/bots/{cloudBotId}` 与 `PATCH /api/bots/{cloudBotId}/identity`
  - `op: "delete_bot"` (`{ bot_id }`) -> 删除非主助理 Bot
- `POST /local/infinite-chat/sync`：
  - 返回 `{ ok: true, messages, messages_epoch, streams_by_bot, bot_context_by_id, panorama, bots, active_bot_id, phone_online, shared_memories }`
- `POST /local/infinite-chat/compact`：
  - 调用云端 `POST /api/context/compact` (`{ bot_id, source: "pc" }`)，在压缩期间前端通过 800ms 轮询 `/local/infinite-chat/sync` 的 `streams_by_bot[botId]` 实时展示流式压缩摘要进度。

---

## 四、 核心持久化数据结构与 SQLite 表契约 (Storage & Database Schemas)

### 4.1 `state.json` 核心字段契约 (`/opt/proactive-agent/data/state.json`)
- `messages` (`List[Dict]`):
  每条消息包含：
  - `id` (`str`, 24位 hex)
  - `role` (`"user" | "assistant"`)
  - `kind` (`"chat" | "compact_summary" | "bot_message" | "bot_reply" | "proactive" | "routine"`)
  - `bot_id` (`str`, 如 `"companion"`, `"research"`, `"contrarian"`, `"whalechan"`)
  - `source` (`"phone" | "phone2" | "pc" | "pc2"`)
  - `text` (`str`)
  - `thought` (`str`, 可选)
  - `trace` (`List[Dict]`, 经 `compact_trace_list` 裁剪后的轻量工具轨迹，严禁包含 `_image_b64` 或大段文件内容)
  - `created` (`float`, Unix 时间戳；**铁律**：`compact_summary` 消息的 `created` 必须等于被归档最后一条消息的 `created + 0.001`，并按时间序精确插入，绝不能追加到压缩期间新产生的对话之后)
- `messages_epoch` (`int`): 每次删除、编辑、回滚或插入归档标记时递增，通知三端丢弃本地旧列表缓存并全量对齐。
- `assistants` (`List[Dict]`): Bot 花名册持久化数组，含 `id`, `name`, `shape`, `color`, `instructions`, `model`, `engine`, `primary`。
- `bot_summaries` (`Dict[str, str]`): 按 `"{bot_id}:{source}"` 键存储的最新归档摘要 Markdown。

### 4.2 SQLite `memory_vault.db` 表结构契约
- **`tool_contexts` 表**（存储归档的原始对话全文与大型工具输出，供 `search_archived_context` 检索）：
  - `context_id TEXT PRIMARY KEY` (如 `"ctx-e724534c"`)
  - `tool_name TEXT` (压缩归档固定为 `"context_compaction"`)
  - `bot_id TEXT`
  - `summary TEXT` (结构化 Markdown 摘要)
  - `full_text TEXT` (**注意：列名为 `full_text`，严禁误写为 `raw_text`！**)
  - `token_count INTEGER`
  - `created_at REAL`

---

## 五、 六大核心问题根因、微模块实现与防 Bug 校验矩阵

### 5.1 问题 1：上下文连续压缩两次、无进度反馈、把压缩当成回答新问题
- **根因**：
  1. `/api/context/compact` 与 `brain._auto_compact_if_needed` 缺少按 `(bot_id, source)` 的互斥锁，且未检查房间末尾是否已经是 `compact_summary`。
  2. 原压缩调用 `silent_complete_fn` 同步阻塞 60~120 秒，期间无任何 `stream` 事件广播。
  3. 原压缩直接把 `[user]: ...` 裸文本作为 `role: "user"` 发送，末尾恰逢用户提问时 LLM 直接回答了问题。
- **微模块 `server/core/compaction_service.py` 强制防错机制**：
  - `get_compaction_lock(bot_id, source)`：同一房间并发触发压缩时立即拦截并返回状态提示。
  - `check_already_compacted(room_msgs, force)`：若最后一条 `compact_summary` 后新增消息 `< 2` 条，直接返回 `already_compacted: True`。
  - `build_compaction_messages(raw_transcript)`：强制使用 `<transcript_to_summarize>...</transcript_to_summarize>` 包裹，并在结尾追加防回答指令。
  - `execute_streaming_compaction(...)`：全流程分 `[1/3]`、`[2/3]`（流式实时输出摘要正文）、`[3/3]` 三个阶段回调 `on_stream` 广播到前端。

### 5.2 问题 2：Bot 外形（Shape）与颜色（Color）调整不生效
- **根因**：
  1. Android `NativePulseIcons.kt` 硬编码 `bot.id == "companion" -> "flower"` 及强制覆盖 `companion` 颜色，且缺少 `oval`/`triangle`/`whale`/`whalechan` 路径。
  2. Android `NativeBotEditorPanel` 顶部预览头像绑定了未修改的 `targetBot`，且没有颜色选择行。
  3. PC 插件 `client.js` 第 566~570 行同样硬编码覆盖了 `companion`/`research`/`contrarian`/`whalechan` 的 `shapeKey` 与 `fillColor`，且 `index.js` 缺少 `update_bot` 接口。
- **三端统一修复契约**：
  - Android `BotAvatarView.kt` + `NativePulseIcons.kt` + `NativePulseScreen.kt`：补齐 11 种形状与 9 色预设行，彻底移除 `bot.id == "companion"` 硬编码覆盖，顶部预览头像实时绑定 `editBotShape` 与 `editBotColor`。
  - PC 插件 `client.js` + `index.js`：`renderBotAvatar` 严格优先使用 `b.shape` 与 `b.color`，新增内联外观编辑器面板与 `/local/bots` (`op: "update_bot"`) 云端同步。

### 5.3 问题 3：虚拟屏桌面显示不对，底下九宫格点开应用排布错乱
- **根因**：ColorOS 15 在虚拟屏 (`Display > 0`) 回系统桌面时加载横屏副屏桌面 (`SecondaryHomeActivity`)，其九宫格在竖屏虚拟屏上严重变形；同时 `FloatingVirtualDisplay.kt` 原内置应用列表为简陋单列且存在 `lastRenderedAppsKey` 缓存不刷新 Bug。
- **修复契约 (`VirtualDisplayAppDrawer.kt` + `FloatingVirtualDisplay.kt`)**：
  - 新建 `VirtualDisplayAppDrawer.kt`：提供原生 4 列等宽高清图标网格 + 顶部实时搜索框 + `LruCache` 图标缓存。
  - 点击虚拟屏底部导航栏 `"⌂ 桌面"` 或右上角 `"九宫格"` 按钮时直接打开该 4 列原生应用桌面，点击任意图标立即在虚拟屏启动应用并自动切回画面。

### 5.4 问题 4：电脑端前端 (`D:\dsh-baike-plugin\client.js`) 独创界面难看且全屏遮死 DSH 工作区
- **根因**：`#dsh-baike-main-view` 使用 `left: 260px; right: 0; top: 38px; bottom: 0`（及在 `mainHost` 内 `inset: 0`）把整个 DSH 主工作区 100% 盖住。
- **修复契约 (`client.js`)**：
  - 改为**右侧停靠式手机同款伴侣抽屉** (`position: fixed; right: 0; top: 38px; bottom: 0; left: auto; width: 400px;`)，支持左边缘拖拽调宽 (`340px ~ 560px`)。
  - 打开时仅给 `mainHost` 设置 `margin-right: 400px`（收起时恢复 `0px`），左侧 DSH 原生会话与代码工作区 **100% 完整露出、随时可交互**。
  - 界面内部 1:1 复刻手机端 `NativePulseScreen` (Telegram Night `#0E1621`)：紧凑顶栏、Bot 切换胶囊、上下文/缓存进度条、内联 Bot 外观抽屉、手机同款气泡与底部斜杠输入栏。

### 5.5 问题 5：定时任务 `（静默...）` 冒泡到聊天界面 & 内联伪 XML 工具调用泄露
- **根因**：`run_scheduled()` 未拦截括号包裹的静默文本；`llm_client.py` 仅解析了 DSML 格式而未覆盖 `<function=NAME><parameter=K>V</parameter></function>` 等变体。
- **修复契约 (`server/core/xml_tool_parser.py`)**：
  - `is_silent_proactive_output(text)` 拦截所有括号/方括号静默声明，在 `run_scheduled()` 与 `curiosity()` 中直接静默丢弃。
  - `extract_inline_tool_calls(text)` 与 `strip_inline_tool_markup(text)` 统一解析并剥离全部 5 类内联工具调用标签。

### 5.6 问题 6：手机虚拟屏单步效率与 Android 76% CPU / 737K 缺页中断风暴
- **修复契约**：
  - `phone_controller.py`：`op="wait"` 等待结束后自动采集并返回 `ui_tree` + 标注截图；移除 `ui_tree` 冗余 `nodes` 数组；转场空窗自动 `550ms` 重试。
  - `SecurePrefs.java`：内存缓存 `cachedSecretKey` / `cachedEncryptedRaw` / `cachedJsonStr` 及 `readFast()`；`RootPhoneHarness.kt`：`tap_node` `240ms` 转场防连击与零拷贝截图日志；`PulseAccessibilityService.kt`：`80ms` 节流与转场轮询。
