# Proactive Personal Agent (DSB) — 全局系统架构与操作逻辑权威规范

> **核心设计哲学：「在看不见的地方做加法，在看得见的地方做减法」**
> 本规范完整还原并固化项目全部已商定、已验证的架构设计、交互状态机、多端互联协议、虚拟屏视觉+无障碍双通道契约、200K 静态前缀缓存与底部分离式动态记忆（Bottom Memory Deltas）机制，以及 UI 极简美学铁律。任何后续迭代与 Bug 修复严禁违背本规范或将既定特性误判为 Bug 删除。

---

## 第一章：全局设计哲学与铁律（绝对禁止事项）

### 1.1 「在看不见的地方做加法，在看得见的地方做减法」
1. **看得见的地方做减法（极致干净的原生 UI）**：
   - **Android 端 (`NativePulseScreen.kt`)**：对标 Telegram iOS 的纯净克制感。
     - 严禁在输入框周围堆砌任何快捷功能横条、状态横幅（如「虚拟屏运行中」底栏）、多余按钮或悬浮球。
     - 严禁出现任何 Emoji 图标污染（全面使用干净的 Canvas 矢量线条图标 `PulseIcon` 与 `PipVectorIconButton`）。
     - 严禁提供死板的「预设助手模板库」（创建助手仅需填写名称、人设提示词、模型，头像自动渲染首字缩略标识或用户自定义图片，像加好友一样纯粹）。
   - **PC 桌面端 (`D:\dsh-baike-plugin`)**：深度融入 `DeepSeek Harness (DSH)` 原生工作台。
     - 采用与原生 `ds-bot`（Grok 风格鲸鱼娘）完全一致的侧边栏分组、顶栏、消息流与输入框样式。
     - 严禁在输入框上方展示冗余胶囊栏；支持拖拽上传、剪贴板粘贴图片与文件，统一走原生附件卡片体验。
2. **看不见的地方做加法（强大的底层能力按需无缝唤醒）**：
   - 当主人提出需求时，AI 通过底层的核心工具（`phone_workspace`, `cloud_desktop`, `dsh_workspace`, `blackboard`, `peek_bots`, `memory_search`, `conversation_search`, `ctx_search`, `memory_pin`, `notify_user`, `emergency_alarm`, `present_choices`, `custom_tool`）静默完成跨端操控、事件黑板协同、文件流转、自动化检索与记忆沉淀。
   - **平时零打扰、零后台耗电**：不使用虚拟屏时不创建 `VirtualDisplay`、不抓帧、不持锁、不弹窗；不执行重任务时不唤醒后台轮询。

### 1.2 模型多模态铁律（严禁区分「文本模型 vs 视觉模型」或二次转发 OCR）
1. **全系统默认多模态视觉模型**：
   - 在现代大模型环境下，接入本系统的主模型均直接具备原生视觉能力。
   - 系统中**从来没有设置过「文本模型 vs 视觉模型」的分类开关**。
2. **绝对禁止的错误行为**：
   - **严禁**编写 `_is_vision_capable_model()`、`_is_current_bot_vision_capable()`、`_pick_vision_model()` 等根据模型名字字符串猜测模型是否为视觉模型的逻辑。
   - **严禁**在 `phone_workspace` 或 `cloud_desktop` 截图后，把图片再转发给另一个所谓的「视觉子模型 / OCR 提取器」（`_vision_extract_ui_tree` / `analyze_image_b64`）去转成文字描述。
   - **唯一正确的做法**：按照标准 OpenAI / FastAPI 多模态消息格式（`{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}}`），将带 `0..1000` 坐标尺与 `@eN` 节点标注的实时截图直接发给当前对话模型本身！

### 1.3 拒绝前 AI 时代的死板硬编码与机械拦截器
1. **严禁同坐标防死循环拦截 (`repeated_same_spot`)**：
   - 在微信等自绘制界面（`node_count == 0`）或分步确认界面中，同一位置连续点击（例如「下一步」与「确认」在同一位置、双击、或列表滚动后点击同一坐标）是完全正常的操作。严禁拦截相距 <55px 的点击。
