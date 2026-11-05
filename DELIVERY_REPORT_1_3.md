# Pulse Bot 1.3.0 交付与验收

验收时间：2026-11-03T04:29:53+08:00（北京时间）。

APK：[公网下载](http://216.23.82.196/proactive-agent/download/proactive-agent.apk)。Android 12 / API 31 及以上；包名 `dev.bubuki.proactive`，版本码 4，可覆盖同证书旧版。

## 文件与签名

| 项目 | 实测 |
|---|---|
| APK 字节数 | 92,431,758 |
| APK SHA-256 | `5cdd288315a185d257d58aaa7dff5bbba5c4eabc633acfe35a546587cd73f3ca` |
| 签名证书 SHA-256 | `879d8a5eb7e55e054925d1030f350b3441276e46bef549214001b2bfc4672329` |
| 公网下载 | `curl -I` 为 `200 OK`；完整 GET 的长度与 SHA-256 均等于本地 APK |
| 源码 ZIP | 35,954,836 字节；`0b012a5e256e419f2c54d36eba4dc9dd0a00d43eb313f681bfb78a3ad3ca7232` |
| 原生组件对应源码 | 109,492,252 字节；`29912d51c323e3dcffc4e1cca0fee54a8e4da5860612a6d4d263eb54b3d17025` |

本地 APK：`D:\bubuki\proactive-personal-agent\dist\proactive-agent.apk`。
本地源码：`D:\bubuki\proactive-personal-agent\dist\proactive-personal-agent-source.zip`。
[原生组件对应源码下载](http://216.23.82.196/proactive-agent/download/mika-corresponding-source.tar.gz) 的 HEAD 返回 200、长度完整，上传前后摘要一致。该源码包未做第二次完整公网下载，勿与 APK 完整 GET 验证混同。

## 已实现

- 首页可直接进入云电脑或此手机；对话也可选择对话、文件执行、云电脑或手机。助手、角色、工具、例行任务、Markdown 记忆、邮箱、节奏、外观和权限均保留二级入口。
- Mika Apache-2.0 原生应用与核心直接集成为两个源码模块，固定上游 `e9daf53d6834b236e713c6a62c3a095338eb7f95`，参考官方 v0.1.33。模型、手机连接、记忆/技能、浏览器、计划、设置均为实际原生页面。原有账号、工具及设置实现随源码保留，界面不是空链接。
- 模型仅在用户明确点击时从本机已有配置复制；本机凭据、任务草稿和外观均有实际存储路径。Mika 接受任务后才清除对应草稿。默认没有常驻悬浮胶囊。
- 云电脑复用 Browser Use 0.13.14：可查看真实桌面、接管、中文输入、导航、补充任务、交还、停止、下载文件；Agent 可使用浏览器和受限 Bash。资料库支持带摘要的分块上传，以及原生 Android 下载目录保存。无模型时可人工接管；AI 任务要先填写自己的执行模型。
- 原 Small Brain、Pi-style Harness、Markdown Vault、确认固化、例行任务、三级通知/邮件/悬浮卡与加密 WebSocket 保留。

## 部署边界

- 日本宿主机 `/opt/proactive-agent/`，原 `proactive-agent.service` 与 venv，主 API `127.0.0.1:18190`。
- 新云电脑专用账号 `proactive-desktop`、`/opt/proactive-agent/cloud-computer/desktop-venv`、`proactive-desktop.service`。Xvfb `:91`、新建 Chromium 与专用 profile；未复用现有浏览器资料。
- 私有控制 18193、专属网页代理 18194、CDP 都限回环。已有 nginx `/proactive-agent/` 负责公网 HTTP / WebSocket，本轮未修改 nginx、既有代理、路由、防火墙、Docker 网络或容器。
- Chromium 沙箱实际保留，并有 Landlock、独立临时目录、其他用户进程隐藏和资源限额。Bash 仅能访问任务工作区，实测不能读取这台新浏览器的 profile，也不能连接宿主 API。
- 受保护服务本轮部署前后快照逐项一致，共 10 项；首次云电脑部署至最终快照一致性：true。只重启了本项目自己的服务。
- Windows 未执行任何性能档位修改；最终回读 `quiet`。任务创建的安卓模拟器与本地预览服务器已停止，未关闭用户其他应用。

## 实测证据

1. 后端：29 项通过，只有依赖原有弃用提示。
2. 同一签名 APK 的固定 SHA-256 验收：页面/催单、动效、Mika、本机 APK 至真实公网电脑四项均通过。没有在验收期间改换 APK。
3. 页面回归包括 24 个页面与状态、记忆编辑回读、工具输出、角色保存、例行任务和原生三级提示确认。
4. Mika：助手签名交接为 uid 2000；创建独立 displayId、取得帧并实际注入点击；真实 Mika 模型循环连接免费隔离 HTTP 桩；791,321 字节经加密上传、原生下载后逐字节相等。
5. 日本机真实进程：坐标点击、中文及 emoji 输入、公开网页导航、768,034 字节浏览器下载回读摘要一致；Browser Use 真实循环调用免费模型桩并收到截图，暂停时等待、接受补充要求、交还后继续、停止后不再执行。
6. 云电脑上传 727,900 字节通过摘要校验；Agent 实际调用 Bash 处理该文件，并验证 profile 与 TCP 隔离。
7. Android 至公网：配对加密状态/截图/文件接口 200，未配对电脑接口 401；真实接管、768,034 字节原生 MediaStore 下载摘要一致；六个 Mika 原生二级页面实际打开并验证跟随所选深色。
8. 测试没有消费付费模型额度、发送真实 SMTP 邮件、操作用户真机或改写生产模型配置。

动效实测为独立位置数量，不等于 FPS：

| 动作 | 可辨识位置 |
|---|---:|
| page-push | 9 |
| page-return | 14 |
| edge-return-cancel | 41 |
| edge-return-commit | 69 |
| sheet-open | 18 |
| sheet-drag-dismiss | 36 |

验收还检查手势取消/提交、面板拖动、后台中断、键盘、旧消息节点保留、工具详情和减弱动画。冷启动首个切页仍有停顿记录，不能宣称恒定 60fps。

## 查看实际运行结果

- `outputs/ui-1.3-preview.png`：当前界面预览。
- `outputs/ui-1.3.mp4`、`outputs/motion-1.3.mp4`、`outputs/mika-local-1.3.mp4`：安卓真实录屏。
- `outputs/screenshots-1.3/`：云端、手机原生及原有页面截图。
- `outputs/*acceptance.json`：对应原始验收摘要。

这些文件位于 `C:\Users\uiwcv\Documents\Codex\2026-11-02\ui-task-spec-for-gpt-md` 下。

## 当前边界与未完成目标

- **不宣称已经完成 Grok Bot 的全部逐像素复刻。** 原版 1.19.0 已下载、校验并安装，但在本模拟器的 `libpairipcore.so` 启动崩溃；没有绕过保护，也未能逐页观察登录后所有页面。前端依据官方公开移动端资料重建，Mika 则直接复用了源码。完整 Grok 逐页/动效对齐目标仍保留为未完成。
- 已证明真实引擎和工具路径，不能把免费模型桩的输出当作真实模型的任务质量验收。当前生产 Small / Big Brain 都尚未配置模型 Key；需在 App 填写后启动自己的 AI 工作。
- x86_64 模拟器验证了 Mika Java/Kotlin 助手、独立屏和聊天；ARM64 Linux 沙箱二进制与上游输入逐字节核对、对应源码齐备，尚未在物理 ARM64 手机上完整运行验证。OEM、OAuth 登录、邮箱实际投递需用户自己的设备和账号条件。
- 云电脑 AI 本轮是 Browser Use 浏览器与 Bash 引擎；人工控制覆盖这台新桌面。未声称任意原生 Linux GUI 应用都有现成自动化策略。

## 私人配对信息（请勿公开此报告）

Server URL：`http://216.23.82.196/proactive-agent`

设备配对码：

```text
RTmVUrG8rjxUtYt-3FiTUTmNI5-uNynUrTBDclBCzgGdEYGn
```

安装后点左上头像 → 云端节点，填写配对码。模型、邮箱密钥请在 App 自行填写；公开 APK 没有内置这些秘密。
