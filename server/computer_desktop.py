"""
[Module Responsibility]
轻量级 1C 1G 10GB 云端 Linux X11 虚拟桌面管理器、GUI/Bash 动作执行与 FastAPI 服务 (`server/computer_desktop.py`)。
负责 Xvfb/Openbox/WebProxy 生命周期 (`start`, `close`, `stop`, `takeover`, `handback`)、
高帧率按需画面合成与标尺叠绘 (`frame`, `status`)、双通道 Tmux Bash 终端 (`exec_bash`)、
GUI 鼠键与 `@bN` 元素动作分发 (`_exec_single_input`, `input`, `run`) 及独立守护进程 HTTP 路由 (`create_app`)。

[Public Interface]
- `CloudDesktop(DesktopBrowserMixin)`: 云端虚拟桌面核心控制器
- `create_app(desktop=None, token=None) -> FastAPI`: 创建独立云桌面守护进程 FastAPI 实例
- `app`: 默认全局 FastAPI 实例
- Re-exports from `computer_browser`: `HOME_HTML`, `DesktopBrowserMixin`, `resolve_grid_or_norm_xy`,
  `_trim_process_memory`, `_draw_papirus_icon`, `_load_cjk_font`, `_generate_win11_wallpaper`, `_set_x11_root_wallpaper`

[Dependencies]
- `computer_browser`: 桌面视觉合成、浏览器/DOM 同步与应用启动器 Mixin
- `cloud_os.agent_browser`, `cloud_os.subsystem`, `cloud_os.window_manager`: 云桌面底层子系统
- `computer_files`, `computer_models`, `computer_security`: 文件传输、数据模型与安全边界
"""
from __future__ import annotations

import asyncio, base64, contextlib, hashlib, hmac, io, os, re, secrets, shutil, signal, subprocess, sys, time
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from cloud_os.agent_browser import cdp_click_or_type_ref, extract_live_cdp_dom_snapshot, fetch_semantic_dom_snapshot, launch_modern_browser, reap_idle_browsers
from cloud_os.subsystem import ensure_subsystem_environment, get_subsystem_telemetry
from cloud_os.tmux_terminal import ensure_single_tmux_terminal
from cloud_os.window_manager import close_window, focus_window, get_clipboard, list_x11_windows, set_clipboard, tile_windows
from computer_browser import (
    DesktopBrowserMixin, HOME_HTML, _draw_papirus_icon, _generate_win11_wallpaper,
    _load_cjk_font, _set_x11_root_wallpaper, _trim_process_memory, resolve_grid_or_norm_xy,
)
from computer_files import WorkspaceFiles, install_file_routes
from computer_models import HEIGHT, WIDTH, InputAction, NavigateInput, RunInput, SteerInput
from computer_security import PublicWebProxy, public_addresses

