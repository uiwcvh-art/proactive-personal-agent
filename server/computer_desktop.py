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

from cloud_os.agent_browser import (
    cdp_click_or_type_ref,
    extract_live_cdp_dom_snapshot,
    fetch_semantic_dom_snapshot,
    launch_modern_browser,
    reap_idle_browsers,
)
from core.search_engine import multi_web_search
from cloud_os.subsystem import ensure_subsystem_environment, get_subsystem_telemetry
from cloud_os.tmux_terminal import ensure_single_tmux_terminal
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

HOME_HTML = '''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>Cloud Workstation · 1C 1G 10GB</title><style>
*{box-sizing:border-box}
body{margin:0;background:radial-gradient(circle at 18% 18%,#1e293b 0%,#0b1120 55%,#060a12 100%);color:#f8fafc;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans CJK SC",sans-serif;height:100vh;overflow:hidden;display:flex;flex-direction:column;justify-content:space-between;padding:28px 36px 48px}
.top{display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid rgba(148,163,184,0.14);padding-bottom:14px}
.brand{display:flex;align-items:center;gap:10px;font-weight:700;font-size:15px;color:#e2e8f0;letter-spacing:0.3px}
.dot{width:10px;height:10px;border-radius:50%;background:#38bdf8;box-shadow:0 0 12px #38bdf8}
.pills{display:flex;gap:10px}
.pill{background:rgba(30,41,59,0.75);border:1px solid rgba(148,163,184,0.2);border-radius:999px;padding:5px 13px;font-size:12px;color:#94a3b8}
.pill b{color:#38bdf8}
.hero{max-width:880px;margin:0 auto;width:100%;text-align:left}
.tag{display:inline-block;background:rgba(56,189,248,0.12);border:1px solid rgba(56,189,248,0.3);color:#38bdf8;border-radius:8px;padding:4px 10px;font-size:12px;font-weight:600;margin-bottom:12px}
h1{font-size:34px;font-weight:800;margin:0 0 8px;letter-spacing:-0.5px;background:linear-gradient(90deg,#ffffff,#93c5fd);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.sub{font-size:14.5px;color:#94a3b8;margin:0 0 24px}
.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:22px}
.card{background:rgba(15,23,42,0.78);border:1px solid rgba(148,163,184,0.16);border-radius:16px;padding:16px;box-shadow:0 12px 28px rgba(0,0,0,0.35)}
.card .ic{font-size:20px;margin-bottom:8px}
.card b{display:block;font-size:14px;color:#f1f5f9;margin-bottom:4px}
.card span{font-size:12px;color:#64748b;line-height:1.4;display:block}
.links{display:flex;gap:10px;flex-wrap:wrap}
.links a{background:rgba(30,41,59,0.85);border:1px solid rgba(148,163,184,0.22);color:#e2e8f0;text-decoration:none;padding:8px 14px;border-radius:10px;font-size:12.5px;font-weight:500}
.foot{display:flex;justify-content:space-between;align-items:center;font-size:12px;color:#64748b;border-top:1px solid rgba(148,163,184,0.12);padding-top:12px}
</style></head><body>
<div class="top">
  <div class="brand"><span class="dot"></span> CLOUD WORKSTATION · AI AGENT DESKTOP</div>
  <div class="pills">
    <div class="pill">CPU <b>1 vCPU</b></div>
    <div class="pill">RAM <b>1024 MB</b></div>
    <div class="pill">SSD <b>10 GB</b></div>
    <div class="pill">CDP <b>:9222 Ready</b></div>
  </div>
</div>
<div class="hero">
  <div class="tag">● HYBRID ACI · VISION + LIVE DOM + TMUX</div>
  <h1>Proactive Cloud Computer</h1>
  <div class="sub">Tri-modal execution environment: Chromium CDP live DOM (@b1..@bN), calibrated 0..1000 vision grounding, and persistent Tmux shell.</div>
  <div class="grid">
    <div class="card"><div class="ic">🌐</div><b>Chromium CDP</b><span>Live JS/SPA rendering, Set-of-Mark @bN refs &amp; WARP egress</span></div>
    <div class="card"><div class="ic">&gt;_</div><b>Persistent Tmux</b><span>Session "agent" preserves cwd, env &amp; background tasks</span></div>
    <div class="card"><div class="ic">🐍</div><b>Python 3 &amp; Dev</b><span>Isolated ~/venv, gcc, git, user-space apt &amp; sudo</span></div>
    <div class="card"><div class="ic">📁</div><b>Workspace</b><span>~/workspace &amp; ~/Downloads synced with file API</span></div>
  </div>
  <div class="links">
    <a href="https://news.ycombinator.com">Hacker News</a>
    <a href="https://github.com/trending">GitHub Trending</a>
    <a href="https://www.bing.com">Bing Search</a>
    <a href="https://docs.qq.com">Tencent Docs</a>
  </div>
</div>
<div class="foot">
  <span>Display :91 (1280×800) · Landlock &amp; cgroup v2 isolated</span>
  <span>Ready for autonomous agent tasks or instant user takeover</span>
</div>
</body></html>'''


