# Pulse Bot · Proactive Personal Agent v2.6.2

一个自建的外部前额叶与多 Bot 协同系统：具备 **Windows 11 风格 1C1G 隔离虚拟云电脑**、**Root 手机独立虚拟屏**、**200K 自动上下文压缩**、**Sleep-Time Compute 睡眠记忆整理** 与 **聊天流实时内嵌虚拟电脑监视器**。

**Android APK**：[下载 Pulse Bot (v2.6.2)](http://216.23.82.196/proactive-agent/download/proactive-agent.apk)  
**日本节点**：[Proactive Agent](http://216.23.82.196/proactive-agent/)  

---

## 核心特性 (v2.6.2)

### 1. ❖ Windows 11 风格 1C1G 隔离虚拟云电脑 (`cloud_desktop`)
- **极致轻量与零 Chromium 负担**：专为 1 核 1GB (`CPUQuota=100%`, `MemoryMax=1024M`) 打造，彻底移除臃肿卡顿的 Chromium，全系采用毫秒级启动的原生组件：
  - **Edge Lite (`NetSurf`) & `Dillo`**：~20–25MB 内存极速图形网页浏览器
  - **Windows Terminal (`XTerm`)**：预置独立隔离的 Python 虚拟环境 (`~/venv`) 与用户级包目录 (`~/.local/bin`)，支持 Agent 自由执行 `pip install`、`python3 -m venv`、`git`、`gcc` 编译运行代码，且命令与输出实时投射在桌面终端窗口中
  - **File Explorer (`PCManFM`) & Notepad (`Mousepad`)**：图形化文件管理器与代码编辑器
  - **Windows 11 居中亚克力任务栏 (`tint2`)**：支持底部多窗口状态显示、居中图标与双行时间日期
- **严格虚拟边界隔离 (Landlock ABI v6 + Safe Web Proxy)**：
  - 拥有独立的持久化存储空间 (`~/workspace`、`~/venv`、`~/.local`、`~/tmp`)，可访问公网 PyPI / GitHub 安装环境，但严格禁止越界访问宿主机其他目录、内网或宿主服务。
- **聊天界面内嵌实时虚拟电脑 (`InlineCloudDesktopMonitor`)**：
  - 当任何 Bot 调用 `cloud_desktop` 操控云电脑时，后端通过 WebSocket 0 延迟推送实时帧 (`cloud_desktop_frame`)，前端对话气泡内自动展开 `1280×800` (16:10) 实时虚拟电脑小窗，支持直接在聊天窗口点按触控（单击/双击/长按右键，1-RTT 极速反馈）或一键放大全屏接管。

### 2. 多 Bot 独立并发与防串台架构
- **Per-Bot 独立执行锁 (`bot_locks[bot_id]`) 与独立流 (`streams_by_bot[bot_id]`)**：
  - 主助理（如 `白kawa`）、`研究助手` 与自定义 Bot 各自拥有独立并发上下文与流状态，给任意助手发消息均立即独立响应，绝不互相阻塞或串台。
- **跨 Bot 协同与报错自愈 (`send_message` / `manage_bot` / `list_models`)**：
  - 支持 `@Bot` 群组式派发任务，遇到模型报错自动附带可用模型列表一键自愈。

### 3. 前沿记忆与额度节省架构 (Token Efficiency & Frontier Memory)
- **100% 静态 System Prompt 前缀缓存命中**：移除每轮变动的秒级时间戳，确保多步工具循环 100% 命中上游 Prefix Cache。
- **200K 自动上下文压缩 (`AUTO_COMPACT_TOKENS = 200,000`)**：保留最近 `64,000` Tokens 对话，自动将早期历史浓缩归档至 `memory_vault/timeline/`。
- **Sleep-Time Compute 睡眠期记忆整理**：借鉴 Letta 与 A-MEM (Zettelkasten) 理论，在用户空闲 $\ge 20$ 分钟时后台静默提炼近期新增事实并生成双链原子笔记 (`memory_vault/notes/*.md`)。
- **连招宏 (`op="batch"`) 与 SHA-256 视觉缓存**：`phone_workspace` 与 `cloud_desktop` 均支持单轮 `op="batch"` 批量执行多步动作，大幅降低模型往返轮次与额度消耗。

---

## 目录结构

- `server/`：FastAPI 云端主脑 (`brain.py`、`harness.py`、`main.py`) 与 1C1G 隔离虚拟云电脑服务 (`computer_desktop.py`、`computer_shell.py`、`computer_security.py`)
- `android/`：Kotlin Jetpack Compose 原生 Android 客户端 (`NativePulseActivity.kt`、`NativePulseScreen.kt`)与 Root 独立虚拟屏引擎
- `dist/`：发布元数据 (`release.json`、`SHA256SUMS.txt`)
