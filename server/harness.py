from __future__ import annotations
import asyncio, fnmatch, ipaddress, json, os, re, signal, socket, sys, time
from pathlib import Path
from urllib.parse import urlsplit, quote_plus
import httpx
from bs4 import BeautifulSoup
from vault_manager import atomic_write

def tool(name, description, properties, required):
    return {'type':'function','function':{'name':name,'description':description,
           'parameters':{'type':'object','properties':properties,'required':required,'additionalProperties':False}}}

STR = {'type':'string'}
INT = {'type':'integer'}
BOOL = {'type':'boolean'}

TOOLS = [
    tool('read_file', '读取工作区或记忆库(memory_vault/)内文件（支持可选起止行号；若传入 .png/.jpg/.webp 等图片文件，会自动调用系统原生多模态视觉秒读图片内容，严禁自己装 OCR 或写脚本读图；也可读取 memory_vault/.history/ 下的历史备份版本）', {'path':STR, 'start_line':INT, 'end_line':INT}, ['path']),
    tool('write_file', '在工作区或记忆库(memory_vault/)创建/覆盖/追加 UTF-8 文件（支持 append=true 追加而不覆盖；修改 memory_vault 文件时会自动在 memory_vault/.history/ 保存旧版本快照）', {'path':STR, 'content':STR, 'append':BOOL}, ['path','content']),
    tool('edit_file', '唯一精确文本替换；不匹配则失败', {'path':STR, 'old':STR, 'new':STR}, ['path','old','new']),
    tool('list_dir', '列出工作区或 memory_vault 目录下的文件与子目录（默认 "."）', {'path':STR}, []),
    tool('glob_files', '按通配符在工作区和 memory_vault 中查找文件（如 "**/*.md" 或 "*.py"）', {'pattern':STR}, ['pattern']),
    tool('grep_search', '在工作区和 memory_vault 中按关键词或正则表达式搜索文件内容并返回行号', {'query':STR, 'path':STR}, ['query']),
    tool('run_bash', '在工作区运行 Bash 命令（最长 120 秒，支持 python3 / pip / curl 等）', {'command':STR, 'timeout':INT}, ['command']),
    tool('bg_task', '管理云端后台常驻进程/长任务（action: start|list|logs|kill；跑完后会自动唤醒你汇报）', {
        'action':STR, 'command':STR, 'task_id':STR, 'notify_on_exit':BOOL
    }, ['action']),
    tool('send_message', '向另一位 Bot 发送消息并让它执行任务（to_bot_id 可填 Bot 的 id 或名字；对方会在当前窗口直接回复或报错）', {'to_bot_id':STR, 'text':STR}, ['to_bot_id','text']),
    tool('manage_bot', '【防呆轮椅工具】查看、创建、修改或删除 Bot（action: list|create|update|delete；可安全修改任何 Bot 的 model、instructions、name）', {
        'action':STR, 'bot_id':STR, 'name':STR, 'model':STR, 'description':STR, 'instructions':STR, 'curiosity_enabled':BOOL
    }, ['action']),
    tool('list_models', '【防呆轮椅工具】直接向当前配置的 API 网关请求 /models，返回所有真实可用的模型 ID 列表', {}, []),
    tool('schedule_task', '【防呆轮椅工具】管理定点闹钟与周期定时任务（action: list|add_timer|add_routine|update|remove；支持精确北京时间钟点 daily_time 如 "07:30" 或 "07:30,19:00"、定点时间 at_time 如 "20:35" 或 "2026-11-05 07:30"、星期过滤 weekdays 如 "1,2,3,4,5"、倒计时 delay_minutes/delay_seconds、间隔小时 interval_hours；到点自动唤醒对应 Bot 执行并推送到手机，不受夜间好奇心静默拦截）', {
        'action':STR, 'goal':STR, 'bot_id':STR, 'task_id':STR,
        'at_time':STR, 'daily_time':STR, 'weekdays':STR,
        'delay_minutes':INT, 'delay_seconds':INT, 'interval_hours':INT, 'enabled':BOOL
    }, ['action']),
    tool('memory_search', '在 memory_vault 所有 Markdown 记忆与时间线中快速检索相关段落', {'query':STR}, ['query']),
    tool('notify_user', '主动向主人手机推送状态栏通知（日常聊天和 Bot 互发消息默认静默，仅在需要提醒主人时调用）', {'title':STR, 'body':STR, 'urgent':BOOL}, ['title','body']),
    tool('cloud_desktop', '操控云端 1C1G Windows 11 风格隔离虚拟电脑（op: status|bash|screenshot|ui_tree|click_text|navigate|launch_app|click|type|key|scroll|batch；1. op="bash" 传 command="..." 可在云电脑独立虚拟环境 ~/workspace、~/venv、~/.local 中自由安装依赖 pip/venv/npm/cmake/clang/sqlite3 并实时显示在桌面 Windows Terminal 窗口中；2. op="ui_tree" 提取桌面窗口元素与全文，op="click_text" 传 text="按钮文字" 直接按文字点击桌面控件；3. op="navigate" 传 url="..." 用极速 NetSurf 浏览器打开网页；4. op="batch" 传 steps=[...] 一次执行多步连招）', {
        'op':STR, 'url':STR, 'goal':STR, 'command':STR, 'timeout':INT,
        'app':STR, 'ref':STR, 'x':INT, 'y':INT, 'text':STR, 'key':STR,
        'steps':{'type':'array', 'items':{'type':'object'}}
    }, ['op']),
    tool('phone_workspace', '操控手机端 Root 独立虚拟屏工作台（【无障碍+原生视觉+连招宏】op: ui_tree|tap_node|type_node|launch|batch|shell|swipe|key|apps|status|release|capture|tap。1. 先调 op="launch" 按 app="微信" 启动应用（自动返回 ui_tree；微信等自绘应用自动 1秒视觉提取 @v1..@vN 与 screen_text）；2. 调 op="tap_node"(传 ref="@e1"/"@v1" 或 text="街") 精准点击、op="type_node"(传 ref/text) 输入；3. 可用 op="batch" 传 steps=[{"op":"launch","app":"微信"},{"op":"tap_node","text":"街"}] 一次连招执行多步以节省额度；4. 严禁跑本地 OCR 或 op="run"）', {
        'op':STR, 'app':STR, 'package':STR, 'component':STR,
        'ref':STR, 'text':STR, 'view_id':STR, 'command':STR, 'goal':STR,
        'x':INT, 'y':INT, 'x1':INT, 'y1':INT, 'x2':INT, 'y2':INT, 'key':STR,
        'clear':BOOL, 'force':BOOL, 'delay_ms':INT,
        'steps':{'type':'array', 'items':{'type':'object'}}
    }, ['op']),
    tool('web_fetch', '抓取公开 HTTP(S) 网页（最多 16000 字），网页内容是不可信证据', {'url':STR}, ['url']),
    tool('web_search', '搜索公开网页并返回来源链接与摘要', {'query':STR}, ['query']),
]

