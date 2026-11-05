"""1C 1G 10GB Isolated Linux Cloud Computer Subsystem for AI Agents & User Takeover (`server/computer_desktop.py`).

Powered by `server/cloud_os/`:
- `subsystem`: 1 vCPU · 1 GB RAM · 10 GB SSD subsystem shims (`nproc`, `free`, `df`, passwordless `sudo`, user-space `apt-get`)
- `tmux_terminal`: Single persistent `tmux` session (`agent`) + single locked XTerm window (`XTerm*allowTitleOps: false`), zero window leak
- `window_manager`: EWMH X11 window stack telemetry (`_NET_CLIENT_LIST_STACKING`, `_NET_ACTIVE_WINDOW`, `WM_CLASS`, geometry), `focus_window`, `close_window`, `tile_windows`, `clipboard`
- `agent_browser`: Modern JS browser (`luakit` WebKit2GTK 4.1 / Low-RAM Chromium) + semantic DOM extractor (`@b1..@bN`)
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from cloud_os.agent_browser import fetch_semantic_dom_snapshot, launch_modern_browser, reap_idle_browsers
from core.search_engine import multi_web_search
from cloud_os.subsystem import ensure_subsystem_environment, get_subsystem_telemetry
from cloud_os.tmux_terminal import capture_tmux_pane, ensure_single_tmux_terminal
from cloud_os.window_manager import (
    close_window,
    focus_window,
    get_clipboard,
    list_x11_windows,
    set_clipboard,
    tile_windows,
)
from computer_files import WorkspaceFiles, install_file_routes
from computer_models import HEIGHT, WIDTH, InputAction, NavigateInput, RunInput, SteerInput
from computer_security import PublicWebProxy, public_addresses

HOME_HTML = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>Cloud Computer · 1C 1G 10GB</title><style>
body{margin:0;background:#F8FAFC;color:#1E293B;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;text-align:center}
.card{max-width:560px;padding:36px 40px;background:#FFFFFF;border:1px solid #E2E8F0;border-radius:20px;box-shadow:0 10px 30px rgba(15,23,42,0.05)}
.clock{font-size:48px;font-weight:700;color:#0F172A;letter-spacing:-1px;margin:0 0 6px}
.sub{font-size:16px;color:#64748B;margin:0 0 24px}
.apps{display:flex;gap:14px;justify-content:center;flex-wrap:wrap;margin-bottom:22px}
.app{background:#F1F5F9;border:1px solid #E2E8F0;border-radius:14px;padding:12px 16px;font-size:13px;color:#334155;min-width:105px}
.app b{display:block;font-size:14px;color:#0F172A;margin-bottom:3px}
.spec{display:inline-block;background:#EFF6FF;color:#2563EB;border-radius:999px;padding:6px 14px;font-size:12px;font-weight:600}
</style><body>
<div class="card">
  <div class="clock">Welcome back</div>
  <div class="sub">This is my computer. Watch me work, or take control when you need to.</div>
  <div class="apps">
    <div class="app"><b>🌐 Browser</b>Chromium / WebKit</div>
    <div class="app"><b>&gt;_ Terminal</b>Persistent Tmux</div>
    <div class="app"><b>📁 Files</b>Workspace</div>
    <div class="app"><b>📝 Editor</b>Code &amp; Notes</div>
  </div>
  <div class="spec">1 vCPU · 1 GB RAM · 10 GB SSD · Isolated Linux</div>
</div></body></html>'''

TINT2_RC = """# Clean Minimal Dock Bar
panel_items = TC
panel_size = 64% 38
panel_margin = 0 8
panel_padding = 14 4 8
panel_background_id = 1
wm_menu = 0
panel_dock = 0
panel_position = bottom center horizontal
panel_layer = top
panel_monitor = all
autohide = 0

rounded = 14
border_width = 1
border_sides = TBLR
background_color = #1e293b 88
border_color = #475569 70

