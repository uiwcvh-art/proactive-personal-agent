"""
[Module Responsibility]
云端电脑视觉合成、浏览器/DOM 同步与桌面应用启动器 (`server/computer_browser.py`)。
负责 Windows 11 风格桌面壁纸与图标绘制 (`_generate_win11_wallpaper`, `_set_x11_root_wallpaper`)、
0..1000 归一化/网格坐标转换 (`resolve_grid_or_norm_xy`)、Chromium CDP 无头 DOM 节点 (`@b1..@bN`) 与真实视口画面同步 (`_sync_active_dom_state`)、
网页导航与多引擎搜索 (`navigate`, `search`) 以及桌面原生应用启动与聚焦 (`_launch_or_focus_desktop_app`)。

[Public Interface]
- `HOME_HTML: str`: 云端浏览器默认起始页 HTML
- `_trim_process_memory() -> None`: 释放 Python/PIL 空闲堆内存
- `_draw_papirus_icon(draw, app_key, cx, cy, size=44) -> None`: 绘制桌面矢量图标
- `_load_cjk_font(size: int)`: 加载中文字体
- `_generate_win11_wallpaper(wallpaper_path: Path, width: int, height: int) -> None`: 生成桌面壁纸与任务栏
- `_set_x11_root_wallpaper(wallpaper_path: Path, display: str) -> bool`: 刷新 X11 根窗口背景
- `resolve_grid_or_norm_xy(x, y, nx=-1, ny=-1, grid='', width=WIDTH, height=HEIGHT) -> tuple[int, int]`: 坐标解析
- `DesktopBrowserMixin`: 为 `CloudDesktop` 提供浏览器/DOM/应用控制 Mixin

[Dependencies]
- `cloud_os.agent_browser`: Chromium CDP 真实 DOM 快照与无障碍树引擎
- `cloud_os.tmux_terminal`: 持久化交互式 Tmux 终端会话
- `cloud_os.window_manager`: X11 窗口聚焦与管理
- `core.search_engine`: 多源并发搜索引擎
- `computer_models`, `computer_security`: 请求模型与安全命令校验
"""
from __future__ import annotations

import asyncio
import contextlib
import shutil
import time
from pathlib import Path
from urllib.parse import quote_plus, urlsplit

import os, subprocess, sys
from cloud_os.agent_browser import extract_live_cdp_dom_snapshot, launch_modern_browser, reap_idle_browsers
from cloud_os.subsystem import ensure_subsystem_environment
from cloud_os.tmux_terminal import ensure_single_tmux_terminal
from cloud_os.window_manager import focus_window, tile_windows
from computer_models import HEIGHT, WIDTH
from computer_security import public_addresses
from core.search_engine import multi_web_search


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
  <div class="tag">● DSB v4.0 SHARED 24/7 CLOUD COMPUTER · PHONE + PC DIRECT CONTROL</div>
  <h1>DSB Shared Cloud Workstation</h1>
  <div class="sub">可控于手机端与 Windows 电脑端 DSH 的全天候隔离 Linux 云电脑：Chromium CDP (@b1..@bN)、0..1000 视觉标尺、持久 Tmux 与共享黑板协同（详见 ~/workspace/ARCHITECTURE.md）。</div>
  <div class="grid">
    <div class="card"><div class="ic">🌐</div><b>Chromium CDP</b><span>Live JS/SPA rendering, Set-of-Mark @bN refs &amp; WARP egress</span></div>
    <div class="card"><div class="ic">&gt;_</div><b>Persistent Tmux</b><span>Session "agent" preserves cwd, env &amp; background tasks</span></div>
    <div class="card"><div class="ic">📋</div><b>Event Blackboard</b><span>Coordinated by DSB cloud hub (@mention, 0-token watchpoints)</span></div>
    <div class="card"><div class="ic">📁</div><b>Workspace</b><span>~/workspace &amp; ~/Downloads synced with Phone &amp; PC</span></div>
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
        x11.XCreateImage.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint, ctypes.c_uint, ctypes.c_int, ctypes.c_int]
        x11.XCreateImage.restype = ctypes.c_void_p
        x11.XPutImage.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint, ctypes.c_uint]
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