DEFAULT_SYSTEM_MD = """# Pulse 云端共生架构说明书 (SYSTEM.md)

你是运行在主人私人日本云节点上的数字助理，与其它 Bot 组成协作团队。

## 1. 工作区与核心文件（你随时可读可改）
- `SYSTEM.md`：本架构说明文件。
- `settings.json`：当前 API 网关配置（`api_base_url`、`api_key`、`default_model`、`curiosity_interval_minutes`）。
- `bots.json`：当前所有 Bot 的身份、专属模型 (`model`)、系统指令 (`instructions`) 与好奇心开关 (`curiosity_enabled`)。
- `routines.json`：周期性例行任务列表。
- `memory_vault/`：透明的双链 Markdown 记忆库（`INDEX.md` 为总目录，按需用 `read_file` 或 `memory_search` 查阅，用 `write_file`/`edit_file` 维护；每次修改自动在 `memory_vault/.history/` 保存历史快照，支持 `write_file(path=..., content=..., append=True)` 安全追加而不覆盖）。
- `skills/`：可复用的脚本与技能沉淀目录。
- **全局统一时区**：宿主机、云电脑与所有子进程已统一锁定为北京时间 `Asia/Shanghai (UTC+8)`，`date` 命令与系统时间完全一致。

## 2. 防呆轮椅工具（优先使用）
- **查可用模型**：直接调用 `list_models()` 即可拿到网关真实支持的 `/models` 列表（也可在 `run_bash` 中用 `$API_BASE_URL` 和 `$API_KEY`）。
- **改 Bot 模型 / 创建新 Bot**：直接调用 `manage_bot(action="list|create|update|delete", ...)`，如果主人让你给某个还不存在的 Bot（比如“b”或“代码助手”）发消息，你可以先用 `manage_bot(action="create", name="...")` 把它建出来，或者发给现有的 `research`（研究助手）。
- **跨 Bot 协作与把关**：调用 `send_message(to_bot_id, text)` 把任务派给其它 Bot。对方会在当前对话中直接回复结果或报错。
  - 如果对方返回报错（如模型 404/不可用），报错信息会自动附带可用模型列表，你只需调用 `manage_bot(action="update", bot_id=..., model="可用模型名")` 帮它修好，再调 `send_message` 让它重试！
  - 如果对方的成果不够好，直接再调 `send_message` 指出问题让它重做，直到你满意为止。
- **后台长任务与精准闹钟/定时例行**：耗时脚本用 `bg_task(action="start", command=...)` 挂在后台；定时任务用 `schedule_task`：
  - **每日/按周定点执行**：`schedule_task(action="add_routine", daily_time="07:30", weekdays="1,2,3,4,5", goal="...")`（支持精确钟点 `daily_time`，多时间点可用逗号分隔如 `"07:30,18:30"`，支持 `action="update"` 修改现有 `task_id` 的 `daily_time`）；
  - **一次性定点/倒计时闹钟**：`schedule_task(action="add_timer", at_time="20:35", goal="...")` 或 `delay_minutes=15` / `delay_seconds=30`；
  - 所有定点闹钟与例行任务均按北京时间准点触发（哪怕在早 7:30 也不会被 0-8 点好奇心免打扰拦截），到点自动唤醒对应 Bot 执行并向手机推送通知。
- **安静原则**：日常对话与 Bot 间协作默认静默；只有在重要任务完成、报错需要主人介入、或主动关心主人时，才调用 `notify_user(title, body, urgent)` 推送手机通知栏。
- **原生多模态看图能力（秒级直读，严禁装本地 OCR）**：
  - 主人上传在对话里的图片（`uploads/*.png|jpg`）会在首轮自动通过系统原生多模态视觉提取全文并附在消息里；
  - 对任何图片文件调用 `read_file("uploads/xxx.png")` 或 `read_file("phone-latest.jpg")` 也会直接返回原生视觉读取结果，**绝对禁止运行 `apt-get install tesseract`、`pip install pillow` 或手写 OCR 脚本**！
- **手机独立虚拟屏规范（Root 无障碍树 + 微信 1 秒原生视觉节点融合）**：
  1. 启动应用：`phone_workspace(op="launch", app="微信")`（虚拟屏永不自动锁屏，且会自动返回节点树 `ui_tree`；遇到微信等屏蔽无障碍的应用，系统会自动在 1 秒内用原生视觉生成 `@v1, @v2...` 节点与 `screen_text` 画面全文）；
  2. 点击与输入：直接调用 `phone_workspace(op="tap_node", ref="@e1")`（或 `@v1`，或直接传 `text="街"`）点击、`phone_workspace(op="type_node", ref="@e2", text="...")` 输入！就算在微信自绘界面里，`tap_node(text="街")` 也会自动通过内置视觉定位坐标并完成点击；
  3. 严禁自己跑 `uiautomator dump` 或调用 `op="run"` 子代理；任务完成后虚拟屏会自动延迟关闭，也可主动调 `phone_workspace(op="release")` 立即关闭。
- **Windows 11 风格隔离云电脑规范 (`cloud_desktop`)**：
  1. 云电脑拥有独立的隔离存储 (`~/workspace`)、预置 Python 虚拟环境 (`~/venv`) 与本地安装前缀 (`~/.local`，已预装 `gcc`, `g++`, `clang`, `cmake`, `sqlite3`, `jq`, `rg`, `tree`, `git`, `node`, `python3`)，且 `nproc` / `free -m` 真实反映 1C1G 容器配额；
  2. 装环境、编译或执行命令直接调用 `cloud_desktop(op="bash", command="pip install ... && python3 ...")`，支持并发与超时自动回收，永不卡队列，`control_ready` 恒为 `true`，且命令和输出会实时同步显示在云电脑桌面的 Windows Terminal 窗口与主人手机前端的虚拟电脑画面上；
  3. 图形界面操作支持 `cloud_desktop(op="ui_tree")` 提取画面控件与文字、`cloud_desktop(op="click_text", text="按钮文字")` 按文字直接点击，以及 `op="navigate"`（极速 `NetSurf` / `Dillo`，已彻底移除 Chromium）、`op="launch_app"`、`op="batch"` 多步连招。
"""

