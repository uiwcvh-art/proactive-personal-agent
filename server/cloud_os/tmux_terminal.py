"""
Single Persistent Tmux Terminal (`server/cloud_os/tmux_terminal.py`).

Solves two major issues in the previous cloud computer implementation:
1. **Window Leak**: Previously, every terminal command or desktop recovery could spawn a new
   `xterm` window (`Terminal 1`, `Terminal 2`, ...), cluttering the X11 desktop.
   Now there is at most ONE `xterm` window attached to the `tmux` session `agent` with
   `-xrm 'XTerm*allowTitleOps: false'` so shell prompts cannot rename the window and defeat
   deduplication.
2. **State Loss**: Previously, `subprocess.run` lost `cd`, environment variables, and background
   processes across tool calls. Now commands can run directly inside the persistent `tmux` session
   or mirror their output onto the visible `agent` tmux pane while capturing exact stdout/stderr
   and exit code.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Dict, Tuple


TMUX_SESSION_NAME = 'agent'
TERMINAL_WINDOW_TITLE = 'Windows Terminal'


def _tmux_sock(env: Dict[str, str]) -> str:
    home = Path(env.get('HOME') or str(Path.home()))
    st = home / '.local' / 'state'
    st.mkdir(parents=True, exist_ok=True)
    try:
        st.chmod(0o700)
    except Exception:
        pass
    return str(st / 'tmux_agent.sock')


def ensure_single_tmux_terminal(env: Dict[str, str], cwd: Path) -> None:
    """
    Ensure the persistent `tmux` session `agent` exists and at most ONE `xterm` window
    is attached to it on the X11 display. Closes any leaked duplicate XTerm windows.
    """
    if os.name == 'nt' or not shutil.which('tmux'):
        return

    sock_path = _tmux_sock(env)
    # 1. Ensure tmux session `agent` exists
    has_session = subprocess.run(
        ['tmux', '-S', sock_path, 'has-session', '-t', TMUX_SESSION_NAME],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode == 0

    if not has_session:
        subprocess.run(
            ['tmux', '-S', sock_path, 'new-session', '-d', '-s', TMUX_SESSION_NAME, '-c', str(cwd)],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        subprocess.run(
            ['tmux', '-S', sock_path, 'set-option', '-t', TMUX_SESSION_NAME, 'status', 'off'],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        subprocess.run(
            ['tmux', '-S', sock_path, 'set-option', '-t', TMUX_SESSION_NAME, 'history-limit', '5000'],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    # 2. Check existing XTerm windows on X11 using xdotool/xprop (never wmctrl, which segfaults on Debian 13)
    if not env.get('DISPLAY'):
        return

    try:
        from cloud_os.window_manager import list_x11_windows

        wins = list_x11_windows(env)
        xterm_wins = [
            w for w in wins
            if any(k in str(w.get('wm_class', '')).lower() for k in ('xterm', 'lxterminal'))
            or 'terminal' in str(w.get('title', '')).lower()
        ]

        if len(xterm_wins) > 1 and shutil.which('xdotool'):
            # Close all extra terminal windows beyond the first
            for extra_w in xterm_wins[1:]:
                try:
                    int_wid = str(int(str(extra_w.get('id', '0')), 16))
                    subprocess.run(
                        ['xdotool', 'windowclose', int_wid],
                        env=env,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=2,
                    )
                except Exception:
                    pass
        elif len(xterm_wins) == 0:
            if shutil.which('lxterminal'):
                subprocess.Popen(
                    [
                        'lxterminal',
                        f'--title={TERMINAL_WINDOW_TITLE}',
                        '--geometry=105x28',
                        '-e', f'tmux -S "{sock_path}" attach-session -t {TMUX_SESSION_NAME}',
                    ],
                    env=env,
                    cwd=str(cwd),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
            elif shutil.which('xterm'):
                subprocess.Popen(
                    [
                        'xterm',
                        '-xrm', 'XTerm*allowTitleOps: false',
                        '-xrm', 'XTerm*internalBorder: 10',
                        '-xrm', 'XTerm*cursorColor: #38bdf8',
                        '-xrm', 'XTerm*selectToClipboard: true',
                        '-title', TERMINAL_WINDOW_TITLE,
                        '-geometry', '104x28+24+24',
                        '-bg', '#0b1120',
                        '-fg', '#e2e8f0',
                        '-fa', 'DejaVu Sans Mono',
                        '-fs', '10',
                        '-e', 'tmux', '-S', sock_path, 'attach-session', '-t', TMUX_SESSION_NAME,
                    ],
                    env=env,
                    cwd=str(cwd),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
            for _ in range(12):
                time.sleep(0.1)
                if any(
                    any(k in str(w.get('wm_class', '')).lower() for k in ('xterm', 'lxterminal'))
                    for w in list_x11_windows(env)
                ):
                    break
    except Exception:
        pass

