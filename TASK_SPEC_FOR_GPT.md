# 个人共生 Agent（Proactive Personal Agent）全栈实现与部署任务书（Master Spec）

> 最新覆盖（2026-11-03）：用户明确要求融合为自己的产品，学习 Mika 的底层工具并实现 Root 虚拟屏能力，不嵌入完整 Mika 应用或照搬其页面。当前方向与验收以 [CURRENT_PRODUCT_DIRECTION.md](CURRENT_PRODUCT_DIRECTION.md) 及用户最新消息为准；既有日本节点安全红线与电脑安静模式约束继续生效。

## 0. 绝对红线与安全约束（MUST OBEY）
1. **指定云端部署目标：日本服务器（`216.23.82.196` / `vps-jp`）**
   - 必须部署在 **日本服务器（`216.23.82.196` / `vps-jp`）** 上！（香港和北京内存/磁盘都很满了，而日本机有 6GB 内存、32GB 可用磁盘，且自带 `nginx` 80 端口公网可达与 `/opt/security-runtimes/node-v22.23.4` / Python3）。
   - 连接方式：参见 `C:\Users\uiwcv\Documents\Codex\2026-10-22\d-bubuki-vps-server-info-md\outputs\private-access\ssh_config`（如果本机直连 `vps-jp` 超时，使用 `ssh -J vps-us-198 -F ... vps-jp` 秒通！）。
2. **日本节点（`216.23.82.196` / `vps-jp`）绝对禁令**：
   - **严禁触碰任何与 Claude Code / BB 浏览器（`bb-jp-*`, `jp-chrome`, `xvfb-jp`）或 Docker 容器相关的服务、目录、网络或代理配置**（Docker 里面漏 IP 容易封号！）。
   - **绝不使用 Docker 部署本项目**！必须直接在宿主机独立目录 `/opt/proactive-agent/` 下使用轻量级 Python 虚拟环境（`venv`）+ 独立 `systemd` 服务（`proactive-agent.service`）运行，并仅在日本机现有的宿主 `nginx`（TCP 80 已公网开放）下挂载一个独立非冲突的子路径（如 `/proactive-agent/` 转发到本机 `127.0.0.1:18190`，同时提供 `/proactive-agent/download/proactive-agent.apk` 公网直链下载），绝不改动 `sing-box`、`MyHeroes`、`bb-jp-*` 等任何既有服务与防火墙/路由规则！
   - 如果需要 SMTP 发信配置，支持在 App 内直接填写 SMTP/收件邮箱并同步到服务端，或使用服务端免依赖发信逻辑。

---

## 1. 交付总目标（明早验收标准）
用户明早醒来需要看到：
1. **一个直接可点击下载的 Android APK 链接（公网 HTTP URL）**，以及本地编译产物路径 `D:\bubuki\proactive-personal-agent\dist\proactive-agent.apk`。
2. **Android App 安装打开后**：
   - 界面干净现代，可直接填写：
     - **Server URL**（已预填默认部署好的 VPS 地址，开箱即用！）
     - **Small Brain API Base URL / API Key / Model**（常驻好奇心小脑，用于高频主动追问、整理 Markdown、嗅探任务，也可一键填入香港/北京现有聚合入口或自定义 OpenAI 兼容 Key）
     - **Big Brain API Base URL / API Key / Model**（主理人大脑 + 视觉 VLM，用于复杂 Harness 执行与虚拟屏幕操控）
     - **用户个人邮箱 & SMTP 配置**（用于失联时的 Stage 2 夺命邮件轰炸）
   - 点击 **「保存并同步到云端服务器（Sync & Start Harness）」** 按钮后，配置实时通过 API/WebSocket 同步到 VPS 服务端，服务端 **Proactive Agent Harness 立即启动运转**！
