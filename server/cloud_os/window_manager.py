"""
EWMH X11 Window Manager & Visual Crosshair (`server/cloud_os/window_manager.py`).

Provides true Agent-Computer Interface (ACI) window awareness:
1. Lists all open X11 windows ordered by actual stacking Z-order (`_NET_CLIENT_LIST_STACKING`),
   marking which window is currently focused (`_NET_ACTIVE_WINDOW`), its `WM_CLASS`, title,
   and exact `(x, y, width, height)` geometry.
2. Supports deterministic window operations:
   - `focus_window(target)` (by window ID, class, or title substring)
   - `close_window(target)` (graceful EWMH close via `wmctrl -ic`)
   - `tile_windows(layout)` (side-by-side browser + terminal or maximized active window)
   - `read_clipboard()` / `write_clipboard(text)` via `xclip`
3. Annotates screenshots with a subtle red crosshair at `(last_x, last_y)` so both the Vision LLM
   and the user can see the exact coordinate where the last pointer action landed.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Optional


def list_x11_windows(env: Dict[str, str]) -> List[Dict[str, object]]:
    """
    Return all open X11 windows in stacking order (top-most first) with:
    `id`, `title`, `wm_class`, `x`, `y`, `width`, `height`, `focused`, `z_index`.
    Uses `xprop` + `xdotool` directly so it never segfaults on 64-bit Debian/Trixie.
    """
    if os.name == 'nt' or not env.get('DISPLAY'):
        return []

    active_id = _get_active_window_id(env)
    stacking_order = _get_stacking_order_ids(env)
    if not stacking_order and shutil.which('xdotool'):
        try:
            out = subprocess.check_output(
                ['xdotool', 'search', '--onlyvisible', '--name', '.'],
                env=env, text=True, stderr=subprocess.DEVNULL, timeout=2,
            )
            stacking_order = [_norm_hex_id(hex(int(w.strip()))) for w in out.splitlines() if w.strip().isdigit()]
        except Exception:
            stacking_order = []

    raw_windows: List[Dict[str, object]] = []
    for idx, norm_wid in enumerate(stacking_order):
        try:
            int_wid = int(norm_wid, 16)
        except ValueError:
            continue
        title = ''
        wm_class = ''
        x, y, w, h = 0, 0, 0, 0
        if shutil.which('xdotool'):
            try:
                wname = subprocess.check_output(
                    ['xdotool', 'getwindowname', str(int_wid)],
                    env=env, text=True, stderr=subprocess.DEVNULL, timeout=2,
                ).strip()
                if wname:
                    title = wname
            except Exception:
                pass
        if shutil.which('xprop'):
            try:
                xp = subprocess.check_output(
                    ['xprop', '-id', norm_wid, '_NET_WM_NAME', 'WM_NAME', 'WM_CLASS'],
                    env=env, text=True, stderr=subprocess.DEVNULL, timeout=2,
                )
                for line in xp.splitlines():
                    if ('_NET_WM_NAME' in line or line.startswith('WM_NAME')) and '=' in line and not title:
                        m = re.search(r'"(.*)"', line)
                        if m:
                            title = m.group(1).strip()
                    elif 'WM_CLASS' in line and '=' in line:
                        classes = re.findall(r'"([^"]+)"', line)
                        if classes:
                            wm_class = '.'.join(classes)
            except Exception:
                pass
        if shutil.which('xdotool'):
            try:
                geom = subprocess.check_output(
                    ['xdotool', 'getwindowgeometry', '--shell', str(int_wid)],
                    env=env, text=True, stderr=subprocess.DEVNULL, timeout=2,
                )
                gmap = dict(
                    ln.split('=', 1) for ln in geom.splitlines() if '=' in ln
                )
                x = int(gmap.get('X', 0))
                y = int(gmap.get('Y', 0))
                w = int(gmap.get('WIDTH', 0))
                h = int(gmap.get('HEIGHT', 0))
            except Exception:
                pass
        if (title or wm_class) in ('Tint2', 'tint2', 'panel') or 'tint2' in wm_class.lower():
            continue
        is_focused = bool(active_id and norm_wid == active_id)
        raw_windows.append({
            'id': norm_wid,
            'norm_id': norm_wid,
            'title': title or wm_class or norm_wid,
            'wm_class': wm_class or 'X11Window',
            'x': x,
            'y': y,
            'width': w,
            'height': h,
            'focused': is_focused,
            '_z_rank': idx,
        })

    # Sort top-most first (highest index in _NET_CLIENT_LIST_STACKING is top-most)
    raw_windows.sort(key=lambda item: (1 if item['focused'] else 0, int(item['_z_rank'])), reverse=True)
    for idx, win in enumerate(raw_windows):
        win.pop('_z_rank', None)
        win.pop('norm_id', None)
        win['z_order'] = idx
    return raw_windows


def focus_window(env: Dict[str, str], target: str) -> Optional[Dict[str, object]]:
    """Focus a window by hex ID (`0x...`), `wm_class`, or title substring."""
    target_q = (target or '').strip().lower()
    if not target_q or os.name == 'nt':
        return None
    windows = list_x11_windows(env)
    matched = _match_window(windows, target_q)
    if not matched:
        return None
    wid = str(matched['id'])
    if shutil.which('xdotool'):
        try:
            subprocess.run(
                ['xdotool', 'windowactivate', str(int(wid, 16))],
                env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2,
            )
            return matched
        except Exception:
            pass
    if shutil.which('wmctrl'):
        subprocess.run(
            ['wmctrl', '-ia', wid],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    return matched


def close_window(env: Dict[str, str], target: str) -> Optional[Dict[str, object]]:
    """Gracefully close a window by hex ID (`0x...`), `wm_class`, or title substring."""
    target_q = (target or '').strip().lower()
    if not target_q or os.name == 'nt':
        return None
    windows = list_x11_windows(env)
    matched = _match_window(windows, target_q)
    if not matched:
        return None
    wid = str(matched['id'])
    if shutil.which('xdotool'):
        try:
            subprocess.run(
                ['xdotool', 'windowclose', str(int(wid, 16))],
                env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2,
            )
            return matched
        except Exception:
            pass
    if shutil.which('wmctrl'):
        subprocess.run(
            ['wmctrl', '-ic', wid],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    return matched


def tile_windows(env: Dict[str, str], width: int = 1280, height: int = 720) -> List[str]:
    """
    Arrange open windows cleanly on the desktop so they don't awkwardly overlap:
    - If 1 window: center/maximize (`24,20` to `width-48, height-76`)
    - If >=2 windows: place top-most 2 windows side by side (left half / right half)
    """
    if os.name == 'nt':
        return []
    windows = list_x11_windows(env)
    if not windows:
        return []

    usable_h = max(400, height - 56)
    tiled: List[str] = []
    if len(windows) == 1:
        w0 = windows[0]
        int_wid = str(int(str(w0['id']), 16))
        if shutil.which('xdotool'):
            subprocess.run(['xdotool', 'windowmove', int_wid, '24', '20'], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(['xdotool', 'windowsize', int_wid, str(width - 48), str(usable_h - 24)], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        tiled.append(f"{w0['title']} (full)")
    else:
        half_w = (width - 36) // 2
        left_w = windows[0]
        right_w = windows[1]
        for idx, (win, x_pos) in enumerate(((left_w, 12), (right_w, 24 + half_w))):
            int_wid = str(int(str(win['id']), 16))
            if shutil.which('xdotool'):
                subprocess.run(['xdotool', 'windowmove', int_wid, str(x_pos), '16'], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                subprocess.run(['xdotool', 'windowsize', int_wid, str(half_w), str(usable_h - 20)], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            tiled.append(f"{win['title']} ({'left' if idx == 0 else 'right'})")
    return tiled


def get_clipboard(env: Dict[str, str]) -> str:
    """Read X11 clipboard text via `xclip`."""
    if os.name == 'nt' or not shutil.which('xclip'):
        return ''
    for sel in ('clipboard', 'primary'):
        try:
            out = subprocess.check_output(
                ['xclip', '-selection', sel, '-o'],
                env=env,
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=2,
            )
            if out:
                return out[:4000]
        except Exception:
            continue
    return ''


def set_clipboard(env: Dict[str, str], text: str) -> bool:
    """Write text into X11 clipboard via `xclip`."""
    if os.name == 'nt' or not shutil.which('xclip'):
        return False
    try:
        for sel in ('clipboard', 'primary'):
            proc = subprocess.Popen(
                ['xclip', '-selection', sel, '-i'],
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            proc.communicate(input=text, timeout=2)
        return True
    except Exception:
        return False


def draw_action_crosshair(image_path: Path, x: Optional[int], y: Optional[int]) -> None:
    """
    Draw a high-contrast red crosshair + ring at `(x, y)` on `image_path`
    so the Vision LLM and user have closed-loop visual feedback on pointer actions.
    """
    if x is None or y is None or not image_path.exists():
        return
    try:
        from PIL import Image, ImageDraw

        with Image.open(image_path) as im:
            im = im.convert('RGBA')
            overlay = Image.new('RGBA', im.size, (0, 0, 0, 0))
            draw = ImageDraw.Draw(overlay)
            r_outer = 14
            r_inner = 4
            # Outer white halo for contrast on dark backgrounds
            draw.ellipse((x - r_outer - 1, y - r_outer - 1, x + r_outer + 1, y + r_outer + 1), outline=(255, 255, 255, 210), width=2)
            # Red target ring
            draw.ellipse((x - r_outer, y - r_outer, x + r_outer, y + r_outer), outline=(239, 68, 68, 245), width=2)
            draw.ellipse((x - r_inner, y - r_inner, x + r_inner, y + r_inner), fill=(239, 68, 68, 245))
            # Crosshair ticks
            draw.line((x - 22, y, x - 7, y), fill=(239, 68, 68, 235), width=2)
            draw.line((x + 7, y, x + 22, y), fill=(239, 68, 68, 235), width=2)
            draw.line((x, y - 22, x, y - 7), fill=(239, 68, 68, 235), width=2)
            draw.line((x, y + 7, x, y + 22), fill=(239, 68, 68, 235), width=2)
            combined = Image.alpha_composite(im, overlay).convert('RGB')
            combined.save(image_path, format='PNG')
    except Exception:
        pass


def _get_active_window_id(env: Dict[str, str]) -> str:
    if not shutil.which('xprop'):
        return ''
    try:
        out = subprocess.check_output(
            ['xprop', '-root', '_NET_ACTIVE_WINDOW'],
            env=env,
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=2,
        )
        m = re.search(r'0x[0-9a-fA-F]+', out)
        return _norm_hex_id(m.group(0)) if m else ''
    except Exception:
        return ''


def _get_stacking_order_ids(env: Dict[str, str]) -> List[str]:
    if not shutil.which('xprop'):
        return []
    try:
        out = subprocess.check_output(
            ['xprop', '-root', '_NET_CLIENT_LIST_STACKING'],
            env=env,
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=2,
        )
        return [_norm_hex_id(m) for m in re.findall(r'0x[0-9a-fA-F]+', out)]
    except Exception:
        return []


def _norm_hex_id(raw: str) -> str:
    try:
        return hex(int(raw, 16)).lower()
    except Exception:
        return raw.lower()


def _match_window(windows: List[Dict[str, object]], query: str) -> Optional[Dict[str, object]]:
    for w in windows:
        if _norm_hex_id(str(w.get('id', ''))) == _norm_hex_id(query):
            return w
    for w in windows:
        title = str(w.get('title', '')).lower()
        wm_class = str(w.get('wm_class', '')).lower()
        if query in title or query in wm_class:
            return w
    return None
