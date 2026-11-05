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
        '列出目录内容（默认 "."）',
        {'path': STR},
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
        '管理置顶核心记忆（action: list|pin|unpin）',
        {'action': STR, 'text': STR, 'pin_id': STR},
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
        '管理自定义 Python 工具插件（action: create|list|delete）',
        {'action': STR, 'name': STR, 'description': STR, 'parameters_json': STR, 'code': STR},
        ['action'],
    ),
    tool(
        'phone_macro',
        '保存或回放手机操作步骤宏（action: save|list|run|delete）',
        {'action': STR, 'name': STR, 'description': STR, 'steps': {'type': 'array', 'items': {'type': 'object'}}},
        ['action'],
    ),
    tool(
        'send_message',
        '向指定 Bot 发送协作消息（to_bot_id 填 id 或名字，禁止发给自己）',
        {'to_bot_id': STR, 'text': STR},
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
        '在 memory_vault Markdown 文件中搜索关键词',
        {'query': STR},
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
        '操作 Linux 云桌面（op: status|bash|search|navigate|screenshot|ui_tree|click_text|click_ref|type_ref|launch_app|focus_window|close_window|tile_windows|clipboard|click|double_click|right_click|hover|drag|type|key|hotkey|scroll|batch；支持 grid="A1..H10" 或 nx/ny 0-1000 坐标）',
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
            'ref': STR,
            'grid': STR,
            'x': INT,
            'y': INT,
            'x2': INT,
            'y2': INT,
            'nx': INT,
            'ny': INT,
            'text': STR,
            'key': STR,
            'steps': {'type': 'array', 'items': {'type': 'object'}},
        },
        ['op'],
    ),
    tool(
        'phone_workspace',
        '操作手机自适应虚拟屏、按需读取手机最近通知或执行终端命令（op: notifications|launch|ui_tree|tap_node|type_node|tap_and_type|tap|batch|swipe|key|apps|status|resize|release|capture|shell；op="notifications" 可零耗电读取最近通知，支持 app="过滤词", limit=25；虚拟屏支持 tier="compact|balanced|large"、grid="A1..F10" 或 nx/ny 0-1000 坐标）',
        {
            'op': STR,
            'tier': STR,
            'app': STR,
            'package': STR,
            'component': STR,
            'ref': STR,
            'grid': STR,
            'text': STR,
            'view_id': STR,
            'command': STR,
            'goal': STR,
            'limit': INT,
            'x': INT,
            'y': INT,
            'nx': INT,
            'ny': INT,
            'x1': INT,
            'y1': INT,
            'x2': INT,
            'y2': INT,
            'key': STR,
            'submit': BOOL,
            'clear': BOOL,
            'force': BOOL,
            'delay_ms': INT,
            'steps': {'type': 'array', 'items': {'type': 'object'}},
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

## 1. 工作区文件（时区：Asia/Shanghai UTC+8）
- `settings.json`：API 网关与默认模型配置。
- `bots.json`：Bot 列表、模型与指令配置。
- `routines.json`：周期任务列表。
- `memory_vault/`：Markdown 记忆库（`INDEX.md` 为目录）。

## 2. 工具使用要点
- `phone_workspace`（自适应手机虚拟屏 & 零耗电通知读取）：
  - **读取手机最近通知（极省电按需拉取，无需开启虚拟屏）**：`op="notifications", app="可选过滤词如微信/QQ/邮件", limit=25`。
  - 启动应用：`op="launch", app="应用名"`；终端命令：`op="shell", command="..."`；切换虚拟屏尺寸档位：`op="resize", tier="compact|balanced|large"`。
  - 有无障碍节点时用 `tap_node`/`type_node`；无节点界面用 `grid="A1..F10"`（6列×10行）、`nx/ny`（0..1000 千分比坐标），或 `op="tap_and_type", nx=450, ny=945, text="...", submit=true`。
- `cloud_desktop`（1280×800 云桌面，闲置 0MB 浏览器按需启动，内置住宅级 WARP 隧道）：
  - 搜索与浏览：`op="search", query="...", engine="google|bing|baidu|sogou|duckduckgo"` 或 `op="navigate", url="..."`（一步打开并返回 `@b1..@bN` 语义 DOM 元素，严禁手工拼凑 `luakit` bash 命令）。
  - 命令执行：`op="bash", command="..."`；界面操作支持 `click`、`double_click`、`right_click`、`drag`、`hover`、`type`、`key`、`hotkey`、`focus_window`、`close_window`、`tile_windows`。
- `conversation_search` / `ctx_search`：检索 200K 以前归档历史、跨房间记录与已索引的大工具输出。
- `integration_hub`（第三方应用与 500+ 接口中枢）：原生支持 Gmail/Outlook/QQ/163 邮件收发 (`email_list`/`email_read`/`email_send`)、日历 (`calendar_list`)、GitHub、Notion、Todoist、Telegram/飞书/Discord 推送，并支持通过 Composio / ACI.dev 动态搜索与调用 500+ 第三方应用接口 (`op="catalog|status|configure|search_actions|connect_oauth|execute"`)。
- `present_choices`：凡是向主人提供多个方向、方案、下一步行动或确认项时，务必调用 `present_choices(choices=["方案1...", "方案2..."])` 生成底部可点击选项胶囊。
- `emergency_alarm`：仅限重大紧急情况且普通通知未回、或用户明确要求真闹钟叫醒时调用，日常提醒严禁使用。

## 3. 渐进补全主人立体个人印象 (`memory_vault/profile/personal_impression.md`)
- 你不仅是处理工作与命令的工具，更是长期陪伴主人的共生伙伴。除了关心正在进行的工作/任务，你还要在日常对话与主动搭话中，自然、温和地逐渐补全对主人的个人印象：
  1. **出生、成长与故乡**（出生地/家乡、生日/年代、成长经历或童年印象）
  2. **身份、学业与事业角色**（当前身份、专业/擅长领域、长期人生目标）
  3. **家庭、羁绊与人际关系**（家庭氛围、重要的人或宠物、社交习惯与边界）
  4. **生活方式、审美与精神角落**（作息节奏、饮食口味、音乐/游戏/影视/阅读喜好）
  5. **性格底色、价值观与相处雷区**（性格特点、看重的事物、讨厌的沟通方式）
- 当主人透露或回答了以上任何信息时，立即用 `edit_file` / `write_file` 更新 `memory_vault/profile/personal_impression.md` 对应条目（把 `（待了解）` 替换为真实事实）。
- 在闲聊收尾或主动搭话时，如果发现某些个人印象维度仍是 `（待了解）`，可以自然地顺手问一个轻量小问题（每次只问一个，不查户口），并调用 `present_choices` 提供 3~4 个贴心选项方便主人直接点选。
"""


async def public_url(url):
    u = urlsplit(url)
    if u.scheme not in ('http', 'https') or not u.hostname or u.username or u.password or u.port not in (None, 80, 443):
        raise ValueError('只允许公开 HTTP(S) 标准端口 URL')
    records = await asyncio.get_running_loop().run_in_executor(
        None,
        lambda: socket.getaddrinfo(u.hostname, u.port or (443 if u.scheme == 'https' else 80)),
    )
    if not records or any(not ipaddress.ip_address(r[4][0]).is_global for r in records):
        raise ValueError('禁止访问内网、环回、链路本地或保留地址')
    return url


class Harness:
    def __init__(self, root: Path, store=None, vault=None, extension_registry: ExtensionRegistry | None = None):
        self.root = root.resolve()
        root.mkdir(parents=True, exist_ok=True)
        (self.root / 'skills').mkdir(parents=True, exist_ok=True)
        (self.root / 'plugins').mkdir(parents=True, exist_ok=True)
        sys_md = self.root / 'SYSTEM.md'
        if not sys_md.exists() or 'personal_impression.md' not in sys_md.read_text('utf-8', errors='replace'):
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
            return self.vault.path(rel)
        path = self.root / p
        if '..' in path.parts or any(x.is_symlink() for x in [path, *list(path.parents)[:len(path.parts) - len(self.root.parts)]]):
            raise ValueError('禁止上级路径或符号链接')
        if not path.resolve().is_relative_to(self.root):
            raise ValueError('超出工作区')
        return path.resolve()

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
                    self.store.sync_from_workspace()
                rel = args['path'] if args['path'].startswith('memory_vault/') else p.relative_to(self.root).as_posix()
                out_res: dict = {'written': rel, 'bytes': len(text.encode())}
                if snap_saved:
                    out_res['previous_backup'] = snap_saved
                    if len(old_text) > len(text) + 80:
                        out_res['warning'] = f'新内容比原文件少 {len(old_text) - len(text)} 字符；旧版已自动备份至 {snap_saved}，如需找回可直接 read_file("{snap_saved}")'
                return out_res
            if name == 'list_dir':
                rel_arg = str(args.get('path') or '.').strip() or '.'
                target = self.path(rel_arg)
                if not target.is_dir():
                    raise ValueError('目标不是目录')
                entries = []
                for child in sorted(target.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))[:200]:
                    if child.name.startswith('.transfers'):
                        continue
                    stat = child.stat()
                    entries.append({
                        'name': child.name + ('/' if child.is_dir() else ''),
                        'type': 'dir' if child.is_dir() else 'file',
                        'bytes': 0 if child.is_dir() else stat.st_size,
                    })
                if rel_arg == '.' and self.vault is not None:
                    entries.append({'name': 'memory_vault/', 'type': 'dir', 'bytes': 0})
                return {'path': rel_arg, 'entries': entries}
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
                timeout_sec = max(5, min(300, int(args.get('timeout') or 60)))
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

                async def collect():
                    output = bytearray()
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
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    kill_group()
                    await proc.wait()
                    raise
                finally:
                    kill_group()
                if self.store is not None:
                    self.store.sync_from_workspace()
                return {'exit_code': proc.returncode, 'output': out.decode('utf-8', errors='replace')}
            if name == 'web_fetch':
                return await self.fetch(args['url'])
            if name == 'web_search':
                q = str(args.get('query') or '').strip()
                if not q:
                    return {'error': '搜索词不能为空'}
                return await multi_web_search(q, max_results=10)
            raise ValueError('未知工具')
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
                arch_doc = sys_md.read_text('utf-8', errors='replace')[:6000]
                self._sys_md_cache = (mtime, arch_doc)
        else:
            arch_doc = DEFAULT_SYSTEM_MD
        return (arch_doc + ('\n\n' + pinned_block if pinned_block else '')).strip()

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
        for _ in range(40):
            tool_indices = [idx for idx, m in enumerate(messages) if m.get('role') == 'tool']
            if len(tool_indices) > 2:
                for idx in tool_indices[:-2]:
                    c_str = str(messages[idx].get('content') or '')
                    if len(c_str) > 16000 and '[早期工具输出已自动折叠]' not in c_str:
                        messages[idx]['content'] = c_str[:1600] + f'\n...[早期工具输出已自动折叠，原长 {len(c_str)} 字符]...'
            msg = await complete(messages, tools=available or None)
            turn_txt = (msg.get('content') or '').strip()
            if turn_txt and (not accumulated or accumulated[-1] != turn_txt):
                accumulated.append(turn_txt)
                if on_event:
                    await on_event({'type': 'turn_text', 'text': '\n\n'.join(accumulated)})
            messages.append(msg)
            calls = msg.get('tool_calls') or []
            if not calls:
                break
            for c in calls[:24]:
                try:
                    args = json.loads(c['function']['arguments'])
                except (ValueError, KeyError):
                    args = {}
                name = c['function']['name']
                if on_event:
                    await on_event({'type': 'tool_start', 'tool': name, 'arguments': args})
                started = asyncio.get_running_loop().time()
                result = await self.execute(name, args) if name in allowed else {'error': '此助手未启用该工具'}
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
                messages.append({'role': 'tool', 'tool_call_id': c['id'], 'content': json.dumps(result, ensure_ascii=False)})
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