2. **严禁连续相同工具调用硬拦截 (`recent_call_sigs >= 8`)**：
   - 连续向下滚动长列表（`swipe(direction="up")`）、连续轮询等待加载完成、或连续点击翻页时，工具名和参数本来就完全相同。严禁因为连续调用相同工具就强行报错中断。
3. **严禁机械关键词死锁与正则挖选项**：
   - 严禁因为用户说「算了」就机械锁死整轮工具调用；
   - 严禁用正则表达式从 AI 正文里强行抠文字生成 `choices` 按钮（选项卡片只能由 AI 显式调用 `choices` 工具生成）。

---

## 第二章：Android 独立虚拟屏 (`phone_workspace`) 核心架构与操作状态机

### 2.1 视觉截图 + 无障碍节点树双通道直传机制（Vision + Accessibility Dual Passthrough）
每次 AI 调用 `phone_workspace` 执行界面交互操作（`launch`/`open`, `open_uri`, `tap`, `long_press`, `swipe`/`scroll`, `type`, `key`, `capture`, `ui_tree`）后，系统严格按以下流水线采集并回传给当前大模型：

1. **Android 端采集**：
   - 在独立虚拟屏 (`displayId > 0`) 执行动作并等待界面动画稳定（`settleMs`）。
   - 调用 `PulseAccessibilityService.dumpDisplayTree(displayId)` 获取当前虚拟屏专属窗口的无障碍节点树 `@e1..@eN`（含 `nodeCount`、`package`、`treeHash`）。
   - 调用 `DisplayRuntime.fastPreview()` 获取虚拟屏最新渲染帧快照（`image` Base64、`frameSequence`、`width`、`height`）。
2. **Server 端标注与直传 (`vision_engine.py` + `phone_controller.py`)**：
   - 使用 PIL 在原始截图外围绘制 26px 深色坐标尺边框，标注 **`0..1000` 归一化刻度线**。
   - 若存在无障碍节点 `@e1..@eN`，在对应坐标框左上角绘制半透明紫色角标 `@eN`。
   - 若本次操作为点击（`tap` / `long_press`），在实际落点绘制红色十字准星 `TAP(nx,ny)`。
   - 将 **无障碍节点树文本**（放在 Tool JSON 结果中）与 **标注后的实时截图**（通过标准 `image_url` 多模态消息块）**同时发送给当前大模型**。

#### 2.1.1 大模型的自主决策逻辑（为什么同时给图和无障碍）
大模型在同一轮同时收到 **无障碍节点树 (`@e1..@eN`)** 与 **带 `0..1000` 坐标尺和 `@eN` 角标的实时截图 (`image_url`)**：
- **当无障碍节点准确时**：模型看到无障碍树里有目标控件（如 `@e4 [Button] "登录"`），且截图上的 `@e4` 紫色标签正对着目标按钮，模型直接调用 `phone_workspace(action="tap", target="@e4")`。
- **当无障碍节点错位/被横幅顶偏时**（如微信顶部出现「iPad 微信已登录」横幅导致无障碍框偏移）：模型通过截图看到 `@eN` 框偏了，直接看截图四边的 **`0..1000` 归一化刻度尺**，调用 `phone_workspace(action="tap", nx=500, ny=320)` 点击真实画面位置。
- **当应用是纯自绘制界面（`node_count == 0`）时**（如微信聊天页、小程序、游戏、Canvas 自绘页）：
  - 此时无障碍树为空（`node_count == 0`），**这是自绘应用的正常现象，绝不是故障**！
  - 模型直接观察实时截图上的 UI 元素，对照四边 `0..1000` 刻度尺，使用 `nx` / `ny` 归一化坐标执行点击（`action="tap", nx=..., ny=...`）或滑动（`action="swipe", direction="up"`）。

