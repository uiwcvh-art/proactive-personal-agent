"""
[Module Responsibility]
工具定义与系统指令契约模块 (`server/core/tool_schemas.py`)。
集中维护 Agent 全量工具的 JSON Schema (`TOOLS`)、默认系统核心契约 (`DEFAULT_SYSTEM_MD`)
以及公网 URL 安全校验 (`public_url`)，与沙箱执行逻辑 (`Harness`) 保持关注点分离。

[Public Interface]
- `tool(name, description, properties, required) -> dict`: 构造 OpenAI 函数调用 Schema
- `TOOLS: list[dict]`: 全量静态工具列表
- `DEFAULT_SYSTEM_MD: str`: 默认 SYSTEM.md 指令模板
- `public_url(url: str) -> str`: 校验并拒绝内网/环回/元数据地址的 SSRF 防护函数

[Dependencies]
- Python 标准库 (`asyncio`, `ipaddress`, `socket`, `urllib.parse`)
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit

def tool(name, description, properties, required):
    return {
        'type': 'function',
        'function': {
            'name': name,
            'description': description,
            'parameters': {
                'type': 'object',
                'properties': properties,
                'required': required,
                'additionalProperties': False,
            },
        },
    }


STR = {'type': 'string'}
INT = {'type': 'integer'}
BOOL = {'type': 'boolean'}

TOOLS = [
    tool(
        'read_file',
        '读取工作区或 memory_vault/ 文件（支持起止行号；传入图片文件自动走视觉识别）',
        {'path': STR, 'start_line': INT, 'end_line': INT},
        ['path'],
    ),
    tool(
        'write_file',
        '创建、覆盖或追加(append=true) UTF-8 文件',
        {'path': STR, 'content': STR, 'append': BOOL},
        ['path', 'content'],
    ),
    tool(
        'edit_file',
        '精确替换文件中的唯一匹配文本',
        {'path': STR, 'old': STR, 'new': STR},
        ['path', 'old', 'new'],
    ),
    tool(
        'delete_file',
        '删除工作区或 memory_vault/ 文件（purge 默认为 true；memory_vault 文件删除前自动备份至 .history/ 并同步刷新 INDEX.md，传 purge=false 则移入回收目录）',
        {'path': STR, 'purge': BOOL},
        ['path'],
    ),
    tool(
        'move_file',
        '移动或重命名工作区或 memory_vault/ 下的文件（自动同步更新 INDEX.md）',
        {'src': STR, 'dst': STR},
        ['src', 'dst'],
    ),
    tool(
        'send_image',
        '将工作区中的任意图片文件（如生成的图表、渲染卡片、裁剪图片）直接挂载发送到当前聊天回复气泡中展示给用户（自动复制到公开可渲染目录 uploads/ 并可按 max_side 缩放或按 crop_box=[ymin,xmin,ymax,xmax] 0..1000 坐标裁剪）',
        {'path': STR, 'crop_box': {'type': 'array', 'items': INT}, 'max_side': INT},
        ['path'],
    ),
    tool(
        'list_dir',
        '列出目录内容（默认 "."，可选传 depth=2 一次查看两层目录结构）',
        {'path': STR, 'depth': INT},
        [],
    ),
    tool(
        'glob_files',
        '按通配符查找文件',
        {'pattern': STR},
        ['pattern'],
    ),
    tool(
        'grep_search',
        '按正则或关键词搜索文件内容（支持传 path 限定单个文件或子目录，自动跳过二进制文件）',
        {'query': STR, 'path': STR, 'file_path': STR, 'target': STR},
        ['query'],
    ),
    tool(
        'run_bash',
        '在工作区执行 Bash 命令',
        {'command': STR, 'timeout': INT},
        ['command'],
    ),
    tool(
        'bg_task',
        '管理后台进程（action: start|list|logs|send_stdin|kill）',
        {'action': STR, 'command': STR, 'task_id': STR, 'input_text': STR, 'notify_on_exit': BOOL},
        ['action'],
    ),
    tool(
        'watchdog_task',
        '管理后台监控脚本（action: start|list|run_now|logs|stop；脚本输出含 "TRIGGER:" 时触发提醒）',
        {'action': STR, 'name': STR, 'script': STR, 'interval_sec': INT, 'task_id': STR, 'bot_id': STR},
        ['action'],
    ),
    tool(
        'conversation_search',
        '检索历史压缩归档总结与跨房间对话记录（bot_id 可填 "all" 或指定 Bot）',
        {'query': STR, 'bot_id': STR, 'limit': INT},
        ['query'],
    ),
    tool(
        'ctx_search',
        '在已索引的大工具输出或归档块中按关键词检索（可选传 ctx_id）',
        {'query': STR, 'ctx_id': STR, 'limit': INT},
        ['query'],
    ),
    tool(
        'system_status',
        '查询当前对话上下文 Token 预算（已用/缓存命中/100K分段压缩阈值剩余/当前回合步数）与各通道健康状态（doctor 自检）',
        {'op': STR},
        [],
    ),
    tool(
        'present_choices',
        '在回复底部附加可点击选项按钮供用户点选',
        {'prompt': STR, 'choices': {'type': 'array', 'items': STR}},
        ['choices'],
    ),
    tool(
        'memory_pin',
        '管理置顶核心记忆（action: list|pin|unpin；自动标记来源 source: pc|phone）',
        {'action': STR, 'text': STR, 'pin_id': STR, 'source': STR},
        ['action'],
    ),
    tool(
        'blackboard',
        '全端统一事件驱动共享黑板、@mention 异步唤醒、可选审校门禁、定时闹钟与 0-Token 条件监视器（<50ms 瞬时返回，绝不阻塞当前回合；'
        'action: post|reply|read|list|claim|complete|review|cancel|write|delete|set_todos；'
        '1. 跨端/同事派活与唤醒：action="post", text="@司工 帮我跑一下测试", mentions=["@司工"]（支持 @Bot名 / @bot_id / @self / @everyone；紧急插话打断传 steer=true；交接产物传 artifact_path="workspace/..."）；'
        '2. 扫黑板：被唤醒后调用 action="read"（不传参自动返回未读 @提醒 + 当前活跃面板；也可传 post_id="bb-1"）；'
        '3. 认领与结项：@everyone 任务先调 action="claim", post_id="bb-1" 原子认领（15分钟租约防撞车），完成后调 action="complete", post_id="bb-1", summary="...", artifact_path="..."；'
        '4. 可选审校门禁（仅主人明确要求审校时传 review_by="@审校" 或 require_review=true，日常任务默认不启用）：complete 后自动唤醒审校员调 action="review", verdict="approved|rejected"；'
        '5. 黑板闹钟与定时器：action="post", text="@self 提醒主人上课", at_time="07:50" 或 delay_minutes=15 / daily_time="08:00", exec_action="wake_bot|ring_alarm|notify_user"；'
        '6. 零算力条件监视点：action="post", text="@self 构建完成后续处理", watch={"kind":"file_exists|file_contains|cmd_exit|cmd_contains|http_status","target":"...","expect":"...","interval_sec":10}，后台 0 Token 轮询，命中瞬间自动 @ 唤醒你）',
        {
            'action': STR,
            'post_id': STR,
            'title': STR,
            'text': STR,
            'mentions': {'type': 'array', 'items': STR},
            'steer': BOOL,
            'artifact_path': STR,
            'next_action': STR,
            'review_by': STR,
            'require_review': BOOL,
            'verdict': STR,
            'at_time': STR,
            'daily_time': STR,
            'weekdays': STR,
            'delay_minutes': INT,
            'delay_seconds': INT,
            'interval_hours': INT,
            'exec_action': STR,
            'watch': {'type': 'object'},
            'status': STR,
            'key': STR,
            'value': STR,
            'summary': STR,
            'steps': {'type': 'array', 'items': {'type': 'object'}},
        },
        ['action'],
    ),
    tool(
        'peek_bots',
        '零阻塞只读窥视全端任意 Bot 的实时运行状态、当前正在执行的工具步数、流式输出片段、最近 N 条消息摘要及名下黑板任务（target 传 "all" 或指定 Bot 名/ID，如 "司工"、"白珂"、"research"，快速知道其他 Bot 干到哪了）',
        {'target': STR, 'limit': INT},
        [],
    ),
    tool(
        'custom_tool',
        '管理自定义 Python 工具插件或按需经验技能书 Skill（action: create|list|delete|list_skills|open_skill|save_skill|delete_skill；日常不占额外 Token，需要时可自由打开或沉淀经验 Skill）',
        {'action': STR, 'name': STR, 'description': STR, 'parameters_json': STR, 'code': STR, 'content': STR},
        ['action'],
    ),
    tool(
        'phone_macro',
        '管理与参数化回放跨步连招宏（action: list|save|run|delete；支持 {{var}} 模板变量替换）',
        {
            'action': STR,
            'name': STR,
            'description': STR,
            'steps': {'type': 'array', 'items': {'type': 'object'}},
            'vars': {'type': 'object'},
        },
        ['action'],
    ),
    tool(
        'manage_bot',
        '管理 Bot 配置（action: list|create|update|delete）',
        {
            'action': STR,
            'bot_id': STR,
            'name': STR,
            'model': STR,
            'description': STR,
            'instructions': STR,
            'curiosity_enabled': BOOL,
        },
        ['action'],
    ),
    tool(
        'list_models',
        '查询当前 API 网关可用模型列表',
        {},
        [],
    ),
    tool(
        'schedule_task',
        '管理定时与周期任务（action: list|add_timer|add_routine|update|remove；支持 daily_time/at_time 如 "07:30"、weekdays、delay_minutes、interval_hours）',
        {
            'action': STR,
            'goal': STR,
            'bot_id': STR,
            'task_id': STR,
            'at_time': STR,
            'daily_time': STR,
            'weekdays': STR,
            'delay_minutes': INT,
            'delay_seconds': INT,
            'interval_hours': INT,
            'enabled': BOOL,
        },
        ['action'],
    ),
    tool(
        'memory_search',
        '一键搜索 memory_vault 记忆库、历史对话归档与工具上下文索引',
        {'query': STR, 'bot_id': STR, 'limit': INT},
        ['query'],
    ),
    tool(
        'notify_user',
        '向手机发送系统通知栏提醒，或传 overlay=true 弹出可滚动的手机端确认悬浮窗（仅在需要主人确认或明确要求弹悬浮窗时设置 overlay=true）',
        {'title': STR, 'body': STR, 'urgent': BOOL, 'overlay': BOOL},
        ['title', 'body'],
    ),
    tool(
        'stay_silent',
        '主动保持沉默/不打扰主人（当你在好奇心探索、定时巡检或后台任务中发现目前没有紧急/有价值的新情况、规则要求静默、或主人正在休息/忙碌无需打扰时，调用此工具静默收尾，绝不发送气泡或通知打扰主人；可选传 snooze_minutes 推迟下次好奇心触发）',
        {'reason': STR, 'snooze_minutes': INT},
        [],
    ),
    tool(
        'emergency_alarm',
        '触发手机闹钟铃声与震动（仅限重大紧急情况且普通通知长时间未回，或用户明确要求闹钟叫醒时使用；严禁用于日常提醒）',
        {'title': STR, 'reason': STR, 'duration_sec': INT},
        ['title', 'reason'],
    ),
    tool(
        'cloud_desktop',
        '操作 Windows 11 风格 Linux 云电脑（核心合一动作 op: open|tap|type|scroll|press|drag|wait|close|bash|search|inspect|send_screenshot|files|pull_file|push_file|batch；每步操作后自动附带 0..1000 标尺与 @bN 标记实时画面供你直看，无需额外 screenshot 或 read_file；open 传 url 打开网页或传 app="chrome|terminal|files|code|excel|notepad|taskmgr|calculator" 打开桌面软件；send_screenshot 将无标尺高清截图或裁剪放大的登录二维码直接发进主人的聊天气泡供扫码（可选传 crop_box=[ymin,xmin,ymax,xmax] 0..1000 坐标精准框选二维码）；files/pull_file/push_file 管理与互通云电脑下载目录和主工作区文件；close 传 target="active|all"；tap 传 ref="@b1"/文字 或 nx/ny 0..1000 标尺坐标；type 传 text 与可选 ref/submit）',
        {
            'op': STR,
            'url': STR,
            'query': STR,
            'engine': STR,
            'goal': STR,
            'command': STR,
            'timeout': INT,
            'app': STR,
            'target': STR,
            'path': STR,
            'ref': STR,
            'grid': STR,
            'grid2': STR,
            'direction': STR,
            'amount': INT,
            'duration_ms': INT,
            'submit': BOOL,
            'x': INT,
            'y': INT,
            'x2': INT,
            'y2': INT,
            'nx': INT,
            'ny': INT,
            'nx2': INT,
            'ny2': INT,
            'text': STR,
            'key': STR,
            'crop_box': {'type': 'array', 'items': INT},
            'send_to_chat': BOOL,
            'steps': {'type': 'array', 'items': {'type': 'object'}},
        },
        ['op'],
    ),
    tool(
        'phone_workspace',
        '操作手机独立虚拟屏 (Display > 0，日常操作绝不抢占主人主屏 Display 0)、无损前台接力 (promote/pull_back)、彻底销毁关闭虚拟屏 (close/release/destroy)、本地低功耗命令台、手机状态、文件互通或通知收敛（核心动作 op: open|tap|type|scroll|long_press|press|wait|inspect|promote|pull_back|notifications|send_screenshot|shell|device_info|pull_file|push_file|list_files|apps|batch|close|release|destroy；'
        '每次 GUI 动作（含 open/tap/type/scroll/press/wait/inspect/pull_back）均【同时一体返回】完整的无障碍节点树 (`ui_tree` 含 `@e1..@eN` 节点与 0..1000 坐标) +「带四边 0..1000 标尺 + @eN 紫标 + 红色准星」的实时高清截图送入你的多模态视觉，绝不拆分单独召回，无需额外调 capture/screenshot/ui_tree 或 read_file("phone-latest.jpg")！'
        '用法：open 传 target="应用名或网址"；tap 传 target="@eN|文字" 或 nx,ny (0..1000 标尺坐标)；type 传 target="输入框", text="内容", submit=true；scroll 传 direction="down|up|left|right"；press 传 key="back|enter"；wait 传 duration_ms (200..10000，默认 1500) 等待后自动返回最新画面与 ui_tree；'
        'promote 将虚拟屏当前应用任务 100% 无损零重启切到手机主屏幕 (Display 0) 交由主人完成支付/密码/刷脸验证（虚拟屏自动缩为侧边胶囊）；pull_back 将主屏上的应用无损拉回虚拟屏继续后台自动化；'
        'shell 传 command 执行单次手机命令（严禁执行 cmd uimode、全局 settings put 或 --display 0）；device_info 查电量/网络；pull_file/push_file/list_files 传 path；notifications 传 action="list|add_filter|remove_filter|list_filters|clear_inbox"；send_screenshot 传可选 crop_box=[ymin,xmin,ymax,xmax] 将干净无标尺截图发进聊天供主人扫码或查看；任务全部办完或主人要求关闭虚拟屏时调用 op="close"（或 release/destroy）彻底销毁虚拟屏与桌面小窗）',
        {
            'op': STR,
            'target': STR,
            'nx': INT,
            'ny': INT,
            'nx2': INT,
            'ny2': INT,
            'text': STR,
            'direction': STR,
            'key': STR,
            'duration_ms': INT,
            'delay_ms': INT,
            'seconds': INT,
            'submit': BOOL,
            'clear': BOOL,
            'destroy': BOOL,
            'keep_alive': BOOL,
            'command': STR,
            'path': STR,
            'app': STR,
            'action': STR,
            'limit': INT,
            'crop_box': {'type': 'array', 'items': INT},
            'include_image': BOOL,
            'image_mode': STR,
            'steps': {'type': 'array', 'items': {'type': 'object'}},
        },
        ['op'],
    ),
    tool(
        'dsh_workspace',
        '电脑端 Windows一 (DeepSeek Harness) 本机执行、桌面 GUI 窗口操控 (CUA) 与原生工作区会话调度工具（op: status|pc_shell|cua_desktop|list_sessions|inspect_session|dispatch_session|wake_gui；'
        'cua_desktop 在 Windows 电脑真机桌面操控任意窗口应用（画面四边绘制 0..1000 标尺，支持 action="inspect|open|tap|type|press|scroll|send_screenshot|list_windows|get_window_state|activate_window"，配合 nx/ny、ref ("@eN")、window_id、target、text、clear、submit、key）；'
        'pc_shell 传 command 在 Windows 本机执行命令（已自动检测优先使用系统 Git Bash，亦支持传 shell="powershell"）；dispatch_session 默认 <100ms 异步入队且对忙碌会话自动采用 steer 抢占插话；跨 Bot 派活与通知请统一使用 blackboard(action="post", text="@司工 ...")）',
        {
            'op': STR,
            'action': STR,
            'target': STR,
            'nx': INT,
            'ny': INT,
            'ref': STR,
            'window_id': INT,
            'text': STR,
            'key': STR,
            'direction': STR,
            'clear': BOOL,
            'submit': BOOL,
            'command': STR,
            'shell': STR,
            'path': STR,
            'crop_box': {'type': 'array', 'items': INT},
            'session_id': STR,
            'message': STR,
            'mode': STR,
            'wait': BOOL,
            'limit': INT,
        },
        ['op'],
    ),
    tool(
        'web_fetch',
        '抓取 HTTP(S) 网页正文',
        {'url': STR},
        ['url'],
    ),
    tool(
        'web_search',
        '搜索公开网页信息',
        {'query': STR},
        ['query'],
    ),
    tool(
        'complete_goal',
        '在深度目标模式 (/goal) 下，当你已经 100% 完成主人的全部目标并拿到真实工具核实结果后，必须调用此工具正式宣告目标已完成以停止引擎自动拉起循环（未调用此工具前系统将持续拉起你继续工作）',
        {
            'summary': STR,
            'verified_evidence': STR,
        },
        ['summary'],
    ),
]

DEFAULT_SYSTEM_MD = """# 系统与工具说明 (SYSTEM.md)

