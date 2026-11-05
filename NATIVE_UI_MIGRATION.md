# 原生首页与聊天页迁移

当前公开版仍为 1.6。新入口 `NativePulseActivity` 尚未切换为默认启动器，且不导出给其他应用。本文件记录本地候选的边界，不能作为整套复刻完成证明。

首页、名册、搜索、长按菜单、聊天输入、消息/工具记录、成果验收、处理方式和底部菜单改为 Compose。继续使用现有 RSA/AES 加密 API 和 Android Keystore 安全存储；Markdown 复用 Mika 的原生组件。云电脑和手机助理入口延续现有实际引擎。

二级页面暂时复用现有 MainActivity。内部入口通过 Activity 的调用包校验后才接受目标页面和动作；普通启动器继续打开原首页。来回切换共享草稿和外观，网络与草稿加密写入放在后台。

迁移的直接原因是已定位的 WebView 冷绘制阻塞。双快照方案在本地虽有约 19 ms 的移动阶段 Window 峰值，但首次准备仍增加 165–267 ms，且退出时恢复错误页面，所以拒绝作为正式动画方案。详见 `work/native-animation-study-1.6.json`。

## 当前验收入口

构建 `:app:assembleRelease :app:assembleReleaseAndroidTest :app:lintRelease` 后，在项目拥有的 emulator-5560 上运行：

```powershell
python -X utf8 scripts/run_ui_acceptance.py --native-surface --record
```

检查真实触摸、键盘输入与收起、加密 API 回复、引用、模式菜单、边缘返回完成/取消、置顶服务端持久化、二级页面返回、共享外观和 Activity 重建后的草稿回读。测试仅连接本地隔离夹具，无付费模型与真实 SMTP。

## 切换默认入口前仍需完成

- 进一步测量原生 FrameTimeline 和实际呈现节奏；本轮 Window 长帧仍存在，动效尚未全量验收。
- 对已存在附件、离线初始名册、Markdown 内部记忆链接、原生手机任务接受回执、图片头像、后台重连、减弱动画和系统预测返回补齐迁移覆盖。
- 逐页对照参照 App 的排版与动效；原生化本身不代表 1:1 视觉验收通过。
- 继续完成主任务中尚缺的删除/重置、完整记忆生命周期、群组与线程、技能/例行任务管理、连接器和语音。

Android 官方资料：[Compose 动画](https://developer.android.com/develop/ui/compose/animation/composables-modifiers)、[在 View 界面逐步引入 Compose](https://developer.android.com/develop/ui/compose/migrate/interoperability-apis/compose-in-views)。

## 本轮最终验收

原生入口真实点按与拼音键盘提交通过；输入、发送到加密 API、收到回复、引用/取消、模式选择、边缘返回完成/取消、置顶并从服务端和 Activity 重建后回读均通过。真实 Harness 写入和读取工作区快照后，原生界面展开对应 write_file 输出，确认成果在服务端创建例行任务；原草稿完整保留。二级页面能够返回原生页面，外观设置共享，伪造内部入口参数被拒绝。

原有 MainActivity 的全部二级页面与原生 WebSocket/提示卡回归通过（58.691 秒）；原生最终流程通过（42.727 秒）。签名 v2 验证通过，证书保持 `879d8a5eb7e55e054925d1030f350b3441276e46bef549214001b2bfc4672329`。应用源码 lintRelease 通过，0 错误、16 警告；随后仅调整了验收程序的缓存读取和截图等待，应用 APK 字节未变。

候选 APK：`work/native-ui-preview.apk`，92,693,678 字节，SHA-256 `b0fef3e85a96c8479fb4056bd83b6649abec3a139401420a2cda5ab5e34962b2`。它仍使用开发中的 1.6 版本字段，默认启动器仍为 MainActivity，原生入口不导出；不作为新的公开版本交付。

| 录屏采样窗口 | Window 峰值 | P95 | 样本数 |
|---|---:|---:|---:|
| 首次点击至稳定 | 71.20 ms | 64.75 ms | 49 |
| 重复点击至稳定 | 48.60 ms | 34.85 ms | 51 |

API 就绪 1089 ms，首页原生帧提交 1794 ms。环境为自有 Android 35 模拟器、2 核/2 GB、宿主安静模式、开启原生录屏。上述值是 Window 回调耗时，并非显示 FPS；采样窗口还包含动画前后时间，不据此声称稳定 60 fps。活动重建回读也不等于手机重启验收。

截图来自同一候选 APK 的最终真实流程。早期输入框组合区同步问题已修正；随后出现的记录展开误判由 UiAutomation 旧节点缓存引起，画面对照确认按钮正常，验收按 [Android UiAutomation.clearCache](https://developer.android.com/reference/android/app/UiAutomation#clearCache()) 刷新节点后，仅使用实际触摸复验。没有用诊断用的无障碍点击替代通过条件。截图等待原生帧提交和窗口过渡完成，避免把旧页面缓冲当作当前页面。

证据：`work/native-surface-acceptance/`、`work/native-surface-summary.json`、`work/native-final-lint.txt`、`work/native-legacy-regression.txt`、`work/native-candidate-signature.txt`。工作目录保留失败尝试和对照图，最终图/录屏位于 Codex 工作区 outputs/native-ui-preview.png 与 native-ui-preview.mp4。

本轮没有部署日本节点，没有更改任何 Docker、Claude、BB、浏览器、网络、代理或电脑性能配置；dist/proactive-agent.apk 仍为原 1.6。没有使用真实手机、付费模型或真实 SMTP。

## 1.7 默认入口候选与实际失败记录

正在验收默认原生启动器、独立本机入口、附件、离线名册、记忆链接和通知回流。尚未发布，不以旧的原生候选截图或旧检查记录计作 1.7 通过。

新迁移流程已证实本机入口和权限页在云端 503 时可用，并完成真实 Mika 模型回环。测试回执检查改为等待界面草稿和磁盘回执都提交；失败时的截图错误不再遮住原始断言。原始日志保存在 `work/native-launch-attempts/`。

另一次实际发生的 ANR 位于从 NativePhoneActivity 进入 MainActivity：日志显示首次 WebView provider 加载在主线程占用约 5.7 秒，未及时确认原生页面的失焦事件。MainActivity 现先创建可响应返回的本机等待页，再使用 AndroidX WebKit 1.16.0 稳定的异步启动 API，回调完成才创建 WebView；启动错误显示返回重试提示。页面退出后不再创建或显示 WebView，进程级启动工作不会因 Activity 销毁而被中断。修复仍需新 APK 重建及完整迁移回归。

官方说明：[WebViewCompat.startUpWebView](https://developer.android.com/reference/androidx/webkit/WebViewCompat)、[AndroidX WebKit 1.16.0](https://developer.android.com/jetpack/androidx/releases/webkit)。新依赖的 AAR 元数据要求 compileSdk 33 和 AGP 7.2，项目现有 SDK 35 / AGP 8.9.2 可满足，无需变更 SDK 或电脑模式。