#### 2.1.2 `ui_changed`（界面是否变化）的双重判定准则
在 `AgentForegroundService.java` 中，判断一次点击/滑动/输入后界面是否发生变化，**严禁只比对无障碍树哈希 (`beforeHash != afterHash`)**！
- **根因**：微信等自绘制界面 `nodeCount` 恒为 `0`，`treeHash` 恒定不变；若只比对 `treeHash`，会导致所有点击都误报 `⚠️ 界面未变` 甚至触发错误的二次重复补点（Double-Tap Bug）！
- **正确算法**：
  1. 记录动作前的无障碍状态 `(beforePkg, beforeHash, beforeNodes)` **以及** 虚拟屏图像帧状态 `(beforeFrameSeq, beforeImgDigest)`。
  2. 动作执行并等待稳定后，采集动作后的 `(afterPkg, afterHash, ncAfter)` 与 `(afterFrameSeq, afterImgDigest)`。
  3. 只要满足以下任一条件，即判定 `ui_changed = true`：
     - `!afterPkg.equals(beforePkg)`（包名变化）
     - `!afterHash.equals(beforeHash)` 或 `beforeNodes != ncAfter`（无障碍树变化）
     - `afterFrameSeq != beforeFrameSeq && !afterImgDigest.equals(beforeImgDigest)`（虚拟屏渲染了新的不同画面帧，完美覆盖微信/小程序/游戏等 `node_count == 0` 的自绘制场景！）
  4. **彻底废除 `AgentForegroundService.java` 中当 `afterHash.equals(beforeHash)` 时自动补点第二次 (`retriedTap`) 的错误逻辑**，确保每次 `tap` 严格只点一次！

---

### 2.2 物理主屏 (`Display 0`) 绝对隔离与防跳出、防退桌面、防变亮铁律

#### 2.2.1 防止微信内嵌小程序（如信用卡还款 `AppBrandUI`）跳到物理主屏 (`Display 0`)
- **现象根因**：微信主进程 (`com.tencent.mm`) 运行在虚拟屏 `displayId > 0` 上，但当在微信内点击打开小程序（如「信用卡还款」，对应独立进程/独立 TaskAffinity 的 `com.tencent.mm/.plugin.appbrand.ui.AppBrandUI*`）或外部 Activity 时，新创建的 `Task` 有时未继承 `launchDisplayId` 而落到 `Display.DEFAULT_DISPLAY (0)`。
- **三道防线 (`DisplayRuntime.java`)**：
  1. **登记受管包名 (`managedVirtualPackages`)**：凡是在虚拟屏启动过的包名（如 `com.tencent.mm`）自动加入受管集合。
  2. **交互前主屏快照 (`onBeforeVirtualInteraction(displayId)`)**：在每次虚拟屏 `launch`、`tap`、`swipe`、`touch`、`key`、`open_uri` 之前，快照物理主屏 `Display 0` 当前最顶层的 `baselineMainTaskId` 与 `baselineMainPkg`。
  3. **交互后多段式逃逸拦截 (`scheduleEscapeContainment(displayId)`)**：在动作发生后的 `+70ms`、`+200ms`、`+450ms` 异步检查 `Display 0` 的顶层 Task：
     - 若发现 `Display 0` 顶层 Task 变成了非原来的 `baselineMainTaskId`，且其包名属于 `managedVirtualPackages`（例如微信的 `AppBrandUI` 小程序子任务）或虚拟屏在 1500ms 内触发的新任务，立即调用 `invokeMoveRootTaskToDisplay(atm, escapedTaskId, displayId, true)` 将该逃逸 Task 抓回虚拟屏 `displayId`，并恢复 `baselineMainTaskId` 到主屏前台！

