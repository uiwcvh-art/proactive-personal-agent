# Pulse Bot 1.4.0 交付与验收

验收时间：2026-11-03T05:47:09+08:00

下载：<http://216.23.82.196/proactive-agent/download/proactive-agent.apk>

## 本次交付

- 首页长按助手、对话菜单中的置顶/取消置顶、隐藏/显示；独立「已隐藏助手」二级页面和恢复按钮。所有助手隐藏时提供恢复入口，设置中也可进入。
- 这些状态通过加密 API 保存到云端，重启后保留。隐藏不删除对话、记忆和任务，不暂停例行任务，也不关闭通知；每个会话的草稿继续保留。
- 修复首帧外观注入；页面加载前写入选定主题，WebView 内容准备完成后再显示。
- 原有 380/330 ms 切页过渡与手势保留。首次冷切页仍有绘制停顿；预绘制实验没有改善实际录屏，已撤回，不计作性能优化成果。未提高电脑性能模式。
- Mika 本机应用、虚拟屏/点击、模型配置、云电脑、资料库和透明 Markdown 记忆库均保留。

## APK 与公网回读

| 项目 | 实测 |
| --- | --- |
| 版本 | 1.4.0 / versionCode 5 |
| 字节数 | 92,435,335 |
| SHA-256 | `a88b6cae8b0bb59956b813c44d0c6689450eeda21d892b93a7c3b6f31e8e7c11` |
| 签名证书 SHA-256 | `879d8a5eb7e55e054925d1030f350b3441276e46bef549214001b2bfc4672329` |
| curl HEAD | 200 OK |
| 完整公网 GET | 92,435,335 字节，SHA-256 与本地 APK 一致 |

## 验收

后端：30 项通过。以下五个安卓流程使用同一签名 Release APK。独立屏检查补足布局/首帧就绪前提后通过，其余已通过项直接沿用；另外运行一次不录屏的性能对照。

| 流程 | 结果 | 用时 |
| --- | --- | --- |
| bots | 通过 | 27.09 s |
| ui | 通过 | 59.93 s |
| motion | 通过 | 24.14 s |
| mika | 通过 | 26.37 s |
| public | 通过 | 64.52 s |

- 名册：真实长按不误入对话、置顶排序、隐藏全部与空状态、恢复、设置入口、草稿保留及 Activity 重建后云端回读。
- UI：对话、工具执行记录、角色权限、笔记编辑回读、例行任务、WebSocket 通知与三级催单回执。
- Motion：正向/返回、快速打断、边缘返回成功/取消、面板拖动与重开、键盘、旧消息节点保留、减弱动画。
- Mika：签名助手以 uid 2000 交接，独立 VirtualDisplay、截图与实际坐标点击；真实本地模型循环接免费隔离 HTTP 桩；791,321 字节加密上传与原生下载相等。
- 公网：APK 直接连接已部署日本节点，查看云电脑、接管、原生下载 768,034 字节并核对哈希；六个 Mika 原生二级页面跟随门户选择的深色主题。
- 此次验收没有付费模型调用、真实 SMTP 发送或真实手机操作。模拟器录屏和 Window FrameMetrics 不等同于实体手机稳定 60 fps 证明。
- 无录屏对照：首次切页 Window 总帧时峰值 205.22 ms，热切页峰值 50.43 ms，二者 JS Long Task 均为 0。录屏组首次峰值 149.93 ms。冷切页的长帧仍然存在，不能把它归因于录屏，也不能把首帧主题修复当作流畅度完成。

## 云端 Harness

日本节点 `216.23.82.196`，宿主机 `/opt/proactive-agent/`，独立 Python venv 与 systemd；主 API 仅监听 `127.0.0.1:18190`，既有 nginx 子路径提供 HTTP/WebSocket。

云电脑复用 Browser Use 0.13.14 与 Playwright 1.56，独立用户、Xvfb :91、Openbox、Chromium profile 和进程；私有控制 18193、网页代理 18194、CDP 均为回环。App 可看画面、暂停/接管、手动输入点击、交还控制、停止任务及上传/下载文件。原有 Pi 工具循环可读写文件、编辑、运行 Bash、抓取和搜索网页。

云电脑 AI 模型与 Bash 的实际调度、中文输入、浏览器下载及中途接管已在 1.3 的日本节点真实进程上使用免费模型桩验证；1.4 未改动该桌面实现。此次再次通过 APK 公网检查。当前模型就绪状态：small_ready=False，big_ready=False；需要填入用户自己的兼容 API Key/Model 才能进行真实 AI 任务。

主服务更新前没有活动 Harness 或云电脑任务。更新后 10 项受保护服务状态与进程记录一致。未修改 nginx、既有 Claude/BB/浏览器/Docker 服务、配置或网络；没有 Docker 部署。Windows 回读 `quiet`。

## 源码与参照

`dist/proactive-personal-agent-source.zip`：35,975,759 字节，SHA-256 `89922ef7df0ab6f3446a075d718974c24b76de579162b1129b8c5e93ae22e1e5`。源码包排除设备配对码、签名密钥、私钥、用户数据和构建缓存。Mika 上游固定 `e9daf53d6834b236e713c6a62c3a095338eb7f95`，Apache-2.0；原生第三方许可证及对应源码随包保留。

原生对应源码：<http://216.23.82.196/proactive-agent/download/mika-corresponding-source.tar.gz>，109,492,252 字节，SHA-256 `29912d51c323e3dcffc4e1cca0fee54a8e4da5860612a6d4d263eb54b3d17025`。

名册交互参照：[Create and manage Bots](https://docs.x.ai/grok-bot/bots)；移动端参照：[Grok Bot for Mobile](https://docs.x.ai/grok-bot/mobile)；消息参照：[Message and collaborate](https://docs.x.ai/grok-bot/chat-and-collaboration)。这些公开文档与商店截图是当前 Grok 参照；原版 1.19 在本机 ARM 转译模拟器的 libpairipcore 崩溃，未进入登录后界面。

## 尚未完成的完整复刻

目标仍在进行。主助手替换、身份 Title/图片头像、删除/重置、群组/线程、完整技能和例行任务管理、连接器、语音仍有缺口；视觉与动画没有逐屏达到原版一致。Mika 原生 ARM Linux 运行时没有在 ARM64 实体手机上验收；当前 uid 2000、VirtualDisplay 和点击为模拟器实测，Root/Shizuku 真机路径不作已验收声明。

证据在 `outputs/screenshots-1.4/`、`bots-1.4.mp4`、`motion-1.4.mp4`、`ui-1.4.mp4`、`mika-local-1.4.mp4`、`release-1.4-acceptance.json`。`MOTION_AND_PARITY_PLAN.md` 记录未完事项。

## 私人设备配对

此段仅供本机用户查看，不在公开报告或源码包中。

设备配对码：`RTmVUrG8rjxUtYt-3FiTUTmNI5-uNynUrTBDclBCzgGdEYGn`

App → 设置 → 云端节点；默认 URL 为 `http://216.23.82.196/proactive-agent`。填入配对码，再配置模型。手机本地模型可在「此手机」中明确复制已经保存的云端模型配置；也可单独配置。