rounded = 10
border_width = 0
border_sides = B
background_color = #334155 92
border_color = #60a5fa 100

rounded = 10
border_width = 0
background_color = #1e293b 40
border_color = #334155 40

taskbar_mode = single_desktop
taskbar_padding = 4 2 6
taskbar_background_id = 0
taskbar_name = 0
task_icon = 1
task_text = 1
task_centered = 1
task_maximum_size = 180 28
task_padding = 10 4 6
task_font = Sans 9
task_font_color = #e2e8f0 92
task_active_font_color = #ffffff 100
task_background_id = 3
task_active_background_id = 2

time1_format = %H:%M
time1_font = Sans Bold 9.5
clock_font_color = #f8fafc 95
clock_padding = 10 2
clock_background_id = 0
"""


def resolve_grid_or_norm_xy(
    x: int,
    y: int,
    nx: int = -1,
    ny: int = -1,
    grid: str = '',
    width: int = WIDTH,
    height: int = HEIGHT,
) -> tuple[int, int]:
    """
    Convert coarse grid cell (`A1..H10`), normalized 0-1000 coordinates (`nx`, `ny`),
    or raw `(x, y)` into exact pixel coordinates `(px, py)`.
    """
    g = (grid or '').strip().upper()
    if g and len(g) >= 2 and g[0].isalpha() and g[1:].replace('.', '', 1).isdigit():
        col_idx = ord(g[0]) - ord('A')
        row_val = float(g[1:]) - 1.0
        cols = 8
        rows = 10
        cx = int(((col_idx + 0.5) / cols) * width)
        cy = int(((row_val + 0.5) / rows) * height)
        return max(0, min(width - 1, cx)), max(0, min(height - 1, cy))
    if 0 <= nx <= 1000 and 0 <= ny <= 1000:
        return (
            max(0, min(width - 1, int(round(nx * width / 1000.0)))),
            max(0, min(height - 1, int(round(ny * height / 1000.0)))),
        )
    return max(0, min(width - 1, int(x))), max(0, min(height - 1, int(y)))


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
        self.current_title = 'Cloud Computer (1C 1G 10GB)'
        self.last_bash_cmd = ''
        self.last_bash_output = ''
        self.last_dom_snapshot: dict = {}
        self.last_frame_hash = ''
        self.last_frame_b64 = ''
        self.last_frame_at = 0.0
        self.last_browser_active_at = 0.0
        self.reaper_task: asyncio.Task | None = None
        self.subsystem_env: dict[str, str] = {}

    def _proxy_env(self) -> dict[str, str]:
        venv_bin = str(self.venv_dir / 'bin')
        sub_path = self.subsystem_env.get('PATH', os.environ.get('PATH', '/usr/local/bin:/usr/bin:/bin'))
        env = {
            'HOME': str(self.home),
            'USER': os.environ.get('USER', 'proactive-desktop'),
            'XDG_RUNTIME_DIR': os.environ.get('XDG_RUNTIME_DIR', '/run/proactive-desktop'),
            'XDG_CONFIG_HOME': str(self.home / '.config'),
            'XDG_CACHE_HOME': str(self.home / '.cache'),
            'DISPLAY': ':91',
            'XAUTHORITY': str(self.home / '.Xauthority'),
            'DBUS_SESSION_BUS_ADDRESS': 'disabled:',
            'DBUS_SYSTEM_BUS_ADDRESS': 'disabled:',
            'PATH': f'{venv_bin}:{sub_path}',
            'LD_LIBRARY_PATH': self.subsystem_env.get('LD_LIBRARY_PATH', ''),
            'VIRTUAL_ENV': str(self.venv_dir),
            'PYTHONUSERBASE': str(self.home / '.local'),
            'PREFIX': str(self.home / '.local'),
            'CMAKE_INSTALL_PREFIX': str(self.home / '.local'),
            'npm_config_prefix': str(self.home / '.local'),
            'PIP_CACHE_DIR': str(self.home / '.cache' / 'pip'),
            'TMPDIR': str(self.home / 'tmp'),
            'SUBSYSTEM_PROFILE': '1 vCPU · 1 GB RAM · 10 GB SSD',
            'TZ': 'Asia/Shanghai',
        }
        return env

    def _quota_info(self) -> dict:
        telem = get_subsystem_telemetry(self.home)
        return {
            'profile': telem['profile'],
            'cpus': 1,
            'memory_limit_mb': 1024,
            'memory_used_mb': telem['ram_used_mb'],
            'memory_free_mb': max(0, 1024 - telem['ram_used_mb']),
            'disk_limit_gb': 10.0,
            'disk_used_gb': round(telem['disk_used_mb'] / 1024.0, 2),
            'disk_free_gb': round(max(0, telem['disk_total_mb'] - telem['disk_used_mb']) / 1024.0, 2),
        }

    def _setup_win11_environment(self):
        """Initialize the 1C 1G 10GB subsystem shims (`nproc=1`, `free=1024Mi`, `df=10G`, `sudo`, `apt-get`)."""
        for sub in ('.local/bin', '.local/sysroot', '.local/root_home', '.local/apt_cache', '.local/state', '.cache/pip', '.config', 'tmp', 'Downloads'):
            (self.home / sub).mkdir(parents=True, exist_ok=True)
        self.subsystem_env = ensure_subsystem_environment(self.home)
        bashrc = self.home / '.bashrc'
        bashrc_content = (
            'export PATH="$HOME/venv/bin:$HOME/.local/bin:$HOME/.local/sysroot/usr/local/bin:$HOME/.local/sysroot/usr/bin:$HOME/.local/sysroot/bin:/usr/local/bin:/usr/bin:/bin"\n'
            'export LD_LIBRARY_PATH="$HOME/.local/sysroot/usr/lib/x86_64-linux-gnu:$HOME/.local/sysroot/lib/x86_64-linux-gnu:$HOME/.local/sysroot/usr/lib"\n'
            'export VIRTUAL_ENV="$HOME/venv"\n'
            'export PREFIX="$HOME/.local"\n'
            'export SUBSYSTEM_PROFILE="1 vCPU · 1 GB RAM · 10 GB SSD"\n'
            'export TZ="Asia/Shanghai"\n'
            'export TMPDIR="$HOME/tmp"\n'
            'export DBUS_SESSION_BUS_ADDRESS="disabled:"\n'
            'export DBUS_SYSTEM_BUS_ADDRESS="disabled:"\n'
            'export PS1="\\[\\e[1;36m\\]cloud-1c1g\\[\\e[0m\\]:\\[\\e[1;34m\\]\\w\\[\\e[0m\\]\\$ "\n'
        )
        with contextlib.suppress(Exception):
            bashrc.write_text(bashrc_content, encoding='utf-8')
        if not self.live_log.exists():
            self.live_log.write_text(
                '\033[1;36mCloud Computer · 1C 1G 10GB Isolated Linux Subsystem\033[0m\n'
                '  Specs   : 1 vCPU | 1024 MB RAM | 10 GB SSD (sudo & user-space apt-get enabled)\n'
                '  Storage : ~/workspace | Python Venv: ~/venv | Sysroot: ~/.local/sysroot\n'
                '------------------------------------------------------------------------\n',
                encoding='utf-8',
            )
        if not (self.venv_dir / 'bin' / 'python3').exists():
            subprocess.Popen(
                [sys.executable, '-m', 'venv', str(self.venv_dir)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
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
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
            timeout=5,
        )
        self.spawn(['/usr/bin/Xvfb', ':91', '-screen', '0', f'{WIDTH}x{HEIGHT}x24', '-nolisten', 'tcp', '-noreset', '-auth', os.environ['XAUTHORITY']])
        for _ in range(80):
            if Path('/tmp/.X11-unix/X91').exists():
                break
            if self.children[0].poll() is not None:
                raise RuntimeError('Dedicated X display failed to start')
            await asyncio.sleep(0.05)
        else:
            raise RuntimeError('Dedicated X display timed out')

        if shutil.which('xsetroot'):
            with contextlib.suppress(Exception):
                subprocess.run(['/usr/bin/xsetroot', '-solid', '#CBD5E1'], env=os.environ.copy(), timeout=3)

        self.spawn(['/usr/bin/openbox', '--config-file', str(Path(__file__).with_name('computer-openbox.xml'))])
        await asyncio.sleep(0.15)

        if shutil.which('tint2'):
            tint2_cfg = self.home / '.tint2rc'
            tint2_cfg.write_text(TINT2_RC, encoding='utf-8')
            self.spawn(['/usr/bin/tint2', '-c', str(tint2_cfg)])

        self.proxy = PublicWebProxy(18194, blocked_hosts={'216.23.82.196'})
        await self.proxy.start()

        welcome_file = self.workspace / '.welcome.html'
        welcome_file.write_text(HOME_HTML, encoding='utf-8')
        # Zero-RAM idle policy: do NOT launch any browser at boot.
        # Browser starts on-demand when `navigate`/`search`/`launch_app` is called and is auto-reaped after 180s of idle.
        self.reaper_task = asyncio.create_task(self._browser_idle_reaper())
        self.started = True

    async def _browser_idle_reaper(self):
        while True:
            await asyncio.sleep(30)
            try:
                if self.last_browser_active_at > 0 and (time.time() - self.last_browser_active_at) >= 180.0:
                    reaped = await asyncio.to_thread(reap_idle_browsers, self._proxy_env())
                    if reaped > 0:
                        self.last_browser_active_at = 0.0
                        self.current_title = 'Cloud Computer (1C 1G 10GB · Idle 0MB Browser)'
            except asyncio.CancelledError:
                break
            except Exception:
                pass

    def spawn(self, args, cwd=None, extra_env=None):
        env = os.environ.copy()
        env.update(self._proxy_env())
        if extra_env:
            env.update(extra_env)
        child = subprocess.Popen(
            args,
            cwd=cwd or str(self.workspace),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
        )
        self.children.append(child)
        return child

    async def close(self):
        if self.reaper_task and not self.reaper_task.done():
            self.reaper_task.cancel()
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
        return list_x11_windows(self._proxy_env())

    def _active_windows_summary(self) -> str:
        wins = self._list_windows()
        if wins:
            top_titles = [str(w.get('title') or '') for w in wins[:2] if w.get('title')]
            if top_titles:
                return ' · '.join(top_titles)
        return self.current_title

    def status(self):
        wins = self._list_windows()
        title = self._active_windows_summary()
        focused_win = next((w for w in wins if w.get('focused')), wins[0] if wins else None)
        return {
            'available': self.started,
            'width': WIDTH,
            'height': HEIGHT,
            'control': 'agent',
            'control_ready': True,
            'quota': self._quota_info(),
            'url': self.current_url,
            'title': title,
            'active_window': focused_win,
            'windows': wins,
            'window_count': len(wins),
            'dom_elements': (self.last_dom_snapshot or {}).get('elements', [])[:25],
            'page_text': (self.last_dom_snapshot or {}).get('page_text', '')[:1200],
            'venv_ready': (self.venv_dir / 'bin' / 'python3').exists(),
            'last_bash_cmd': self.last_bash_cmd,
            'last_bash_output': self.last_bash_output[-1500:] if self.last_bash_output else '',
            'job': None,
            'engine': 'Cloud Computer · 1C 1G 10GB Isolated Linux Subsystem',
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

    def _open_url_in_modern_browser(self, url: str):
        self.last_browser_active_at = time.time()
        browser_used, snap = launch_modern_browser(
            url,
            env=self._proxy_env(),
            cwd=self.workspace,
            width=WIDTH,
            height=HEIGHT,
        )
        self.current_url = str(snap.get('url') or url)
        page_title = str(snap.get('title') or '').strip()
        self.current_title = f'{page_title} · {browser_used}' if page_title else f'Browser ({browser_used}) · {url}'
        self.last_dom_snapshot = snap
        return snap

    async def search(self, query: str, engine: str = 'auto', include_frame: bool = True):
        self._auto_takeover_for_interactive()
        q = (query or '').strip()
        if not q:
            raise ValueError('搜索词不能为空')
        eng = (engine or 'auto').strip().lower()
        from urllib.parse import quote_plus
        engine_urls = {
            'google': f'https://www.google.com/search?q={quote_plus(q)}&hl=zh-CN',
            'bing': f'https://www.bing.com/search?q={quote_plus(q)}',
            'baidu': f'https://www.baidu.com/s?wd={quote_plus(q)}',
            'sogou': f'https://www.sogou.com/web?query={quote_plus(q)}',
            'duckduckgo': f'https://html.duckduckgo.com/html/?q={quote_plus(q)}',
            'yahoo': f'https://search.yahoo.com/search?p={quote_plus(q)}',
        }
        target_url = engine_urls.get(eng, engine_urls['duckduckgo'])
        search_res = await multi_web_search(q, max_results=10)
        snap = await asyncio.to_thread(self._open_url_in_modern_browser, target_url)
        tile_windows(self._proxy_env(), WIDTH, HEIGHT)
        res = self.status()
        res['query'] = q
        res['engine'] = eng
        res['results'] = search_res.get('results', [])
        res['dom_snapshot'] = snap
        if include_frame:
            await asyncio.sleep(0.12)
            res.update(await asyncio.to_thread(self.frame, 60))
        return res

    async def navigate(self, url: str, include_frame: bool = True):
        self._auto_takeover_for_interactive()
        target_url = (url or '').strip()
        if not target_url.startswith(('http://', 'https://')):
            target_url = 'https://' + target_url
        parsed = urlsplit(target_url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError('请输入公开网页的 HTTP(S) 地址')
        await public_addresses(parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80), {'216.23.82.196'})
        snap = await asyncio.to_thread(self._open_url_in_modern_browser, target_url)
        tile_windows(self._proxy_env(), WIDTH, HEIGHT)
        res = self.status()
        res['dom_snapshot'] = snap
        if include_frame:
            await asyncio.sleep(0.15)
            res.update(await asyncio.to_thread(self.frame, 60))
        return res

    def _ensure_live_terminal_window(self):
        ensure_single_tmux_terminal(self._proxy_env(), self.workspace)

    def _launch_or_focus_desktop_app(self, app_name: str, arg_text: str = ''):
        app_key = (app_name or '').strip().lower()
        env = self._proxy_env()
        if app_key in ('lite_browser', 'netsurf', 'browser', 'lite', 'edge', 'luakit', 'chrome', 'chromium'):
            target_url = arg_text.strip() if arg_text.strip().startswith('http') else (self.current_url or 'https://news.ycombinator.com')
            if not arg_text.strip().startswith('http'):
                for b_class in ('luakit', 'chromium', 'netsurf'):
                    if focus_window(env, b_class):
                        return f'{b_class}_focused'
            self._open_url_in_modern_browser(target_url)
            return 'modern_browser'
        if app_key in ('dillo', 'fast_browser'):
            target_url = arg_text.strip() if arg_text.strip().startswith('http') else (self.current_url or 'https://lite.duckduckgo.com/lite/')
            if not arg_text.strip().startswith('http') and focus_window(env, 'dillo'):
                return 'dillo_focused'
            if shutil.which('dillo'):
                self.spawn(['/usr/bin/dillo', target_url], extra_env=env)
                self.current_url = target_url
                self.current_title = f'Dillo · {target_url}'
                return 'dillo'
            return 'no_dillo'
        if app_key in ('terminal', 'xterm', 'bash', 'shell', 'powershell', 'tmux'):
            ensure_single_tmux_terminal(env, self.workspace)
            focus_window(env, 'xterm')
            self.current_title = 'Terminal (tmux · 1C1G10G)'
            return 'tmux_xterm'
        if app_key in ('files', 'file_manager', 'pcmanfm', 'folder', 'explorer'):
            if focus_window(env, 'pcmanfm'):
                return 'files_focused'
            if shutil.which('pcmanfm'):
                self.spawn(['/usr/bin/pcmanfm', str(self.workspace)], cwd=str(self.workspace))
                self.current_title = 'File Explorer (PCManFM)'
                return 'pcmanfm'
        if app_key in ('editor', 'mousepad', 'notepad', 'code'):
            if not arg_text and focus_window(env, 'mousepad'):
                return 'editor_focused'
            if shutil.which('mousepad'):
                cmd = ['/usr/bin/mousepad']
                if arg_text and not arg_text.startswith('http'):
                    cmd.append(str(self.workspace / Path(arg_text).name))
                self.spawn(cmd, cwd=str(self.workspace))
                self.current_title = 'Notepad (Mousepad)'
                return 'mousepad'
        return 'unknown_app'

    async def exec_bash(self, command: str, timeout: int = 35) -> dict:
        """Execute command inside the 1C 1G 10GB isolated subsystem and mirror onto the single persistent tmux terminal."""
        cmd = (command or '').strip()
        if not cmd:
            return {'exit_code': 0, 'output': ''}
        self.last_bash_cmd = cmd[:400]
        await asyncio.to_thread(self._ensure_live_terminal_window)

        env = self._proxy_env()
        if shutil.which('tmux'):
            with contextlib.suppress(Exception):
                preview_cmd = cmd.replace('\n', '; ')[:110]
                subprocess.run(
                    ['tmux', 'send-keys', '-t', 'agent', f'# [agent] $ {preview_cmd}', 'C-m'],
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=2,
                )

        process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(Path(__file__).with_name('computer_shell.py')),
            str(self.workspace),
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )

        async def _read():
            output = bytearray()
            while True:
                try:
                    wait_t = 0.2 if process.returncode is not None else 1.0
                    chunk = await asyncio.wait_for(process.stdout.read(4096), timeout=wait_t)
                except asyncio.TimeoutError:
                    if process.returncode is not None:
                        break
                    continue
                if not chunk:
                    break
                output.extend(chunk[:24000 - len(output)])
                if len(output) >= 24000:
                    break
            return output

        eff_timeout = max(3, min(120, int(timeout or 35)))
        timed_out = False
        capped_out = False
        try:
            raw_out = await asyncio.wait_for(_read(), eff_timeout)
            if len(raw_out) >= 24000:
                capped_out = True
            else:
                await asyncio.wait_for(process.wait(), 2)
        except asyncio.TimeoutError:
            timed_out = True
            raw_out = b''
        finally:
            if timed_out or capped_out:
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
                f.write(f'\n$ {cmd}\n{out_str[:4000]}\n')

        cwd_file = self.home / '.local' / 'state' / 'agent_cwd'
        cur_cwd = str(self.workspace)
        if cwd_file.exists():
            with contextlib.suppress(Exception):
                cur_cwd = cwd_file.read_text(encoding='utf-8').strip() or cur_cwd

        return {
            'exit_code': -1 if timed_out else (process.returncode or 0),
            'output': out_str,
            'timed_out': timed_out,
            'cwd': cur_cwd,
            'venv': str(self.venv_dir),
            'quota': self._quota_info(),
        }

    async def _exec_single_input(
        self,
        kind: str,
        x: int = 0,
        y: int = 0,
        x2: int = 0,
        y2: int = 0,
        nx: int = -1,
        ny: int = -1,
        grid: str = '',
        ref: str = '',
        target: str = '',
        text: str = '',
        command: str = '',
        key: str = '',
        direction: str = 'down',
        amount: int = 3,
        app: str = '',
        timeout: int = 35,
    ) -> dict | None:
        env = self._proxy_env()

        def run(*args):
            subprocess.run(['/usr/bin/xdotool', *args], env=env, check=True, timeout=5, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        if kind == 'bash':
            return await self.exec_bash(command or text, timeout=timeout)
        if kind == 'launch_app':
            launched = await asyncio.to_thread(self._launch_or_focus_desktop_app, app or target or text, text if app else '')
            await asyncio.sleep(0.22)
            return {'launched': launched}
        if kind == 'focus_window':
            matched = await asyncio.to_thread(focus_window, env, target or app or text)
            return {'focused_window': matched}
        if kind == 'close_window':
            closed = await asyncio.to_thread(close_window, env, target or app or text)
            return {'closed_window': closed}
        if kind == 'tile_windows':
            tiled = await asyncio.to_thread(tile_windows, env, WIDTH, HEIGHT)
            return {'tiled_windows': tiled}
        if kind == 'clipboard':
            if text:
                ok = await asyncio.to_thread(set_clipboard, env, text)
                return {'clipboard_set': ok, 'characters': len(text)}
            clip_val = await asyncio.to_thread(get_clipboard, env)
            return {'clipboard_text': clip_val}
        if kind in ('click_ref', 'type_ref'):
            ref_id = (ref or target or '').strip()
            elems = (self.last_dom_snapshot or {}).get('elements', [])
            matched_el = next((el for el in elems if str(el.get('ref', '')).lower() == ref_id.lower() or (ref_id and ref_id.lower() in str(el.get('label', '')).lower())), None)
            if matched_el and matched_el.get('href') and kind == 'click_ref':
                nav_res = await self.navigate(str(matched_el['href']), include_frame=False)
                return {'clicked_ref': matched_el.get('ref'), 'navigated_to': matched_el.get('href'), 'title': nav_res.get('title')}
            if kind == 'type_ref' and text:
                kind = 'type'
            else:
                return {'clicked_ref': ref_id, 'element': matched_el}

        px, py = resolve_grid_or_norm_xy(x, y, nx=nx, ny=ny, grid=grid, width=WIDTH, height=HEIGHT)

        if kind in ('click', 'double_click', 'right_click', 'move', 'hover', 'scroll', 'drag'):
            await asyncio.to_thread(run, 'mousemove', '--sync', str(px), str(py))
        if kind == 'click':
            await asyncio.to_thread(run, 'click', '1')
        elif kind == 'double_click':
            await asyncio.to_thread(run, 'click', '--repeat', '2', '--delay', '80', '1')
        elif kind == 'right_click':
            await asyncio.to_thread(run, 'click', '3')
        elif kind == 'drag':
            await asyncio.to_thread(run, 'mousedown', '1')
            await asyncio.sleep(0.05)
            await asyncio.to_thread(run, 'mousemove', '--sync', str(max(0, min(WIDTH - 1, x2))), str(max(0, min(HEIGHT - 1, y2))))
            await asyncio.sleep(0.05)
            await asyncio.to_thread(run, 'mouseup', '1')
        elif kind == 'scroll':
            await asyncio.to_thread(run, 'click', '--repeat', str(max(1, min(12, amount))), '4' if direction == 'up' else '5')
        elif kind in ('key', 'hotkey'):
            raw_k = (key or text or 'Return').strip()
            mapped = {'Enter': 'Return', 'Backspace': 'BackSpace', 'PageDown': 'Page_Down', 'PageUp': 'Page_Up', 'Esc': 'Escape'}.get(raw_k, raw_k)
            await asyncio.to_thread(run, 'key', '--clearmodifiers', mapped)
        elif kind == 'type':
            await asyncio.to_thread(set_clipboard, env, text)
            await asyncio.sleep(0.04)
            active_title = self._active_windows_summary().lower()
            if 'terminal' in active_title or 'xterm' in active_title:
                await asyncio.to_thread(run, 'type', '--clearmodifiers', '--delay', '10', text)
            else:
                await asyncio.to_thread(run, 'key', '--clearmodifiers', 'ctrl+v')
        return {'x': px, 'y': py} if kind in ('click', 'double_click', 'right_click', 'move', 'hover') else None

    async def input(self, action: InputAction):
        self._auto_takeover_for_interactive()
        async with self.input_lock:
            extra_res = None
            if action.kind == 'batch' and action.steps:
                for step in action.steps[:25]:
                    s_kind = str(step.get('kind') or step.get('op') or 'click')
                    r = await self._exec_single_input(
                        kind=s_kind,
                        x=int(step.get('x') or 0),
                        y=int(step.get('y') or 0),
                        x2=int(step.get('x2') or 0),
                        y2=int(step.get('y2') or 0),
                        nx=int(step.get('nx') if step.get('nx') is not None else -1),
                        ny=int(step.get('ny') if step.get('ny') is not None else -1),
                        grid=str(step.get('grid') or ''),
                        ref=str(step.get('ref') or ''),
                        target=str(step.get('target') or ''),
                        text=str(step.get('text') or ''),
                        command=str(step.get('command') or ''),
                        key=str(step.get('key') or ''),
                        direction=str(step.get('direction') or 'down'),
                        amount=int(step.get('amount') or 3),
                        app=str(step.get('app') or ''),
                        timeout=int(step.get('timeout') or 35),
                    )
                    if r is not None:
                        extra_res = r
                    await asyncio.sleep(0.06)
            else:
                extra_res = await self._exec_single_input(
                    kind=action.kind,
                    x=action.x,
                    y=action.y,
                    x2=action.x2,
                    y2=action.y2,
                    nx=action.nx,
                    ny=action.ny,
                    grid=action.grid,
                    ref=action.ref,
                    target=action.target,
                    text=action.text,
                    command=action.command,
                    key=action.key,
                    direction=action.direction,
                    amount=action.amount,
                    app=action.app,
                    timeout=action.timeout,
                )
            res: dict = {
                'ok': True,
                'control': self.control,
                'windows': self._list_windows(),
                'quota': self._quota_info(),
            }
            if extra_res is not None:
                res.update(extra_res)
            if action.include_frame:
                await asyncio.sleep(0.06)
                res.update(await asyncio.to_thread(self.frame, 60))
            return res

    async def run(self, request: RunInput):
        run_id = secrets.token_hex(8)
        goal = (request.goal or '').strip()
        m_url = re.search(r'https?://[^\s"\'<>]+', goal)
        res_summary = '已就绪'
        if m_url and not any(k in goal for k in (';', '&&', '|', 'python', 'pip', 'gcc', 'apt', 'sudo')):
            await self.navigate(m_url.group(0), include_frame=False)
            res_summary = f'已在现代浏览器打开 {m_url.group(0)}'
        elif goal:
            b_res = await self.exec_bash(goal, timeout=35)
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
                files.append({
                    'path': namespace + '/' + relative.as_posix(),
                    'name': path.name,
                    'size': stat.st_size,
                    'bytes': stat.st_size,
                    'modified_at': stat.st_mtime,
                })
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

    class SearchInput(BaseModel):
        query: str = ''
        engine: str = 'auto'
        include_frame: bool = True

    @app.post('/search', dependencies=[Depends(auth)])
    async def search_route(body: SearchInput):
        return await operation(desktop.search(body.query, body.engine, body.include_frame))

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
            return {
                'data': base64.b64encode(data).decode(),
                'size': total,
                'offset': offset,
                'next_offset': offset + len(data),
                'eof': offset + len(data) >= total,
                'version': fingerprint,
            }
        except (ValueError, OSError) as error:
            raise HTTPException(400, '云电脑文件不可读取') from error

    return app


app = create_app()