class CloudDesktop(DesktopBrowserMixin):
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
            'WEBKIT_DISABLE_SANDBOX_THIS_IS_DANGEROUS': '1',
            'WEBKIT_DISABLE_COMPOSITING_MODE': '1',
            'WEBKIT_DISABLE_DMABUF_RENDERER': '1',
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
                subprocess.run(['/usr/bin/xsetroot', '-solid', '#090E1A'], env=os.environ.copy(), timeout=3)

        self.spawn(['/usr/bin/openbox', '--config-file', str(Path(__file__).with_name('computer-openbox.xml'))])
        await asyncio.sleep(0.18)

        # Paint the Windows 11 Wallpaper + 8 Desktop Icons + Bottom Taskbar directly onto X11 root window (0 MB daemon RAM!)
        wallpaper_path = self.home / '.config' / 'wallpaper.png'
        await asyncio.to_thread(_set_x11_root_wallpaper, wallpaper_path, ':91')

        self.proxy = PublicWebProxy(18194, blocked_hosts={'216.23.82.196'})
        await self.proxy.start()

        welcome_file = self.workspace / '.welcome.html'
        welcome_file.write_text(HOME_HTML, encoding='utf-8')
        self.current_title = 'Windows 11 Desktop · 1C 1G (+2G Swap) 10GB'
        self.reaper_task = asyncio.create_task(self._browser_idle_reaper())
        self.started = True
        _trim_process_memory()

    def spawn(self, args, cwd=None, extra_env=None):
        self.children = [c for c in self.children if c.poll() is None]
        env = {}
        for k, v in os.environ.items():
            kl = k.upper()
            if 'TOKEN' in kl or 'SECRET' in kl or 'API_KEY' in kl or 'PASSWORD' in kl:
                continue
            env[k] = v
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

    def _list_windows(self, force: bool = False) -> list[dict]:
        now_m = time.monotonic()
        cached_w = getattr(self, '_wins_cache', None)
        if not force and cached_w is not None and (now_m - cached_w[0]) < 0.6:
            return list(cached_w[1])
        wins = list_x11_windows(self._proxy_env())
        self._wins_cache = (now_m, wins)
        return list(wins)

    def _active_windows_summary(self, wins: list[dict] | None = None) -> str:
        if wins is None:
            wins = self._list_windows()
        if wins:
            top_titles = [str(w.get('title') or '') for w in wins[:2] if w.get('title')]
            if top_titles:
                return ' · '.join(top_titles)
        return self.current_title


    def status(self):
        wins, dom_elems, page_txt = self._sync_active_dom_state()
        title = self._active_windows_summary(wins)
        focused_win = next((w for w in wins if w.get('focused')), wins[0] if wins else None)
        return {
            'available': self.started,
            'width': WIDTH,
            'height': HEIGHT,
            'control': self.control or 'agent',
            'control_ready': self.started,
            'quota': self._quota_info(),
            'url': self.current_url,
            'title': title,
            'active_window': focused_win,
            'windows': wins,
            'window_count': len(wins),
            'dom_elements': dom_elems,
            'page_text': page_txt,
            'venv_ready': (self.venv_dir / 'bin' / 'python3').exists(),
            'last_bash_cmd': self.last_bash_cmd,
            'last_bash_output': self.last_bash_output[-1500:] if self.last_bash_output else '',
            'job': self.job,
            'engine': 'Cloud Computer · 1C 1G (+2G Swap) 10GB Isolated Linux Subsystem',
            'error': self.error,
        }

    def frame(self, quality: int = 62, since_hash: str = ''):
        import mss
        from PIL import Image, ImageDraw

        with mss.mss(display=':91') as capture:
            shot = capture.grab({'left': 0, 'top': 0, 'width': WIDTH, 'height': HEIGHT})
            raw_bytes = shot.raw
            now_tm = time.localtime()
            t_str = time.strftime('%H:%M', now_tm)
            d_str = time.strftime('%Y/%m/%d', now_tm)
            h = hashlib.md5(raw_bytes[::64] + f'|{t_str}|{d_str}'.encode('ascii')).hexdigest()[:16]
            title = self._active_windows_summary()
            if since_hash and since_hash == h:
                return {'unchanged': True, 'hash': h, 'width': WIDTH, 'height': HEIGHT, 'url': self.current_url, 'title': title, 'control': self.control, 'captured_at': time.time()}
            if h == self.last_frame_hash and self.last_frame_b64 and quality == 62:
                b64 = self.last_frame_b64
            else:
                image = Image.frombytes('RGB', shot.size, shot.bgra, 'raw', 'BGRX')
                try:
                    tb_y0, tb_y1 = HEIGHT - 43, HEIGHT - 3
                    draw_clk = ImageDraw.Draw(image)
                    draw_clk.rectangle((WIDTH - 92, tb_y0 + 2, WIDTH - 15, tb_y1 - 2), fill=(15, 23, 42))
                    draw_clk.text((WIDTH - 72, tb_y0 + 5), t_str, font=_load_cjk_font(12), fill=(248, 250, 252))
                    draw_clk.text((WIDTH - 84, tb_y0 + 22), d_str, font=_load_cjk_font(10), fill=(203, 213, 225))
                except Exception:
                    pass
                output = io.BytesIO()
                image.save(output, 'JPEG', quality=max(30, min(90, quality)), optimize=False)
                b64 = base64.b64encode(output.getvalue()).decode()
                self.last_frame_hash = h
                self.last_frame_b64 = b64
                self.last_frame_at = time.time()
        return {'image': b64, 'hash': h, 'unchanged': False, 'mime': 'image/jpeg', 'width': WIDTH, 'height': HEIGHT, 'url': self.current_url, 'title': title, 'control': self.control, 'captured_at': time.time()}

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
                sock_path = str(self.home / '.local' / 'state' / 'tmux_agent.sock')
                preview_cmd = cmd.replace('\n', '; ')[:110]
                subprocess.run(
                    ['tmux', '-S', sock_path, 'send-keys', '-t', 'agent', f'# [agent] $ {preview_cmd}', 'C-m'],
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
        self, kind: str, x: int = 0, y: int = 0, x2: int = 0, y2: int = 0,
        nx: int = -1, ny: int = -1, nx2: int = -1, ny2: int = -1,
        grid: str = '', grid2: str = '', ref: str = '', target: str = '', text: str = '',
        command: str = '', key: str = '', direction: str = 'down', amount: int = 3, app: str = '', timeout: int = 35,
    ) -> dict | None:
        env = self._proxy_env()

        def run(*args):
            clean_args = [a for a in args if a != '--sync']
            with contextlib.suppress(subprocess.TimeoutExpired, subprocess.CalledProcessError):
                subprocess.run(['/usr/bin/xdotool', *clean_args], env=env, check=False, timeout=3, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        if kind == 'wait':
            wait_sec = min(10.0, max(0.05, float(amount or 1)))
            await asyncio.sleep(wait_sec)
            return {'waited_sec': wait_sec}
        if kind == 'find_text':
            q = (text or target or ref or '').strip().lower()
            wins_now, dom_elems_now, page_txt_now = await asyncio.to_thread(self._sync_active_dom_state, None, True)
            matches = [
                el for el in dom_elems_now
                if q and (q in str(el.get('label', '')).lower() or q in str(el.get('ref', '')).lower())
            ]
            return {'query': q, 'matches': matches, 'found_in_page_text': bool(q and q in page_txt_now.lower())}
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
            q_close = (target or app or text or 'active').strip().lower()
            if q_close in ('all', 'desktop', '*', '全部', '所有'):
                wins_before = self._list_windows()
                for w_it in wins_before:
                    await asyncio.to_thread(close_window, env, str(w_it.get('id') or ''))
                await asyncio.to_thread(reap_idle_browsers, env)
                await asyncio.sleep(0.25)
                self.last_dom_snapshot = {}
                self.current_url = ''
                self.current_title = 'Windows 11 Desktop · 1C 1G (+2G Swap) 10GB'
                return {'closed_all': len(wins_before)}
            if q_close in ('active', 'current', ''):
                wins_before = self._list_windows()
                q_close = str(wins_before[0].get('id') or '') if wins_before else ''
            closed = await asyncio.to_thread(close_window, env, q_close) if q_close else None
            await asyncio.sleep(0.22)
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
            wins_now = self._list_windows()
            has_b_win = any(
                any(k in str(w.get('wm_class', '')).lower() for k in ('chromium', 'chrome', 'luakit'))
                for w in wins_now
            )
            elems = (self.last_dom_snapshot or {}).get('elements', []) if has_b_win else self._desktop_icon_elements()
            matched_el = next(
                (
                    el for el in elems
                    if str(el.get('ref', '')).lower() == ref_id.lower()
                    or (ref_id and ref_id.lower() in str(el.get('label', '')).lower())
                ),
                None,
            )
            if matched_el and matched_el.get('app') and kind == 'click_ref':
                launched = await asyncio.to_thread(self._launch_or_focus_desktop_app, str(matched_el['app']))
                return {
                    'clicked_ref': matched_el.get('ref'),
                    'launched': launched,
                    'x': int(matched_el.get('x') or 60),
                    'y': int(matched_el.get('y') or 66),
                    'element': matched_el,
                }
            target_ref = str(matched_el.get('ref') if matched_el else ref_id)
            cdp_res = await asyncio.to_thread(
                cdp_click_or_type_ref,
                target_ref,
                text if kind == 'type_ref' else None,
            )
            if cdp_res and isinstance(cdp_res, dict):
                cx = int(cdp_res.get('x') or (matched_el or {}).get('x') or 0)
                cy = int(cdp_res.get('y') or (matched_el or {}).get('y') or 0)
                # Refresh live DOM snapshot after CDP click/type
                live_after = await asyncio.to_thread(extract_live_cdp_dom_snapshot, 280)
                if live_after:
                    self.last_dom_snapshot = live_after
                    if live_after.get('url'):
                        self.current_url = str(live_after['url'])
                    if live_after.get('title'):
                        self.current_title = f"{live_after['title']} · chromium-cdp"
                return {
                    'clicked_ref': target_ref,
                    'x': cx,
                    'y': cy,
                    'element': matched_el or cdp_res,
                    'dom_elements': (self.last_dom_snapshot or {}).get('elements', [])[:25],
                    'page_text': (self.last_dom_snapshot or {}).get('page_text', '')[:1200],
                }
            if matched_el and matched_el.get('x') and matched_el.get('y'):
                x = int(matched_el['x'])
                y = int(matched_el['y'])
                nx, ny, grid = -1, -1, ''
                kind = 'type' if (kind == 'type_ref' and text) else 'click'
            elif matched_el and matched_el.get('href') and kind == 'click_ref':
                nav_res = await self.navigate(str(matched_el['href']), include_frame=False)
                return {'clicked_ref': matched_el.get('ref'), 'navigated_to': matched_el.get('href'), 'title': nav_res.get('title')}
            elif kind == 'type_ref' and text:
                kind = 'type'
            else:
                return {'clicked_ref': ref_id, 'element': matched_el}

        px, py = resolve_grid_or_norm_xy(x, y, nx=nx, ny=ny, grid=grid, width=WIDTH, height=HEIGHT)

        if kind in ('click', 'double_click', 'right_click', 'move', 'hover', 'scroll', 'drag'):
            await asyncio.to_thread(run, 'mousemove', '--sync', str(px), str(py))
        if kind in ('click', 'double_click'):
            if kind == 'click':
                await asyncio.to_thread(run, 'click', '1')
            else:
                await asyncio.to_thread(run, 'click', '--repeat', '2', '--delay', '80', '1')
            # 1) Check bottom Windows 11 taskbar launcher slots (y >= HEIGHT - 45)
            if py >= HEIGHT - 45:
                taskbar_apps = ['chrome', 'terminal', 'files', 'code', 'excel', 'notepad', 'taskmgr', 'calculator']
                for idx, tb_app in enumerate(taskbar_apps):
                    tx_center = 38 + idx * 34
                    if abs(px - tx_center) <= 16:
                        await asyncio.to_thread(self._launch_or_focus_desktop_app, tb_app)
                        break
            # 2) If desktop is visible (no covering window), check the 8 painted desktop icons
            elif not self._list_windows():
                for d_icon in self._desktop_icon_elements():
                    ix, iy = int(d_icon['x']), int(d_icon['y'])
                    if abs(px - ix) <= 44 and abs(py - iy) <= 46:
                        await asyncio.to_thread(self._launch_or_focus_desktop_app, str(d_icon['app']))
                        break
        elif kind == 'right_click':
            await asyncio.to_thread(run, 'click', '3')
        elif kind == 'drag':
            px2, py2 = resolve_grid_or_norm_xy(x2, y2, nx=nx2, ny=ny2, grid=grid2, width=WIDTH, height=HEIGHT)
            await asyncio.to_thread(run, 'mousedown', '1')
            await asyncio.sleep(0.05)
            await asyncio.to_thread(run, 'mousemove', '--sync', str(px2), str(py2))
            await asyncio.sleep(0.05)
            await asyncio.to_thread(run, 'mouseup', '1')
        elif kind == 'scroll':
            scroll_btn = {'up': '4', 'down': '5', 'left': '6', 'right': '7'}.get(direction, '5')
            await asyncio.to_thread(run, 'click', '--repeat', str(max(1, min(20, amount))), scroll_btn)
        elif kind in ('key', 'hotkey'):
            raw_k = (key or text or 'Return').strip()
            if '+' in raw_k:
                parts_k = [p.strip() for p in raw_k.split('+') if p.strip()]
                norm_parts = []
                for p in parts_k:
                    pl = p.lower()
                    if pl in ('ctrl', 'control'):
                        norm_parts.append('ctrl')
                    elif pl in ('alt', 'shift', 'super'):
                        norm_parts.append(pl)
                    elif pl in ('enter', 'return'):
                        norm_parts.append('Return')
                    elif pl in ('esc', 'escape'):
                        norm_parts.append('Escape')
                    elif len(p) == 1:
                        norm_parts.append(pl)
                    else:
                        norm_parts.append(p)
                mapped = '+'.join(norm_parts)
            else:
                mapped = {'Enter': 'Return', 'enter': 'Return', 'Backspace': 'BackSpace', 'PageDown': 'Page_Down', 'PageUp': 'Page_Up', 'Esc': 'Escape', 'esc': 'Escape'}.get(raw_k, raw_k)
            await asyncio.to_thread(run, 'key', '--clearmodifiers', mapped)
        elif kind == 'type':
            if px > 0 or py > 0:
                await asyncio.to_thread(run, 'mousemove', '--sync', str(px), str(py))
                await asyncio.to_thread(run, 'click', '1')
                await asyncio.sleep(0.05)
            await asyncio.to_thread(set_clipboard, env, text)
            await asyncio.sleep(0.04)
            active_title = self._active_windows_summary().lower()
            if 'terminal' in active_title or 'xterm' in active_title:
                await asyncio.to_thread(run, 'type', '--clearmodifiers', '--delay', '10', text)
            else:
                await asyncio.to_thread(run, 'key', '--clearmodifiers', 'ctrl+v')

        # Synchronize live DOM or desktop icon state with actual open windows after any action!
        await asyncio.sleep(0.12)
        wins_after, dom_elems_after, page_txt_after = await asyncio.to_thread(
            self._sync_active_dom_state,
            None,
            kind in ('click', 'double_click', 'scroll', 'key', 'hotkey', 'type'),
        )

        out_meta: dict = {
            'windows': wins_after,
            'window_count': len(wins_after),
            'dom_elements': dom_elems_after,
            'page_text': page_txt_after,
            'title': self._active_windows_summary(),
            'url': self.current_url,
        }
        if kind in ('click', 'double_click', 'right_click', 'move', 'hover', 'type'):
            out_meta['x'] = px
            out_meta['y'] = py
        return out_meta

    async def input(self, action: InputAction):
        self._auto_takeover_for_interactive()
        async with self.input_lock:
            extra_res = None
            if action.kind == 'batch' and action.steps:
                for step in action.steps[:25]:
                    s_kind = str(step.get('kind') or step.get('op') or 'click')
                    r = await self._exec_single_input(
                        kind=s_kind, x=int(step.get('x') or 0), y=int(step.get('y') or 0),
                        x2=int(step.get('x2') or 0), y2=int(step.get('y2') or 0),
                        nx=int(step.get('nx') if step.get('nx') is not None else -1),
                        ny=int(step.get('ny') if step.get('ny') is not None else -1),
                        nx2=int(step.get('nx2') if step.get('nx2') is not None else -1),
                        ny2=int(step.get('ny2') if step.get('ny2') is not None else -1),
                        grid=str(step.get('grid') or ''), grid2=str(step.get('grid2') or ''),
                        ref=str(step.get('ref') or ''), target=str(step.get('target') or ''),
                        text=str(step.get('text') or ''), command=str(step.get('command') or ''),
                        key=str(step.get('key') or ''), direction=str(step.get('direction') or 'down'),
                        amount=int(step.get('amount') or 3), app=str(step.get('app') or ''), timeout=int(step.get('timeout') or 35),
                    )
                    if r is not None:
                        extra_res = r
                    await asyncio.sleep(0.06)
            else:
                extra_res = await self._exec_single_input(
                    kind=action.kind, x=action.x, y=action.y, x2=action.x2, y2=action.y2,
                    nx=action.nx, ny=action.ny, nx2=action.nx2, ny2=action.ny2,
                    grid=action.grid, grid2=action.grid2, ref=action.ref, target=action.target,
                    text=action.text, command=action.command, key=action.key,
                    direction=action.direction, amount=action.amount, app=action.app, timeout=action.timeout,
                )
            if isinstance(extra_res, dict) and 'windows' in extra_res and 'dom_elements' in extra_res:
                wins_now = extra_res['windows']
                dom_elems_now = extra_res['dom_elements']
                page_txt_now = extra_res.get('page_text', '')
            else:
                wins_now, dom_elems_now, page_txt_now = await asyncio.to_thread(self._sync_active_dom_state)
            res: dict = {
                'ok': True,
                'control': self.control,
                'title': self._active_windows_summary(),
                'url': self.current_url,
                'windows': wins_now,
                'window_count': len(wins_now),
                'dom_elements': dom_elems_now,
                'page_text': page_txt_now,
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
        except (ValueError, subprocess.TimeoutExpired) as error:
            raise HTTPException(409, str(error)) from None

    @app.get('/home', response_class=HTMLResponse)
    async def home():
        return HOME_HTML

    @app.get('/status', dependencies=[Depends(auth)])
    async def status():
        return await asyncio.to_thread(desktop.status)

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