#### 2.2.2 防止物理主屏 (`Display 0`) 莫名退回手机桌面 (Home Launcher)
1. **彻底删除 `DisplayRuntime.release()` 中向 `Display 0` 注入 `KEYCODE_HOME` 的代码**。
2. **禁止在虚拟屏注入 `KEYCODE_HOME (3)` / `KEYCODE_APP_SWITCH (187)`**：Android 系统的 `PhoneWindowManager` 处理 `KEYCODE_HOME` 时，无论事件带的 `displayId` 是多少，都会默认唤醒 `Display 0` 的系统桌面 Launcher！在 `DisplayRuntime.java` 的 `key` 操作中，若收到 `KEYCODE_HOME`，一律转为 `KEYCODE_BACK` 或安全忽略，绝不向系统注入 `KEYCODE_HOME`。
3. **Root Shell `am start` 强制绑定虚拟屏**：当 AI 使用 `phone_workspace(action="shell", command="am start ...")` 时，自动检测并补齐 `--display <currentDisplayId>`，严禁在 `Display 0` 裸起 Activity。

#### 2.2.3 防止虚拟屏运行期间手机物理屏幕突然变亮
1. **`WakeLock` 全面改为 `PowerManager.PARTIAL_WAKE_LOCK`**：在 `RootPhoneHarness.kt` 中严禁使用 `SCREEN_DIM_WAKE_LOCK` 或 `ON_AFTER_RELEASE`，仅保持 CPU 唤醒，绝不触碰物理屏幕背光。
2. **`FLAG_OWN_DISPLAY_GROUP (1 << 9)` 隔离显示组**：创建虚拟屏时优先启用 `VIRTUAL_DISPLAY_FLAG_OWN_DISPLAY_GROUP = 1 << 9`（配合 `FLAG_TRUSTED = 1 << 10`, `FLAG_OWN_FOCUS = 1 << 14`, `FLAG_STEAL_TOP_FOCUS_DISABLED = 1 << 16`），防止微信支付/还款页面的窗口级 `screenBrightness` 传染给物理主屏；同时移除 2.5s 保活循环中会惊动主屏背光的 `IPowerManager.userActivity` 调用。

---

### 2.3 虚拟屏生命周期、自适应尺寸比例与字号控制算法（Layer 1 + Layer 2）

#### 2.3.1 Layer 1：虚拟屏内部创建尺寸、真实长宽比与 `swDp` 字号黄金法则 (`DisplayRuntime.computeAdaptiveDisplaySpec`)
1. **宿主长宽比 1:1 继承（零黑边、零拉伸）**：
   - 读取宿主物理主屏 (`Display.DEFAULT_DISPLAY`) 的真实像素尺寸 `rw x rh` 与 `realDpi`，计算长宽比 `aspect = clamp(longSide / shortSide, 1.55f, 2.35f)`，并以 16px GPU 步长对齐（`roundTo16`）计算虚拟屏宽高 `targetW x targetH`。
2. **`swDp`（最小宽度 dp）决定应用内字号与布局形态（核心铁律）**：
   - Android 应用（如微信、B站、浏览器）内部文字大小与 UI 控件缩放完全由 `smallestWidthDp (swDp = targetW * 160 / targetDpi)` 决定：
     - 若 `swDp < 360dp`：字号会变得巨大无比、控件拥挤不堪；
     - 若 `swDp >= 600dp`：应用会误判为平板（如微信弹出平板分栏模式）。
   - 因此，三档规格（`compact` / `balanced` / `large`）必须将 `targetSwDp` 严格锚定在现代直板手机的黄金舒适区间（`384dp ~ 420dp`），再反推 `targetDpi = round((targetW * 160f) / targetSwDp)`：
     - **S 档 (`compact`)**：`resScale = 0.58f`，短边 `544..704px`，`targetSwDp = clamp(hostSwDp, 384f, 411f)`，`previewShortSide = 460`。
     - **M 档 (`balanced`，默认)**：`resScale = 0.667f`，短边 `640..864px`，`targetSwDp = clamp(hostSwDp, 388f, 412f)`，`previewShortSide = 540`。
     - **L 档 (`large`)**：`resScale = 0.78f`，短边 `768..1088px`，`targetSwDp = clamp(hostSwDp, 392f, 420f)`，`previewShortSide = 640`。