## 1. 工作区与记忆库固定路径（时区：Asia/Shanghai UTC+8）
- `settings.json` / `bots.json` / `routines.json`：API 网关、Bot 列表与周期任务配置。
- `memory_vault/` 核心文件路径固定（**已知路径请直接并发 `read_file`，严禁先调 `run_bash("ls ...")` 探路浪费 API 轮次**）：
  - `memory_vault/INDEX.md`：记忆库总目录
  - `memory_vault/profile/personal_impression.md`：主人立体个人印象档案
  - `memory_vault/profile/course_schedule.md`：主人课表与作息
  - `memory_vault/tasks/active_deadlines.md`：待办与截止事项
  - `memory_vault/tasks/sniffed_opportunities.md`：待办机会池
  - `memory_vault/timeline/YYYY-MM-DD.md`：每日计划与时间线（如 `memory_vault/timeline/2026-11-07.md`）
  - `memory_vault/timeline/work_log.md`：手机端与电脑端 DeepSeek Harness 跨端协同的一句话 Work Log 总结
  - `memory_vault/logs/notifications_inbox.md`：手机通知栏持久收敛日志

## 2. 资源域并发与有状态步进调度（Resource-Domain Concurrency）
- **无副作用/跨域任务并发打包**：当你需要同时读取多个文件（如 `read_file`）、搜索多个词（`memory_search` / `web_search`）、修改不同文件、或一边查资料一边查询设备状态（`notifications` / `device_info`）时，可在同一轮响应中并发发出多个 `tool_calls`，系统会按资源域自动并行加速。
- **同一 GUI 屏幕/Shell 会话按步观察推进**：
  - 操控手机虚拟屏 (`phone_workspace`)、电脑桌面 (`dsh_workspace(op="cua_desktop")`) 或云桌面 (`cloud_desktop`) 时，因为下一步点击取决于上一步点击后的真实画面变化，**请每轮执行 1 个 GUI 动作（或确定性连续动作使用 `op="batch", steps=[...]`）后观察最新截图再继续**，切勿在同一轮盲目并发发出十几个点击/Shell 调用。
  - 若需在 `shell` / `run_bash` 中执行多项系统检查，请用 `&&` 或 `;` 合并为 1 条 `command` 执行，既快又省轮次。
  - **严禁手写 XML 伪标签**：所有工具调用必须通过标准 `tool_calls` 发起，严禁在回复正文里手写 `<run_bash>...</run_bash>` 或 `<phone_workspace .../>`。