3. **服务端 Harness（参考 Pi Coding Agent `pi-mono` 极简哲学：Read / Write / Edit / Bash + Markdown Memory Vault + Proactive Heartbeat）**：
   - **Curiosity Loop（好奇心闲时追问循环）**：定时或根据状态主动向手机端发消息询问（如“现在在干嘛呢？”、“上次说的那个卡在哪了？”），顺着用户回答追问细节，直到上下文完整或用户点击/回复“别吵（触发烦人度调节阀 Annoyance Valve）”。
   - **闲时信息搜集（Idle Curiosity Research）**：根据闲聊搜集到的用户痛点或兴趣，在后台调用自身 Harness（`bash`、`web_search`/`fetch`、`read`/`write`/`edit`）主动搜集整理正确的信息，或者试探性干完第一步活，然后捧着结果问用户：*“喂，我试着帮你整理/弄了这个，是你需要的吗？以后要我接着干吗？”*
   - **Obsidian 风格 Markdown 记忆库（`memory_vault/`）**：所有记忆透明存储为 `.md` 文件（`INDEX.md`, `profile/user_traits.md`, `profile/annoyance_valve.md`, `timeline/YYYY-MM-DD.md`, `tasks/sniffed_opportunities.md`, `tasks/active_deadlines.md`, `playbooks/routines.md`），App 内置「记忆库（Vault）」标签页可实时浏览和编辑这些 Markdown！
   - **三级反逃避催单阶梯（3-Stage Escalation Ladder）**：
     - **平时状态**：手机桌面**绝不显示常驻悬浮球**，零打扰、超省电后台 WebSocket/轮询监听。
     - **Stage 1（T+0m）**：发送标准 Android 通知（带快捷动作或点开直接回复/点“别吵延后1小时”）。
     - **Stage 2（未读超时 / 强催单）**：VPS 自动向用户邮箱发送人格化催单邮件（包含当前为你准备好的草稿/问题摘要），并在 App 内提供「立即测试邮件轰炸」按钮以便明早一键验收。
     - **Stage 3（终极拦截）**：当消息超时未读或触发死线，且手机亮屏时，Android 端动用 `SYSTEM_ALERT_WINDOW` 权限直接拉起**全屏/半屏流氓催单悬浮窗（Aggressive Overlay）**盖在屏幕最顶层，必须回答或确认才能关闭（App 内同样提供「一键测试 3 级霸屏催单」按钮以便明早验收）。
   - **Android 后台虚拟屏幕引擎（VirtualDisplay + Root/Shizuku 三级权限自适应架构）**：
     - 包含 `PrivilegeManager`（自动检测 Tier 0 Root `su` / Tier 1 Shizuku / Tier 2 标准模式）与后台 `VirtualDisplay` 创建、截图上传云端 VLM 算坐标 `(x, y)` 并指定 `displayId` 注入点击的模块实现。

---

## 2. 项目目录结构与技术栈要求
请将所有代码创建在 `D:\bubuki\proactive-personal-agent` 下：