#### 2.3.2 Layer 2：悬浮小窗 (`FloatingVirtualDisplay`) 幂律自适应缩放与无级调节
1. **外框容器严格貼合虚拟屏画面比例**：
   - 小窗内部画布宽高严格满足 `innerH = innerW * aspect`，配合 `ImageView.ScaleType.FIT_CENTER`，保证在任何大小下**零黑边、文字永不拉伸变形**。
2. **连续幂律缩放因子 $\sigma = (\text{innerWidthDp} / 240)^{0.65}$**：
   - 悬浮窗的磨砂玻璃边框厚度 `framePadPx`、内外圆角 `outerRadiusPx / innerRadiusPx`、顶栏矢量按钮尺寸 `btnSizePx`、底部「返回 / 待你处理」胶囊字号 `capsuleSp` 全部乘上 $\sigma \in [0.66, 1.35]$，确保小窗缩小时按钮精致不挡画面、放大时触控舒适。
3. **S/M/L 档位切换与右下角无级拖拽缩放**：
   - 拖动右下角 `ResizeGripView` 时，实时按比例无级缩放悬浮窗大小；切换档位调用 `resize(keep_resolution=true)` 时保持虚拟屏底层分辨率稳定，避免应用因重设分辨率而闪退或字号突变。

#### 2.3.3 虚拟屏创建、显示与销毁生命周期
1. **绝不无故弹窗**：执行 `device_info`、`status`、`notifications`、`apps`、`shell`、`list_files`、`pull_file`、`push_file` 时，**严禁创建或弹出虚拟屏**。
2. **操作时必须显示悬浮小窗 (`FloatingVirtualDisplay`)**：
   - 只要执行真正的虚拟屏交互（`launch`、`open_uri`、`tap`、`tap_node`、`tap_and_type`、`swipe`、`text`、`type_node`、`key`、`long_press`），必须确保 `FloatingVirtualDisplay` 处于可见状态（自动调用 `FloatingVirtualDisplay.show(context)` 展开小窗并启动 `startLivePreview`），并且移除聊天输入框上方多余的「虚拟屏运行中」绿条横幅。
3. **同一任务内复用 `displayId`，任务结束彻底销毁 (`destroy = true`)**：
   - 在同一个连续任务周期内，复用当前已创建的 `VirtualDisplay`（`recreate = false`），保证 `displayId` 稳定不递增。
   - 当任务结束触发 `auto_release` / `release`，或主人点击悬浮窗右上角的红色 `×` 关闭按钮时：除后台正在播放音频的音乐软件外，**一律执行 `release(destroy = true)` 彻底销毁 `VirtualDisplay`、关闭 `ImageReader`、释放 `Surface` 并移除悬浮窗**，绝不留在手机后台占用内存与序号。

---

## 第三章：多设备全网互联与事件黑板架构 (`安卓一` / `安卓二` / `Windows一` / `共享云电脑`)

### 3.1 设备节点标识、独立花名册 (`home_node`) 与工作区工具
| 节点 `source` ID | 标准显示名称 | 常驻专属 Bot (`home_node`) | 专属与共享工作区工具 |
| :--- | :--- | :--- | :--- |
| `phone` / `phone2` | **安卓一 / 安卓二** | 手机专属助理团（`companion`, `research`, `contrarian`, `whalechan`） | `phone_workspace` + 共享 `cloud_desktop` |
| `pc` / `pc2` | **Windows一 / Windows二** | 电脑专属助理团（`pc_engineer` 司工, `pc_research` 探微, `pc_reviewer` 审校, `pc_companion` 云汐） | `dsh_workspace` (含 Git Bash `pc_shell` / `cua_desktop` / 秒回 `steer` `dispatch_session`) + **直接操控共享 `cloud_desktop`** |
| `cloud` | **共享云电脑** | 全端共享 24/7 隔离 Linux 工作站 (`proactive-desktop`) | `cloud_desktop`（手机端与电脑端均可直控） |

