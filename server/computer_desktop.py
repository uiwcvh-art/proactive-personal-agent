"""This project's 1C1G Windows 11 Style Ultra-Lightweight Linux Cloud Computer for AI Agents & User Takeover.

Runs on its own isolated X display (:91) with Openbox + Windows 11 centered taskbar (tint2) + lightweight apps:
- NetSurf & Dillo (ultra-low RAM graphical browsers, ~20-35MB, zero Chromium bloat!)
- Windows Terminal / XTerm (live agent command console + interactive shell with isolated ~/venv)
- File Explorer / PCManFM (file manager)
- Notepad / Mousepad (code/text editor)
Supports 1-RTT action+frame response, isolated virtual environment (~/venv + ~/.local + /tmp),
and structured Agent UI/Window state introspection.
"""
from __future__ import annotations
import asyncio, base64, contextlib, hashlib, hmac, io, json, os, re, secrets, shutil, subprocess, sys, time, signal
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from computer_security import PublicWebProxy, public_addresses
from computer_models import WIDTH, HEIGHT, RunInput, InputAction, NavigateInput, SteerInput
from computer_files import WorkspaceFiles, install_file_routes

HOME_HTML = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>Windows 11 Cloud PC · Pulse Agent OS</title><style>
body{margin:0;background:#0F1420;color:#F3F6FC;font-family:sans-serif;padding:22px 26px}
.win{background:#182032;border:1px solid #2B3754;padding:24px 28px;margin-bottom:16px}
.badge{color:#60CDFF;font-weight:bold;font-size:12px;letter-spacing:1.2px}
h1{font-size:24px;color:#FFFFFF;margin:8px 0 10px}
p{line-height:1.6;color:#B8C4DC;font-size:14px;margin:8px 0 16px}
.grid{width:100%;border-collapse:separate;border-spacing:10px}
.tile{background:#212B42;border:1px solid #314063;padding:12px 14px;color:#FFFFFF;font-size:13px}
.tile b{color:#60CDFF;display:block;margin-bottom:4px;font-size:14px}
.status{background:#132A26;border:1px solid #1E564B;color:#66E8C4;padding:10px 14px;font-size:13px}
</style><body>
<div class="win">
  <div class="badge">❖ WINDOWS 11 CLOUD PC · 1C1G AGENT VIRTUAL BOX</div>
  <h1>独立虚拟云电脑已就绪</h1>
  <p>已移除臃肿的 Chromium，全系采用毫秒级轻量级原生桌面组件，并预置独立隔离的 Python 虚拟环境 (<code>~/venv</code>) 与包管理器权限。</p>
  <div class="status">✓ 隔离存储与虚拟环境已挂载 (<code>~/workspace</code> · <code>~/venv</code> · <code>~/.local</code>) &nbsp;|&nbsp; ✓ 1-RTT 极速触控与 Agent 实时同步</div>
  <table class="grid"><tr>
    <td class="tile"><b>⚡ Edge Lite (NetSurf)</b>25MB 极速图形网页浏览</td>
    <td class="tile"><b>💻 Windows Terminal</b>支持 pip / venv / gcc / git</td>
    <td class="tile"><b>📁 File Explorer</b>PCManFM 图形文件管理</td>
    <td class="tile"><b>📝 Notepad</b>Mousepad 代码与文本编辑</td>
  </tr></table>
</div></body></html>'''

# Windows 11 Centered Acrylic Taskbar configuration for tint2
TINT2_RC = """# Windows 11 Centered Acrylic Taskbar
panel_items = TSC
panel_size = 100% 40
panel_margin = 0 0
panel_padding = 12 5 10
panel_background_id = 1
wm_menu = 1
panel_dock = 0
panel_position = bottom center horizontal
panel_layer = top
panel_monitor = all
autohide = 0

# Background 1: Win11 Dark Acrylic Bar
rounded = 0
border_width = 1
border_sides = T
background_color = #141824 94
border_color = #2c3652 100

# Background 2: Win11 Active Pill (Ice Blue Accent Bottom Border)
rounded = 7
border_width = 2
border_sides = B
background_color = #28324b 92
border_color = #60cdff 100

# Background 3: Win11 Inactive Task Pill
rounded = 7
border_width = 0
background_color = #1d2436 80
border_color = #2c3652 50

# Taskbar (Centered like Windows 11!)
taskbar_mode = single_desktop
taskbar_padding = 6 2 8
taskbar_background_id = 0
taskbar_name = 1
taskbar_name_padding = 10 4
taskbar_name_background_id = 2
taskbar_name_active_background_id = 2
taskbar_name_font = Sans Bold 9.5
taskbar_name_font_color = #60cdff 100
taskbar_name_active_font_color = #60cdff 100
task_icon = 1
task_text = 1
task_centered = 1
task_maximum_size = 190 30
task_padding = 10 4 6
task_font = Sans 9.5
task_font_color = #d8e2f8 92
task_active_font_color = #ffffff 100
task_background_id = 3
task_active_background_id = 2

# System Tray
systray_padding = 6 4 6
systray_background_id = 0
systray_sort = ascending
systray_icon_size = 18

# Win11 Right Corner Clock
time1_format = %H:%M
time1_font = Sans Bold 9.5
time2_format = %Y-%m-%d
time2_font = Sans 7.5
clock_font_color = #f3f6fc 96
clock_padding = 12 2
clock_background_id = 0
"""


class CloudDesktop:
    def __init__(self):
        self.home = Path(os.getenv('HOME', '/nonexistent'))
        self.files = self.home / 'Downloads'
        self.workspace = self.home / 'workspace'
        self.venv_dir = self.home / 'venv'
        self.live_log = self.workspace / '.agent_terminal.log'
        self.children = []
        self.browser_proc = None
        self.term_proc = None
        self.proxy = None
        self.task = None
        self.control = 'user'
        self.step_running = False
        self.job = None
        self.error = ''
        self.clipboard = None
        self.input_lock = asyncio.Lock()
        self.started = False
        self.current_url = ''
        self.current_title = 'Windows 11 Cloud PC (1C1G)'
        self.last_bash_cmd = ''
        self.last_bash_output = ''
        self.last_frame_hash = ''
        self.last_frame_b64 = ''
        self.last_frame_at = 0.0

    def _proxy_env(self) -> dict[str, str]:
        venv_bin = str(self.venv_dir / 'bin')
        local_bin = str(self.home / '.local' / 'bin')
        cur_path = os.environ.get('PATH', '/usr/local/bin:/usr/bin:/bin')
        return {
            'PATH': f'{venv_bin}:{local_bin}:{cur_path}',
            'VIRTUAL_ENV': str(self.venv_dir),
            'PYTHONUSERBASE': str(self.home / '.local'),
            'PREFIX': str(self.home / '.local'),
            'CMAKE_INSTALL_PREFIX': str(self.home / '.local'),
            'npm_config_prefix': str(self.home / '.local'),
            'PIP_CACHE_DIR': str(self.home / '.cache' / 'pip'),
            'TMPDIR': str(self.home / 'tmp'),
            'TZ': 'Asia/Shanghai',
            'http_proxy': 'http://127.0.0.1:18194',
            'https_proxy': 'http://127.0.0.1:18194',
            'HTTP_PROXY': 'http://127.0.0.1:18194',
            'HTTPS_PROXY': 'http://127.0.0.1:18194',
        }

    def _quota_info(self) -> dict:
        mem_limit_mb = 1024
        mem_used_mb = 95
        try:
            for cg in (
                Path('/sys/fs/cgroup/system.slice/proactive-desktop.service'),
                Path('/sys/fs/cgroup'),
            ):
                m_cur = cg / 'memory.current'
                m_max = cg / 'memory.max'
                if m_cur.exists():
                    mem_used_mb = max(1, round(int(m_cur.read_text().strip()) / (1024 * 1024)))
                if m_max.exists():
                    raw_max = m_max.read_text().strip()
                    if raw_max.isdigit():
                        mem_limit_mb = max(128, round(int(raw_max) / (1024 * 1024)))
                if m_cur.exists():
                    break
        except Exception:
            pass
        disk_free_gb = 0.0
        try:
            usage = shutil.disk_usage(self.workspace)
            disk_free_gb = round(usage.free / (1024 ** 3), 2)
        except Exception:
            pass
        return {
            'cpus': 1,
            'memory_limit_mb': mem_limit_mb,
            'memory_used_mb': min(mem_used_mb, mem_limit_mb),
            'memory_free_mb': max(0, mem_limit_mb - mem_used_mb),
            'disk_free_gb': disk_free_gb,
        }

    def _setup_win11_environment(self):
        """Prepare isolated virtual environment, honest 1C1G quota shims, bashrc, and Windows 11 dark bloom background."""
        for sub in ('.local/bin', '.cache/pip', 'tmp', 'Downloads'):
            (self.home / sub).mkdir(parents=True, exist_ok=True)
        local_bin = self.home / '.local' / 'bin'
        # Honest container quota wrappers so `nproc` and `free` report the true 1C1G container quota instead of host 2C/5.8G
        nproc_shim = local_bin / 'nproc'
        nproc_shim.write_text('#!/bin/sh\necho 1\n', encoding='utf-8')
        nproc_shim.chmod(0o755)
        free_shim = local_bin / 'free'
        free_shim.write_text(
            '#!/usr/bin/env python3\n'
            'import sys\n'
            'from pathlib import Path\n'
            'limit_b = 1024 * 1024 * 1024\n'
            'used_b = 96 * 1024 * 1024\n'
            'for p in [Path("/sys/fs/cgroup/system.slice/proactive-desktop.service"), Path("/sys/fs/cgroup")]:\n'
            '    try:\n'
            '        if (p / "memory.max").exists():\n'
            '            v = (p / "memory.max").read_text().strip()\n'
            '            if v.isdigit(): limit_b = int(v)\n'
            '        if (p / "memory.current").exists():\n'
            '            used_b = int((p / "memory.current").read_text().strip())\n'
            '            break\n'
            '    except Exception:\n'
            '        pass\n'
            'avail_b = max(0, limit_b - used_b)\n'
            'args = " ".join(sys.argv[1:])\n'
            'if "-h" in args:\n'
            '    print("               total        used        free      shared  buff/cache   available")\n'
            '    print(f"Mem:          {limit_b/1048576:5.0f}Mi      {used_b/1048576:5.0f}Mi     {avail_b/1048576:5.0f}Mi         0Mi         0Mi     {avail_b/1048576:5.0f}Mi")\n'
            '    print("Swap:            0B          0B          0B")\n'
            'else:\n'
            '    div = 1048576 if "-m" in args else (1073741824 if "-g" in args else 1024)\n'
            '    print("               total        used        free      shared  buff/cache   available")\n'
            '    print(f"Mem:    {limit_b//div:12d}{used_b//div:12d}{avail_b//div:12d}           0           0{avail_b//div:12d}")\n'
            '    print("Swap:              0           0           0")\n',
            encoding='utf-8'
        )
        free_shim.chmod(0o755)
        bashrc = self.home / '.bashrc'
        bashrc_content = (
            'export PATH="$HOME/venv/bin:$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"\n'
            'export VIRTUAL_ENV="$HOME/venv"\n'
            'export PREFIX="$HOME/.local"\n'
            'export TZ="Asia/Shanghai"\n'
            'export TMPDIR="$HOME/tmp"\n'
            'export http_proxy="http://127.0.0.1:18194"\n'
            'export https_proxy="http://127.0.0.1:18194"\n'
            'export PS1="\\[\\e[1;36m\\]❖ win11-cloud\\[\\e[0m\\]:\\[\\e[1;34m\\]\\w\\[\\e[0m\\]\\$ "\n'
        )
        with contextlib.suppress(Exception):
            bashrc.write_text(bashrc_content, encoding='utf-8')
        if not self.live_log.exists():
            self.live_log.write_text(
                '\033[1;36m❖ Windows 11 Cloud PC · Agent Isolated Virtual Environment\033[0m\n'
                '  Storage : ~/workspace (Isolated Landlock Box) | Python Venv: ~/venv\n'
                '  Network : Public Web & PyPI/GitHub Enabled (Host/LAN Strictly Blocked)\n'
                '------------------------------------------------------------------------\n',
                encoding='utf-8'
            )
        # Create isolated Python venv in background if not present
        if not (self.venv_dir / 'bin' / 'python3').exists():
            subprocess.Popen(
                [sys.executable, '-m', 'venv', str(self.venv_dir)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )

    async def start(self):
        if sys.platform != 'linux' or os.geteuid() == 0:
            raise RuntimeError('Cloud computer needs its dedicated Linux account')
        self.files.mkdir(parents=True, exist_ok=True)
        self.workspace.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(self._setup_win11_environment)

        os.environ['DISPLAY'] = ':91'
        os.environ['XAUTHORITY'] = str(self.home / '.Xauthority')
        cookie = secrets.token_hex(16)
        subprocess.run(
            [os.environ['COMPUTER_XAUTH_EXECUTABLE'], '-f', os.environ['XAUTHORITY']],
            input=('add :91 . ' + cookie + '\n').encode(),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=5
        )
        self.spawn(['/usr/bin/Xvfb', ':91', '-screen', '0', f'{WIDTH}x{HEIGHT}x24', '-nolisten', 'tcp', '-noreset', '-auth', os.environ['XAUTHORITY']])
        for _ in range(80):
            if Path('/tmp/.X11-unix/X91').exists():
                break
            if self.children[0].poll() is not None:
                raise RuntimeError('Dedicated X display failed to start')
            await asyncio.sleep(.05)
        else:
            raise RuntimeError('Dedicated X display timed out')

        # Windows 11 Midnight Sapphire root backdrop
        if shutil.which('xsetroot'):
            with contextlib.suppress(Exception):
                subprocess.run(['/usr/bin/xsetroot', '-solid', '#0F172A'], env=os.environ.copy(), timeout=3)

        self.spawn(['/usr/bin/openbox', '--config-file', str(Path(__file__).with_name('computer-openbox.xml'))])
        await asyncio.sleep(0.15)
        if shutil.which('wmctrl'):
            with contextlib.suppress(Exception):
                subprocess.run(['/usr/bin/wmctrl', '-N', '❖ Start'], env=os.environ.copy(), timeout=2)

        if shutil.which('tint2'):
            tint2_cfg = self.home / '.tint2rc'
            tint2_cfg.write_text(TINT2_RC, encoding='utf-8')
            self.spawn(['/usr/bin/tint2', '-c', str(tint2_cfg)])

        self.proxy = PublicWebProxy(18194, blocked_hosts={'216.23.82.196'})
        await self.proxy.start()

        # Open Windows 11 Welcome Dashboard in NetSurf (left/main area) + live terminal ready
        welcome_file = self.workspace / '.welcome.html'
        welcome_file.write_text(HOME_HTML, encoding='utf-8')
        welcome_uri = f'file://{welcome_file}'
        if shutil.which('netsurf-gtk'):
            self.browser_proc = self.spawn(['/usr/bin/netsurf-gtk', welcome_uri], extra_env=self._proxy_env())
            await asyncio.sleep(0.25)
            if shutil.which('wmctrl'):
                with contextlib.suppress(Exception):
                    subprocess.run(['/usr/bin/wmctrl', '-r', 'NetSurf', '-e', '0,24,20,1232,724'], env=os.environ.copy(), timeout=2)
        elif shutil.which('dillo'):
            self.browser_proc = self.spawn(['/usr/bin/dillo', welcome_uri], extra_env=self._proxy_env())

        self.started = True

    def spawn(self, args, cwd=None, extra_env=None):
        env = os.environ.copy()
        env.update(self._proxy_env())
        if extra_env:
            env.update(extra_env)
        child = subprocess.Popen(
            args, cwd=cwd or str(self.workspace),
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env
        )
        self.children.append(child)
        return child

    async def close(self):
        await self.stop()
        if self.clipboard:
            self.clipboard.terminate()
        if self.proxy:
            await self.proxy.close()
        for child in reversed(self.children):
            if child.poll() is None:
                child.terminate()
        for child in self.children:
            with contextlib.suppress(subprocess.TimeoutExpired):
                await asyncio.to_thread(child.wait, 2)

    def _list_windows(self) -> list[dict]:
        wmctrl = shutil.which('wmctrl')
        if not wmctrl:
            return []
        windows = []
        try:
            out = subprocess.check_output([wmctrl, '-lG'], env=os.environ.copy(), timeout=1.2).decode('utf-8', errors='replace')
            for line in out.splitlines():
                parts = line.split(None, 7)
                if len(parts) >= 8:
                    wid, desk, x, y, w, h, host, title = parts[0], parts[1], int(parts[2]), int(parts[3]), int(parts[4]), int(parts[5]), parts[6], parts[7].strip()
                    if 'tint2' in title.lower():
                        continue
                    windows.append({'id': wid, 'title': title, 'x': x, 'y': y, 'width': w, 'height': h})
        except Exception:
            pass
        return windows

    def _active_windows_summary(self) -> str:
        wins = self._list_windows()
        if wins:
            return ' · '.join(w['title'] for w in wins[-2:])
        return self.current_title

    def status(self):
        wins = self._list_windows()
        title = ' · '.join(w['title'] for w in wins[-2:]) if wins else self.current_title
        return {
            'available': self.started,
            'width': WIDTH,
            'height': HEIGHT,
            'control': 'agent',
            'control_ready': True,
            'quota': self._quota_info(),
            'url': self.current_url,
            'title': title,
            'windows': wins,
            'venv_ready': (self.venv_dir / 'bin' / 'python3').exists(),
            'last_bash_cmd': self.last_bash_cmd,
            'last_bash_output': self.last_bash_output[-1200:] if self.last_bash_output else '',
            'job': None,
            'engine': 'Windows 11 Cloud PC · 1C1G Isolated Agent Box',
            'error': self.error,
        }

    def frame(self, quality: int = 62, since_hash: str = ''):
        import mss
        from PIL import Image
        with mss.mss(display=':91') as capture:
            shot = capture.grab({'left': 0, 'top': 0, 'width': WIDTH, 'height': HEIGHT})
            raw_bytes = shot.raw
            h = hashlib.md5(raw_bytes[::64]).hexdigest()[:16]
            title = self._active_windows_summary()
            if since_hash and since_hash == h:
                return {
                    'unchanged': True,
                    'hash': h,
                    'width': WIDTH,
                    'height': HEIGHT,
                    'url': self.current_url,
                    'title': title,
                    'control': self.control,
                    'captured_at': time.time(),
                }
            if h == self.last_frame_hash and self.last_frame_b64 and quality == 62:
                b64 = self.last_frame_b64
            else:
                image = Image.frombytes('RGB', shot.size, shot.bgra, 'raw', 'BGRX')
                output = io.BytesIO()
                image.save(output, 'JPEG', quality=max(30, min(90, quality)), optimize=False)
                b64 = base64.b64encode(output.getvalue()).decode()
                self.last_frame_hash = h
                self.last_frame_b64 = b64
                self.last_frame_at = time.time()
        return {
            'image': b64,
            'hash': h,
            'unchanged': False,
            'mime': 'image/jpeg',
            'width': WIDTH,
            'height': HEIGHT,
            'url': self.current_url,
            'title': title,
            'control': self.control,
            'captured_at': time.time(),
        }

    async def takeover(self):
        self.control = 'user'
        return self.status()

    async def handback(self):
        self.control = 'agent'
        return self.status()

    async def stop(self):
        if self.task and not self.task.done():
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self.task
        self.step_running = False
        self.control = 'user'
        self.job = None
        return self.status()

    def _auto_takeover_for_interactive(self):
        if not self.started:
            raise ValueError('云电脑尚未就绪')
        self.control = 'agent'

    def _open_url_in_lite_browser(self, url: str, prefer_dillo: bool = False):
        if self.browser_proc and self.browser_proc.poll() is None:
            with contextlib.suppress(Exception):
                self.browser_proc.terminate()
        with contextlib.suppress(Exception):
            subprocess.run(['/usr/bin/pkill', '-f', 'netsurf-gtk'], timeout=2)
        if prefer_dillo and shutil.which('dillo'):
            self.browser_proc = self.spawn(['/usr/bin/dillo', url], extra_env=self._proxy_env())
        elif shutil.which('netsurf-gtk'):
            self.browser_proc = self.spawn(['/usr/bin/netsurf-gtk', url], extra_env=self._proxy_env())
        elif shutil.which('dillo'):
            self.browser_proc = self.spawn(['/usr/bin/dillo', url], extra_env=self._proxy_env())
        self.current_url = url
        self.current_title = f'Edge Lite (NetSurf) · {url}'

    async def navigate(self, url: str, include_frame: bool = True):
        self._auto_takeover_for_interactive()
        parsed = urlsplit(url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError('请输入公开网页的 HTTP(S) 地址')
        await public_addresses(parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80), {'216.23.82.196'})
        await asyncio.to_thread(self._open_url_in_lite_browser, url, False)
        await asyncio.sleep(0.35)
        if shutil.which('wmctrl'):
            with contextlib.suppress(Exception):
                subprocess.run(['/usr/bin/wmctrl', '-r', 'NetSurf', '-e', '0,20,16,1240,728'], env=os.environ.copy(), timeout=2)
                subprocess.run(['/usr/bin/wmctrl', '-a', 'NetSurf'], env=os.environ.copy(), timeout=2)
        res = self.status()
        if include_frame:
            await asyncio.sleep(0.15)
            res.update(await asyncio.to_thread(self.frame, 60))
        return res

    def _ensure_live_terminal_window(self):
        wmctrl = shutil.which('wmctrl')
        if wmctrl and subprocess.run([wmctrl, '-a', 'Windows Terminal'], env=os.environ.copy()).returncode == 0:
            return
        if shutil.which('xterm'):
            self.term_proc = self.spawn([
                '/usr/bin/xterm',
                '-title', 'Windows Terminal · Agent Isolated Shell',
                '-fa', 'DejaVu Sans Mono',
                '-fs', '11',
                '-bg', '#0C0F17',
                '-fg', '#E2E8F0',
                '-geometry', '116x32+36+24',
                '-e', '/bin/bash', '-c', f'tail -n 40 -f {self.live_log}; exec /bin/bash -i'
            ], cwd=str(self.workspace))

    def _launch_or_focus_desktop_app(self, app_name: str, arg_text: str = ''):
        app_key = (app_name or '').strip().lower()
        wmctrl = shutil.which('wmctrl')
        if app_key in ('lite_browser', 'netsurf', 'browser', 'lite', 'edge'):
            target_url = arg_text.strip() if arg_text.strip().startswith('http') else (self.current_url or 'https://news.ycombinator.com')
            if not arg_text.strip().startswith('http') and wmctrl and subprocess.run([wmctrl, '-a', 'NetSurf'], env=os.environ.copy()).returncode == 0:
                return 'netsurf_focused'
            self._open_url_in_lite_browser(target_url, prefer_dillo=False)
            return 'netsurf-gtk'
        if app_key in ('dillo', 'fast_browser'):
            target_url = arg_text.strip() if arg_text.strip().startswith('http') else (self.current_url or 'https://lite.duckduckgo.com/lite/')
            if not arg_text.strip().startswith('http') and wmctrl and subprocess.run([wmctrl, '-a', 'Dillo'], env=os.environ.copy()).returncode == 0:
                return 'dillo_focused'
            if shutil.which('dillo'):
                self.spawn(['/usr/bin/dillo', target_url], extra_env=self._proxy_env())
                self.current_url = target_url
                self.current_title = f'Dillo · {target_url}'
                return 'dillo'
            return 'no_dillo'
        if app_key in ('terminal', 'xterm', 'bash', 'shell', 'powershell'):
            if wmctrl and subprocess.run([wmctrl, '-a', 'Windows Terminal'], env=os.environ.copy()).returncode == 0:
                return 'terminal_focused'
            if shutil.which('xterm'):
                self.spawn([
                    '/usr/bin/xterm',
                    '-title', 'Windows Terminal · ~/workspace (venv)',
                    '-fa', 'DejaVu Sans Mono',
                    '-fs', '11',
                    '-bg', '#0C0F17',
                    '-fg', '#F1F5F9',
                    '-geometry', '116x32+36+24',
                    '-e', '/bin/bash', '--rcfile', str(self.home / '.bashrc'), '-i',
                ], cwd=str(self.workspace))
                self.current_title = 'Windows Terminal (~/venv)'
                return 'xterm'
        if app_key in ('files', 'file_manager', 'pcmanfm', 'folder', 'explorer'):
            if wmctrl and subprocess.run([wmctrl, '-a', 'workspace'], env=os.environ.copy()).returncode == 0:
                return 'files_focused'
            if shutil.which('pcmanfm'):
                self.spawn(['/usr/bin/pcmanfm', str(self.workspace)], cwd=str(self.workspace))
                self.current_title = 'File Explorer (PCManFM)'
                return 'pcmanfm'
        if app_key in ('editor', 'mousepad', 'notepad', 'code'):
            if wmctrl and subprocess.run([wmctrl, '-a', 'Mousepad'], env=os.environ.copy()).returncode == 0 and not arg_text:
                return 'editor_focused'
            if shutil.which('mousepad'):
                cmd = ['/usr/bin/mousepad']
                if arg_text and not arg_text.startswith('http'):
                    cmd.append(str(self.workspace / Path(arg_text).name))
                self.spawn(cmd, cwd=str(self.workspace))
                self.current_title = 'Notepad (Mousepad)'
                return 'mousepad'
        return 'unknown_app'

    async def exec_bash(self, command: str, timeout: int = 25) -> dict:
        """Execute command inside the isolated virtual computer environment and mirror it to the live desktop terminal."""
        cmd = (command or '').strip()
        if not cmd:
            return {'exit_code': 0, 'output': ''}
        self.last_bash_cmd = cmd[:400]
        await asyncio.to_thread(self._ensure_live_terminal_window)
        with contextlib.suppress(Exception):
            with self.live_log.open('a', encoding='utf-8') as f:
                f.write(f'\n\033[1;36m❖ win11-cloud:~/workspace$\033[0m {cmd}\n')

        process = await asyncio.create_subprocess_exec(
            sys.executable, str(Path(__file__).with_name('computer_shell.py')), str(self.workspace), cmd,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, start_new_session=True
        )
        async def _read():
            output = bytearray()
            while chunk := await process.stdout.read(4096):
                output.extend(chunk[:24000 - len(output)])
                if len(output) >= 24000:
                    break
            return output
        eff_timeout = max(3, min(90, int(timeout or 25)))
        timed_out = False
        try:
            raw_out = await asyncio.wait_for(_read(), eff_timeout)
            if len(raw_out) < 24000:
                await asyncio.wait_for(process.wait(), 2)
        except asyncio.TimeoutError:
            timed_out = True
            raw_out = b''
        finally:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            with contextlib.suppress(Exception):
                await asyncio.wait_for(process.wait(), 2)

        out_str = raw_out.decode('utf-8', errors='replace')
        if timed_out:
            out_str += f'\n[命令在 {eff_timeout} 秒后已由云电脑安全限时停止]'
        self.last_bash_output = out_str[-4000:]
        with contextlib.suppress(Exception):
            with self.live_log.open('a', encoding='utf-8') as f:
                f.write(out_str[:4000] + ('\n' if not out_str.endswith('\n') else ''))
        return {
            'exit_code': -1 if timed_out else (process.returncode or 0),
            'output': out_str,
            'timed_out': timed_out,
            'cwd': str(self.workspace),
            'venv': str(self.venv_dir),
            'quota': self._quota_info(),
        }

    async def _exec_single_input(self, kind: str, x: int = 0, y: int = 0, text: str = '', command: str = '', key: str = '', direction: str = 'down', amount: int = 3, app: str = '', timeout: int = 25) -> dict | None:
        def run(*args):
            subprocess.run(['/usr/bin/xdotool', *args], env=os.environ.copy(), check=True, timeout=5, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        if kind == 'bash':
            return await self.exec_bash(command or text, timeout=timeout)
        if kind == 'launch_app':
            await asyncio.to_thread(self._launch_or_focus_desktop_app, app or text, text if app else '')
            await asyncio.sleep(0.22)
            return None
        if kind in ('click', 'double_click', 'right_click', 'move', 'scroll'):
            await asyncio.to_thread(run, 'mousemove', '--sync', str(max(0, min(WIDTH - 1, x))), str(max(0, min(HEIGHT - 1, y))))
        if kind == 'click':
            await asyncio.to_thread(run, 'click', '1')
        elif kind == 'double_click':
            await asyncio.to_thread(run, 'click', '--repeat', '2', '--delay', '80', '1')
        elif kind == 'right_click':
            await asyncio.to_thread(run, 'click', '3')
        elif kind == 'scroll':
            await asyncio.to_thread(run, 'click', '--repeat', str(max(1, min(12, amount))), '4' if direction == 'up' else '5')
        elif kind == 'key':
            keys = {k: k for k in ['Return', 'Tab', 'Escape', 'BackSpace', 'Delete', 'Up', 'Down', 'Left', 'Right', 'Home', 'End', 'Page_Up', 'Page_Down', 'space']}
            keys.update({f'ctrl+{k}': f'ctrl+{k}' for k in 'acvlrwtz'})
            keys['shift+Tab'] = 'shift+Tab'
            mapped = keys.get(key) or keys.get({'Enter': 'Return', 'Backspace': 'BackSpace', 'PageDown': 'Page_Down', 'PageUp': 'Page_Up'}.get(key, ''), 'Return')
            await asyncio.to_thread(run, 'key', '--clearmodifiers', mapped)
        elif kind == 'type':
            if self.clipboard and self.clipboard.poll() is None:
                self.clipboard.terminate()
            executable = os.environ['COMPUTER_XCLIP_EXECUTABLE']
            self.clipboard = subprocess.Popen([executable, '-selection', 'clipboard', '-in', '-quiet'], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=os.environ.copy())
            self.clipboard.stdin.write(text.encode('utf-8'))
            self.clipboard.stdin.close()
            await asyncio.sleep(.05)
            active_title = self._active_windows_summary().lower()
            if 'terminal' in active_title or 'xterm' in active_title:
                await asyncio.to_thread(run, 'type', '--clearmodifiers', '--delay', '10', text)
            else:
                await asyncio.to_thread(run, 'key', '--clearmodifiers', 'ctrl+v')
        return None

    async def input(self, action: InputAction):
        self._auto_takeover_for_interactive()
        async with self.input_lock:
            bash_res = None
            if action.kind == 'batch' and action.steps:
                for step in action.steps[:20]:
                    s_kind = str(step.get('kind') or step.get('op') or 'click')
                    r = await self._exec_single_input(
                        kind=s_kind,
                        x=int(step.get('x') or 0),
                        y=int(step.get('y') or 0),
                        text=str(step.get('text') or ''),
                        command=str(step.get('command') or ''),
                        key=str(step.get('key') or ''),
                        direction=str(step.get('direction') or 'down'),
                        amount=int(step.get('amount') or 3),
                        app=str(step.get('app') or ''),
                        timeout=int(step.get('timeout') or 25),
                    )
                    if r is not None:
                        bash_res = r
                    await asyncio.sleep(0.06)
            else:
                bash_res = await self._exec_single_input(
                    kind=action.kind,
                    x=action.x,
                    y=action.y,
                    text=action.text,
                    command=action.command,
                    key=action.key,
                    direction=action.direction,
                    amount=action.amount,
                    app=action.app,
                    timeout=action.timeout,
                )
            res: dict = {'ok': True, 'control': self.control, 'windows': self._list_windows()}
            if bash_res is not None:
                res.update(bash_res)
            if action.include_frame:
                await asyncio.sleep(0.06)
                res.update(await asyncio.to_thread(self.frame, 60))
            return res

    async def run(self, request: RunInput):
        run_id = secrets.token_hex(8)
        goal = (request.goal or '').strip()
        m_url = re.search(r'https?://[^\s"\'<>]+', goal)
        res_summary = '已就绪'
        if m_url and not any(k in goal for k in (';', '&&', '|', 'python', 'pip', 'gcc')):
            await self.navigate(m_url.group(0), include_frame=False)
            res_summary = f'已在轻量浏览器打开 {m_url.group(0)}'
        elif goal:
            b_res = await self.exec_bash(goal, timeout=25)
            res_summary = (b_res.get('output') or '').strip()[:1500] or f'exit={b_res.get("exit_code", 0)}'
        self.job = None
        st = self.status()
        st['job'] = {
            'id': run_id,
            'state': 'done',
            'goal': goal,
            'step': 1,
            'max_steps': 1,
            'result': res_summary,
            'trace': [],
        }
        return st

    async def steer(self, text):
        return {'ok': True}

    def file_path(self, name):
        if not name or '\\' in name or ':' in name:
            raise ValueError('文件路径无效')
        namespace, _, relative = name.partition('/')
        root = {'downloads': self.files, 'workspace': self.workspace}.get(namespace)
        if root is None or not relative:
            raise ValueError('文件路径无效')
        path = root / relative
        if '..' in Path(relative).parts or any(item.is_symlink() for item in [path, *path.parents] if item != root.parent):
            raise ValueError('文件路径无效')
        if not path.resolve().is_relative_to(root.resolve()):
            raise ValueError('文件不在云电脑工作区内')
        return path

    def list_files(self):
        files = []
        for namespace, root in [('downloads', self.files), ('workspace', self.workspace)]:
            for path in root.rglob('*'):
                if path.is_symlink() or not path.is_file():
                    continue
                relative = path.relative_to(root)
                if any(part.startswith('.') for part in relative.parts):
                    continue
                stat = path.stat()
                files.append({'path': namespace + '/' + relative.as_posix(), 'name': path.name, 'size': stat.st_size, 'bytes': stat.st_size, 'modified_at': stat.st_mtime})
                if len(files) >= 250:
                    return files
        return sorted(files, key=lambda item: item['modified_at'], reverse=True)


def create_app(desktop=None, token=None):
    desktop = desktop or CloudDesktop()
    token = token or os.environ.get('COMPUTER_CONTROL_TOKEN', '')

    @asynccontextmanager
    async def lifespan(app):
        if not token:
            raise RuntimeError('Private desktop control token is missing')
        try:
            await desktop.start()
            yield
        finally:
            await desktop.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.desktop = desktop

    async def auth(authorization: str = Header('')):
        if not token or not hmac.compare_digest(authorization, 'Bearer ' + token):
            raise HTTPException(401, 'Private computer endpoint')

    install_file_routes(app, auth, WorkspaceFiles(desktop.workspace, desktop.home / '.transfers'), prefix='/library')

    async def operation(coroutine):
        try:
            return await coroutine
        except ValueError as error:
            raise HTTPException(409, str(error)) from None

    @app.get('/home', response_class=HTMLResponse)
    async def home():
        return HOME_HTML

    @app.get('/status', dependencies=[Depends(auth)])
    async def status():
        return desktop.status()

    @app.get('/frame', dependencies=[Depends(auth)])
    async def frame(since: str = '', quality: int = 62):
        return await asyncio.to_thread(desktop.frame, quality, since)

    @app.post('/takeover', dependencies=[Depends(auth)])
    async def takeover():
        return await desktop.takeover()

    @app.post('/handback', dependencies=[Depends(auth)])
    async def handback():
        return await desktop.handback()

    @app.post('/stop', dependencies=[Depends(auth)])
    async def stop():
        return await desktop.stop()

    @app.post('/navigate', dependencies=[Depends(auth)])
    async def navigate(body: NavigateInput):
        return await operation(desktop.navigate(body.url, body.include_frame))

    @app.post('/input', dependencies=[Depends(auth)])
    async def input_action(body: InputAction):
        return await operation(desktop.input(body))

    @app.post('/run', dependencies=[Depends(auth)])
    async def run(body: RunInput):
        return await operation(desktop.run(body))

    @app.post('/steer', dependencies=[Depends(auth)])
    async def steer(body: SteerInput):
        return await operation(desktop.steer(body.text))

    @app.get('/files', dependencies=[Depends(auth)])
    async def files():
        return {'files': await asyncio.to_thread(desktop.list_files)}

    @app.get('/file', dependencies=[Depends(auth)])
    async def read_file(path: str, offset: int = 0, size: int = 262144, version: str | None = None):
        if offset < 0 or not 1 <= size <= 524288:
            raise HTTPException(400, '文件分块参数无效')
        try:
            target = desktop.file_path(path)
            stat = target.stat()
            total = stat.st_size
            fingerprint = f'{stat.st_mtime_ns}-{total}'
            if version and version != fingerprint:
                raise ValueError('文件在下载期间发生变化')
            if not target.is_file() or total > 50_000_000:
                raise ValueError('文件不可读取或超过 50 MB')
            with target.open('rb') as stream:
                stream.seek(offset)
                data = stream.read(size)
            return {'data': base64.b64encode(data).decode(), 'size': total, 'offset': offset, 'next_offset': offset + len(data), 'eof': offset + len(data) >= total, 'version': fingerprint}
        except (ValueError, OSError) as error:
            raise HTTPException(400, '云电脑文件不可读取') from error

    return app

app = create_app()
