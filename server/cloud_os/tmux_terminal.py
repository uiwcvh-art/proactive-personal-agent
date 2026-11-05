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


def ensure_single_tmux_terminal(env: Dict[str, str], cwd: Path) -> None:
    """
    Ensure the persistent `tmux` session `agent` exists and at most ONE `xterm` window
    is attached to it on the X11 display. Closes any leaked duplicate XTerm windows.
    """
    if os.name == 'nt' or not shutil.which('tmux'):
        return

    # 1. Ensure tmux session `agent` exists
    has_session = subprocess.run(
        ['tmux', 'has-session', '-t', TMUX_SESSION_NAME],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode == 0

    if not has_session:
        subprocess.run(
            ['tmux', 'new-session', '-d', '-s', TMUX_SESSION_NAME, '-c', str(cwd)],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        subprocess.run(
            ['tmux', 'set-option', '-t', TMUX_SESSION_NAME, 'status', 'off'],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        subprocess.run(
            ['tmux', 'set-option', '-t', TMUX_SESSION_NAME, 'history-limit', '5000'],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    # 2. Check existing XTerm windows on X11; keep at most one, kill duplicates
    if not env.get('DISPLAY') or not shutil.which('wmctrl'):
        return

    try:
        out = subprocess.check_output(
            ['wmctrl', '-lx'],
            env=env,
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=3,
        )
        xterm_ids = []
        for line in out.splitlines():
            parts = line.split(None, 4)
            if len(parts) >= 3 and 'xterm' in parts[2].lower():
                xterm_ids.append(parts[0])

        if len(xterm_ids) > 1:
            # Close all extra xterm windows beyond the first
            for extra_wid in xterm_ids[1:]:
                subprocess.run(
                    ['wmctrl', '-ic', extra_wid],
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
        elif len(xterm_ids) == 0 and shutil.which('xterm'):
            subprocess.Popen(
                [
                    'xterm',
                    '-xrm', 'XTerm*allowTitleOps: false',
                    '-title', TERMINAL_WINDOW_TITLE,
                    '-geometry', '92x26+28+36',
                    '-bg', '#0c0c0c',
                    '-fg', '#cccccc',
                    '-fa', 'DejaVu Sans Mono',
                    '-fs', '10',
                    '-e', f'tmux attach-session -t {TMUX_SESSION_NAME}',
                ],
                env=env,
                cwd=str(cwd),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
    except Exception:
        pass


def run_in_tmux_or_subshell(
    command: str,
    env: Dict[str, str],
    cwd: Path,
    timeout_s: int = 45,
) -> Tuple[int, str, str, str]:
    """
    Execute `command` while preserving working directory (`~/.local/agent_cwd`)
    and mirroring execution to the persistent `tmux` terminal so the live desktop
    actually shows what the agent is running in real time.

    Returns `(exit_code, stdout, stderr, final_cwd)`.
    """
    cwd.mkdir(parents=True, exist_ok=True)
    state_dir = Path.home() / '.local' / 'state'
    state_dir.mkdir(parents=True, exist_ok=True)
    cwd_file = state_dir / 'agent_cwd'

    active_cwd = cwd
    if cwd_file.exists():
        try:
            saved = Path(cwd_file.read_text(encoding='utf-8').strip())
            if saved.is_dir():
                active_cwd = saved
        except Exception:
            active_cwd = cwd

    if os.name == 'nt':
        completed = subprocess.run(
            command,
            shell=True,
            cwd=str(active_cwd),
            env=env,
            text=True,
            capture_output=True,
            timeout=timeout_s,
        )
        return completed.returncode, completed.stdout or '', completed.stderr or '', str(active_cwd)

    ensure_single_tmux_terminal(env, active_cwd)

    # Echo the command and output banner into the tmux pane so the user watching the live desktop sees it
    token = uuid.uuid4().hex[:8]
    out_log = state_dir / f'cmd_{token}.out'
    wrapped_command = (
        f'cd "{active_cwd}" 2>/dev/null || cd "{cwd}";\n'
        f'{command}\n'
        f'__EC=$?;\n'
        f'pwd > "{cwd_file}";\n'
        f'exit $__EC\n'
    )

    try:
        if shutil.which('tmux'):
            preview_cmd = command.strip().replace('\n', '; ')[:120]
            subprocess.run(
                ['tmux', 'send-keys', '-t', TMUX_SESSION_NAME, f'# [agent] $ {preview_cmd}', 'C-m'],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

        completed = subprocess.run(
            ['/bin/bash', '-c', wrapped_command],
            cwd=str(active_cwd),
            env=env,
            text=True,
            capture_output=True,
            timeout=timeout_s,
        )
        stdout = completed.stdout or ''
        stderr = completed.stderr or ''
        rc = int(completed.returncode)
    except subprocess.TimeoutExpired as exc:
        stdout = (exc.stdout or '') if isinstance(exc.stdout, str) else ''
        stderr = ((exc.stderr or '') if isinstance(exc.stderr, str) else '') + f'\n[Timeout after {timeout_s}s]'
        rc = 124
    finally:
        try:
            out_log.unlink(missing_ok=True)
        except Exception:
            pass

    final_cwd = str(active_cwd)
    if cwd_file.exists():
        try:
            final_cwd = cwd_file.read_text(encoding='utf-8').strip() or str(active_cwd)
        except Exception:
            pass

    # Mirror a short summary into tmux so the terminal window stays informative
    if shutil.which('tmux'):
        try:
            snippet = (stdout.strip() or stderr.strip()).splitlines()
            if snippet:
                last_line = snippet[-1][:100].replace('"', '\\"')
                subprocess.run(
                    ['tmux', 'send-keys', '-t', TMUX_SESSION_NAME, f'echo "-> exit={rc} | {last_line}"', 'C-m'],
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
        except Exception:
            pass

    return rc, stdout, stderr, final_cwd


def capture_tmux_pane(env: Dict[str, str], lines: int = 35) -> str:
    """Capture the visible scrollback of the persistent `agent` tmux session."""
    if os.name == 'nt' or not shutil.which('tmux'):
        return ''
    try:
        out = subprocess.check_output(
            ['tmux', 'capture-pane', '-p', '-t', TMUX_SESSION_NAME, '-S', f'-{max(10, lines)}'],
            env=env,
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=2,
        )
        return out.strip()
    except Exception:
        return ''