```text
D:\bubuki\proactive-personal-agent\
├── README.md                       # 完整架构说明、部署文档、APK 下载链接与快速验收指南
├── server\                         # VPS 云端大脑与 Pi-style Harness (Python FastAPI + WebSocket + Asyncio Scheduler)
│   ├── main.py                     # FastAPI 入口：REST API (/api/config, /api/chat, /api/vault, /api/test-escalation, /download/proactive-agent.apk) + WebSocket (/ws/client)
│   ├── harness.py                  # Pi-style 极简强悍 Agent Harness：支持 bash, read_file, write_file, edit_file, web_fetch/search 工具循环
│   ├── brain.py                    # 大小脑双模型调度器：Curiosity 小脑（追问/写日志/嗅探机会/闲时搜集）+ Execution 大脑（调用 Harness 干活/固化 routine）
│   ├── escalation.py               # 三级催单状态机：未读计时 -> Stage 1 WS Push -> Stage 2 SMTP Email 发送 -> Stage 3 Overlay 强弹窗指令
│   ├── vault_manager.py            # Obsidian Markdown 记忆库初始化与读写引擎
│   ├── memory_vault\               # 初始 Markdown 记忆库模板
│   └── requirements.txt
├── android\                        # 原生 Android 客户端项目 (Kotlin/Java, 使用本机 C:\Users\uiwcv\AppData\Local\Android\Sdk 编译)
│   ├── app\
│   │   ├── build.gradle
│   │   └── src\main\
│   │       ├── AndroidManifest.xml # 声明 INTERNET, FOREGROUND_SERVICE, POST_NOTIFICATIONS, SYSTEM_ALERT_WINDOW, BIND_NOTIFICATION_LISTENER_SERVICE
│   │       ├── java\...\
│   │       │   ├── MainActivity.kt         # 四合一现代化界面：1. 对话与催单验收  2. API Key & 服务器同步设置  3. Obsidian 记忆库实时查看  4. Root/VirtualDisplay 与系统状态
│   │       │   ├── AgentForegroundService.kt # 平时零悬浮球静默保活 WebSocket 监听，触发 Stage 1 通知与 Stage 3 SYSTEM_ALERT_WINDOW 霸屏拦截窗
│   │       │   ├── OverlayController.kt    # Stage 3 半屏/全屏人格化流氓催单悬浮窗（WindowManager TYPE_APPLICATION_OVERLAY）
│   │       │   └── VirtualDisplayEngine.kt # Tier 0 Root (su) / Tier 1 Shizuku / Tier 2 后台 VirtualDisplay 虚拟屏幕与 input 注入引擎
├── dist\
│   └── proactive-agent.apk         # 编译并签名好的可安装 APK
└── scripts\
    └── deploy_vps.py               # 自动部署 server 与 APK 到 VPS 并启动 systemd 服务的脚本
```

---

## 3. 执行步骤（请一气呵成全部完成，不要停下反问用户！）
1. **第一步：搭建并测试 `server/` 云端大脑与 Pi-style Harness**
   - 实现完整的 FastAPI 服务端，支持通过 `/api/config` 动态热更新 API Key（Small Brain / Big Brain / SMTP 邮箱配置），并持久化保存到 `config.json`。
   - 内置预配置回退：如果用户在 App 里还没填 Key 或点一键测试，默认也可支持调用用户现有的聚合网关，或者使用用户在 App 填入的任意 OpenAI 兼容 API Key。
   - 实装 Pi-style Harness 工具集（`read_file`, `write_file`, `edit_file`, `run_bash`, `web_fetch`）与后台主动心跳调度器（Curiosity Heartbeat + Unread Escalation Monitor）。
   - 编写自动化单元测试确保 `/api/config`、`/api/chat`、`/api/vault`、`/api/trigger-curiosity`、`/api/test-escalation`、`/ws/client` 全部 200 OK。