def _trim_process_memory() -> None:
    """Release unused Python/PIL heap pages back to the Linux kernel immediately."""
    try:
        import ctypes
        import gc

        gc.collect()
        libc = ctypes.CDLL('libc.so.6')
        libc.malloc_trim(0)
    except Exception:
        pass


def _draw_papirus_icon(draw, app_key: str, cx: int, cy: int, size: int = 44) -> None:
    """Draw crisp Papirus-Dark style colorful vector app icons directly via PIL."""
    r = size // 2
    x0, y0, x1, y1 = cx - r, cy - r, cx + r, cy + r
    rad = max(6, size // 5)
    # Soft drop shadow
    draw.rounded_rectangle((x0 + 1, y0 + 2, x1 + 1, y1 + 2), radius=rad, fill=(2, 6, 23, 140))

    if app_key == 'chrome':
        draw.ellipse((x0, y0, x1, y1), fill=(37, 99, 235, 255), outline=(147, 197, 253, 220), width=max(1, size // 22))
        ir = int(r * 0.56)
        draw.ellipse((cx - ir, cy - ir, cx + ir, cy + ir), fill=(241, 245, 249, 255))
        cr = int(r * 0.36)
        draw.ellipse((cx - cr, cy - cr, cx + cr, cy + cr), fill=(56, 189, 248, 255))
    elif app_key == 'terminal':
        draw.rounded_rectangle((x0, y0, x1, y1), radius=rad, fill=(51, 65, 85, 255), outline=(100, 116, 139, 210), width=1)
        draw.rounded_rectangle((x0 + 3, y0 + 3, x1 - 3, y1 - 3), radius=max(4, rad - 2), fill=(30, 41, 59, 255))
        p_x = x0 + max(6, size // 5)
        p_y = cy - max(4, size // 8)
        d = max(4, size // 7)
        w_line = max(2, size // 14)
        draw.line([(p_x, p_y - d), (p_x + d, p_y), (p_x, p_y + d)], fill=(248, 250, 252, 255), width=w_line)
        draw.line([(p_x + d + 3, p_y + d), (p_x + d * 2 + 4, p_y + d)], fill=(56, 189, 248, 255), width=w_line)
    elif app_key == 'files':
        draw.rounded_rectangle((x0, y0 + 4, x1, y1), radius=rad, fill=(148, 163, 184, 255))
        draw.rounded_rectangle((x0 + 3, y0 + 1, cx + 2, y0 + 10), radius=4, fill=(234, 179, 8, 255))
        draw.rounded_rectangle((x0 + 2, y0 + 6, x1 - 2, y0 + 14), radius=3, fill=(56, 189, 248, 255))
        draw.rounded_rectangle((x0, y0 + 11, x1, y1), radius=rad, fill=(100, 116, 139, 255), outline=(203, 213, 225, 180), width=1)
        bar_w = max(6, size // 5)
        draw.line([(cx - bar_w, cy + 4), (cx + bar_w, cy + 4)], fill=(241, 245, 249, 240), width=max(2, size // 16))
    elif app_key == 'code':
        draw.rounded_rectangle((x0, y0, x1, y1), radius=rad, fill=(234, 179, 8, 255), outline=(253, 224, 71, 220), width=1)
        draw.rounded_rectangle((x0 + 5, y0 + 6, x1 - 5, y1 - 6), radius=4, fill=(161, 98, 7, 255))
        for dx, col in ((-7, (239, 68, 68, 255)), (0, (34, 197, 94, 255)), (7, (56, 189, 248, 255))):
            sx = cx + int(dx * size / 44)
            sr = max(2, size // 14)
            draw.ellipse((sx - sr, cy - sr, sx + sr, cy + sr), fill=col)
    elif app_key == 'excel':
        draw.rounded_rectangle((x0, y0, x1, y1), radius=rad, fill=(34, 197, 94, 255), outline=(134, 239, 172, 220), width=1)
        gx0, gy0, gx1, gy1 = x0 + size // 4, y0 + size // 4, x1 - size // 4, y1 - size // 4
        draw.rectangle((gx0, gy0, gx1, gy1), fill=(248, 250, 252, 245), outline=(21, 128, 61, 255), width=1)
        draw.line([(cx, gy0), (cx, gy1)], fill=(21, 128, 61, 255), width=1)
        draw.line([(gx0, cy), (gx1, cy)], fill=(21, 128, 61, 255), width=1)
    elif app_key == 'notepad':
        draw.rounded_rectangle((x0 + 3, y0, x1 - 3, y1), radius=rad, fill=(226, 232, 240, 255), outline=(148, 163, 184, 220), width=1)
        for ly in (-7, -1, 5):
            yy = cy + int(ly * size / 44)
            draw.line([(x0 + 8, yy), (x1 - 9, yy)], fill=(100, 116, 139, 230), width=max(1, size // 20))
        pr = max(5, size // 5)
        draw.ellipse((x1 - pr * 2, y1 - pr * 2, x1 + 1, y1 + 1), fill=(239, 68, 68, 255))
    elif app_key == 'taskmgr':
        draw.rounded_rectangle((x0, y0, x1, y1), radius=rad, fill=(51, 65, 85, 255), outline=(100, 116, 139, 210), width=1)
        pts = [
            (x0 + 4, cy + 3),
            (cx - 8, cy + 3),
            (cx - 4, cy - 9),
            (cx + 1, cy + 9),
            (cx + 5, cy - 6),
            (cx + 9, cy + 3),
            (x1 - 4, cy + 3),
        ]
        draw.line(pts, fill=(34, 197, 94, 255), width=max(2, size // 15))
    elif app_key == 'calculator':
        draw.rounded_rectangle((x0, y0, x1, y1), radius=rad, fill=(100, 116, 139, 255), outline=(203, 213, 225, 200), width=1)
        draw.rounded_rectangle((cx, cy, x1 - 2, y1 - 2), radius=max(3, rad - 2), fill=(20, 184, 166, 255))
        s = max(2, size // 12)
        draw.line([(cx - 7 - s, cy - 7), (cx - 7 + s, cy - 7)], fill=(248, 250, 252, 255), width=2)
        draw.line([(cx - 7, cy - 7 - s), (cx - 7, cy - 7 + s)], fill=(248, 250, 252, 255), width=2)
        draw.line([(cx + 6 - s, cy - 7), (cx + 6 + s, cy - 7)], fill=(248, 250, 252, 255), width=2)
        draw.line([(cx + 6 - s, cy + 5), (cx + 6 + s, cy + 5)], fill=(248, 250, 252, 255), width=2)
        draw.line([(cx + 6 - s, cy + 9), (cx + 6 + s, cy + 9)], fill=(248, 250, 252, 255), width=2)


def _load_cjk_font(size: int):
    from PIL import ImageFont

    for fp in (
        '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
        '/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc',
        '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
    ):
        if Path(fp).exists():
            try:
                return ImageFont.truetype(fp, size)
            except Exception:
                pass
    return ImageFont.load_default()


def _generate_win11_wallpaper(wallpaper_path: Path, width: int = WIDTH, height: int = HEIGHT) -> None:
    """Generate a sleek DeepSeek Whale Girl (沙软二次元大鲸鱼娘) desktop wallpaper with built-in zero-RAM desktop icons & bottom taskbar."""
    try:
        from PIL import Image, ImageDraw, ImageFilter, ImageOps

        bg_asset = Path(__file__).with_name('cloud_os') / 'whale_girl_bg.jpg'
        if bg_asset.exists():
            img = Image.open(bg_asset).convert('RGB')
            if img.size != (width, height):
                img = ImageOps.fit(img, (width, height), method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))
        else:
            img = Image.new('RGB', (width, height), (9, 14, 26))
            draw = ImageDraw.Draw(img)
            for y in range(height):
                t = y / max(1, height - 1)
                r = int(9 + 12 * (1 - t) + 6 * t)
                g = int(14 + 22 * (1 - t) + 10 * t)
                b = int(28 + 46 * (1 - t) + 20 * t)
                draw.line([(0, y), (width, y)], fill=(r, g, b))

            glow = Image.new('RGBA', (width, height), (0, 0, 0, 0))
            gdraw = ImageDraw.Draw(glow)
            cx, cy = int(width * 0.56), int(height * 0.46)
            petals = [
                ((cx - 260, cy - 210, cx + 180, cy + 160), (37, 99, 235, 95)),
                ((cx - 120, cy - 250, cx + 290, cy + 120), (56, 189, 248, 85)),
                ((cx - 200, cy - 90, cx + 260, cy + 240), (99, 102, 241, 88)),
                ((cx - 60, cy - 140, cx + 330, cy + 210), (14, 165, 233, 80)),
                ((cx - 140, cy - 150, cx + 160, cy + 130), (125, 211, 252, 75)),
            ]
            for bbox, col in petals:
                gdraw.ellipse(bbox, fill=col, outline=(186, 230, 253, 45), width=2)
            glow = glow.filter(ImageFilter.GaussianBlur(radius=36))
            img = Image.alpha_composite(img.convert('RGBA'), glow).convert('RGB')
            del glow

        overlay = Image.new('RGBA', (width, height), (0, 0, 0, 0))
        odraw = ImageDraw.Draw(overlay)

        font_icon = _load_cjk_font(13)
        font_badge = _load_cjk_font(12)
        font_clock = _load_cjk_font(12)
        font_date = _load_cjk_font(10)

        # 1. Paint the 8 Desktop Shortcut Icons directly onto the wallpaper (0 MB resident RAM!)
        desktop_specs = [
            ('chrome', 'Chrome 浏览器', 60, 66),
            ('terminal', '终端 Terminal', 60, 172),
            ('files', '此电脑 Files', 60, 278),
            ('code', 'Geany 代码IDE', 60, 384),
            ('excel', 'Excel 表格', 174, 66),
            ('notepad', '记事本 Notes', 174, 172),
            ('taskmgr', '任务管理器', 174, 278),
            ('calculator', '科学计算器', 174, 384),
        ]
        for app_key, label, ix, iy in desktop_specs:
            _draw_papirus_icon(odraw, app_key, ix, iy - 10, size=44)
            try:
                tb = odraw.textbbox((0, 0), label, font=font_icon)
                tw = tb[2] - tb[0]
            except Exception:
                tw = len(label) * 8
            tx = ix - tw // 2
            ty = iy + 20
            odraw.rounded_rectangle((tx - 6, ty - 2, tx + tw + 6, ty + 16), radius=6, fill=(15, 23, 42, 200))
            odraw.text((tx, ty), label, font=font_icon, fill=(248, 250, 252, 255))

        # 2. Subtle HUD badge in bottom-right above taskbar
        badge_w, badge_h = 350, 54
        bx, by = width - badge_w - 22, height - badge_h - 56
        odraw.rounded_rectangle((bx, by, bx + badge_w, by + badge_h), radius=12, fill=(15, 23, 42, 175), outline=(56, 189, 248, 90), width=1)
        odraw.text((bx + 16, by + 10), 'Proactive Cloud Desktop · Windows 11 Mode', font=font_badge, fill=(241, 245, 249, 235))
        odraw.text((bx + 16, by + 29), '1 vCPU · 1 GB RAM (+2 GB Swap) · 10 GB SSD', font=font_badge, fill=(56, 189, 248, 220))

        # 3. Paint the sleek Windows 11 Bottom Taskbar (replaces 28MB tint2 daemon with 0 MB RAM!)
        tb_y0, tb_y1 = height - 43, height - 3
        odraw.rounded_rectangle((12, tb_y0, width - 12, tb_y1), radius=12, fill=(15, 23, 42, 240), outline=(51, 65, 85, 215), width=1)
        taskbar_apps = ['chrome', 'terminal', 'files', 'code', 'excel', 'notepad', 'taskmgr', 'calculator']
        for idx, app_key in enumerate(taskbar_apps):
            tx_center = 38 + idx * 34
            _draw_papirus_icon(odraw, app_key, tx_center, (tb_y0 + tb_y1) // 2, size=24)

        now_tm = time.localtime()
        t_str = time.strftime('%H:%M', now_tm)
        d_str = time.strftime('%Y/%m/%d', now_tm)
        odraw.text((width - 72, tb_y0 + 5), t_str, font=font_clock, fill=(248, 250, 252, 245))
        odraw.text((width - 84, tb_y0 + 22), d_str, font=font_date, fill=(203, 213, 225, 220))

        img = Image.alpha_composite(img.convert('RGBA'), overlay).convert('RGB')
        del overlay
        wallpaper_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(wallpaper_path, format='PNG')
        del img
        _trim_process_memory()
    except Exception:
        pass


def _set_x11_root_wallpaper(wallpaper_path: Path, display: str = ':91') -> bool:
    """Set `wallpaper.png` onto the X11 root window via `libX11.so.6` with RetainPermanent (0 MB resident daemon!)."""
    try:
        import ctypes
        from PIL import Image

        if not wallpaper_path.exists():
            return False
        img = Image.open(wallpaper_path).convert('RGB')
        w, h = img.size
        bgrx_bytes = img.tobytes('raw', 'BGRX')
        del img

        x11 = ctypes.CDLL('libX11.so.6')
        x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        x11.XOpenDisplay.restype = ctypes.c_void_p
        x11.XDefaultScreen.argtypes = [ctypes.c_void_p]
        x11.XDefaultScreen.restype = ctypes.c_int
        x11.XRootWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
        x11.XRootWindow.restype = ctypes.c_ulong
        x11.XDefaultDepth.argtypes = [ctypes.c_void_p, ctypes.c_int]
        x11.XDefaultDepth.restype = ctypes.c_int
        x11.XDefaultVisual.argtypes = [ctypes.c_void_p, ctypes.c_int]
        x11.XDefaultVisual.restype = ctypes.c_void_p
        x11.XDefaultGC.argtypes = [ctypes.c_void_p, ctypes.c_int]
        x11.XDefaultGC.restype = ctypes.c_void_p
        x11.XCreatePixmap.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint]
        x11.XCreatePixmap.restype = ctypes.c_ulong
        x11.XFreePixmap.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        x11.XFreePixmap.restype = ctypes.c_int
        x11.XCreateImage.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_uint,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
            ctypes.c_uint,
            ctypes.c_int,
            ctypes.c_int,
        ]
        x11.XCreateImage.restype = ctypes.c_void_p
        x11.XPutImage.argtypes = [
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint,
            ctypes.c_uint,
        ]
        x11.XPutImage.restype = ctypes.c_int
        x11.XSetWindowBackgroundPixmap.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong]
        x11.XSetWindowBackgroundPixmap.restype = ctypes.c_int
        x11.XClearWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        x11.XClearWindow.restype = ctypes.c_int
        x11.XSetCloseDownMode.argtypes = [ctypes.c_void_p, ctypes.c_int]
        x11.XSetCloseDownMode.restype = ctypes.c_int
        x11.XFlush.argtypes = [ctypes.c_void_p]
        x11.XCloseDisplay.argtypes = [ctypes.c_void_p]

        dpy = x11.XOpenDisplay(display.encode('utf-8'))
        if not dpy:
            return False
        try:
            scr = x11.XDefaultScreen(dpy)
            root = x11.XRootWindow(dpy, scr)
            depth = x11.XDefaultDepth(dpy, scr)
            vis = x11.XDefaultVisual(dpy, scr)
            gc = x11.XDefaultGC(dpy, scr)

            pix_file = Path('/tmp/.x91_root_pixmap')
            if pix_file.exists():
                with contextlib.suppress(Exception):
                    old_pm = int(pix_file.read_text('utf-8').strip())
                    if old_pm > 0:
                        x11.XFreePixmap(dpy, old_pm)

            pixmap = x11.XCreatePixmap(dpy, root, w, h, depth)
            buf = ctypes.create_string_buffer(bgrx_bytes, len(bgrx_bytes))
            ximg = x11.XCreateImage(dpy, vis, depth, 2, 0, ctypes.cast(buf, ctypes.c_char_p), w, h, 32, w * 4)
            if ximg:
                x11.XPutImage(dpy, pixmap, gc, ximg, 0, 0, 0, 0, w, h)
            x11.XSetWindowBackgroundPixmap(dpy, root, pixmap)
            x11.XClearWindow(dpy, root)
            x11.XSetCloseDownMode(dpy, 1)  # RetainPermanent
            x11.XFlush(dpy)
            with contextlib.suppress(Exception):
                pix_file.write_text(str(pixmap), encoding='utf-8')
        finally:
            x11.XCloseDisplay(dpy)
        del bgrx_bytes
        _trim_process_memory()
        return True
    except Exception:
        return False


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

    def _setup_win11_environment(self):
        """Initialize the 1C 1G 10GB subsystem shims + Windows 11 Desktop Icons, Wallpaper & GTK/PCManFM config."""
        for sub in (
            '.local/bin',
            '.local/sysroot',
            '.local/root_home',
            '.local/apt_cache',
            '.local/state',
            '.cache/pip',
            '.config/gtk-3.0',
            '.config/libfm',
            '.config/pcmanfm/default',
            'tmp',
            'Downloads',
            'Desktop',
        ):
            (self.home / sub).mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(Exception):
            (self.home / '.local' / 'state').chmod(0o700)
        self.subsystem_env = ensure_subsystem_environment(self.home)

        # 1. Generate Windows 11 Dark Bloom Wallpaper
        wallpaper_path = self.home / '.config' / 'wallpaper.png'
        _generate_win11_wallpaper(wallpaper_path, WIDTH, HEIGHT)

        # 2. Configure GTK-2 / GTK-3 Dark Theme & Papirus-Dark Colorful Icons
        gtk3_ini = self.home / '.config' / 'gtk-3.0' / 'settings.ini'
        gtk3_ini.write_text(
            '[Settings]\n'
            'gtk-application-prefer-dark-theme=1\n'
            'gtk-theme-name=Adwaita-dark\n'
            'gtk-icon-theme-name=Papirus-Dark\n'
            'gtk-font-name=Noto Sans CJK SC 10\n',
            encoding='utf-8',
        )
        gtk2_rc = self.home / '.gtkrc-2.0'
        gtk2_rc.write_text(
            'gtk-theme-name="Adwaita-dark"\n'
            'gtk-icon-theme-name="Papirus-Dark"\n'
            'gtk-font-name="Noto Sans CJK SC 10"\n',
            encoding='utf-8',
        )

        # 3. Configure LibFM so desktop icons open with a SINGLE click and NEVER prompt "Execute file?" dialog
        libfm_conf = self.home / '.config' / 'libfm' / 'libfm.conf'
        libfm_conf.write_text(
            '[config]\n'
            'single_click=1\n'
            'quick_exec=1\n'
            'confirm_del=0\n'
            'confirm_trash=0\n'
            'big_icon_size=48\n'
            'small_icon_size=24\n'
            'pane_icon_size=24\n'
            'thumbnail_local=1\n',
            encoding='utf-8',
        )

        bashrc = self.home / '.bashrc'
        bashrc_content = (
            'export PATH="$HOME/venv/bin:$HOME/.local/bin:$HOME/.local/sysroot/usr/local/bin:$HOME/.local/sysroot/usr/bin:$HOME/.local/sysroot/bin:/usr/local/bin:/usr/bin:/bin"\n'
            'export LD_LIBRARY_PATH="$HOME/.local/sysroot/usr/lib/x86_64-linux-gnu:$HOME/.local/sysroot/lib/x86_64-linux-gnu:$HOME/.local/sysroot/usr/lib"\n'
            'export VIRTUAL_ENV="$HOME/venv"\n'
            'export PREFIX="$HOME/.local"\n'
            'export SUBSYSTEM_PROFILE="1 vCPU · 1 GB RAM (+2 GB Swap) · 10 GB SSD"\n'
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
                '\033[1;36mCloud Computer · 1C 1G (+2G Swap) 10GB Isolated Linux Subsystem\033[0m\n'
                '  Specs   : 1 vCPU | 1024 MB RAM (+2048 MB Swap) | 10 GB SSD\n'
                '  Apps    : Chrome, Terminal (Tmux), Files, Geany IDE, Gnumeric Excel, Mousepad, TaskMgr, Calculator\n'
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

    async def _browser_idle_reaper(self):
        hibernate_sec = float(os.getenv('DS_CLAW_BROWSER_HIBERNATE_SEC') or 1200.0)
        while True:
            await asyncio.sleep(30)
            try:
                if self.last_browser_active_at > 0 and (time.time() - self.last_browser_active_at) >= hibernate_sec:
                    reaped = await asyncio.to_thread(reap_idle_browsers, self._proxy_env())
                    if reaped > 0:
                        self.last_browser_active_at = 0.0
                        self.last_dom_snapshot = {}
                        self.current_url = ''
                        self.current_title = 'Windows 11 Desktop · 1C 1G (+2G Swap) 10GB (Browser Hibernated)'
                        _trim_process_memory()
            except asyncio.CancelledError:
                break
            except Exception:
                pass

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

    def _desktop_icon_elements(self) -> list[dict]:
        raw_icons = [
            {'ref': '@b1', 'tag': 'icon', 'type': 'desktop_icon', 'label': 'Chrome 浏览器', 'app': 'chrome', 'x': 60, 'y': 66, 'w': 72, 'h': 80},
            {'ref': '@b2', 'tag': 'icon', 'type': 'desktop_icon', 'label': '终端 Terminal', 'app': 'terminal', 'x': 60, 'y': 172, 'w': 72, 'h': 80},
            {'ref': '@b3', 'tag': 'icon', 'type': 'desktop_icon', 'label': '此电脑 Files', 'app': 'files', 'x': 60, 'y': 278, 'w': 72, 'h': 80},
            {'ref': '@b4', 'tag': 'icon', 'type': 'desktop_icon', 'label': 'Geany 代码IDE', 'app': 'code', 'x': 60, 'y': 384, 'w': 72, 'h': 80},
            {'ref': '@b5', 'tag': 'icon', 'type': 'desktop_icon', 'label': 'Excel 表格', 'app': 'excel', 'x': 174, 'y': 66, 'w': 72, 'h': 80},
            {'ref': '@b6', 'tag': 'icon', 'type': 'desktop_icon', 'label': '记事本 Notes', 'app': 'notepad', 'x': 174, 'y': 172, 'w': 72, 'h': 80},
            {'ref': '@b7', 'tag': 'icon', 'type': 'desktop_icon', 'label': '任务管理器', 'app': 'taskmgr', 'x': 174, 'y': 278, 'w': 72, 'h': 80},
            {'ref': '@b8', 'tag': 'icon', 'type': 'desktop_icon', 'label': '科学计算器', 'app': 'calculator', 'x': 174, 'y': 384, 'w': 72, 'h': 80},
        ]
        for ic in raw_icons:
            ic['nx'] = round(ic['x'] * 1000 / WIDTH)
            ic['ny'] = round(ic['y'] * 1000 / HEIGHT)
        return raw_icons

    def _sync_active_dom_state(self, wins: list[dict] | None = None, refresh_cdp: bool = False) -> tuple[list[dict], list[dict], str]:
        """
        Synchronize `last_dom_snapshot`, `dom_elements`, and `page_text` with actual open X11 windows:
        - If no browser window is open, immediately purge stale `last_dom_snapshot` and `current_url`.
        - If `len(wins) == 0` (clean desktop visible), return the 8 desktop shortcut icons `@b1..@b8`.
        - If top-most window is a browser, return its live `@b1..@bN` DOM elements.
        - If top-most window is a native desktop app (Excel, Geany, TaskMgr, Terminal), return `[]` so stale web badges are never drawn over native apps.
        """
        if wins is None:
            wins = self._list_windows()
        has_browser_win = any(
            any(k in str(w.get('wm_class', '')).lower() for k in ('chromium', 'chrome', 'luakit'))
            for w in wins
        )
        if not has_browser_win:
            self.last_dom_snapshot = {}
            self.current_url = ''
            if not wins:
                self.current_title = 'Windows 11 Desktop · 1C 1G (+2G Swap) 10GB'
                return (
                    wins,
                    self._desktop_icon_elements(),
                    'Windows 11 Desktop · 桌面图标：[@b1] Chrome 浏览器 | [@b2] 终端 Terminal | [@b3] 此电脑 Files | [@b4] Geany 代码IDE | [@b5] Excel 表格 | [@b6] 记事本 Notes | [@b7] 任务管理器 | [@b8] 科学计算器',
                )
            top_title = str(wins[0].get('title') or wins[0].get('wm_class') or 'Desktop App')
            self.current_title = top_title
            return wins, [], f'当前活动桌面窗口：{top_title}'

        top_is_browser = any(
            k in str(wins[0].get('wm_class', '')).lower() for k in ('chromium', 'chrome', 'luakit')
        )
        if not top_is_browser:
            top_title = str(wins[0].get('title') or wins[0].get('wm_class') or 'Desktop App')
            return wins, [], f'当前活动桌面窗口：{top_title}'

        if refresh_cdp or not self.last_dom_snapshot:
            live = extract_live_cdp_dom_snapshot(wait_ms=120 if refresh_cdp else 40)
            if live and (live.get('page_text') or live.get('elements')):
                self.last_dom_snapshot = live
                if live.get('url'):
                    self.current_url = str(live['url'])
                if live.get('title'):
                    self.current_title = f"{live['title']} · Chromium"
        elems = (self.last_dom_snapshot or {}).get('elements', [])[:25]
        ptxt = (self.last_dom_snapshot or {}).get('page_text', '')[:1200]
        return wins, elems, ptxt

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
        try:
            await public_addresses(parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80), {'216.23.82.196'})
        except OSError as exc:
            raise ValueError(f'无法解析或访问目标域名 {parsed.hostname} ({exc})') from None
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
            target_url = arg_text.strip() if arg_text.strip().startswith('http') else (self.current_url or 'https://www.bing.com')
            if not arg_text.strip().startswith('http'):
                for b_class in ('chromium', 'luakit', 'netsurf'):
                    if focus_window(env, b_class):
                        return f'{b_class}_focused'
            self._open_url_in_modern_browser(target_url)
            tile_windows(env, WIDTH, HEIGHT)
            return 'modern_browser'
        if app_key in ('terminal', 'lxterminal', 'xterm', 'bash', 'shell', 'powershell', 'tmux'):
            ensure_single_tmux_terminal(env, self.workspace)
            focus_window(env, 'lxterminal') or focus_window(env, 'xterm')
            self.current_title = 'Windows Terminal (Tmux)'
            return 'tmux_terminal'
        if app_key in ('files', 'file_manager', 'pcmanfm', 'folder', 'explorer', '此电脑'):
            if shutil.which('pcmanfm'):
                target_dir = str(self.workspace / arg_text) if arg_text and not arg_text.startswith('http') else str(self.workspace)
                self.spawn(['/usr/bin/pcmanfm', target_dir], cwd=str(self.workspace))
                time.sleep(0.45)
                self.current_title = 'File Explorer (PCManFM)'
                return 'pcmanfm'
        if app_key in ('code', 'ide', 'geany', 'vscode', 'studio', '代码'):
            if not arg_text and focus_window(env, 'geany'):
                return 'geany_focused'
            if shutil.which('geany'):
                cmd = ['/usr/bin/geany']
                if arg_text and not arg_text.startswith('http'):
                    cmd.append(str(self.workspace / arg_text))
                self.spawn(cmd, cwd=str(self.workspace))
                time.sleep(0.55)
                tile_windows(env, WIDTH, HEIGHT)
                self.current_title = 'Geany Code IDE'
                return 'geany'
        if app_key in ('excel', 'spreadsheet', 'gnumeric', 'sheet', 'calc_sheet', '表格'):
            if not arg_text and focus_window(env, 'gnumeric'):
                return 'gnumeric_focused'
            if shutil.which('gnumeric'):
                cmd = ['/usr/bin/gnumeric']
                if arg_text and not arg_text.startswith('http'):
                    cmd.append(str(self.workspace / arg_text))
                self.spawn(cmd, cwd=str(self.workspace))
                time.sleep(0.6)
                tile_windows(env, WIDTH, HEIGHT)
                self.current_title = 'Gnumeric Spreadsheet (Excel)'
                return 'gnumeric'
        if app_key in ('editor', 'mousepad', 'notepad', 'notes', 'word', '记事本'):
            if not arg_text and focus_window(env, 'mousepad'):
                return 'editor_focused'
            if shutil.which('mousepad'):
                cmd = ['/usr/bin/mousepad']
                if arg_text and not arg_text.startswith('http'):
                    cand = (self.workspace / arg_text).resolve()
                    if cand.is_relative_to(self.workspace.resolve()):
                        cmd.append(str(cand))
                self.spawn(cmd, cwd=str(self.workspace))
                time.sleep(0.45)
                self.current_title = 'Notepad (Mousepad)'
                return 'mousepad'
        if app_key in ('taskmgr', 'task_manager', 'monitor', 'lxtask', 'top', '任务管理器'):
            if focus_window(env, 'lxtask'):
                return 'lxtask_focused'
            if shutil.which('lxtask'):
                self.spawn(['/usr/bin/lxtask'], cwd=str(self.workspace))
                time.sleep(0.4)
                self.current_title = 'Task Manager (LXTask)'
                return 'lxtask'
        if app_key in ('calculator', 'calc', 'galculator', '计算器'):
            if focus_window(env, 'galculator'):
                return 'galculator_focused'
            if shutil.which('galculator'):
                self.spawn(['/usr/bin/galculator'], cwd=str(self.workspace))
                time.sleep(0.4)
                self.current_title = 'Calculator (Galculator)'
                return 'galculator'
        if app_key in ('pdf', 'zathura', 'reader'):
            if shutil.which('zathura'):
                cmd = ['/usr/bin/zathura']
                if arg_text:
                    cmd.append(str(self.workspace / arg_text))
                self.spawn(cmd, cwd=str(self.workspace))
                time.sleep(0.4)
                self.current_title = 'PDF Viewer (Zathura)'
                return 'zathura'
        if app_key in ('image', 'viewer', 'gpicview', 'photo'):
            if shutil.which('gpicview'):
                cmd = ['/usr/bin/gpicview']
                if arg_text:
                    cmd.append(str(self.workspace / arg_text))
                self.spawn(cmd, cwd=str(self.workspace))
                time.sleep(0.4)
                self.current_title = 'Image Viewer (GPicView)'
                return 'gpicview'
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
        self,
        kind: str,
        x: int = 0,
        y: int = 0,
        x2: int = 0,
        y2: int = 0,
        nx: int = -1,
        ny: int = -1,
        nx2: int = -1,
        ny2: int = -1,
        grid: str = '',
        grid2: str = '',
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
                        kind=s_kind,
                        x=int(step.get('x') or 0),
                        y=int(step.get('y') or 0),
                        x2=int(step.get('x2') or 0),
                        y2=int(step.get('y2') or 0),
                        nx=int(step.get('nx') if step.get('nx') is not None else -1),
                        ny=int(step.get('ny') if step.get('ny') is not None else -1),
                        nx2=int(step.get('nx2') if step.get('nx2') is not None else -1),
                        ny2=int(step.get('ny2') if step.get('ny2') is not None else -1),
                        grid=str(step.get('grid') or ''),
                        grid2=str(step.get('grid2') or ''),
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
                    nx2=action.nx2,
                    ny2=action.ny2,
                    grid=action.grid,
                    grid2=action.grid2,
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
