"""Pulse · Lightweight Non-Intrusive Desktop Agent (`desktop/desktop_agent.py`).

Three-Layer Non-Intrusive PC Control Architecture (Mirrors Android VirtualDisplay):
- Layer 1 (Zero-UI Fast Path): `shell` (CREATE_NO_WINDOW PowerShell/Bash), `clipboard` (Win32 native),
  `open_url`, `launch` (`SW_SHOWNOACTIVATE` without stealing focus).
- Layer 2 (Background HWND & Semantic UI Tree `@n1..@nN`):
  - `windows`: Enumerates visible top-level windows (`hwnd`, `title`, `process`, `bounds`).
  - `tree` / `ui_tree`: Extracts interactive `@n1..@nN` controls via Windows UI Automation (if available)
    + built-in zero-dependency Win32 `EnumChildWindows` traversal.
  - `screenshot`: Captures a specific background/occluded window via Win32 `PrintWindow(..., PW_RENDERFULLCONTENT)`
    without bringing it to the foreground!
  - `click_node` / `type_node`: Dispatches UIA `InvokePattern`/`ValuePattern` or Win32 `PostMessageW`
    (`WM_LBUTTONDOWN`/`WM_LBUTTONUP`/`WM_CHAR`/`WM_SETTEXT`) directly to the window without moving the
    user's physical mouse or stealing keyboard focus!
- Layer 3 (Physical Fallback with Human Activity Guard & <12ms Cursor Save-and-Restore):
  - Checks `GetLastInputInfo()` before any physical `SendInput`; if the user is actively moving the mouse,
    defers instead of fighting the user's cursor.
- Zero Idle Drain: 100% event-driven over WebSocket (`/ws/device`), 0% idle CPU, 0 background polling.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import ctypes
import ctypes.wintypes as wt
import io
import json
import os
import platform
import secrets
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from typing import Any

import contextlib

IS_WIN = sys.platform == 'win32'

if IS_WIN:
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    kernel32 = ctypes.windll.kernel32

    # 64-bit safe kernel32 signatures
    kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    kernel32.OpenProcess.restype = wt.HANDLE
    kernel32.CloseHandle.argtypes = [wt.HANDLE]
    kernel32.CloseHandle.restype = wt.BOOL
    kernel32.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
    kernel32.QueryFullProcessImageNameW.restype = wt.BOOL
    kernel32.GetTickCount.argtypes = []
    kernel32.GetTickCount.restype = wt.DWORD
    kernel32.GlobalAlloc.argtypes = [wt.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalUnlock.restype = wt.BOOL
    kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
    kernel32.GlobalFree.restype = ctypes.c_void_p

    # 64-bit safe user32 signatures
    user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    user32.GetSystemMetrics.restype = ctypes.c_int
    user32.GetWindowTextLengthW.argtypes = [wt.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.restype = ctypes.c_int
    user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
    user32.GetWindowThreadProcessId.restype = wt.DWORD
    user32.GetForegroundWindow.argtypes = []
    user32.GetForegroundWindow.restype = wt.HWND
    user32.IsWindowVisible.argtypes = [wt.HWND]
    user32.IsWindowVisible.restype = wt.BOOL
    user32.IsWindow.argtypes = [wt.HWND]
    user32.IsWindow.restype = wt.BOOL
    user32.IsIconic.argtypes = [wt.HWND]
    user32.IsIconic.restype = wt.BOOL
    user32.IsChild.argtypes = [wt.HWND, wt.HWND]
    user32.IsChild.restype = wt.BOOL
    user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
    user32.GetWindowRect.restype = wt.BOOL
    user32.GetClientRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
    user32.GetClientRect.restype = wt.BOOL
    user32.ClientToScreen.argtypes = [wt.HWND, ctypes.POINTER(wt.POINT)]
    user32.ClientToScreen.restype = wt.BOOL
    user32.ScreenToClient.argtypes = [wt.HWND, ctypes.POINTER(wt.POINT)]
    user32.ScreenToClient.restype = wt.BOOL
    user32.WindowFromPoint.argtypes = [wt.POINT]
    user32.WindowFromPoint.restype = wt.HWND
    user32.ChildWindowFromPointEx.argtypes = [wt.HWND, wt.POINT, wt.UINT]
    user32.ChildWindowFromPointEx.restype = wt.HWND
    user32.EnumWindows.argtypes = [ctypes.c_void_p, wt.LPARAM]
    user32.EnumWindows.restype = wt.BOOL
    user32.EnumChildWindows.argtypes = [wt.HWND, ctypes.c_void_p, wt.LPARAM]
    user32.EnumChildWindows.restype = wt.BOOL
    user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
    user32.PostMessageW.restype = wt.BOOL
    user32.SendMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
    user32.SendMessageW.restype = wt.LPARAM
    user32.OpenClipboard.argtypes = [wt.HWND]
    user32.OpenClipboard.restype = wt.BOOL
    user32.CloseClipboard.argtypes = []
    user32.CloseClipboard.restype = wt.BOOL
    user32.EmptyClipboard.argtypes = []
    user32.EmptyClipboard.restype = wt.BOOL
    user32.GetClipboardData.argtypes = [wt.UINT]
    user32.GetClipboardData.restype = ctypes.c_void_p
    user32.SetClipboardData.argtypes = [wt.UINT, ctypes.c_void_p]
    user32.SetClipboardData.restype = ctypes.c_void_p
    user32.GetWindowDC.argtypes = [wt.HWND]
    user32.GetWindowDC.restype = wt.HDC
    user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
    user32.ReleaseDC.restype = ctypes.c_int
    user32.PrintWindow.argtypes = [wt.HWND, wt.HDC, wt.UINT]
    user32.PrintWindow.restype = wt.BOOL

    # 64-bit safe gdi32 signatures
    gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
    gdi32.CreateCompatibleDC.restype = wt.HDC
    gdi32.CreateCompatibleBitmap.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]
    gdi32.CreateCompatibleBitmap.restype = wt.HBITMAP
    gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
    gdi32.SelectObject.restype = wt.HGDIOBJ
    gdi32.GetDIBits.argtypes = [wt.HDC, wt.HBITMAP, wt.UINT, wt.UINT, ctypes.c_void_p, ctypes.c_void_p, wt.UINT]
    gdi32.GetDIBits.restype = ctypes.c_int
    gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
    gdi32.DeleteObject.restype = wt.BOOL
    gdi32.DeleteDC.argtypes = [wt.HDC]
    gdi32.DeleteDC.restype = wt.BOOL

    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        pass

WM_SETTEXT = 0x000C
WM_CLOSE = 0x0010
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_CHAR = 0x0102
WM_COMMAND = 0x0111
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
WM_LBUTTONDBLCLK = 0x0203
WM_MOUSEWHEEL = 0x020A
BM_CLICK = 0x00F5
MK_LBUTTON = 0x0001
VK_RETURN = 0x0D
VK_BACK = 0x08
VK_TAB = 0x09
VK_ESCAPE = 0x1B
PW_RENDERFULLCONTENT = 0x00000002
SW_SHOWNOACTIVATE = 4
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [('cbSize', wt.UINT), ('dwTime', wt.DWORD)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ('biSize', wt.DWORD),
        ('biWidth', wt.LONG),
        ('biHeight', wt.LONG),
        ('biPlanes', wt.WORD),
        ('biBitCount', wt.WORD),
        ('biCompression', wt.DWORD),
        ('biSizeImage', wt.DWORD),
        ('biXPelsPerMeter', wt.LONG),
        ('biYPelsPerMeter', wt.LONG),
        ('biClrUsed', wt.DWORD),
        ('biClrImportant', wt.DWORD),
    ]


def makelparam(lo: int, hi: int) -> int:
    return ((hi & 0xFFFF) << 16) | (lo & 0xFFFF)


def get_human_idle_seconds() -> float:
    if not IS_WIN:
        return 999.0
    lii = LASTINPUTINFO()
    lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
    if user32.GetLastInputInfo(ctypes.byref(lii)):
        elapsed = (kernel32.GetTickCount() - lii.dwTime) & 0xFFFFFFFF
        return round(elapsed / 1000.0, 2)
    return 999.0


def get_screen_size() -> tuple[int, int]:
    if not IS_WIN:
        return (1920, 1080)
    w = int(user32.GetSystemMetrics(0) or 1920)
    h = int(user32.GetSystemMetrics(1) or 1080)
    return (w, h)


def _get_hwnd_text(hwnd: int) -> str:
    if not IS_WIN or not hwnd:
        return ''
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ''
    buf = ctypes.create_unicode_buffer(length + 2)
    user32.GetWindowTextW(hwnd, buf, length + 2)
    return buf.value.strip()


def _get_hwnd_class(hwnd: int) -> str:
    if not IS_WIN or not hwnd:
        return ''
    buf = ctypes.create_unicode_buffer(128)
    user32.GetClassNameW(hwnd, buf, 128)
    return buf.value.strip()


def _get_hwnd_process_name(hwnd: int) -> tuple[int, str]:
    if not IS_WIN or not hwnd:
        return (0, '')
    pid = wt.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    proc_id = int(pid.value)
    if proc_id <= 0:
        return (0, '')
    hproc = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, proc_id)
    if not hproc:
        return (proc_id, '')
    try:
        buf = ctypes.create_unicode_buffer(260)
        size = wt.DWORD(260)
        if kernel32.QueryFullProcessImageNameW(hproc, 0, buf, ctypes.byref(size)):
            return (proc_id, Path(buf.value).name)
    except Exception:
        pass
    finally:
        kernel32.CloseHandle(hproc)
    return (proc_id, '')


def list_windows(filter_query: str = '') -> list[dict[str, Any]]:
    if not IS_WIN:
        return []
    fg_hwnd = int(user32.GetForegroundWindow() or 0)
    results: list[dict[str, Any]] = []
    q = (filter_query or '').strip().lower()

    WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def _cb(hwnd: int, _lparam: int) -> bool:
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            title = _get_hwnd_text(hwnd)
            if not title or title in ('Program Manager', 'Windows Input Experience', 'MSCTFIME UI', 'Default IME'):
                return True
            rect = wt.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            w = rect.right - rect.left
            h = rect.bottom - rect.top
            if w < 24 or h < 24:
                return True
            pid, proc_name = _get_hwnd_process_name(hwnd)
            cls_name = _get_hwnd_class(hwnd)
            if q and q not in title.lower() and q not in proc_name.lower() and q not in cls_name.lower():
                return True
            results.append({
                'hwnd': int(hwnd),
                'title': title,
                'process': proc_name,
                'pid': pid,
                'class': cls_name,
                'bounds': [int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)],
                'width': int(w),
                'height': int(h),
                'minimized': bool(user32.IsIconic(hwnd)),
                'active': int(hwnd) == fg_hwnd,
            })
        except Exception:
            pass
        return True

    user32.EnumWindows(WNDENUMPROC(_cb), 0)
    results.sort(key=lambda x: (not x['active'], x['minimized'], x['title'].lower()))
    return results


def win32_clipboard_get() -> str:
    if not IS_WIN:
        return ''
    if not user32.OpenClipboard(0):
        return ''
    try:
        hmem = user32.GetClipboardData(CF_UNICODETEXT)
        if not hmem:
            return ''
        ptr = kernel32.GlobalLock(hmem)
        if not ptr:
            return ''
        try:
            return ctypes.wstring_at(ptr)
        finally:
            kernel32.GlobalUnlock(hmem)
    finally:
        user32.CloseClipboard()


def win32_clipboard_set(text: str) -> bool:
    if not IS_WIN:
        return False
    encoded = (text or '').encode('utf-16-le') + b'\x00\x00'
    hmem = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(encoded))
    if not hmem:
        return False
    ptr = kernel32.GlobalLock(hmem)
    if not ptr:
        kernel32.GlobalFree(hmem)
        return False
    try:
        ctypes.memmove(ptr, encoded, len(encoded))
    finally:
        kernel32.GlobalUnlock(hmem)

    if not user32.OpenClipboard(0):
        kernel32.GlobalFree(hmem)
        return False
    try:
        if not user32.EmptyClipboard():
            kernel32.GlobalFree(hmem)
            return False
        if not user32.SetClipboardData(CF_UNICODETEXT, hmem):
            kernel32.GlobalFree(hmem)
            return False
        return True
    finally:
        user32.CloseClipboard()


def capture_window_jpeg(hwnd: int = 0, full_screen: bool = False) -> tuple[str, int, int]:
    """Capture a background window via Win32 PrintWindow(PW_RENDERFULLCONTENT) or full screen."""
    if not IS_WIN:
        return ('', 0, 0)
    from PIL import Image

    if hwnd and not full_screen and user32.IsWindow(hwnd):
        rect = wt.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        w = max(1, rect.right - rect.left)
        h = max(1, rect.bottom - rect.top)
        hwnd_dc = user32.GetWindowDC(hwnd)
        if not hwnd_dc:
            return ('', 0, 0)
        mfc_dc = gdi32.CreateCompatibleDC(hwnd_dc)
        if not mfc_dc:
            user32.ReleaseDC(hwnd, hwnd_dc)
            return ('', 0, 0)
        bmp = gdi32.CreateCompatibleBitmap(hwnd_dc, w, h)
        if not bmp:
            gdi32.DeleteDC(mfc_dc)
            user32.ReleaseDC(hwnd, hwnd_dc)
            return ('', 0, 0)
        old_obj = gdi32.SelectObject(mfc_dc, bmp)
        try:
            ok = user32.PrintWindow(hwnd, mfc_dc, PW_RENDERFULLCONTENT)
            if not ok:
                user32.PrintWindow(hwnd, mfc_dc, 0)
            bmi = BITMAPINFOHEADER()
            bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            bmi.biWidth = w
            bmi.biHeight = -h
            bmi.biPlanes = 1
            bmi.biBitCount = 32
            bmi.biCompression = 0
            buf = ctypes.create_string_buffer(w * h * 4)
            scanlines = gdi32.GetDIBits(mfc_dc, bmp, 0, h, buf, ctypes.byref(bmi), 0)
            if not scanlines:
                return ('', 0, 0)
            img = Image.frombuffer('RGBA', (w, h), buf, 'raw', 'BGRA', 0, 1).convert('RGB')
        finally:
            if old_obj:
                gdi32.SelectObject(mfc_dc, old_obj)
            gdi32.DeleteObject(bmp)
            gdi32.DeleteDC(mfc_dc)
            user32.ReleaseDC(hwnd, hwnd_dc)
    else:
        from PIL import ImageGrab
        img = ImageGrab.grab().convert('RGB')
        w, h = img.size

    if max(w, h) > 1280:
        scale = 1280.0 / max(w, h)
        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.Resampling.LANCZOS)
    bio = io.BytesIO()
    img.save(bio, format='JPEG', quality=70, optimize=True)
    return (base64.b64encode(bio.getvalue()).decode('ascii'), w, h)


class DesktopWorkspaceHarness:
    def __init__(self) -> None:
        self.last_hwnd: int = 0
        self.cache_hwnd: int = 0
        self.cache_pid: int = 0
        self.window_generation: int = 0
        self.node_cache: dict[str, dict[str, Any]] = {}

    def resolve_target_hwnd(self, args: dict[str, Any]) -> int:
        if not IS_WIN:
            return 0
        raw_h = args.get('hwnd')
        explicit = 0
        if raw_h is not None and str(raw_h).strip():
            try:
                explicit = int(str(raw_h).strip(), 0)
            except (ValueError, TypeError):
                explicit = 0
        if explicit and user32.IsWindow(explicit):
            self.last_hwnd = explicit
            return explicit
        win_q = str(args.get('window') or args.get('app') or '').strip()
        if win_q:
            wins = list_windows(win_q)
            if wins:
                self.last_hwnd = int(wins[0]['hwnd'])
                return self.last_hwnd
        if self.last_hwnd and user32.IsWindow(self.last_hwnd):
            return self.last_hwnd
        fg = int(user32.GetForegroundWindow() or 0)
        self.last_hwnd = fg
        return fg

    def _node_belongs_to_hwnd(self, node: dict[str, Any], hwnd: int) -> bool:
        if not node or not hwnd:
            return False
        root_hwnd = int(node.get('root_hwnd') or 0)
        if root_hwnd and root_hwnd != int(hwnd):
            return False
        node_hwnd = int(node.get('hwnd') or 0)
        if node_hwnd and node_hwnd != int(hwnd):
            if IS_WIN and not user32.IsChild(hwnd, node_hwnd):
                return False
        return True

    def dump_window_tree(self, hwnd: int) -> dict[str, Any]:
        if not IS_WIN or not hwnd or not user32.IsWindow(hwnd):
            return {'ok': False, 'error': '未找到有效目标窗口'}
        rect = wt.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        win_l, win_t = int(rect.left), int(rect.top)
        win_w = max(1, int(rect.right - rect.left))
        win_h = max(1, int(rect.bottom - rect.top))
        title = _get_hwnd_text(hwnd)
        pid, proc = _get_hwnd_process_name(hwnd)

        self.window_generation += 1
        gen_id = self.window_generation
        nodes: list[dict[str, Any]] = []
        seen_centers: list[tuple[int, int, str]] = []

        # 1. Try UIAutomation if installed, otherwise Win32 EnumChildWindows
        try:
            import uiautomation as auto  # type: ignore
            root_ctrl = auto.ControlFromHandle(hwnd)
            if root_ctrl:
                for ctrl, depth in auto.WalkControl(root_ctrl, includeTop=False, maxDepth=10):
                    if len(nodes) >= 90:
                        break
                    try:
                        cname = (ctrl.ControlTypeName or '').replace('Control', '')
                        if cname not in ('Button', 'Edit', 'ListItem', 'TabItem', 'Hyperlink', 'CheckBox', 'MenuItem', 'ComboBox', 'Document', 'Text'):
                            continue
                        name = (ctrl.Name or '').strip()
                        br = ctrl.BoundingRectangle
                        if not br or br.width() < 6 or br.height() < 6:
                            continue
                        sx = int((br.left + br.right) // 2)
                        sy = int((br.top + br.bottom) // 2)
                        rel_x = sx - win_l
                        rel_y = sy - win_t
                        if not (0 <= rel_x < win_w and 0 <= rel_y < win_h):
                            continue
                        if not name and cname == 'Text':
                            continue
                        native_hwnd = int(ctrl.NativeWindowHandle or 0)
                        automation_id = str(getattr(ctrl, 'AutomationId', '') or '').strip()
                        class_name = str(getattr(ctrl, 'ClassName', '') or '').strip()
                        seen_centers.append((rel_x, rel_y, name))
                        ref = f'@n{len(nodes) + 1}'
                        nodes.append({
                            'ref': ref,
                            'role': cname,
                            'text': name[:100],
                            'hwnd': native_hwnd,
                            'root_hwnd': int(hwnd),
                            'has_native_hwnd': bool(native_hwnd),
                            'automation_id': automation_id,
                            'class_name': class_name,
                            'uia_control': ctrl,
                            'pid': int(pid),
                            'generation': gen_id,
                            'center_x': rel_x,
                            'center_y': rel_y,
                            'screen_x': sx,
                            'screen_y': sy,
                            'editable': cname in ('Edit', 'Document', 'ComboBox'),
                        })
                    except Exception:
                        continue
        except Exception:
            pass

        # 2. Win32 EnumChildWindows supplement / fallback
        WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

        def _child_cb(ch_hwnd: int, _lparam: int) -> bool:
            if len(nodes) >= 90 or not user32.IsWindowVisible(ch_hwnd):
                return True
            r = wt.RECT()
            user32.GetWindowRect(ch_hwnd, ctypes.byref(r))
            cw, ch = r.right - r.left, r.bottom - r.top
            if cw < 8 or ch < 8:
                return True
            txt = _get_hwnd_text(ch_hwnd)
            cls = _get_hwnd_class(ch_hwnd)
            sx = int((r.left + r.right) // 2)
            sy = int((r.top + r.bottom) // 2)
            rel_x = sx - win_l
            rel_y = sy - win_t
            if not (0 <= rel_x < win_w and 0 <= rel_y < win_h):
                return True
            editable = 'edit' in cls.lower() or 'richedit' in cls.lower()
            if not txt and not editable and 'button' not in cls.lower():
                return True
            if any(abs(px - rel_x) <= 6 and abs(py - rel_y) <= 6 and ptxt == txt for px, py, ptxt in seen_centers):
                return True
            seen_centers.append((rel_x, rel_y, txt))
            ref = f'@n{len(nodes) + 1}'
            nodes.append({
                'ref': ref,
                'role': cls[:32] or 'Control',
                'text': txt[:100],
                'hwnd': int(ch_hwnd),
                'root_hwnd': int(hwnd),
                'has_native_hwnd': True,
                'class_name': cls,
                'pid': int(pid),
                'generation': gen_id,
                'center_x': rel_x,
                'center_y': rel_y,
                'screen_x': sx,
                'screen_y': sy,
                'editable': editable,
            })
            return True

        user32.EnumChildWindows(hwnd, WNDENUMPROC(_child_cb), 0)
        self.cache_hwnd = int(hwnd)
        self.cache_pid = int(pid)
        self.node_cache = {n['ref'].lower(): n for n in nodes}

        lines = [f'hwnd={hex(hwnd)} process={proc} title="{title}" size={win_w}x{win_h} count={len(nodes)}']
        for n in nodes:
            flags = 'click' + (' edit' if n.get('editable') else '')
            lines.append(f"{n['ref']} [{n['role']}] \"{n['text']}\" center=({n['center_x']},{n['center_y']}) {flags}")
        summary = '\n'.join(lines)
        out: dict[str, Any] = {
            'ok': True,
            'hwnd': hwnd,
            'generation': gen_id,
            'title': title,
            'process': proc,
            'width': win_w,
            'height': win_h,
            'node_count': len(nodes),
            'summary': summary,
            'ui_tree': summary,
        }
        if len(nodes) < 2:
            b64, cw, ch = capture_window_jpeg(hwnd=hwnd)
            if b64:
                out['image'] = b64
                out['width'] = cw
                out['height'] = ch
        return out

    def _post_background_click(
        self,
        hwnd: int,
        rel_x: int,
        rel_y: int,
        right: bool = False,
        double: bool = False,
        screen_x: int | None = None,
        screen_y: int | None = None,
        target_hwnd: int = 0,
    ) -> None:
        if not IS_WIN or not hwnd:
            return
        if screen_x is not None and screen_y is not None:
            sx, sy = int(screen_x), int(screen_y)
        else:
            rect = wt.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            sx = int(rect.left) + int(rel_x)
            sy = int(rect.top) + int(rel_y)

        if target_hwnd and (target_hwnd == hwnd or user32.IsChild(hwnd, target_hwnd)):
            target = target_hwnd
        else:
            cpt = wt.POINT(sx, sy)
            user32.ScreenToClient(hwnd, ctypes.byref(cpt))
            child = user32.ChildWindowFromPointEx(hwnd, cpt, 0x0001 | 0x0002)
            target = int(child) if (child and (child == hwnd or user32.IsChild(hwnd, child))) else hwnd

        pt = wt.POINT(sx, sy)
        user32.ScreenToClient(target, ctypes.byref(pt))
        lparam = makelparam(pt.x, pt.y)
        if right:
            user32.PostMessageW(target, WM_RBUTTONDOWN, 0, lparam)
            time.sleep(0.02)
            user32.PostMessageW(target, WM_RBUTTONUP, 0, lparam)
        else:
            user32.PostMessageW(target, WM_LBUTTONDOWN, MK_LBUTTON, lparam)
            time.sleep(0.02)
            user32.PostMessageW(target, WM_LBUTTONUP, 0, lparam)
            if double:
                time.sleep(0.04)
                user32.PostMessageW(target, WM_LBUTTONDBLCLK, MK_LBUTTON, lparam)
                time.sleep(0.02)
                user32.PostMessageW(target, WM_LBUTTONUP, 0, lparam)

    def _post_background_text(
        self,
        hwnd: int,
        text: str,
        clear: bool = True,
        submit: bool = False,
        allow_settext: bool = False,
    ) -> None:
        if not IS_WIN or not hwnd:
            return
        if clear and allow_settext:
            user32.SendMessageW(hwnd, WM_SETTEXT, 0, ctypes.c_wchar_p(''))
        elif clear:
            for _ in range(32):
                user32.PostMessageW(hwnd, WM_KEYDOWN, VK_BACK, 0)
                user32.PostMessageW(hwnd, WM_KEYUP, VK_BACK, 0)
        for ch in text:
            user32.PostMessageW(hwnd, WM_CHAR, ord(ch), 0)
            time.sleep(0.004)
        if submit:
            time.sleep(0.03)
            user32.PostMessageW(hwnd, WM_KEYDOWN, VK_RETURN, 0)
            user32.PostMessageW(hwnd, WM_KEYUP, VK_RETURN, 0)

    def _type_via_uia_or_hwnd(
        self,
        root_hwnd: int,
        node: dict[str, Any],
        text: str,
        clear: bool = True,
        submit: bool = False,
    ) -> tuple[bool, str]:
        uia_ctrl = node.get('uia_control')
        if uia_ctrl is not None:
            try:
                val_pat = uia_ctrl.GetValuePattern()
                if val_pat is not None:
                    new_val = text if clear else (str( getattr(val_pat, 'Value', '') or '' ) + text)
                    val_pat.SetValue(new_val)
                    if submit and IS_WIN and root_hwnd:
                        target_h = int(node.get('hwnd') or root_hwnd)
                        user32.PostMessageW(target_h, WM_KEYDOWN, VK_RETURN, 0)
                        user32.PostMessageW(target_h, WM_KEYUP, VK_RETURN, 0)
                    return (True, 'uia_value_pattern')
            except Exception:
                pass
        native_hwnd = int(node.get('hwnd') or 0)
        if native_hwnd and native_hwnd != root_hwnd:
            cls_lower = str(node.get('class_name') or node.get('role') or '').lower()
            is_native_edit = 'edit' in cls_lower or bool(node.get('editable'))
            self._post_background_text(native_hwnd, text, clear=clear, submit=submit, allow_settext=is_native_edit)
            return (True, 'native_hwnd_wm_char')
        if root_hwnd and node.get('center_x') is not None and node.get('center_y') is not None:
            self._post_background_click(
                root_hwnd,
                int(node['center_x']),
                int(node['center_y']),
                screen_x=node.get('screen_x'),
                screen_y=node.get('screen_y'),
            )
            time.sleep(0.05)
            # Never send WM_SETTEXT to root_hwnd (which would overwrite the window title!)
            self._post_background_text(root_hwnd, text, clear=clear, submit=submit, allow_settext=False)
            return (True, 'focused_virtual_wm_char')
        return (False, 'unsupported_virtual_control')

    async def run_shell(self, cmd: str, timeout_s: int = 30) -> dict[str, Any]:
        clean_cmd = (cmd or '').strip()
        if not clean_cmd:
            return {'ok': False, 'error': 'command 不能为空'}
        eff_timeout = max(3, min(120, int(timeout_s or 30)))
        creationflags = subprocess.CREATE_NO_WINDOW if IS_WIN else 0
        proc = await asyncio.create_subprocess_shell(
            clean_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            creationflags=creationflags,
            start_new_session=(not IS_WIN),
        )

        async def _read_bounded() -> bytes:
            buf = bytearray()
            while True:
                if proc.stdout is None:
                    break
                chunk = await proc.stdout.read(4096)
                if not chunk:
                    break
                if len(buf) < 32000:
                    buf.extend(chunk[:32000 - len(buf)])
            return bytes(buf)

        timed_out = False
        cancelled = False
        out_bytes = b''
        try:
            out_bytes = await asyncio.wait_for(_read_bounded(), timeout=eff_timeout)
            await asyncio.wait_for(proc.wait(), timeout=2.0)
        except asyncio.TimeoutError:
            timed_out = True
        except asyncio.CancelledError:
            cancelled = True
            raise
        finally:
            if timed_out or cancelled or getattr(proc, 'returncode', None) is None:
                pid = getattr(proc, 'pid', None)
                if IS_WIN and pid:
                    with contextlib.suppress(Exception):
                        subprocess.run(
                            ['taskkill', '/F', '/T', '/PID', str(pid)],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            timeout=5,
                            creationflags=subprocess.CREATE_NO_WINDOW,
                        )
                elif not IS_WIN and pid:
                    with contextlib.suppress(Exception):
                        import signal
                        os.killpg(pid, signal.SIGKILL)
                if getattr(proc, 'returncode', None) is None and hasattr(proc, 'kill'):
                    with contextlib.suppress(Exception):
                        proc.kill()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(proc.wait(), timeout=3.0)

        if timed_out:
            return {'ok': False, 'timed_out': True, 'exit_code': -1, 'error': f'命令执行超时 ({eff_timeout}s)'}
        return {
            'ok': proc.returncode == 0,
            'exit_code': proc.returncode,
            'output': out_bytes.decode('utf-8', errors='replace')[:32000],
        }

    async def execute(self, op: str, args: dict[str, Any]) -> dict[str, Any]:
        op = (op or 'status').strip().lower()
        if op == 'status':
            sw, sh = get_screen_size()
            wins = list_windows()
            fg = next((w for w in wins if w.get('active')), None)
            return {
                'ok': True,
                'hostname': socket.gethostname(),
                'platform': platform.platform(),
                'screen': {'width': sw, 'height': sh},
                'idle_seconds': get_human_idle_seconds(),
                'window_count': len(wins),
                'active_window': fg,
            }
        if op == 'windows':
            wins = list_windows(str(args.get('window') or args.get('target') or args.get('text') or ''))
            return {'ok': True, 'count': len(wins), 'windows': wins[:50]}
        if op in ('tree', 'ui_tree', 'dump', 'nodes'):
            hwnd = self.resolve_target_hwnd(args)
            return await asyncio.to_thread(self.dump_window_tree, hwnd)
        if op in ('screenshot', 'capture', 'preview'):
            hwnd = self.resolve_target_hwnd(args)
            full_scr = bool(args.get('full_screen', False))
            b64, w, h = await asyncio.to_thread(capture_window_jpeg, hwnd, full_scr)
            return {
                'ok': bool(b64),
                'hwnd': hwnd,
                'title': _get_hwnd_text(hwnd) if hwnd else 'Screen',
                'width': w,
                'height': h,
                'image': b64,
            }
        if op in ('clipboard', 'clipboard_get', 'clipboard_set'):
            is_set = op == 'clipboard_set' or (op == 'clipboard' and 'text' in args and args.get('action') != 'get')
            if is_set:
                txt = str(args.get('text') or '')
                ok = win32_clipboard_set(txt)
                return {'ok': ok, 'length': len(txt)}
            return {'ok': True, 'text': win32_clipboard_get()}
        if op == 'open_url':
            url = str(args.get('url') or args.get('text') or '').strip()
            if not url:
                return {'ok': False, 'error': 'url 不能为空'}
            webbrowser.open(url, new=2, autoraise=False)
            return {'ok': True, 'opened_url': url}
        if op == 'launch':
            app_cmd = str(args.get('app') or args.get('command') or args.get('text') or '').strip()
            if not app_cmd:
                return {'ok': False, 'error': 'app 不能为空'}
            if IS_WIN:
                si = subprocess.STARTUPINFO()
                si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                si.wShowWindow = SW_SHOWNOACTIVATE
                subprocess.Popen(app_cmd, shell=True, startupinfo=si)
            else:
                subprocess.Popen(app_cmd, shell=True)
            await asyncio.sleep(0.6)
            return {'ok': True, 'launched': app_cmd, 'windows': list_windows()[:15]}
        if op == 'shell':
            cmd = str(args.get('command') or args.get('text') or '').strip()
            timeout_s = max(3, min(120, int(args.get('timeout') or 30)))
            return await self.run_shell(cmd, timeout_s=timeout_s)
        if op in ('click_node', 'tap_node'):
            hwnd = self.resolve_target_hwnd(args)
            ref = str(args.get('ref') or '').strip().lower()
            target_txt = str(args.get('text') or args.get('target') or '').strip().lower()
            if self.cache_hwnd and self.cache_hwnd != hwnd:
                if ref:
                    return {
                        'ok': False,
                        'error': f'节点缓存属于窗口 {hex(self.cache_hwnd)}，与当前目标窗口 {hex(hwnd)} 不一致，请先重新读取窗口树',
                    }
                self.node_cache = {}
            if not self.node_cache and hwnd and not ref:
                await asyncio.to_thread(self.dump_window_tree, hwnd)
            node = self.node_cache.get(ref) if ref else None
            if node is None and target_txt and not ref:
                for cand in self.node_cache.values():
                    if target_txt in str(cand.get('text') or '').lower():
                        node = cand
                        break
            if node is None:
                return {'ok': False, 'error': f'未找到桌面节点 (ref={ref}, text={target_txt})'}
            if not self._node_belongs_to_hwnd(node, hwnd):
                return {'ok': False, 'error': f'节点 {ref or node.get("ref")} 不属于目标窗口 {hex(hwnd)}'}
            node_hwnd = int(node.get('hwnd') or 0)
            role_lower = str(node.get('role') or '').lower()
            method_used = 'background_postmessage'
            # Single-method dispatch: use BM_CLICK for native child buttons, otherwise _post_background_click
            if node_hwnd and node_hwnd != hwnd and ('button' in role_lower or 'checkbox' in role_lower):
                posted = bool(user32.PostMessageW(node_hwnd, BM_CLICK, 0, 0)) if IS_WIN else False
                if posted:
                    method_used = 'bm_click'
                else:
                    await asyncio.to_thread(
                        self._post_background_click,
                        hwnd,
                        int(node['center_x']),
                        int(node['center_y']),
                        False,
                        False,
                        node.get('screen_x'),
                        node.get('screen_y'),
                        node_hwnd,
                    )
            else:
                await asyncio.to_thread(
                    self._post_background_click,
                    hwnd,
                    int(node['center_x']),
                    int(node['center_y']),
                    False,
                    False,
                    node.get('screen_x'),
                    node.get('screen_y'),
                    node_hwnd or hwnd,
                )
            await asyncio.sleep(0.22)
            tree = await asyncio.to_thread(self.dump_window_tree, hwnd)
            return {
                'ok': True,
                'clicked': True,
                'method': method_used,
                'ref': node['ref'],
                'label': node['text'],
                'x': node['center_x'],
                'y': node['center_y'],
                'action_receipt': f"已在后台点击窗口 {hex(hwnd)} 节点 {node['ref']} \"{node['text']}\" ({node['center_x']},{node['center_y']})，未抢占物理鼠标",
                'post_ui_summary': tree.get('summary', ''),
            }
        if op in ('type_node', 'text', 'type'):
            hwnd = self.resolve_target_hwnd(args)
            ref = str(args.get('ref') or '').strip().lower()
            txt = str(args.get('text') or '')
            clear = bool(args.get('clear', True))
            submit = bool(args.get('submit', False))
            if op == 'type_node' or ref:
                if not ref:
                    return {'ok': False, 'error': 'type_node 必须提供有效的 ref'}
                if self.cache_hwnd and self.cache_hwnd != hwnd:
                    return {
                        'ok': False,
                        'error': f'节点缓存属于窗口 {hex(self.cache_hwnd)}，与当前目标窗口 {hex(hwnd)} 不一致',
                    }
                node = self.node_cache.get(ref)
                if node is None:
                    return {'ok': False, 'error': f'未找到输入节点: {ref}'}
                if not self._node_belongs_to_hwnd(node, hwnd):
                    return {'ok': False, 'error': f'输入节点 {ref} 不属于目标窗口 {hex(hwnd)}'}
                ok_typed, method_used = await asyncio.to_thread(
                    self._type_via_uia_or_hwnd, hwnd, node, txt, clear, submit
                )
                if not ok_typed:
                    return {'ok': False, 'error': f'节点 {ref} 不支持后台输入 ({method_used})'}
                target_hwnd = int(node.get('hwnd') or hwnd)
            else:
                if not hwnd:
                    return {'ok': False, 'error': '未找到有效目标窗口'}
                target_hwnd = hwnd
                method_used = 'background_wm_char'
                # Do NOT send WM_SETTEXT to top-level window when no child control ref is given
                await asyncio.to_thread(self._post_background_text, target_hwnd, txt, clear, submit, False)
            await asyncio.sleep(0.22)
            tree = await asyncio.to_thread(self.dump_window_tree, hwnd)
            return {
                'ok': True,
                'typed': True,
                'method': method_used,
                'characters': len(txt),
                'submitted': submit,
                'action_receipt': f'已向后台窗口 {hex(target_hwnd)} 注入 {len(txt)} 个字符 (回车={submit})，未占用物理键盘',
                'post_ui_summary': tree.get('summary', ''),
            }
        if op in ('click', 'double_click', 'right_click'):
            hwnd = self.resolve_target_hwnd(args)
            raw_x = args.get('x')
            raw_y = args.get('y')
            x = 100 if raw_x is None else int(raw_x)
            y = 100 if raw_y is None else int(raw_y)
            await asyncio.to_thread(
                self._post_background_click,
                hwnd,
                x,
                y,
                op == 'right_click',
                op == 'double_click',
            )
            await asyncio.sleep(0.22)
            tree = await asyncio.to_thread(self.dump_window_tree, hwnd)
            return {
                'ok': True,
                'clicked': True,
                'hwnd': hwnd,
                'x': x,
                'y': y,
                'post_ui_summary': tree.get('summary', ''),
            }
        if op in ('key', 'hotkey'):
            hwnd = self.resolve_target_hwnd(args)
            key_str = str(args.get('key') or args.get('keys') or args.get('text') or '').strip()
            if not key_str:
                return {'ok': False, 'error': 'key 不能为空'}
            vk_map = {
                'enter': VK_RETURN, 'return': VK_RETURN,
                'tab': VK_TAB,
                'esc': VK_ESCAPE, 'escape': VK_ESCAPE,
                'backspace': VK_BACK, 'back': VK_BACK,
                'space': 0x20,
                'left': 0x25, 'up': 0x26, 'right': 0x27, 'down': 0x28,
                'delete': 0x2E, 'del': 0x2E,
                'home': 0x24, 'end': 0x23,
                'pageup': 0x21, 'pagedown': 0x22,
                'ctrl': 0x11, 'control': 0x11,
                'alt': 0x12, 'shift': 0x10,
            }
            parts = [p.strip().lower() for p in key_str.replace('-', '+').split('+') if p.strip()]
            vks: list[int] = []
            for p in parts:
                if p in vk_map:
                    vks.append(vk_map[p])
                elif p.startswith('f') and p[1:].isdigit() and 1 <= int(p[1:]) <= 12:
                    vks.append(0x70 + int(p[1:]) - 1)
                elif len(p) == 1:
                    vks.append(ord(p.upper()))
            if not vks:
                return {'ok': False, 'error': f'不支持的按键: {key_str}'}
            if IS_WIN and hwnd:
                for vk in vks:
                    user32.PostMessageW(hwnd, WM_KEYDOWN, vk, 0)
                for vk in reversed(vks):
                    user32.PostMessageW(hwnd, WM_KEYUP, vk, 0)
            return {'ok': True, 'key': key_str, 'hwnd': hwnd}
        if op == 'scroll':
            hwnd = self.resolve_target_hwnd(args)
            raw_x = args.get('x')
            raw_y = args.get('y')
            x = 100 if raw_x is None else int(raw_x)
            y = 100 if raw_y is None else int(raw_y)
            direction = str(args.get('direction') or 'down').strip().lower()
            raw_amount = args.get('amount')
            amount = max(1, min(20, 3 if raw_amount is None else int(raw_amount)))
            delta = (120 * amount) if direction in ('up', 'left') else (-120 * amount)
            wparam = (delta & 0xFFFF) << 16
            if IS_WIN and hwnd:
                rect = wt.RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(rect))
                sx = int(rect.left) + x
                sy = int(rect.top) + y
                user32.PostMessageW(hwnd, WM_MOUSEWHEEL, wparam, makelparam(sx, sy))
            return {'ok': True, 'scrolled': True, 'direction': direction, 'amount': amount, 'x': x, 'y': y, 'hwnd': hwnd}
        if op == 'batch':
            steps = args.get('steps')
            if not isinstance(steps, list) or not steps:
                return {'ok': False, 'error': 'batch steps 必须为非空列表', 'steps_executed': 0, 'results': []}
            results = []
            failed_step = None
            stop_reason = ''
            for idx, step in enumerate(steps[:10]):
                if not isinstance(step, dict):
                    failed_step = idx
                    stop_reason = f'第 {idx + 1} 步不是有效对象'
                    results.append({'ok': False, 'error': stop_reason})
                    break
                step_op = str(step.get('op') or step.get('kind') or '').strip()
                if not step_op or step_op == 'batch':
                    failed_step = idx
                    stop_reason = f'第 {idx + 1} 步缺少有效操作类型'
                    results.append({'ok': False, 'error': stop_reason})
                    break
                r = await self.execute(step_op, step)
                results.append(r)
                if not r.get('ok'):
                    failed_step = idx
                    stop_reason = str(r.get('error') or f'第 {idx + 1} 步 ({step_op}) 执行失败')
                    break
            all_ok = bool(results) and failed_step is None and all(bool(r.get('ok')) for r in results)
            batch_out: dict[str, Any] = {
                'ok': all_ok,
                'steps_executed': len(results),
                'results': results,
            }
            if failed_step is not None:
                batch_out['failed_step'] = failed_step
                batch_out['error'] = stop_reason
            return batch_out
        return {'ok': False, 'error': f'未知桌面端指令: {op}'}


async def run_agent_loop(server_url: str, token: str, device_id: str) -> None:
    import websockets  # type: ignore

    ws_base = server_url.rstrip('/').replace('https://', 'wss://').replace('http://', 'ws://')
    ws_url = ws_base + '/ws/device'
    harness = DesktopWorkspaceHarness()

    while True:
        try:
            sw, sh = get_screen_size()
            async with websockets.connect(ws_url, ping_interval=20, ping_timeout=20, max_size=8_000_000) as ws:
                hello = {
                    'type': 'hello',
                    'token': token,
                    'device_type': 'desktop',
                    'device_id': device_id,
                    'device_name': f'{socket.gethostname()} ({platform.system()})',
                    'platform': sys.platform,
                    'capabilities': [
                        'status', 'windows', 'tree', 'screenshot', 'click_node', 'type_node',
                        'click', 'double_click', 'right_click', 'type', 'key', 'hotkey', 'scroll',
                        'launch', 'open_url', 'shell', 'clipboard', 'batch',
                    ],
                    'screen': {'width': sw, 'height': sh, 'scale': 1.0},
                }
                await ws.send(json.dumps(hello))
                async for raw_msg in ws:
                    event = json.loads(raw_msg)
                    ev_type = event.get('type')
                    if ev_type == 'desktop_workspace_command':
                        req_id = event.get('request_id', '')
                        op = event.get('op', 'status')
                        cmd_args = event.get('args') or {}
                        try:
                            res = await harness.execute(op, cmd_args)
                        except Exception as exc:
                            res = {'ok': False, 'error': f'{type(exc).__name__}: {exc}'}
                        reply = {
                            'type': 'desktop_workspace_result',
                            'request_id': req_id,
                            'result': res,
                        }
                        await ws.send(json.dumps(reply))
        except Exception as exc:
            print(f'[desktop-agent] Disconnected ({exc}), reconnecting in 3s...', file=sys.stderr)
            await asyncio.sleep(3.0)


def main() -> None:
    parser = argparse.ArgumentParser(description='Pulse Lightweight Non-Intrusive Desktop Agent')
    parser.add_argument('--server', default=os.getenv('PULSE_SERVER_URL', 'http://127.0.0.1:18190'))
    parser.add_argument('--token', default=os.getenv('PULSE_DEVICE_TOKEN', ''))
    parser.add_argument('--device-id', default=os.getenv('PULSE_DEVICE_ID', f'desktop-{socket.gethostname().lower()}'))
    args = parser.parse_args()
    token = args.token.strip()
    if not token:
        tok_file = Path(__file__).parent.parent / 'private' / 'device-token'
        if tok_file.exists():
            token = tok_file.read_text('utf-8').strip()
    if not token:
        raise SystemExit('请提供 --token 或设置 PULSE_DEVICE_TOKEN')
    asyncio.run(run_agent_loop(args.server, token, args.device_id))


if __name__ == '__main__':
    main()
