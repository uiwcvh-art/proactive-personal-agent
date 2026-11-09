from __future__ import annotations

import asyncio
import fnmatch
import ipaddress
import json
import os
import re
import signal
import socket
import sys
import time
from pathlib import Path
from urllib.parse import quote_plus, urlsplit

import httpx
from bs4 import BeautifulSoup

from core.search_engine import multi_web_search
from storage import compact_trace_list
from transport.extension_registry import ExtensionRegistry
from vault_manager import atomic_write


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
        '按正则或关键词搜索文件内容',
        {'query': STR, 'path': STR},
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
        '检索 200K 以前归档总结与跨房间历史记录（bot_id 可填 "all" 或指定 Bot）',
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
        '多 Bot 共享键值存储与任务步骤进度（action: write|read|list|delete|set_todos）',
        {'action': STR, 'key': STR, 'value': STR, 'summary': STR, 'steps': {'type': 'array', 'items': {'type': 'object'}}},
        ['action'],
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
        'send_message',
        '向同端或跨端的任意指定 Bot 发送协作消息，直接进入目标 Bot 自己的专属会话并等待回复（同端填 Bot 的 id/名字，如 "research"、"杠精"、"鲸鱼娘"；跨端填 "节点:Bot名"，如 "Windows一:白珂"、"Windows一:研究助手"、"Windows一:杠精"、"Windows一:鲸鱼娘"、"安卓一:白珂"、"安卓二:白珂"）',
        {'to_bot_id': STR, 'text': STR, 'to_node': STR},
        ['to_bot_id', 'text'],
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
        '向手机发送系统通知栏提醒',
        {'title': STR, 'body': STR, 'urgent': BOOL},
        ['title', 'body'],
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
        '操作手机虚拟屏、本地低功耗命令台、手机状态与文件互通或收敛通知栏（核心动作 op: open|apps|tap|type|scroll|long_press|press|inspect|notifications|send_screenshot|shell|device_info|pull_file|push_file|list_files|batch|release；每步视觉动作自动将带 0..1000 标尺与 @eN 标记的实时画面直接送入你的视觉，严禁再调 capture 或 read_file("phone-latest.jpg")！虚拟屏收尾时调用 op="release" 即可将虚拟屏静默保留在后台并缩小到屏幕侧边，绝不放大抢占主人手机主屏前台！shell 传 command 在手机本地按需执行单次 Root/Shell 命令并立即退出（零待机耗电）；device_info 按需查看手机电量/充电/温度/内存/存储/网络/锁屏状况（零耗电）；pull_file/push_file/list_files 传 path 直接拉取或推送手机文件；notifications 支持 action="list|add_filter|remove_filter|list_filters|clear_inbox"；send_screenshot 将无标尺干净截图或 crop_box 裁剪二维码直接发进聊天气泡供主人扫码）',
        {
            'op': STR,
            'action': STR,
            'tier': STR,
            'app': STR,
            'package': STR,
            'component': STR,
            'uri': STR,
            'url': STR,
            'path': STR,
            'source': STR,
            'target': STR,
            'label': STR,
            'ref': STR,
            'grid': STR,
            'grid2': STR,
            'text': STR,
            'view_id': STR,
            'id': STR,
            'command': STR,
            'goal': STR,
            'direction': STR,
            'amount': INT,
            'limit': INT,
            'x': INT,
            'y': INT,
            'nx': INT,
            'ny': INT,
            'nx2': INT,
            'ny2': INT,
            'x1': INT,
            'y1': INT,
            'x2': INT,
            'y2': INT,
            'start_x': INT,
            'start_y': INT,
            'end_x': INT,
            'end_y': INT,
            'duration_ms': INT,
            'key': STR,
            'submit': BOOL,
            'clear': BOOL,
            'force': BOOL,
            'refresh': BOOL,
            'annotate': BOOL,
            'delay_ms': INT,
            'filter_package': STR,
            'filter_app': STR,
            'filter_keyword': STR,
            'filter_id': STR,
            'reason': STR,
            'crop_box': {'type': 'array', 'items': INT},
            'send_to_chat': BOOL,
            'steps': {'type': 'array', 'items': {'type': 'object'}},
        },
        ['op'],
    ),
    tool(
        'dsh_workspace',
        '电脑端 Windows一 (DeepSeek Harness) 本机执行、桌面 GUI 窗口操控 (CUA) 与状态查看工具（op: status|pc_shell|cua_desktop|list_sessions|inspect_session|dispatch_session|wake_gui；'
        'cua_desktop 在 Windows 电脑真机桌面操控任意窗口应用（如 QQ/浏览器等），传 action="list_windows|get_window_state|activate_window|click|type_text|set_value|press_key|scroll|launch_app"，配合 window_id、element_index、include_screenshot=true/false、text、clear、submit、key 等；'
        'pc_shell 传 command 在 Windows 本机执行 PowerShell/CMD 命令并返回输出；status 查看电脑端在线状态与最近对话；如需给电脑端任意 Bot 发消息请调用 send_message(to_bot_id="Windows一:白珂", text="...")）',
        {
            'op': STR,
            'command': STR,
            'tool': STR,
            'action': STR,
            'window_id': INT,
            'element_index': INT,
            'include_screenshot': BOOL,
            'include_text': BOOL,
            'filter': STR,
            'full_tree': BOOL,
            'click_count': INT,
            'mouse_button': STR,
            'clear': BOOL,
            'submit': BOOL,
            'direction': STR,
            'pages': INT,
            'app': STR,
            'value': STR,
            'args': {'type': 'object'},
            'x': INT,
            'y': INT,
            'key': STR,
            'url': STR,
            'path': STR,
            'limit': INT,
            'source': STR,
            'device_id': STR,
            'session_id': STR,
            'new_session': BOOL,
            'message': STR,
            'text': STR,
            'query': STR,
            'workspace': STR,
            'cwd': STR,
            'max_messages': INT,
            'max_tools': INT,
            'include_reasoning': BOOL,
            'include_blank': BOOL,
            'timeout_sec': INT,
            'wake_gui': BOOL,
            'bot_id': STR,
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

## 2. 并发工具调用（省 API 额度铁律 · Parallel Tool Calls）
- **单轮打包多工具并发调用**：当你需要读取多个文件（如同时读 `active_deadlines.md`、`course_schedule.md` 与今日 `timeline/YYYY-MM-DD.md`）、检索多个关键词、或者一边更新文件/记忆一边调用 `present_choices` / `schedule_task` / `phone_workspace` 时，**必须在同一次 API 响应中一次性发出多个 `tool_calls`（并发工具调用）**！
- **严禁挤牙膏式串行调用**：能用 1 轮并发工具调用搞定的事，绝不允许拆成 3~6 轮单工具请求，最大限度为主人节省 API 调用额度与等待时间。

## 3. 极速 GUI 工具使用规范（严禁冗余步数）
- `phone_workspace`（自适应手机虚拟屏 & 智能通知收敛中心）：
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
    7. **虚拟屏后台驻留与缩小停靠 (`op="release"`)**：
       - 虚拟屏任务结束时调用 `phone_workspace(op="release")`（或由系统自动收尾），虚拟屏内的应用将静默保留在后台运行、悬浮窗自动缩小停靠到屏幕侧边胶囊，绝不放大或跳到主人手机主屏幕前台打断主人。
- `custom_tool`（按需经验技能书 Skill 与动态工具）：
  - 日常对话不预载 Skill 正文以节省 Token；遇到复杂/重复任务时，你可自由调用 `custom_tool(action="list_skills")` 查看已有经验技能、`action="open_skill", name="..."` 按需打开阅读，或在摸索跑通一套复杂流程后用 `action="save_skill", name="...", description="...", content="..."` 总结沉淀为可复用的 Skill（保存在 `memory_vault/skills/`）。
- `cloud_desktop`（1280×800 Windows 11 风格云桌面）：
  - 同样每步自动附带 `0..1000` 标尺画面供你直看，且每个 `@bN` 元素均同时提供像素 `(x, y)` 与 `0..1000` 标尺坐标 `(nx, ny)`，严禁额外调 `read_file("desktop-latest.jpg")`！
  - 支持 `op="open", url="https://..."` 或 `app="chrome|terminal|files|code|excel|notepad|taskmgr|calculator"`、`op="search", query="..."`、`op="tap", ref="@b1或文字", nx=..., ny=...`、`op="type", text="...", submit=true`、`op="scroll", direction="down|up"`、`op="close", target="active|all"`、`op="bash", command="..."`。
  - **扫码登录神器 (`op="send_screenshot")**：在云电脑打开腾讯文档、学习通、微信网页版、B站等需要扫码登录的网站时，直接调用 `cloud_desktop(op="send_screenshot", crop_box=[ymin, xmin, ymax, xmax])`（根据画面 0..1000 标尺框选二维码区域，或不传 `crop_box` 发送全屏干净截图），即可把高清无标尺二维码直接发到主人的聊天框气泡里供主人扫码！云电脑 Chromium 配置了持久化登录目录，主人扫一次码后续长期免登。
- `send_message`（多端多 Bot 对等独立架构与跨端协同铁律）：
  - **三端职责严格隔离，绝不越界串台**：
    1. **手机端 (`安卓一` / `安卓二`)**：专职负责手机本机 `phone_workspace` 与共享云桌面 `cloud_desktop`。**手机端没有任何直接操控 Windows 电脑底层的工具通道**！凡是主人要求查看 Windows 电脑窗口、操作电脑版 QQ/软件、在电脑上跑命令/脚本、或给电脑端派活，手机端**必须且只能**调用 `send_message(to_bot_id="Windows一:白珂", text="...")`（或 `"Windows一:研究助手"` / `"Windows一:杠精"` / `"Windows一:鲸鱼娘"` / `"会话:<工作区会话标题>"`），交给电脑端上的 Bot 在她自己的专属会话里执行并回信！严禁在回复中编造“我有直接抓电脑底层的通道B”之类的话，也严禁只手打 `@Windows一:白珂` 纯文本而不调工具。
    2. **Windows 电脑端 (`Windows一`)**：专职负责 Windows 电脑本机 `dsh_workspace(op="cua_desktop" | "pc_shell" | "dispatch_session")` 与本机工作区文件读写。电脑端同样不能直接操控手机屏幕，凡需手机端配合的操作，必须调用 `send_message(to_bot_id="安卓一:白珂", text="...")` 交给手机端执行。
    3. **收到跨端/同事 `send_message` 来信时的实干铁律**：当你收到来自其他节点或同事 Bot 的协作来信时，如果对方请求你查看窗口、检查消息、运行命令或读取文件，**你必须先在当前回合真实发起对应的 `tool_calls` 获取最新实测结果**，拿到结果后再用自然语言正文直接回复对方（系统会自动将你的最终正文回传给发件方，无需再次调用 `send_message` 回传），严禁不调工具就凭旧记忆或口头敷衍！
- `memory_search`：一键同时检索 `memory_vault/` 记忆库与历史对话/归档上下文。
- `integration_hub`：原生支持邮件收发 (`email_list`/`email_read`/`email_send`)、日历 (`calendar_list`)、GitHub、Notion、Todoist、Telegram/飞书/Discord 推送及 500+ 第三方接口。
- `present_choices` / `<choices>` 标签（宁缺毋滥）：日常聊天、总结汇报、测试报告【严禁】生成选项！仅在真正需要主人拍板下一步动作时使用。
- `emergency_alarm`：仅限重大紧急情况且普通通知未回、或用户明确要求真闹钟叫醒时调用，日常提醒严禁使用。

## 4. 尊重主人的实时指令与诚实汇报（最高铁律）
- **严禁误报或编造故障原因**：手机控制权限默认随时可用，**绝不允许在未实际调用工具的情况下声称“手机权限还锁着/被挡回来了”**；遇到点击未跳转时，直接观察附图上的红色准星与 `0..1000` 标尺调整，严禁凭空编造“被系统识别成平板布局”、“列表抖动”等借口。
- **像真正关心主人的亲近人类伙伴一样交流**：温和、真诚、有同理心，顺着主人的节奏说话。

## 5. 双层记忆机制与诚实落盘铁律 (`memory_vault/profile/personal_impression.md`)
- **第一层：日常闲聊后台自动归档（45秒静默整理）**：主人在日常聊天中提到的个人印象（出生、家庭、宠物、身份、喜好）与事项状态变更，系统会在对话停歇 45 秒后由后台记忆引擎自动提取并写入 `memory_vault/profile/personal_impression.md`、`user_traits.md` 与 `notes/`，日常闲聊无需每句话都手动调工具改文件。
- **第二层：当场立即写盘（显式操作）**：当主人明确要求“现在记进档案/备忘”、或你决定当场立即落盘时，直接调用 `edit_file` / `write_file` 更新 `memory_vault/profile/personal_impression.md` 或调用 `memory_pin(action="pin", text="...")`（一步到位，无需先调 `grep_search`）。
- **严禁嘴上谎称已写文件**：如果你在本轮对话中**没有实际调用** `edit_file`、`write_file` 或 `memory_pin` 工具，**严禁在文字回复中声称“已经记进档案了 / 已经填进表里了”**（未调工具时只能说“余记在心里啦”）；只有真实调用了写入工具后，才能说“已写入档案”！
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


class Harness:
    def __init__(self, root: Path, store=None, vault=None, extension_registry: ExtensionRegistry | None = None):
        self.root = root.resolve()
        root.mkdir(parents=True, exist_ok=True)
        (self.root / 'skills').mkdir(parents=True, exist_ok=True)
        (self.root / 'plugins').mkdir(parents=True, exist_ok=True)
        sys_md = self.root / 'SYSTEM.md'
        if not sys_md.exists() or sys_md.read_text('utf-8', errors='replace') != DEFAULT_SYSTEM_MD:
            atomic_write(sys_md, DEFAULT_SYSTEM_MD)
        self.store = store
        self.vault = vault
        self.vision_reader = None
        self.custom_handlers = {}
        self.extension_registry = extension_registry or ExtensionRegistry()
        self.extension_registry.discover_plugins_dir(Path(__file__).parent / 'plugins')
        self.extension_registry.discover_plugins_dir(self.root / 'plugins')
        self._web_cache: dict[str, tuple[float, dict]] = {}
        self._sys_md_cache: tuple[int, str] | None = None

    def all_tools(self) -> list[dict]:
        base_names = {t['function']['name'] for t in TOOLS}
        extra = [s for s in self.extension_registry.tool_schemas() if s['function']['name'] not in base_names]
        return TOOLS + extra

    def path(self, p):
        if not p or p == '.':
            return self.root
        if Path(p).is_absolute() or '\\' in p or ':' in p:
            raise ValueError('只允许工作区相对路径')
        if (p == 'memory_vault' or p.startswith('memory_vault/')) and self.vault is not None:
            rel = p[len('memory_vault'):].lstrip('/')
            if not rel:
                return self.vault.root
            cand = (self.vault.root / rel).resolve()
            if '..' in Path(rel).parts or not cand.is_relative_to(self.vault.root.resolve()):
                raise ValueError('禁止上级路径或符号链接')
            if cand.is_dir():
                return cand
            return self.vault.path(rel)
        path = self.root / p
        if '..' in path.parts or any(x.is_symlink() for x in [path, *list(path.parents)[:len(path.parts) - len(self.root.parts)]]):
            raise ValueError('禁止上级路径或符号链接')
        if not path.resolve().is_relative_to(self.root):
            raise ValueError('超出工作区')
        resolved = path.resolve()
        if not resolved.exists() and self.vault is not None:
            vault_cand = (self.vault.root / p).resolve()
            if vault_cand.is_relative_to(self.vault.root.resolve()) and vault_cand.exists():
                return vault_cand
        return resolved

    async def fetch(self, url):
        now = time.time()
        cached = self._web_cache.get(url)
        if cached and now - cached[0] < 600:
            return dict(cached[1])
        async with httpx.AsyncClient(timeout=20, trust_env=False) as client:
            orig_url = url
            for _ in range(5):
                await public_url(url)
                async with client.stream('GET', url, headers={'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36'}) as r:
                    if r.is_redirect:
                        url = str(r.url.join(r.headers['location']))
                        continue
                    r.raise_for_status()
                    if not any(x in r.headers.get('content-type', '') for x in ('text/', 'json', 'xml')):
                        raise ValueError('仅接受文本网页')
                    buf = bytearray()
                    async for chunk in r.aiter_bytes():
                        buf.extend(chunk)
                        if len(buf) > 512_000:
                            raise ValueError('网页超过 512 KB')
                    soup = BeautifulSoup(buf.decode('utf-8', errors='replace'), 'html.parser')
                    for node in soup(['script', 'style', 'nav', 'footer']):
                        node.decompose()
                    text = soup.get_text(' ', strip=True)
                    links = [{'title': a.get_text(' ', strip=True)[:180], 'url': a['href']} for a in soup.select('a[href]') if a['href'].startswith('http')][:16]
                    res = {'source': url, 'text': text[:16000], 'links': links}
                    if len(self._web_cache) > 120:
                        self._web_cache.clear()
                    self._web_cache[orig_url] = (now, res)
                    return res
            raise ValueError('重定向过多')

    async def execute(self, name, args):
        try:
            if name in self.custom_handlers:
                return await self.custom_handlers[name](args)
            if name in self.extension_registry.tool_plugins:
                return await self.extension_registry.execute_tool(name, args, {'root': str(self.root)})
            if name == 'read_file':
                p = self.path(args['path'])
                if p.is_dir():
                    return await self.execute('list_dir', {'path': args['path']})
                if p.suffix.lower() in ('.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp'):
                    if self.vision_reader is not None:
                        return await self.vision_reader(p)
                    raise ValueError('图片文件请通过原生视觉读取')
                if p.stat().st_size > 1_000_000:
                    raise ValueError('文件过大')
                raw_text = p.read_text('utf-8', errors='replace')
                if 'start_line' in args or 'end_line' in args:
                    lines = raw_text.splitlines()
                    s = max(1, int(args.get('start_line') or 1))
                    e = min(len(lines), int(args.get('end_line') or (s + 800)))
                    sliced = '\n'.join(f'{idx}: {lines[idx - 1]}' for idx in range(s, e + 1) if 1 <= idx <= len(lines))
                    return {'content': sliced[:160000], 'total_lines': len(lines), 'range': f'{s}-{e}'}
                return {'content': raw_text[:160000]}
            if name in ('write_file', 'edit_file'):
                p = self.path(args['path'])
                old_text = p.read_text('utf-8', errors='replace') if p.exists() else ''
                if name == 'write_file':
                    if args.get('append') and old_text:
                        sep = '' if old_text.endswith('\n') else '\n'
                        text = old_text + sep + args['content']
                    else:
                        text = args['content']
                else:
                    if not p.exists():
                        raise ValueError('文件不存在')
                    if not args['old'] or old_text.count(args['old']) != 1:
                        raise ValueError('旧文本必须精确匹配一次')
                    text = old_text.replace(args['old'], args['new'], 1)
                if len(text.encode()) > 1_000_000:
                    raise ValueError('文件过大')
                snap_saved = None
                if self.vault is not None and args['path'].startswith('memory_vault/'):
                    rel_vault = args['path'][len('memory_vault/'):].lstrip('/')
                    snap_saved = self.vault.write(rel_vault, text)
                    if args['path'] != 'memory_vault/INDEX.md':
                        self.vault.reindex()
                else:
                    atomic_write(p, text)
                if self.store is not None:
                    self.store.sync_from_workspace(force=True)
                rel = args['path'] if args['path'].startswith('memory_vault/') else p.relative_to(self.root).as_posix()
                out_res: dict = {'written': rel, 'bytes': len(text.encode())}
                if snap_saved:
                    out_res['previous_backup'] = snap_saved
                    if len(old_text) > len(text) + 80:
                        out_res['warning'] = f'新内容比原文件少 {len(old_text) - len(text)} 字符；旧版已自动备份至 {snap_saved}，如需找回可直接 read_file("{snap_saved}")'
                return out_res
            if name == 'list_dir':
                rel_arg = str(args.get('path') or '.').strip() or '.'
                max_depth = max(1, min(3, int(args.get('depth') or 1)))
                target = self.path(rel_arg)
                if not target.is_dir():
                    raise ValueError('目标不是目录')
                entries = []

                def _walk_dir(cur_dir: Path, rel_prefix: str, cur_depth: int):
                    if len(entries) >= 250:
                        return
                    try:
                        children = sorted(cur_dir.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
                    except OSError:
                        return
                    for child in children:
                        if len(entries) >= 250:
                            break
                        if child.name.startswith('.transfers') or child.name == '.versions':
                            continue
                        try:
                            is_d = child.is_dir() and not child.is_symlink()
                            stat = child.stat()
                        except OSError:
                            continue
                        rel_name = f'{rel_prefix}{child.name}' + ('/' if is_d else '')
                        entries.append({
                            'name': rel_name,
                            'type': 'dir' if is_d else 'file',
                            'bytes': 0 if is_d else stat.st_size,
                        })
                        if is_d and cur_depth < max_depth:
                            _walk_dir(child, rel_name, cur_depth + 1)

                _walk_dir(target, '', 1)
                if rel_arg == '.' and self.vault is not None:
                    entries.append({'name': 'memory_vault/', 'type': 'dir', 'bytes': 0})
                    if max_depth > 1:
                        _walk_dir(self.vault.root, 'memory_vault/', 2)
                return {'path': rel_arg, 'depth': max_depth, 'entries': entries}
            if name == 'glob_files':
                pattern = str(args.get('pattern') or '**/*').strip()
                matches = []
                for f in sorted(self.root.rglob('*')):
                    if f.is_file() and not f.is_symlink():
                        rel = f.relative_to(self.root).as_posix()
                        if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(f.name, pattern):
                            matches.append(rel)
                if self.vault is not None:
                    for f in sorted(self.vault.root.rglob('*.md')):
                        if f.is_file() and not f.is_symlink():
                            rel = 'memory_vault/' + f.relative_to(self.vault.root).as_posix()
                            if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(f.name, pattern):
                                matches.append(rel)
                return {'pattern': pattern, 'matches': matches[:200]}
            if name == 'grep_search':
                query = str(args.get('query') or '').strip()
                if not query:
                    raise ValueError('query 不能为空')
                rx = re.compile(query, re.IGNORECASE)
                search_roots = [(self.root, '')]
                if self.vault is not None:
                    search_roots.append((self.vault.root, 'memory_vault/'))
                hits = []
                for base_dir, prefix in search_roots:
                    for f in sorted(base_dir.rglob('*')):
                        if not f.is_file() or f.is_symlink() or f.stat().st_size > 512_000:
                            continue
                        rel = prefix + f.relative_to(base_dir).as_posix()
                        try:
                            for idx, line in enumerate(f.read_text('utf-8', errors='ignore').splitlines(), 1):
                                if rx.search(line):
                                    hits.append({'file': rel, 'line': idx, 'text': line[:220]})
                                    if len(hits) >= 80:
                                        return {'query': query, 'hits': hits, 'truncated': True}
                        except Exception:
                            continue
                return {'query': query, 'hits': hits}
            if name == 'memory_search':
                query = str(args.get('query') or '').strip()
                if not query or self.vault is None:
                    return {'hits': []}
                rx = re.compile(re.escape(query), re.IGNORECASE)
                hits = []
                for f in self.vault.files():
                    content = self.vault.read(f['path'])
                    if rx.search(content) or rx.search(f['path']):
                        hits.append({'path': 'memory_vault/' + f['path'], 'snippet': content[:800]})
                return {'query': query, 'hits': hits[:20]}
            if name == 'run_bash':
                if sys.platform != 'linux':
                    return {'error': 'Bash 隔离执行仅在 Linux 部署节点可用'}
                timeout_sec = max(3, min(90, int(args.get('timeout') or 30)))
                env = os.environ.copy()
                py_user_base = self.root / '.python_packages'
                py_user_base.mkdir(parents=True, exist_ok=True)
                env['TZ'] = 'Asia/Shanghai'
                env['PYTHONUSERBASE'] = str(py_user_base)
                env['PIP_USER'] = '1'
                env['PATH'] = f"{py_user_base / 'bin'}:{env.get('PATH', '/usr/bin:/bin')}"
                if self.store is not None:
                    env['API_BASE_URL'] = self.store.config.effective_base_url()
                    env['API_KEY'] = self.store.config.effective_api_key(big=True) or self.store.config.effective_api_key(big=False)
                    env['DEFAULT_MODEL'] = self.store.config.effective_model(big=True) or self.store.config.effective_model(big=False)
                proc = await asyncio.create_subprocess_exec(
                    sys.executable,
                    str(Path(__file__).with_name('sandbox.py')),
                    str(self.root),
                    args['command'],
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    start_new_session=True,
                    env=env,
                )

                def kill_group():
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass

                output = bytearray()

                async def collect():
                    while True:
                        chunk = await proc.stdout.read(4096)
                        if not chunk:
                            break
                        output.extend(chunk[:32000 - len(output)])
                        if len(output) >= 32000:
                            kill_group()
                            break
                    await proc.wait()
                    return bytes(output)

                try:
                    out = await asyncio.wait_for(collect(), timeout_sec)
                except asyncio.TimeoutError:
                    kill_group()
                    await proc.wait()
                    if self.store is not None:
                        self.store.sync_from_workspace(force=True)
                    return {
                        'error': f'命令执行超时 ({timeout_sec}s)，已自动终止。若需长时后台运行请使用 bg_task 工具。',
                        'timed_out': True,
                        'exit_code': -9,
                        'output': bytes(output).decode('utf-8', errors='replace'),
                    }
                except asyncio.CancelledError:
                    kill_group()
                    await proc.wait()
                    raise
                finally:
                    kill_group()
                if self.store is not None:
                    self.store.sync_from_workspace(force=True)
                return {'exit_code': proc.returncode, 'output': out.decode('utf-8', errors='replace')}
            if name == 'web_fetch':
                return await self.fetch(args['url'])
            if name == 'web_search':
                q = str(args.get('query') or '').strip()
                if not q:
                    return {'error': '搜索词不能为空'}
                return await multi_web_search(q, max_results=10)
            raise ValueError('未知工具')
        except asyncio.CancelledError:
            raise
        except Exception as e:
            return {'error': f'{type(e).__name__}: {str(e)[:300]}'}

    def static_system_instructions(self, pinned_block: str = '') -> str:
        """100% static system instructions (no dynamic INDEX.md or timestamp) to lock upstream Prefix Cache."""
        sys_md = self.root / 'SYSTEM.md'
        if sys_md.exists():
            mtime = sys_md.stat().st_mtime_ns
            if self._sys_md_cache and self._sys_md_cache[0] == mtime:
                arch_doc = self._sys_md_cache[1]
            else:
                arch_doc = sys_md.read_text('utf-8', errors='replace')[:12000]
                self._sys_md_cache = (mtime, arch_doc)
        else:
            arch_doc = DEFAULT_SYSTEM_MD
        sticker_hint = self.extension_registry.sticker_prompt_hint()
        img_hint = '【发送图片与表情贴纸规范】：需要向主人展示工作区图片（如 `desktop-latest.jpg`、`phone-latest.jpg` 或生成的图表）或网络图片时，直接在回复中写 `![图片](文件名或URL)` 或 `<image:文件名或URL>` 即可在聊天气泡内直接渲染图片。'
        if sticker_hint:
            img_hint += '\n' + sticker_hint
        return (arch_doc + '\n\n' + img_hint + ('\n\n' + pinned_block if pinned_block else '')).strip()

    def system_instructions(self, context: str) -> str:
        return (self.static_system_instructions() + ('\n\n' + context if context else '')).strip()

    async def run(self, goal, complete, context, allowed_tools=None, on_event=None):
        all_schemas = self.all_tools()
        allowed = set(t['function']['name'] for t in all_schemas) if allowed_tools is None else set(allowed_tools)
        available = [t for t in all_schemas if t['function']['name'] in allowed]
        messages = [{'role': 'system', 'content': self.system_instructions(context)}, {'role': 'user', 'content': goal}]
        trace = []
        accumulated: list[str] = []
        msg = {'content': ''}
        latest_mm_frame: dict | None = None
        for _ in range(40):
            call_messages = messages
            if latest_mm_frame is not None:
                call_messages = messages + [{
                    'role': 'user',
                    'content': [
                        {'type': 'text', 'text': latest_mm_frame['caption']},
                        {'type': 'image_url', 'image_url': {'url': f"data:{latest_mm_frame['mime']};base64,{latest_mm_frame['b64']}"}},
                    ],
                }]
                latest_mm_frame = None
            msg = await complete(call_messages, tools=available or None)
            turn_txt = (msg.get('content') or '').strip()
            if turn_txt and (not accumulated or accumulated[-1] != turn_txt):
                accumulated.append(turn_txt)
                if on_event:
                    await on_event({'type': 'turn_text', 'text': '\n\n'.join(accumulated)})
            messages.append(msg)
            calls = msg.get('tool_calls') or []
            if not calls:
                break
            batch_calls = calls[:24]
            gui_cnt = sum(1 for c in batch_calls if c.get('function', {}).get('name') in ('phone_workspace', 'cloud_desktop'))
            if len(batch_calls) > 1 and gui_cnt <= 1:
                parsed_batch = []
                for c in batch_calls:
                    try:
                        c_args = json.loads(c['function']['arguments'])
                    except (ValueError, KeyError):
                        c_args = {}
                    c_name = c['function']['name']
                    parsed_batch.append((c, c_name, c_args))
                    if on_event:
                        await on_event({'type': 'tool_start', 'tool': c_name, 'arguments': c_args})

                async def _exec_one(c_item, c_name, c_args):
                    t0 = asyncio.get_running_loop().time()
                    res = await self.execute(c_name, c_args) if c_name in allowed else {'error': '此助手未启用该工具'}
                    dur = round((asyncio.get_running_loop().time() - t0) * 1000)
                    return c_item, c_name, c_args, res, dur

                results_p = await asyncio.gather(*[_exec_one(c_i, c_n, c_a) for c_i, c_n, c_a in parsed_batch])
                for c_item, name, args, result, dur in results_p:
                    if isinstance(result, dict) and result.get('_multimodal_b64'):
                        mm_b64 = str(result.pop('_multimodal_b64'))
                        mm_mime = str(result.pop('_multimodal_mime', 'image/jpeg'))
                        mm_cap = str(result.pop('_multimodal_caption', f'[{name} 实时画面（含 0..1000 标尺）]'))
                        latest_mm_frame = {'b64': mm_b64, 'mime': mm_mime, 'caption': mm_cap}
                    entry = compact_trace_list([{
                        'tool': name,
                        'arguments': args,
                        'result': result,
                        'duration_ms': dur,
                        'status': 'done',
                    }])[0]
                    trace.append(entry)
                    if on_event:
                        await on_event({'type': 'tool_end', 'entry': entry})
                    out_str = json.dumps(result, ensure_ascii=False)
                    if len(out_str) > 16000:
                        out_str = out_str[:4000] + f'\n...[工具单步大输出已截断，原长 {len(out_str)} 字符]...\n' + out_str[-1500:]
                    messages.append({'role': 'tool', 'tool_call_id': c_item['id'], 'content': out_str})
            else:
                for c in batch_calls:
                    try:
                        args = json.loads(c['function']['arguments'])
                    except (ValueError, KeyError):
                        args = {}
                    name = c['function']['name']
                    if on_event:
                        await on_event({'type': 'tool_start', 'tool': name, 'arguments': args})
                    started = asyncio.get_running_loop().time()
                    result = await self.execute(name, args) if name in allowed else {'error': '此助手未启用该工具'}
                    if isinstance(result, dict) and result.get('_multimodal_b64'):
                        mm_b64 = str(result.pop('_multimodal_b64'))
                        mm_mime = str(result.pop('_multimodal_mime', 'image/jpeg'))
                        mm_cap = str(result.pop('_multimodal_caption', f'[{name} 实时画面（含 0..1000 标尺）]'))
                        latest_mm_frame = {'b64': mm_b64, 'mime': mm_mime, 'caption': mm_cap}
                    entry = compact_trace_list([{
                        'tool': name,
                        'arguments': args,
                        'result': result,
                        'duration_ms': round((asyncio.get_running_loop().time() - started) * 1000),
                        'status': 'done',
                    }])[0]
                    trace.append(entry)
                    if on_event:
                        await on_event({'type': 'tool_end', 'entry': entry})
                    out_str = json.dumps(result, ensure_ascii=False)
                    if len(out_str) > 16000:
                        out_str = out_str[:4000] + f'\n...[工具单步大输出已截断，原长 {len(out_str)} 字符]...\n' + out_str[-1500:]
                    messages.append({'role': 'tool', 'tool_call_id': c['id'], 'content': out_str})
        final_text = '\n\n'.join(accumulated).strip() if accumulated else (msg.get('content') or '').strip()
        if not final_text and trace:
            summary_msg = await complete(
                messages + [{'role': 'user', 'content': '请基于以上工具执行结果，直接用自然清晰的语言给出最终答复，不要再调用工具。'}],
                tools=None,
            )
            sum_txt = (summary_msg.get('content') or '').strip()
            if sum_txt and (not accumulated or accumulated[-1] != sum_txt):
                accumulated.append(sum_txt)
            final_text = '\n\n'.join(accumulated).strip()
        return {'text': final_text or '本轮没有生成可验收成果。', 'trace': trace}
