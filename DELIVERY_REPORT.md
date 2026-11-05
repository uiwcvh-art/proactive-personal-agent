<!-- current-client-1.7 -->
最新客户端：**1.7.0 融合版**。验收见 [DELIVERY_REPORT_1_7.md](DELIVERY_REPORT_1_7.md)，[APK 下载](http://216.23.82.196/proactive-agent/download/proactive-agent.apk)。以下保留历史部署与本地配对信息。

# Pulse Bot 1.6.0 交付与验收

时间：2026-11-03T08:44:54+08:00

APK：<http://216.23.82.196/proactive-agent/download/proactive-agent.apk>

## 本次完成

- 独立身份编辑页：名称与 Title 标签；首页、对话顶部和详情同步更新，保存后不会被旧的角色设置草稿覆盖。
- 头像提供形状/颜色、生成、上传入口。上传由原生照片选择器或相机读取，照片在手机上拖动、双指/滑块缩放并裁剪；点击完成才同步云端。返回时可继续编辑或放弃修改。
- 生成入口复用执行大脑的服务与密钥，并单独填写图像模型；先展示裁剪预览，确认后保存。配置缺失或接口不支持时显示真实错误。
- 主助手选择页、菜单入口与首页标记。选中的助手接收后续主动追问、默认对话、闲时研究和全局到期提醒。原有对话、工具权限、通知开关与例行任务保留；进行中的回复维持原会话归属。
- PNG 头像经加密接口单独读取，名册不反复传输图片；服务端解码并去除图片元数据。图片可以改回形状头像。
- 同一 APK 的主模块与 Mika 版本为 1.6.0 / code 7。

交互参照 [Grok 官方 Bot 管理说明](https://docs.x.ai/grok-bot/bots)。该页列出的移动端 Name / Title、头像形状/生成/上传与主助手替换是本轮对照范围。原版登录后的逐屏视觉对照仍未完成。

## 签名与公网

| 项目 | 实测 |
| --- | --- |
| 客户端 | 1.6.0 / versionCode 7 / dev.bubuki.proactive |
| APK 大小 | 92,446,340 bytes |
| 本地 SHA-256 | 28e4f151a0c14c738f432052056620858142b6c0ca65941cf25e3eb2a6108dea |
| 公网 HEAD | 200 OK |
| 完整下载大小 | 92,446,340 bytes |
| 完整下载 SHA-256 | 28e4f151a0c14c738f432052056620858142b6c0ca65941cf25e3eb2a6108dea |
| APK v2 签名证书 SHA-256 | 879d8a5eb7e55e054925d1030f350b3441276e46bef549214001b2bfc4672329 |
| 服务端健康版本 | 1.6.0 |
| 源码 ZIP 大小 | 36,022,386 bytes |
| 源码 ZIP SHA-256 | 419db08b7d77c02a59cecdc14b5b0ffc0a998df6162383c958b184da72c48128 |
| APK / 源码敏感值检查 | 通过；不含本机配对码、签名密码或私钥 |

本地 APK：D:/bubuki/proactive-personal-agent/dist/proactive-agent.apk

源码：D:/bubuki/proactive-personal-agent/dist/proactive-personal-agent-source.zip

Mika 原生配套源码：[下载对应源码](http://216.23.82.196/proactive-agent/download/mika-corresponding-source.tar.gz)，109,492,252 bytes，SHA-256 29912d51c323e3dcffc4e1cca0fee54a8e4da5860612a6d4d263eb54b3d17025。本轮原生 C 二进制沿用既有版本，对应源码文件未替换。

## 验收证据

| 流程 | 结果 | 耗时 |
| --- | --- | --- |
| identity | 通过 | 55.94 s |
| bots | 通过 | 26.45 s |
| ui | 通过 | 68.04 s |
| motion | 通过 | 33.08 s |
| mika | 通过 | 23.97 s |
| public | 通过 | 78.70 s |

后端 34 项检查通过，覆盖身份持久化、图片实际解码/元数据清理、非法图片拒绝、权限认证、主助手选择、默认消息/研究路由、进行中会话归属、通知开关及加密 API。生成请求通过真实 HTTP 到隔离的本地图像服务夹具，未调用付费模型。

新增安卓验收实际打开系统照片选择器，选择 MediaStore 测试照片，在裁剪页触摸缩放/拖动，保存并重启 Activity 后从 API 回读图片；还验证主助手选择取消/确认、实际追问归属、放弃未保存修改、缺失 Key 提示、图像服务回环与图片改回形状。交付目录中 identity-1.6.mp4 是本次原生录屏，screenshots-1.6/identity-* 是相应截图。identity-1.6-preview.gif 是同一录屏中照片裁剪的常速节选。

其他五项继续验证置顶/隐藏、完整 UI、页面/面板/手势与键盘、本机 Mika 引擎及日本节点云电脑。云电脑验收含真实公网连接、接管页面、文件入口和原生 MediaStore 下载 768,034 bytes 后逐字节核对。

## 部署边界

仅更新 /opt/proactive-agent/server/，在现有应用 venv 中增加 Pillow==13.0.0，并重启 proactive-agent.service。其监听仍为 127.0.0.1:18190，现有 nginx 子路径沿用。没有 Docker 命令、路由/代理/防火墙修改，也没有重启云电脑服务或既有浏览器。

部署前后 10 项受保护服务状态、PID 与启动时间一致；19 个部署文件与本地哈希一致。旧代码备份：/opt/proactive-agent/.deploy/server-before-identity-20261103T002655Z.tgz。

电脑档位回读 quiet；验收使用独立 Grok_Reference 模拟器，未操作实体手机。应用的免打扰与 Windows 安静档位是分别管理的设置。

本机模拟器直连日本 IP 的 TCP 80 曾超时；公网流程经现有本机 HTTP 代理完成，该代理仅作为独立模拟器的启动参数。APK 的公网 HEAD/完整下载核验也经现有代理。手机使用时需能访问指定的日本节点。

## 完整目标仍待完成

- 原版 Grok Bot 1.19.0 在本机 ARM 转译环境的 libpairipcore.so 启动崩溃，登录后页面尚不能逐屏实看；当前不能宣称像素及产品能力完全一致。
- 冷启动长帧尚未解决。本轮含录屏的首开 Window 帧时峰值 152.3 ms、热切页 35.25 ms，样本分别 19 / 33 个。它们是模拟器 Window 采样，不能作为实体手机稳定 60 fps 的证明；原始摘要见 motion-1.6-observations.json。
- 删除/重置、各 Bot 独立记忆的完整生命周期、群组/线程/提及/表情、完整技能与例行任务、连接器和语音等仍有缺项，继续按原目标推进。
- 真实图像模型、真实 SMTP 与实体手机 Root/Shizuku/ARM64 Linux 运行环境没有在本轮验收。相机读取与授权 URI 路径已实现；本轮照片流程实测覆盖系统照片选择器，相机快门流程尚未走查。
- 模型 Key 需由使用者填入。无 Key 时的本地引导和文件快照会如实标明；云电脑手动查看、接管与文件功能可独立使用。


## 私人配对信息

节点：http://216.23.82.196/proactive-agent

设备配对码：RTmVUrG8rjxUtYt-3FiTUTmNI5-uNynUrTBDclBCzgGdEYGn

这份本机报告含配对码，不上传到公网或包含在源码 ZIP 中。


## 后续原生迁移进展（本地候选，未发布）

原生首页/聊天候选完成模拟器真实输入、消息、工具执行记录与成果确认，以及原二级页往返、置顶和草稿回读；原界面回归和签名、应用 Lint 通过。完整复刻和动效验收仍在进行，公开 1.6 APK 与部署未替换。证据和缺口见 `NATIVE_UI_MIGRATION.md`；Codex 工作区提供 `outputs/native-ui-preview.png` 与 `outputs/native-ui-preview.mp4`。