async def public_url(url):
    u = urlsplit(url)
    if u.scheme not in ('http','https') or not u.hostname or u.username or u.password or u.port not in (None,80,443):
        raise ValueError('只允许公开 HTTP(S) 标准端口 URL')
    records = await asyncio.get_running_loop().run_in_executor(None,lambda:socket.getaddrinfo(u.hostname,u.port or (443 if u.scheme=='https' else 80)))
    if not records or any(not ipaddress.ip_address(r[4][0]).is_global for r in records):
        raise ValueError('禁止访问内网、环回、链路本地或保留地址')
    return url

class Harness:
    def __init__(self, root: Path, store=None, vault=None):
        self.root = root.resolve()
        root.mkdir(parents=True, exist_ok=True)
        (self.root / 'skills').mkdir(parents=True, exist_ok=True)
        sys_md = self.root / 'SYSTEM.md'
        if not sys_md.exists() or '@v1' not in sys_md.read_text('utf-8', errors='replace'):
            atomic_write(sys_md, DEFAULT_SYSTEM_MD)
        self.store = store
        self.vault = vault
        self.vision_reader = None
        self.custom_handlers = {}
        self._web_cache: dict[str, tuple[float, dict]] = {}
        self._sys_md_cache: tuple[int, str] | None = None

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
        if '..' in path.parts or any(x.is_symlink() for x in [path, *list(path.parents)[:len(path.parts)-len(self.root.parts)]]):
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
                async with client.stream('GET', url, headers={'User-Agent':'ProactiveAgent/2.5 (+personal-research)'}) as r:
                    if r.is_redirect:
                        url = str(r.url.join(r.headers['location']))
                        continue
                    r.raise_for_status()
                    if not any(x in r.headers.get('content-type','') for x in ('text/','json','xml')):
                        raise ValueError('仅接受文本网页')
                    buf = bytearray()
                    async for chunk in r.aiter_bytes():
                        buf.extend(chunk)
                        if len(buf) > 512_000:
                            raise ValueError('网页超过 512 KB')
                    soup = BeautifulSoup(buf.decode('utf-8', errors='replace'), 'html.parser')
                    for node in soup(['script','style','nav','footer']):
                        node.decompose()
                    text = soup.get_text(' ', strip=True)
                    links = [{'title':a.get_text(' ', strip=True)[:180], 'url':a['href']} for a in soup.select('a[href]') if a['href'].startswith('http')][:16]
                    res = {'source':url, 'text':text[:16000], 'links':links}
                    if len(self._web_cache) > 120:
                        self._web_cache.clear()
                    self._web_cache[orig_url] = (now, res)
                    return res
            raise ValueError('重定向过多')

    async def execute(self, name, args):
        try:
            if name in self.custom_handlers:
                return await self.custom_handlers[name](args)
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
                    sliced = '\n'.join(f'{idx}: {lines[idx-1]}' for idx in range(s, e + 1) if 1 <= idx <= len(lines))
                    return {'content':sliced[:160000], 'total_lines':len(lines), 'range':f'{s}-{e}'}
                return {'content':raw_text[:160000]}
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
                out_res: dict = {'written':rel, 'bytes':len(text.encode())}
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
                    entries.append({'name':'memory_vault/', 'type':'dir', 'bytes':0})
                return {'path':rel_arg, 'entries':entries}
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
                return {'pattern':pattern, 'matches':matches[:200]}
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
                                    hits.append({'file':rel, 'line':idx, 'text':line[:220]})
                                    if len(hits) >= 80:
                                        return {'query':query, 'hits':hits, 'truncated':True}
                        except Exception:
                            continue
                return {'query':query, 'hits':hits}
            if name == 'memory_search':
                query = str(args.get('query') or '').strip()
                if not query or self.vault is None:
                    return {'hits':[]}
                rx = re.compile(re.escape(query), re.IGNORECASE)
                hits = []
                for f in self.vault.files():
                    content = self.vault.read(f['path'])
                    if rx.search(content) or rx.search(f['path']):
                        hits.append({'path':'memory_vault/' + f['path'], 'snippet':content[:800]})
                return {'query':query, 'hits':hits[:20]}
            if name == 'run_bash':
                if sys.platform != 'linux':
                    return {'error':'Bash 隔离执行仅在 Linux 部署节点可用'}
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
                    sys.executable, str(Path(__file__).with_name('sandbox.py')), str(self.root), args['command'],
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, start_new_session=True, env=env
                )
                def kill_group():
                    try: os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError: pass
                async def collect():
                    output = bytearray()
                    while True:
                        chunk = await proc.stdout.read(4096)
                        if not chunk: break
                        output.extend(chunk[:32000-len(output)])
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
                return {'exit_code':proc.returncode, 'output':out.decode('utf-8', errors='replace')}
            if name == 'web_fetch':
                return await self.fetch(args['url'])
            if name == 'web_search':
                rss_url = 'https://www.bing.com/search?format=rss&q=' + quote_plus(args['query'])
                res = await self.fetch(rss_url)
                return res
            raise ValueError('未知工具')
        except Exception as e:
            return {'error':f'{type(e).__name__}: {str(e)[:300]}'}

    def system_instructions(self, context: str) -> str:
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
        return arch_doc + '\n\n' + context

    async def run(self, goal, complete, context, allowed_tools=None, on_event=None):
        allowed = set(t['function']['name'] for t in TOOLS) if allowed_tools is None else set(allowed_tools)
        available = [t for t in TOOLS if t['function']['name'] in allowed]
        messages = [{'role':'system', 'content':self.system_instructions(context)}, {'role':'user', 'content':goal}]
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
                    await on_event({'type':'turn_text', 'text':'\n\n'.join(accumulated)})
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
                    await on_event({'type':'tool_start', 'tool':name, 'arguments':args})
                started = asyncio.get_running_loop().time()
                result = await self.execute(name, args) if name in allowed else {'error':'此助手未启用该工具'}
                entry = {'tool':name, 'arguments':args, 'result':result, 'duration_ms':round((asyncio.get_running_loop().time()-started)*1000)}
                trace.append(entry)
                if on_event:
                    await on_event({'type':'tool_end', 'entry':entry})
                messages.append({'role':'tool', 'tool_call_id':c['id'], 'content':json.dumps(result, ensure_ascii=False)})
        final_text = '\n\n'.join(accumulated).strip() if accumulated else (msg.get('content') or '').strip()
        if not final_text and trace:
            summary_msg = await complete(
                messages + [{'role':'user', 'content':'请基于以上工具执行结果，直接用自然清晰的语言给出最终答复，不要再调用工具。'}],
                tools=None
            )
            sum_txt = (summary_msg.get('content') or '').strip()
            if sum_txt and (not accumulated or accumulated[-1] != sum_txt):
                accumulated.append(sum_txt)
            final_text = '\n\n'.join(accumulated).strip()
        return {'text':final_text or '本轮没有生成可验收成果。', 'trace':trace}