1. **分端不同名 + 快速导入导出 (`.dsb-bot.json`)**：
   - 手机端与电脑端采用各自独立命名的 Bot 花名册（`home_node: "phone" | "pc"`），全局名字唯一，黑板 `@Bot名` 零歧义；支持一键导出/导入 Bot 完整档案（含设定、外观、聊天记录、置顶记忆与关联笔记）。
2. **废除点对点 `send_message`，全面采用事件黑板 (`blackboard`) + 只读窥视 (`peek_bots`)**：
   - **`blackboard`**：`<50ms` 瞬时发帖并异步 `@Bot` / `@self` / `@everyone` 唤醒；支持 `action="claim"` 租约认领、`artifact_path` 产物交接、按需 `review_by` 审校门禁（仅在明确要求时开启）、闹钟/定时自唤醒（`ring_alarm`/`notify_user`/`wake_bot`）及 0-Token `watch` 事件探针。
   - **`peek_bots`**：只读查看任意端 Bot 最新消息、当前运行步骤与黑板任务进度。
   - **抢占式插话 (`steer`)**：用户或高优指导第二轮输入直接打断第一轮旧执行，保留已执行工具轨迹并带着新上下文无缝继续。

---

## 第四章：原生 Agent 工具历史、98%~99% 静态前缀缓存与满 100K 分段局部压缩

1. **原生 `assistant(tool_calls)` + `role: "tool"` 历史回放**：
   - 严格保持大模型原生 Agent 训练格式回放真实工具调用与返回，跨轮自动剥离旧 `reasoning_content`，杜绝伪造旁白导致的工具调用报错。
2. **小于 `100K` 免压缩 + 满 `100K`「告一段落」分段局部压缩**：
   - 上下文 `< 100K` Tokens 时 0 压缩纯追加（Append-Only），配合底部 `inject_memory_deltas()` 保持 98%~99% Prefix Cache 命中率。
   - 满 `100K` Tokens 时仅压缩前序「已告一段落」的旧对话段并原地替换为结构化摘要，完整保留近期活跃的 30K~40K 原生上下文。

---

## 第五章：服务端模块化架构 (`server/` 去石山化规范)

为彻底杜绝单文件 6000+ 行无法维护的「石山代码」，服务端拆分为职责单一、结构清晰的领域模块：

- `server/main.py`：FastAPI 路由、WebSocket 加密网关、多端文件同步接口。
- `server/brain.py`：核心对话编排器（Turn Loop、流式推送、Curiosity 调度、状态持久化）。
- `server/core/llm_client.py`：OpenAI 兼容多模态流式客户端、Token 估算与上下文裁剪。
- `server/core/memory_engine.py`：Obsidian 风格 Markdown 记忆金库、BM25 检索与底部增量注入。
- `server/core/vision_engine.py`：`0..1000` 归一化坐标尺绘制、`@eN` 紫色节点标注、点击准星合成。
- `server/core/phone_controller.py`：手机虚拟屏 (`phone_workspace` / `phone_macro`) 控制器与多模态图像直传。
- `server/core/desktop_controller.py`：电脑端 (`dsh_workspace` / `cloud_desktop`) 双工作区控制器。
- `server/core/interconnect_router.py`：跨设备/同设备/DSH 会话 (`send_message`) 路由与双气泡投递器。
- `server/core/tool_registry.py`：12 大核心工具 Schema 定义与系统级辅助工具执行器。
- `server/core/scheduler_engine.py`：定时看门狗 (`watchdog`) 与后台协程任务池 (`bg_task`)。
- `server/core/context_search.py`：历史会话全文检索与 FTS5 沙盒上下文缓存。
- `server/core/notification_hub.py`：跨端通知推送、紧急闹钟与免打扰状态管理。
