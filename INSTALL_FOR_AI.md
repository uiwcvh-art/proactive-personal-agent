# 🐳 DeepSeek Claw (`DS-Claw`) · AI 零配置全自动部署指南 (`INSTALL_FOR_AI.md`)

> **给用户的极简用法**：
> 你不需要手动敲复杂的命令，只需要把下面这句话复制发给你的 **DeepSeek Harness (`dsh`) / Claude Code / Codex**：
> ```text
> 请阅读 D:\bubuki\proactive-personal-agent\INSTALL_FOR_AI.md 并帮我全自动部署好 DeepSeek Claw（包括复用我现有的模型密钥、挂载电脑端 dsh 插件与开机自启哨兵，以及配置云端 1C1G 低内存模式）。
> ```

---

## 🤖 给 AI Agent 的自动执行指令 (Instructions for AI Assistant)

当你（AI Agent）收到用户要求部署 **DeepSeek Claw (`DS-Claw`)** 时，请按顺序自动执行以下步骤，无需反复向用户索要已存在于本机的配置：

### Step 1: 自动复用本机 `~/.dsh` 模型与 API Key（零重复输入）
1. 检查用户的 `~/.dsh/profiles/desktop/cordis.patch.yml` 或 `D:\DeepSeek-Harness\data\profiles\web\cordis.patch.yml`。
2. 如果服务端环境变量尚未配置 LLM API Key，自动从上述配置或系统环境变量中读取已配置的 `baseURL`、`model` 与 `apiKeyEnv`（例如 `DS_API_KEY`），绝不让用户二次手动输入。

### Step 2: 一键挂载电脑端 `DeepSeek Harness` 插件与轻量开机哨兵 (`<15MB` 内存)
在电脑端直接运行：
```powershell
node D:\bubuki\proactive-personal-agent\dsh-plugin\setup.mjs
```
该脚本会自动完成：
1. 生成 `dsh-plugin/bridge_config.json`，自动绑定云端大脑与鉴权 Token。
2. 将 `dsh-deepseek-claw`（含社区版**鲸鱼娘 Whale-chan / 首席白米饭品鉴官**与**白珂**双主题、Grok-Bot 风格会眨眼的大鲸鱼图标、跨端同事对讲二级专窗、1280×800 云电脑工作站、本机 CUA 操控台、Memory Vault 二级编辑器）挂载进 `~/.dsh/profiles/desktop` 与 `D:\DeepSeek-Harness\data\profiles\web`。
3. 在 Windows 启动目录注册无窗口轻量级开机自启哨兵 `DeepSeek-Claw-Sentinel.vbs`（运行 `dsh_sentinel.py`，常驻内存 `<15MB`，收到手机端 Bot 发来的协同消息时按需唤醒 `DeepSeek Harness`）。
4. 输出手机 App 一键粘贴配对码 `DSCLAW1:...`。

### Step 3: 1核1G (`1C1G`) 轻量云服务器低内存部署模式 (Low-RAM VPS Optimization)
如果用户的云端部署在 **1核 1GB 内存 (`1C1G`)** 的轻量级 VPS 上，请按以下步骤开启低内存优化：
1. **自动配置 1.5GB Swap / ZRAM**（当物理内存 `<= 2048 MB` 时）：
   ```bash
   if [ ! -f /swapfile ]; then
     sudo fallocate -l 1536M /swapfile || sudo dd if=/dev/zero of=/swapfile bs=1M count=1536
     sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
     echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
   fi
   ```
2. **启用 `DS_CLAW_1C1G=1` 低内存桌面与浏览器 5 分钟自动休眠**：
   - `server/computer_desktop.py` 会自动检测系统内存 `<= 2048 MB`（或 `DS_CLAW_1C1G=1`），自动跳过不必要的 `pcmanfm --desktop` 壁纸进程（立省 `~100MB` 内存）。
   - `server/cloud_os/agent_browser.py` 已内置 **On-Demand Browser Hibernation（按需唤醒 + 5 分钟空闲自动休眠）**：
     - 仅当收到 `cloud_desktop` 工具调用或面板操作时，才以 `--renderer-process-limit=1 --js-flags=--max-old-space-size=192 --disable-dev-shm-usage --disable-gpu` 启动 Chromium（峰值仅 `~250MB`）。
     - 连续 **5 分钟 (`300s`) 无操作**后自动关闭 Chromium 进程释放内存，同时完整保留 `--user-data-dir` 中的登录 Cookie 与会话状态，使 1C1G 服务器平时空闲总内存稳定在 `~220MB`！

### Step 4: 跨端协同架构自检 ("手机控制手机，电脑控制电脑，互发消息协同")
部署完成后，请运行自检命令验证连通性：
```powershell
D:\bubuki\proactive-personal-agent\server\.venv\Scripts\python.exe D:\bubuki\proactive-personal-agent\dsh-plugin\dsh_sentinel.py --self-check
```
并将生成的 `DSCLAW1:...` 一键配对码展示给用户。