class DesktopBrowserMixin:
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

    def _setup_win11_environment(self):
        """Initialize the 1C 1G 10GB subsystem shims + Windows 11 Desktop Icons, Wallpaper & GTK/PCManFM config."""
        for sub in ('.local/bin', '.local/sysroot', '.local/root_home', '.local/apt_cache', '.local/state', '.cache/pip', '.config/gtk-3.0', '.config/libfm', '.config/pcmanfm/default', 'tmp', 'Downloads', 'Desktop'):
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
                '\033[1;36mDSB v4.0 Shared Cloud Computer · 1C 1G (+2G Swap) 10GB Isolated Linux Workstation\033[0m\n'
                '  Role    : 24/7 Shared Cloud Sandbox directly controllable by BOTH Phone Bots & Windows PC Bots\n'
                '  Specs   : 1 vCPU | 1024 MB RAM (+2048 MB Swap) | 10 GB SSD | Display :91 (1280x800)\n'
                '  ArchDoc : ~/workspace/ARCHITECTURE.md  (MemoryVault lives on host via native read_file/memory_search)\n'
                '------------------------------------------------------------------------\n',
                encoding='utf-8',
            )
        arch_doc_text = (
            '# 🏛️ DSB (DeepSeek Bridge) 云电脑节点自述与系统架构指南 (v4.0)\n\n'
            '你当前正处于 **DSB 全天候共享 Linux 云电脑 (`cloud_desktop` / `proactive-desktop.service`)** 内部！\n\n'
            '## 1. 我是谁？我在整个 DSB 架构中的位置\n'
            '- **定位**：24/7 在线的云端隔离 Linux 虚拟工作站（`1 vCPU · 1GB RAM (+2GB Swap) · 10GB SSD`，X11 `:91` `1280x800`）。\n'
            '- **双端直控**：**手机端 Bot**（如白珂/研究助手/杠精）与 **Windows 电脑端 Bot / DSH 会话**（如司工/探微/审校/云汐）**均可直接调用 `cloud_desktop` 操控本云电脑**！\n'
            '- **三大执行平面分工**：\n'
            '  1. `phone_workspace`：操控主人的安卓手机独立虚拟屏。\n'
            '  2. `dsh_workspace` / Windows `pc_shell` (Git Bash)：操控主人的 Windows 电脑本机与 DSH 会话。\n'
            '  3. `cloud_desktop`（**当前环境**）：负责 24/7 云端挂机脚本、Chromium CDP 网页渲染 (`:9222`, `@b1..@bN`)、Linux 编译测试与跨端文件暂存（`~/workspace` 与 `~/Downloads`）。\n\n'
            '## 2. 重要边界与常见误区（必读！）\n'
            '- **为什么在当前 Linux 终端里 `ls` 看不到 `memory_vault/` 或 `state.json`？**\n'
            '  因为本云电脑运行在独立的受限系统账号 `proactive-desktop`（`HOME=/opt/proactive-agent/cloud-computer/desktop-home`）中，与主控服务 `/opt/proactive-agent/data/memory_vault/` 物理隔离。\n'
            '  👉 **正确做法**：要读写记忆库、查询其他 Bot 进度或操作共享黑板，请直接调用 Agent 原生工具：`read_file("memory_vault/...")`、`memory_search`、`blackboard(action="...")`、`peek_bots()`，不要在本机 `/workspace` 里 `find memory_vault`！\n'
            '- **多 Bot 协同方式**：点对点 `send_message` 已废除；跨 Bot 派活、交接文件、定闹钟或挂 0-Token 探针统一使用 **`blackboard`** 工具（支持 `@Bot名`、`@self`、`@everyone`、`claim`、`artifact_path`、`watch`）。\n'
        )
        for doc_p in (self.workspace / 'ARCHITECTURE.md', self.home / 'Desktop' / 'DSB架构自述.md'):
            with contextlib.suppress(Exception):
                doc_p.parent.mkdir(parents=True, exist_ok=True)
                doc_p.write_text(arch_doc_text, encoding='utf-8')
        if not (self.venv_dir / 'bin' / 'python3').exists():
            subprocess.Popen(
                [sys.executable, '-m', 'venv', str(self.venv_dir)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

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