2. **第二步：开发并编译高颜值旗舰级 `android/` 客户端 APK（绝不能做成简陋丑陋的 Demo！）**
   - **视觉与交互设计铁律（Crafted Dark UI / Linear & Grok Bot & Arc 级审美）**：
     - 严禁使用丑陋的原生灰白表单或生硬排版！必须打造**极具高级感的深色玻璃拟态 / 极客流体界面（Obsidian Dark `#0B0D12` 底色 + 柔和微光卡片 `#131722` + 1px 细腻描边 `#232A3B` + 电光紫/赛博青渐变点缀 `#7C5CFF` / `#00E5FF` + 圆润几何卡片 `20dp` 圆角 + 细腻呼吸状态灯与流畅过渡动画）**。
     - **顶部实时连接与心率状态栏（Live Pulse Header）**：展示云端 Harness 在线呼吸灯、当前活跃脑区（Curiosity Idle / Deep Research / Escalation Level）、烦人度阀门状态（可一键切换「专注免打扰 1h」/「好奇心全开」）。
     - **底部丝滑四栏导航（Modern Bottom Navigation）**：
       1. **「共生对话（Pulse Chat）」**：
          - 区分「好奇心小脑追问气泡（带灵动标签）」与「主理人 Harness 成果卡片（可展开查看它替你搜集的信息、执行的命令、修改的 Markdown，并附带优雅的『正是我要的，以后接着干！』与『方向不对』微交互按钮）」。
          - 顶部设有一排精致的胶囊快捷操作栏（Pill Actions）：`✨ 触发好奇心追问`、`🔍 闲时信息搜集试探`、`🚨 演练三级霸屏催单`、`💤 别吵我 1 小时`。
       2. **「透明记忆库（Obsidian Vault）」**：
          - 像移动端 Obsidian / Notion 一样美观的文件树卡片 + Markdown 实时高亮预览与在线编辑保存（随时查看 `INDEX.md`、今日时间轴 `timeline/`、嗅探到的代劳机会 `sniffed_opportunities.md`、常驻手册 `routines.md`）。
       3. **「引擎与算力同步（Brain & Sync）」**：
          - 分组清晰的现代化配置卡片：
            - **VPS 云端节点**：默认预填 `http://216.23.82.196/proactive-agent`，带实时延迟 Ping 检测徽章。
            - **常驻小脑（Small Brain - Curiosity）**：Base URL、API Key（带眼睛显隐切换）、Model 名称，支持一键填入预设网关。
            - **主理人大脑（Big Brain - Harness & VLM）**：Base URL、API Key、Model 名称。
            - **Stage 2 夺命邮件配置**：收件邮箱、SMTP 选项与「发送测试催单邮件」按钮。
            - 底部醒目的渐变主按钮：**「⚡ 保存并同步至云端启动 Harness」**，点击后有进度反馈并实时唤醒日本服务器上的常驻循环。
       4. **「特权与虚拟屏（Root & CUA）」**：
          - 仪表盘式展示三级权限状态卡片（Tier 0 Root `su` 满血模式 / Tier 1 Shizuku / Tier 2 悬浮窗与通知监听）、一键跳转授权开关、后台 `VirtualDisplay` 虚拟屏幕状态预览与坐标注入测试面板。
     - **Stage 3 全局悬浮窗（Aggressive Overlay）设计**：
       - 当触发第三级催单时，弹出的全屏/半屏拦截窗也必须是**高颜值暗黑毛玻璃危机卡片（带脉冲红/紫警示光晕、Agent 催单质问文案、为你准备好的草稿预览，以及必须点击的『我看完了，这就拍板』/『再宽限 15 分钟』大按钮）**，平时无事时桌面 0 悬浮球、0 打扰。
   - 检查本机 `C:\Users\uiwcv\AppData\Local\Android\Sdk` 与 `java` 环境，编写完整 Android 工程并用 Gradle 编译生成已签名 APK，复制到 `D:\bubuki\proactive-personal-agent\dist\proactive-agent.apk`，**确保 100% 编译通过、无闪退**。
3. **第三步：部署到日本服务器（`216.23.82.196` / `vps-jp`）并提供直接下载链接**
   - 通过 SSH 将 `server/` 与 `proactive-agent.apk` 部署到 **日本服务器 `216.23.82.196`（`vps-jp`，若直连超时则加 `-J vps-us-198`）** 的 `/opt/proactive-agent/`。
   - **严禁触碰日本机上的 Docker、Claude Code 或 `bb-jp-*` 服务**！直接在宿主机创建 Python `venv`，配置独立 `systemd` 常驻服务 `proactive-agent.service`（监听 `127.0.0.1:18190`）。
   - 在日本机现有的宿主 `nginx`（TCP 80 已公网开放）下挂载独立子路径 `/proactive-agent/` 反代到 `127.0.0.1:18190`（支持 HTTP 与 WebSocket Upgrade），确保：
     - 公网 APK 下载链接 `http://216.23.82.196/proactive-agent/download/proactive-agent.apk` 实测返回 `200 OK` 且文件大小完整！
     - Android App 默认预填公网 Server URL `http://216.23.82.196/proactive-agent`，用户明早打开 App 填好 API Key 点同步即可直接联通日本云端 Harness！
4. **第四步：写入最终验收报告**
   - 将公网 APK 下载链接、服务端公网地址、本地文件路径、测试结果汇总写入 `D:\bubuki\proactive-personal-agent\DELIVERY_REPORT.md`。