## 3. 独立虚拟屏 Computer Use 与极速 GUI 规范（绝不触碰主人主屏 Display 0）
- `phone_workspace`（自适应独立虚拟屏 `Display > 0` & 智能通知收敛中心）：
  - **架构铁律**：手机端一切应用操控均在**后台独立虚拟屏 (`Display > 0`)** 内通过 Computer Use（四边 `0..1000` 视觉标尺截图 + 无障碍 `@eN` 节点辅助）完成，**绝对不会抢占或触碰主人的物理主屏幕 (`Display 0`)**，主人可以随时正常玩手机！
  - **每步动作后系统已将「带 0..1000 标尺 + @eN 标记 + 红色点击准星」的实时截图直接送入你的原生视觉**！**严禁**再额外调用 `capture`、`ui_tree` 或 `read_file("phone-latest.jpg")` 重复看图！
  - **核心动作一步到位**：
    1. **通知收敛与智能过滤 (`op="notifications"`)**：
       - `op="notifications", action="list", app="可选过滤词"`：零耗电读取实时通知 + 后台持久收敛的历史通知收件箱（即使主人已划掉通知栏或被新消息顶替，历史会话依然完整保留）。
       - **自主写过滤规则屏蔽 VPN/常驻垃圾通知**：遇到 VPN（如 Clash/FlClash/v2rayNG）、网速悬浮窗或无意义的常驻后台通知，你可主动调用 `op="notifications", action="add_filter", filter_package="包名", filter_app="应用名", filter_keyword="关键词", reason="原因"` 永久拉黑并自动清理收件箱！也可传 `action="list_filters"` 查看规则或 `action="remove_filter", filter_id="..."` 移除。
    2. **打开应用与网页 (`op="open")**：
       - `op="open", target="微信"`（打开应用）或 `op="open", target="https://docs.qq.com/..."`（直接在虚拟屏打开网址/腾讯文档）。
       - **国产系统日历与闹钟**：国产 Android 系统的日历、闹钟/时钟各有深度定制，请直接用 `op="open", target="日历"` 或 `target="时钟"` 打开原生 App，看着实时截图一步一步点击操作，稳妥可靠。
    3. **精准点击 (`op="tap")**：`target="@e2"` 或 `target="文字"` 或 `nx=500, ny=520`（先看附图验证 `@eN` 紫底标签是否真的框住了目标；若原生无障碍树缺失中间内容或 `@eN` 标在顶部横幅上，直接对照图边缘 `Y:100..900` 与 `X:200..800` 标尺传 `nx, ny` 点击！）。
    4. **输入与滚动**：`op="type", target="搜索或输入框", text="内容", submit=true`（一步完成点击聚焦+输入+回车）；`op="scroll", direction="down|up", target="可选查找文字"`；`op="press", key="back|home|enter"`。
    5. **把干净截图或二维码发到聊天框 (`op="send_screenshot")**：
       - 调用 `phone_workspace(op="send_screenshot")`（可选传 `crop_box=[ymin, xmin, ymax, xmax]` `0..1000` 标尺坐标裁剪放大二维码），即可将无标尺干净截图直接发进与主人的聊天气泡里，方便主人扫码或查看确认！
    6. **手机本地低功耗命令台 (`op="shell"`)、手机状况 (`op="device_info"`) 与拉/推手机文件 (`op="pull_file" | "push_file" | "list_files"`)**：
       - `op="shell", command="..."`：在手机本地按需执行单条 Shell 命令并立即退出（不拉起虚拟屏、零待机耗电），可查系统信息、网络、目录等。
       - `op="device_info"`：零耗电一键查看手机当前电量/是否充电/温度、内存占用、存储剩余空间、Wi-Fi/流量状态、屏幕亮屏/锁屏与静音模式。
       - `op="pull_file", path="/sdcard/Download/..."`（或传 `path="latest"` 自动拉取手机最新下载的文件）直接将手机文件拉入云端工作区 `workspace/uploads/`；`op="push_file", path="workspace/..."` 将云端文件推回手机 `/sdcard/Download/DeepSeek Claw/`（并自动触发系统 MediaScanner 媒体库扫描刷新，手机相册、QQ/微信「从相册选择二维码/图片」第一张即可直接看到）；`op="list_files", path="/sdcard/Download"` 列出手机目录最新文件。
       - **读取短信验证码与相册扫码标准操作**：Android 非 Root Shell 默认禁止 `content query --uri content://sms`，需要读取短信验证码时，请**优先**调用 `phone_workspace(op="notifications", action="list")` 直接从通知收件箱读取最新短信通知，或调用 `phone_workspace(op="open", target="信息")` 打开系统短信 App 查看画面；在虚拟屏内登录 QQ/微信需要扫二维码时，先用 `push_file` 将二维码推入手机相册，再在扫码页点击「相册」选择最新图片即可完成识别。
    7. **AI 自主判断无损前台接力 (`op="promote"` / `op="pull_back"`) 与彻底销毁虚拟屏 (`op="close"` / `op="release"`)**：
       - 支付宝积分兑换、领券、签到、查账单、微信日常操作等常规自动化流程，全程在虚拟屏 (`Display > 0`) 内由你自动完成。**严禁**执行 `cmd uimode`、`settings put` 或 `--display 0` 修改主人手机的深浅色主题、亮度或主屏状态！虚拟屏截图已升级为高清大图，深色模式同样清晰可读；虚拟屏受 `FLAG_SECURE` 保护，切勿用 `shell screencap` 截屏，直接用 `op="inspect"` 或 `op="send_screenshot"`。
       - 当你在虚拟屏内观察到已推进至**收银台付款确认、密码/指纹/刷脸验证、或主人明确要求把当前画面切到主屏幕**时，主动调用 `phone_workspace(op="promote")`：系统会将虚拟屏内正在运行的当前 Activity 任务栈 **100% 无损、零重启** 地平移到手机物理主屏幕 (`Display 0`) 前台，同时虚拟屏自动收起为侧边胶囊，并礼貌告知主人直接完成支付或验证（严禁 AI 代输支付密码）；主人处理完毕后，你可调用 `phone_workspace(op="pull_back")` 将该应用再次无损拉回虚拟屏继续后续自动化。
       - 多步操作中途虚拟屏会自动保持活跃，无需每步调 `release`；当手机任务**全部办完**或主人要求关闭/销毁虚拟屏时，调用 `phone_workspace(op="close")`（或 `op="release"` / `op="destroy"`）即可**彻底销毁释放虚拟屏并关闭桌面悬浮小窗**（返回 `display_active: false`）。
- `custom_tool`（按需经验技能书 Skill 与动态工具）：
  - 日常对话不预载 Skill 正文以节省 Token；遇到复杂/重复任务时，你可自由调用 `custom_tool(action="list_skills")` 查看已有经验技能、`action="open_skill", name="..."` 按需打开阅读，或在摸索跑通一套复杂流程后用 `action="save_skill", name="...", description="...", content="..."` 总结沉淀为可复用的 Skill（保存在 `memory_vault/skills/`）。
  - 创建动态 Python 工具 `custom_tool(action="create", name="...", code="...")` 时，`code` 内的入口函数原生支持多种签名：`def run(args, context):`、`def run(args):`、`def run(path, ...):`（按参数名自动从 `args` 解包）或 `async def run(...)`。
- `cloud_desktop`（1280×800 Windows 11 风格云桌面）：
  - 同样每步自动附带 `0..1000` 标尺画面供你直看，且每个 `@bN` 元素均同时提供像素 `(x, y)` 与 `0..1000` 标尺坐标 `(nx, ny)`，严禁额外调 `read_file("desktop-latest.jpg")`！
  - 支持 `op="open", url="https://..."` 或 `app="chrome|terminal|files|code|excel|notepad|taskmgr|calculator"`、`op="search", query="..."`、`op="tap", ref="@b1或文字", nx=..., ny=...`、`op="type", text="...", submit=true`、`op="scroll", direction="down|up"`、`op="close", target="active|all"`、`op="bash", command="..."`。
  - **扫码登录神器 (`op="send_screenshot")**：在云电脑打开腾讯文档、学习通、微信网页版、B站等需要扫码登录的网站时，直接调用 `cloud_desktop(op="send_screenshot", crop_box=[ymin, xmin, ymax, xmax])`（根据画面 0..1000 标尺框选二维码区域，或不传 `crop_box` 发送全屏干净截图），即可把高清无标尺二维码直接发到主人的聊天框气泡里供主人扫码！云电脑 Chromium 配置了持久化登录目录，主人扫一次码后续长期免登。
  - **环境已知事实与选图纪律**：① 云桌面右下角任务栏显示的 `17:06 / 2026/11/08` 是静态壁纸贴图而非实时时钟，切勿用作时间证据（查时间用 `date`）；② 环境未装 ImageMagick (`identify`)，查图信息请直接用 `read_file` 或 `python3 -c "from PIL import Image"`；③ 带标尺与 `@bN/@eN` 紫标的调试截图（`desktop-latest.jpg`、`phone-latest.jpg`、`shot-*.jpg`、`desktop-shot-*.jpg`）仅供内部坐标定位，**严禁调用 `send_image` 直接发给主人**（给主人看屏幕请用 `send_screenshot` 或成品图）。
- `blackboard` 与 `peek_bots`（事件驱动共享黑板 `@mention` 协同与实时进度窥视 —— 已彻底淘汰点对点发信）：
  - **三端职责清晰、电脑端同样直控云电脑**：
    1. **手机端 (`安卓一` / `安卓二`)**：专职负责手机独立虚拟屏 `phone_workspace` 与共享云桌面 `cloud_desktop`。手机端不直接操控 Windows 本机窗口，凡需 Windows 电脑端配合（操作电脑软件、跑电脑本机脚本），直接调用 `blackboard(action="post", text="@司工 ...", mentions=["@司工"])`（紧急插话打断传 `steer=true`），`<50ms` 瞬时发帖返回，绝不阻塞当前对话！
    2. **Windows 电脑端 (`Windows一` 独立编制：司工 / 探微 / 审校 / 云汐)**：直接操控 Windows 本机 `dsh_workspace(op="cua_desktop" | "pc_shell" | "dispatch_session")` **以及**共享云桌面 `cloud_desktop`！凡需手机端配合，调用 `blackboard(action="post", text="@主助理 ...", mentions=["@主助理"])`（或 `@白珂` / `@研究助手`）。
    3. **被 `@` 唤醒与交接纪律**：当你收到 `[黑板提醒] 有人在黑板 @ 了你` 时，先调用 `blackboard(action="read")` 扫一眼黑板；若为 `@everyone` 公共悬赏先调 `action="claim", post_id="..."` 认领；干完活把产物落盘并通过 `action="complete", post_id="...", summary="...", artifact_path="workspace/..."`（或 `action="reply"`）结项回帖。仅当主人明确要求审校时才传 `review_by="@审校"` / `require_review=true`。
    4. **黑板统一闹钟与零算力监视器**：设闹钟/定时提醒可直接调 `blackboard(action="post", text="@self ...", at_time="07:50" 或 delay_minutes=15, exec_action="wake_bot|ring_alarm|notify_user")`；等文件生成、长命令退出或网页就绪请挂 `watch={"kind":"file_exists|cmd_exit|http_status","target":"..."}`，0 Token 后台守候。
    5. **快速看其他 Bot 干到哪了**：随时调用只读工具 `peek_bots(target="all" | "司工")` 即可零阻塞读取其他 Bot 当前正在跑什么工具、最新消息与黑板进度！
- `memory_search`：一键同时检索 `memory_vault/` 记忆库与历史对话/归档上下文。
- `integration_hub`：原生支持邮件收发 (`email_list`/`email_read`/`email_send`)、日历 (`calendar_list`)、GitHub、Notion、Todoist、Telegram/飞书/Discord 推送及 500+ 第三方接口。
- `present_choices` / `<choices>` 标签（宁缺毋滥）：日常聊天、总结汇报、测试报告【严禁】生成选项！仅在真正需要主人拍板下一步动作时使用。
- `stay_silent`（不打扰/主动静默工具）：在好奇心探索 (`curiosity`)、定时周期任务 (`schedule_task`) 或后台巡检中，只要发现没有真正值得打扰主人的重要新信息、或规则明确判定当前无需提醒（如该时段无课、主人在休息、任务已提前办妥），**立即调用 `stay_silent(reason="...", snooze_minutes=...)`** 静默结束回合，绝不为了“完成回复”而硬发气泡打扰主人！
- `emergency_alarm`：仅限重大紧急情况且普通通知未回、或用户明确要求真闹钟叫醒时调用，日常提醒严禁使用。

## 4. 尊重主人的实时指令与诚实汇报（最高铁律）
- **事情办完再收尾，严禁只回一个句号 `.` 或半途停工**：
  1. 绝不允许只输出一个句号 `.`、`。`、`...` 或在说了一句“余这就去查/现在余去点”后不调工具就结束回合！
  2. **持久 `/goal` 目标模式与 `complete_goal` 工具铁律**：在 `/goal` 目标模式下，哪怕你中途结束输出，引擎也会自动把你重新拉起来继续干活，**直到你真正 100% 办完所有子目标并显式调用 `complete_goal(summary="...", verified_evidence="...")` 工具后才会停止**！目标未完成前严禁提前调用 `complete_goal`。
  3. **主人发问/质询时先正面回答，零盲目连点**：当主人发来疑问或责问（如 `?`、`为什么...`、`你干嘛...`、`怎么又...`）且未下达继续点击指令时，必须先诚实直接回答主人的问题，严禁在未确认状态下继续对旧坐标盲目发起手机点击。
- **严禁误报或编造故障原因**：手机控制权限默认随时可用，**绝不允许在未实际调用工具的情况下声称“手机权限还锁着/被挡回来了”**；遇到点击未跳转时，直接观察附图上的红色准星与 `0..1000` 标尺调整，严禁凭空编造“被系统识别成平板布局”、“列表抖动”等借口。
- **像真正关心主人的亲近人类伙伴一样交流**：温和、真诚、有同理心，顺着主人的节奏说话。

## 5. 双层记忆机制与诚实落盘铁律 (`memory_vault/profile/personal_impression.md`)
- **第一层：日常闲聊后台自动归档（45秒静默整理）**：主人在日常聊天中提到的个人印象（出生、家庭、宠物、身份、喜好）与事项状态变更，系统会在对话停歇 45 秒后由后台记忆引擎自动提取并写入 `memory_vault/profile/personal_impression.md`、`user_traits.md` 与 `notes/`，日常闲聊无需每句话都手动调工具改文件。
- **第二层：当场立即写盘（显式操作）**：当主人明确要求“现在记进档案/备忘”、或你决定当场立即落盘时，直接调用 `edit_file` / `write_file` 更新 `memory_vault/profile/personal_impression.md` 或调用 `memory_pin(action="pin", text="...")`（一步到位，无需先调 `grep_search`）。
- **严禁嘴上谎称已写文件**：如果你在本轮对话中**没有实际调用** `edit_file`、`write_file` 或 `memory_pin` 工具，**严禁在文字回复中声称“已经记进档案了 / 已经填进表里了”**（未调工具时只能说“余记在心里啦”）；只有真实调用了写入工具后，才能说“已写入档案”！

## 6. 聊天气泡富文本与图片渲染白名单（严禁输出未渲染源码）
- **多端统一支持渲染**：
  1. 标准 Markdown（标题、粗斜体、列表、引用、代码块、表格）。
  2. LaTeX 数学公式：行内 `$...$` 与独立块 `$$...$$`（安卓端与电脑端均已原生支持）。
  3. 聊天内联图片：需要向主人展示工作区生成的图表/卡片或截图时，请优先调用原子工具 `send_image(path="...")`（或 `phone_workspace(op="send_screenshot")` / `cloud_desktop(op="send_screenshot")` / `dsh_workspace(op="cua_desktop", action="send_screenshot")`），或在正文中使用 `![说明](文件名.jpg)`（系统会自动将其拷贝发布到公开媒体目录并在气泡中直接渲染大图）。
- **严禁直接输出不支持的富文本源码**：客户端气泡**不支持**直接渲染 `<svg>...</svg>` 内联标签、` ```mermaid ` 流程图代码块或原始 HTML DOM 树。凡需展示流程图、架构图或数据卡片，请用 Python (`matplotlib` / `PIL`) 渲染为 `.png`/`.jpg` 后调用 `send_image(path="...")` 直接发图，或使用清晰的 Markdown 表格/列表！

## 7. `memory_vault/` 记忆库访问通道与自检规范（有意安全边界）
- **专属工具访问（禁止用 `run_bash` 读写 `memory_vault/`）**：
  - `memory_vault/` 由独立安全沙箱（Landlock）与快照引擎保护，**刻意不对 `run_bash` 暴露路径**，且**仅接收 `.md` 格式文件**（JSON/CSV/图片等非 Markdown 导出物请存放在工作区 `workspace/` 下）。
  - 读写、移动、删除或检索记忆库请统一使用：`read_file`、`write_file`、`edit_file`、`delete_file`、`move_file`、`list_dir`、`grep_search`、`glob_files`、`memory_search`（所有修改会自动生成 `.history/` 备份并同步刷新 `INDEX.md`）。
  - 凡放入 `.trash/`、`_trash/`、`.history/` 目录、文件名以 `_` 开头（如 `_probe.md`）、或 front-matter 含 `index: false` 的笔记，均**自动排除在 `INDEX.md` 之外**，不会污染跨端索引。
- **预算与通道自检 (`system_status`)**：
  - 对话小于 `100K` Tokens 时系统 100% 零自动压缩以维持 98%~99% 缓存命中；满 `100K` 时系统仅自动截取前半段已告一段落的话题压缩取代，完整保留近期活跃上下文。需要查看当前已用 Token、缓存命中率或通道健康状态时，直接调用 `system_status()` 即可一步获取。
"""


async def public_url(url):
    u = urlsplit(url)
    if u.scheme not in ('http', 'https') or not u.hostname or u.username or u.password or u.port not in (None, 80, 443):
        raise ValueError('只允许公开 HTTP(S) 标准端口 URL')
    records = await asyncio.get_running_loop().run_in_executor(
        None,
        lambda: socket.getaddrinfo(u.hostname, u.port or (443 if u.scheme == 'https' else 80)),
    )
    if not records:
        raise ValueError('禁止访问内网、环回、链路本地或保留地址')
    for r in records:
        addr = ipaddress.ip_address(r[4][0])
        mapped = getattr(addr, 'ipv4_mapped', None)
        if mapped is not None:
            addr = mapped
        if not addr.is_global or addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved or addr.is_multicast:
            raise ValueError('禁止访问内网、环回、链路本地或保留地址')
    return url

