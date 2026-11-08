"""
SOTA Hybrid CDP Browser & Live Semantic DOM + Bounding-Box Engine (`server/cloud_os/agent_browser.py`).

Inspired by Browser-Use, Stagehand, OSWorld, and OmniParser V2:
1. **Persistent Hot-Reusable Chromium with CDP (`127.0.0.1:9222`)**:
   - Keeps a single tuned Chromium window alive on `:91` (`--remote-debugging-port=9222`),
     navigating in-place via CDP `Page.navigate` instead of killing and cold-restarting on every URL.
   - Falls back cleanly to `luakit` (with WebKit2 sandbox/compositing workarounds) if Chromium is unavailable.
2. **Live Rendered DOM + Screen Bounding-Box Extraction (`@b1..@b35`)**:
   - Evaluates live JS inside the rendered page via CDP `Runtime.evaluate` to extract:
     - `title`, `url`, rendered `page_text` (`document.body.innerText`)
     - Interactive elements `@b1..@b35` with **both** semantic labels (`label`, `tag`, `href`)
       AND exact X11 screen coordinates (`x`, `y`, `w`, `h`) so `@bN` works on dynamic SPAs
       (Tencent Docs, React/Vue apps, canvas toolbars, dialogs) via either CDP DOM action or X11 pointer click!
3. **WARP Anti-Bot Egress (`127.0.0.1:40001`) + Search Healing**:
   - Automatically routes through Cloudflare WARP when available and heals datacenter-blocked search pages.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import html as html_mod
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import time
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from cloud_os.window_manager import list_x11_windows

CDP_PORT = 9222


def _warp_proxy_url() -> Optional[str]:
    try:
        with socket.create_connection(('127.0.0.1', 40001), timeout=0.15):
            return 'http://127.0.0.1:40001'
    except OSError:
        return None


def _cdp_get_targets(timeout: float = 0.6) -> List[Dict[str, object]]:
    """Return active CDP page targets if Chromium is listening on `127.0.0.1:9222`."""
    try:
        req = urllib.request.Request(f'http://127.0.0.1:{CDP_PORT}/json/list')
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode('utf-8', errors='replace'))
            if isinstance(data, list):
                return [t for t in data if isinstance(t, dict) and t.get('type') == 'page' and t.get('webSocketDebuggerUrl')]
    except Exception:
        pass
    return []


def _ws_cdp_call(ws_url: str, commands: List[ Tuple[str, Dict[str, object]] ], timeout: float = 4.5) -> List[Dict[str, object]]:
    """
    Minimal zero-dependency RFC 6455 WebSocket client for local Chromium CDP RPC calls.
    Executes a list of `(method, params)` sequentially and returns their result dicts.
    """
    parsed = urllib.parse.urlsplit(ws_url)
    host = parsed.hostname or '127.0.0.1'
    port = parsed.port or CDP_PORT
    path = parsed.path or '/'
    if parsed.query:
        path += '?' + parsed.query

    sock = socket.create_connection((host, port), timeout=timeout)
    try:
        sock.settimeout(timeout)
        key = base64.b64encode(os.urandom(16)).decode('ascii')
        handshake = (
            f'GET {path} HTTP/1.1\r\n'
            f'Host: {host}:{port}\r\n'
            f'Upgrade: websocket\r\n'
            f'Connection: Upgrade\r\n'
            f'Sec-WebSocket-Key: {key}\r\n'
            f'Sec-WebSocket-Version: 13\r\n\r\n'
        )
        sock.sendall(handshake.encode('ascii'))
        resp_buf = b''
        while b'\r\n\r\n' not in resp_buf:
            chunk = sock.recv(4096)
            if not chunk:
                raise RuntimeError('CDP WebSocket handshake closed early')
            resp_buf += chunk
        header_end = resp_buf.index(b'\r\n\r\n') + 4
        leftover = bytearray(resp_buf[header_end:])

        def _send_frame(payload_str: str) -> None:
            raw = payload_str.encode('utf-8')
            mask_key = os.urandom(4)
            header = bytearray([0x81])  # FIN + text frame
            length = len(raw)
            if length < 126:
                header.append(0x80 | length)
            elif length < 65536:
                header.append(0x80 | 126)
                header.extend(struct.pack('!H', length))
            else:
                header.append(0x80 | 127)
                header.extend(struct.pack('!Q', length))
            header.extend(mask_key)
            masked = bytes(b ^ mask_key[i % 4] for i, b in enumerate(raw))
            sock.sendall(bytes(header) + masked)

        def _recv_exact(n: int) -> bytes:
            while len(leftover) < n:
                chunk = sock.recv(65536)
                if not chunk:
                    raise RuntimeError('CDP WebSocket EOF')
                leftover.extend(chunk)
            out = bytes(leftover[:n])
            del leftover[:n]
            return out

        def _recv_frame() -> str:
            while True:
                b0, b1 = _recv_exact(2)
                opcode = b0 & 0x0F
                masked = bool(b1 & 0x80)
                length = b1 & 0x7F
                if length == 126:
                    length = struct.unpack('!H', _recv_exact(2))[0]
                elif length == 127:
                    length = struct.unpack('!Q', _recv_exact(8))[0]
                mask_k = _recv_exact(4) if masked else b''
                payload = _recv_exact(length)
                if masked:
                    payload = bytes(b ^ mask_k[i % 4] for i, b in enumerate(payload))
                if opcode == 0x8:  # close
                    raise RuntimeError('CDP WebSocket closed')
                if opcode == 0x9:  # ping -> pong
                    continue
                if opcode in (0x1, 0x2):
                    return payload.decode('utf-8', errors='replace')

        results: List[Dict[str, object]] = []
        for idx, (method, params) in enumerate(commands, 1):
            req_obj = {'id': idx, 'method': method, 'params': params or {}}
            _send_frame(json.dumps(req_obj))
            deadline = time.time() + timeout
            while time.time() < deadline:
                msg_raw = _recv_frame()
                msg = json.loads(msg_raw)
                if isinstance(msg, dict) and msg.get('id') == idx:
                    results.append(msg.get('result') or {})
                    break
        return results
    finally:
        try:
            sock.close()
        except Exception:
            pass


LIVE_DOM_EXTRACT_JS = r"""
(() => {
  const title = (document.title || '').trim();
  const url = location.href || '';
  const chromeTop = Math.max(0, (window.outerHeight - window.innerHeight) || 0);
  const chromeLeft = Math.max(0, Math.round(((window.outerWidth - window.innerWidth) || 0) / 2));
  const winX = (typeof window.screenX === 'number' ? window.screenX : 14) + chromeLeft;
  const winY = (typeof window.screenY === 'number' ? window.screenY : 10) + chromeTop;

  const selectors = 'a[href], button, input:not([type="hidden"]), textarea, select, [role="button"], [role="link"], [role="tab"], [role="menuitem"], [contenteditable="true"]';
  const entries = [];
  for (const el of Array.from(document.querySelectorAll(selectors))) {
    if (el.tagName && el.tagName.toLowerCase() === 'a' && el.getAttribute('target') === '_blank') {
      el.setAttribute('target', '_self');
    }
    entries.push({ el, ox: 0, oy: 0 });
  }
  for (const ifr of Array.from(document.querySelectorAll('iframe'))) {
    try {
      const idoc = ifr.contentDocument;
      const r = ifr.getBoundingClientRect();
      if (idoc && r && r.width > 20 && r.height > 20) {
        for (const el of Array.from(idoc.querySelectorAll(selectors))) {
          if (el.tagName && el.tagName.toLowerCase() === 'a' && el.getAttribute('target') === '_blank') {
            el.setAttribute('target', '_self');
          }
          entries.push({ el, ox: r.left, oy: r.top });
        }
      }
    } catch (e) {}
  }
  const elements = [];
  const seen = new Set();

  for (const item of entries) {
    if (elements.length >= 32) break;
    const el = item.el;
    const rect = el.getBoundingClientRect();
    if (!rect || rect.width < 8 || rect.height < 8) continue;
    const absLeft = rect.left + item.ox;
    const absTop = rect.top + item.oy;
    if (absTop + rect.height < 0 || absLeft + rect.width < 0 || absTop > window.innerHeight || absLeft > window.innerWidth) continue;
    const style = (el.ownerDocument.defaultView || window).getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden' || parseFloat(style.opacity || '1') < 0.05) continue;

    let label = (
      el.getAttribute('aria-label') ||
      el.getAttribute('placeholder') ||
      el.getAttribute('title') ||
      el.innerText ||
      el.value ||
      el.getAttribute('name') ||
      ''
    ).replace(/\s+/g, ' ').trim().slice(0, 85);

    const tag = (el.tagName || '').toLowerCase();
    const href = (el.href && typeof el.href === 'string') ? el.href : '';
    if (!label && !href && tag !== 'input' && tag !== 'textarea') continue;
    if (!label) label = href ? href.slice(0, 60) : tag;

    const cx = Math.round(winX + absLeft + rect.width / 2);
    const cy = Math.round(winY + absTop + rect.height / 2);
    const key = `${tag}:${label.slice(0,24)}:${Math.round(cx/12)}:${Math.round(cy/12)}`;
    if (seen.has(key)) continue;
    seen.add(key);

    const idx = elements.length + 1;
    const ref = `@b${idx}`;
    el.setAttribute('data-agent-ref', ref);
    const nx = Math.max(0, Math.min(1000, Math.round((cx * 1000) / 1280)));
    const ny = Math.max(0, Math.min(1000, Math.round((cy * 1000) / 800)));
    elements.push({
      ref,
      tag,
      type: el.getAttribute('type') || tag,
      label,
      href,
      x: cx,
      y: cy,
      nx,
      ny,
      w: Math.round(rect.width),
      h: Math.round(rect.height),
    });
  }

  let bodyText = '';
  try {
    bodyText = (document.body ? document.body.innerText : '') || '';
  } catch (e) {}
  bodyText = bodyText.replace(/[ \t]+/g, ' ').replace(/\n{3,}/g, '\n\n').trim().slice(0, 3600);

  return JSON.stringify({ title, url, page_text: bodyText, elements });
})()
"""


def extract_live_cdp_dom_snapshot(wait_ms: int = 350) -> Optional[Dict[str, object]]:
    """If Chromium CDP is active, extract the live rendered DOM and `@b1..@bN` bounding boxes."""
    targets = _cdp_get_targets(timeout=0.5)
    if not targets:
        return None
    ws_url = str(targets[0].get('webSocketDebuggerUrl') or '')
    if not ws_url:
        return None
    if wait_ms > 0:
        time.sleep(wait_ms / 1000.0)
    try:
        res_list = _ws_cdp_call(
            ws_url,
            [('Runtime.evaluate', {'expression': LIVE_DOM_EXTRACT_JS, 'returnByValue': True})],
            timeout=3.5,
        )
        if not res_list:
            return None
        val = ((res_list[0].get('result') or {}).get('value'))
        if isinstance(val, str) and val:
            parsed = json.loads(val)
            if isinstance(parsed, dict):
                return parsed
    except Exception:
        pass
    return None


def cdp_click_or_type_ref(ref_id: str, text: Optional[str] = None) -> Optional[Dict[str, object]]:
    """
    Attempt to click or type into `[data-agent-ref="@bN"]` directly inside the live DOM via CDP,
    returning its screen coordinates and tag metadata so the caller can also trigger native events if needed.
    """
    targets = _cdp_get_targets(timeout=0.5)
    if not targets:
        return None
    ws_url = str(targets[0].get('webSocketDebuggerUrl') or '')
    if not ws_url:
        return None
    js = f"""
    (() => {{
      const ref = {json.dumps(ref_id)};
      let el = document.querySelector(`[data-agent-ref=${{JSON.stringify(ref)}}]`);
      if (!el) return null;
      el.scrollIntoView({{block: 'center', inline: 'center'}});
      const rect = el.getBoundingClientRect();
      const chromeTop = Math.max(0, (window.outerHeight - window.innerHeight) || 0);
      const chromeLeft = Math.max(0, Math.round(((window.outerWidth - window.innerWidth) || 0) / 2));
      const cx = Math.round((window.screenX || 14) + chromeLeft + rect.left + rect.width / 2);
      const cy = Math.round((window.screenY || 10) + chromeTop + rect.top + rect.height / 2);
      el.focus();
      const textVal = {json.dumps(text)};
      if (textVal !== null && textVal !== undefined) {{
        if ('value' in el) {{
          const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set
            || Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value')?.set;
          if (setter) setter.call(el, textVal);
          else el.value = textVal;
          el.dispatchEvent(new Event('input', {{bubbles: true}}));
          el.dispatchEvent(new Event('change', {{bubbles: true}}));
        }} else if (el.isContentEditable) {{
          el.innerText = textVal;
          el.dispatchEvent(new Event('input', {{bubbles: true}}));
        }}
      }} else {{
        if (el.tagName && el.tagName.toLowerCase() === 'a' && el.getAttribute('target') === '_blank') {{
          el.setAttribute('target', '_self');
        }}
        el.click();
      }}
      return JSON.stringify({{ref, x: cx, y: cy, tag: (el.tagName || '').toLowerCase(), href: el.href || ''}});
    }})()
    """
    try:
        res_list = _ws_cdp_call(ws_url, [('Runtime.evaluate', {'expression': js, 'returnByValue': True})], timeout=3.0)
        if res_list:
            val = ((res_list[0].get('result') or {}).get('value'))
            if isinstance(val, str) and val:
                return json.loads(val)
    except Exception:
        pass
    return None


def reap_idle_browsers(env: Optional[Dict[str, str]] = None) -> int:
    """Terminate any running browser windows/processes when explicitly requested."""
    if os.name == 'nt':
        return 0
    reaped = 0
    run_env = dict(os.environ)
    if env:
        run_env.update(env)
    for proc_name in ('luakit', 'chromium', 'chromium-browser', 'netsurf-gtk', 'dillo'):
        try:
            r = subprocess.run(
                ['pkill', '-f', proc_name],
                env=run_env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2,
            )
            if r.returncode == 0:
                reaped += 1
        except Exception:
            pass
    return reaped


class _SemanticDOMParser(HTMLParser):
    """Lightweight static HTML fallback parser extracting clean readable text and `@b1..@bN` elements."""

    def __init__(self, base_url: str = '') -> None:
        super().__init__()
        self.base_url = base_url
        self._skip_depth = 0
        self._in_title = False
        self.title = ''
        self.text_chunks: List[str] = []
        self.elements: List[Dict[str, object]] = []
        self._active_elem: Optional[Dict[str, object]] = None

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        t = tag.lower()
        if t in ('script', 'style', 'noscript', 'svg', 'path', 'head'):
            if t != 'head':
                self._skip_depth += 1
            return
        if t == 'title':
            self._in_title = True
            return
        if self._skip_depth > 0:
            return

        attr_map = {k.lower(): (v or '') for k, v in attrs}
        if t in ('a', 'button', 'input', 'textarea', 'select'):
            if t == 'input' and attr_map.get('type', '').lower() in ('hidden',):
                return
            label = (
                attr_map.get('aria-label')
                or attr_map.get('placeholder')
                or attr_map.get('title')
                or attr_map.get('value')
                or attr_map.get('name')
                or ''
            ).strip()
            href = attr_map.get('href', '').strip()
            if href and self.base_url and not href.startswith(('http://', 'https://', 'javascript:', '#')):
                href = urllib.parse.urljoin(self.base_url, href)
            elem = {
                'tag': t,
                'type': attr_map.get('type', '') if t == 'input' else t,
                'label': label,
                'href': href,
                '_text': [],
            }
            self._active_elem = elem

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t in ('script', 'style', 'noscript', 'svg', 'path'):
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if t == 'title':
            self._in_title = False
            return
        if self._active_elem and t == self._active_elem.get('tag'):
            inner = ' '.join(self._active_elem.pop('_text', [])).strip()
            if not self._active_elem.get('label'):
                self._active_elem['label'] = inner
            elif inner and inner != self._active_elem['label']:
                self._active_elem['label'] = f"{self._active_elem['label']} ({inner})"
            label_clean = re.sub(r'\s+', ' ', str(self._active_elem.get('label') or '')).strip()[:90]
            self._active_elem['label'] = label_clean or self._active_elem.get('href', '')[:60] or t
            if self._active_elem['label'] and len(self.elements) < 35:
                idx = len(self.elements) + 1
                self._active_elem['ref'] = f'@b{idx}'
                self.elements.append(self._active_elem)
            self._active_elem = None

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
            return
        if self._skip_depth > 0:
            return
        cleaned = re.sub(r'\s+', ' ', data).strip()
        if not cleaned:
            return
        self.text_chunks.append(cleaned)
        if self._active_elem is not None:
            self._active_elem['_text'].append(cleaned)


def _extract_search_query_from_url(url: str) -> Optional[str]:
    try:
        parsed = urllib.parse.urlsplit(url)
        host = (parsed.hostname or '').lower()
        qs = urllib.parse.parse_qs(parsed.query)
        if any(h in host for h in ('bing.com', 'google.', 'duckduckgo.com', 'so.com', 'ecosia.org', 'brave.com')):
            for k in ('q', 'query', 'wd', 'p'):
                if qs.get(k) and qs[k][0].strip():
                    return qs[k][0].strip()
        if 'baidu.com' in host:
            for k in ('wd', 'word', 'q'):
                if qs.get(k) and qs[k][0].strip():
                    return qs[k][0].strip()
        if 'sogou.com' in host:
            for k in ('query', 'keyword', 'q'):
                if qs.get(k) and qs[k][0].strip():
                    return qs[k][0].strip()
        if 'yahoo.' in host:
            for k in ('p', 'q'):
                if qs.get(k) and qs[k][0].strip():
                    return qs[k][0].strip()
    except Exception:
        pass
    return None


def _is_poisoned_or_blocked_search(query: str, title: str, page_text: str) -> bool:
    combined = f'{title} {page_text}'.lower()
    if len(page_text.strip()) < 120:
        return True
    if any(bad in combined for bad in ('anomaly-modal', 'unusual traffic', '安全验证', '百度安全验证', 'there are no results for')):
        return True
    q_tokens = [t.lower() for t in re.split(r'\s+', query.strip()) if len(t) >= 2]
    if not q_tokens:
        q_tokens = [query.strip().lower()]
    cjk_chars = [ch for ch in query if '\u4e00' <= ch <= '\u9fff']
    if len(cjk_chars) >= 3:
        bigram = ''.join(cjk_chars[:2])
        if bigram not in combined:
            return True
    elif q_tokens and not any(tok in combined for tok in q_tokens):
        return True
    return False


def _run_fallback_multi_search_sync(query: str) -> Dict[str, object]:
    from core.search_engine import multi_web_search
    try:
        return asyncio.run(multi_web_search(query, max_results=12))
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(multi_web_search(query, max_results=12))
        finally:
            loop.close()


def _render_search_results_html(query: str, original_url: str, search_data: Dict[str, object], cwd: Path) -> Path:
    items = search_data.get('results') or []
    rows_html = []
    for idx, item in enumerate(items, 1):
        t = html_mod.escape(str(item.get('title') or ''))
        u = html_mod.escape(str(item.get('url') or ''))
        s = html_mod.escape(str(item.get('snippet') or ''))
        eng = html_mod.escape(str(item.get('engine') or 'web'))
        rows_html.append(
            f'<div class="res"><div class="meta">[{idx}] {eng} · <span class="url">{u[:95]}</span></div>'
            f'<h3><a href="{u}">{t}</a></h3><p>{s}</p></div>'
        )
    body_rows = '\n'.join(rows_html) if rows_html else '<p>未找到相关结果</p>'
    q_esc = html_mod.escape(query)
    html_doc = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>{q_esc} - 搜索结果</title>
<style>
body{{margin:0;font-family:'Noto Sans CJK SC',system-ui,sans-serif;background:#0b1120;color:#f8fafc;padding:20px 28px}}
.bar{{display:flex;align-items:center;gap:12px;padding-bottom:14px;border-bottom:1px solid #1e293b;margin-bottom:16px}}
.qbox{{background:#111827;border:1px solid #334155;border-radius:10px;padding:9px 16px;color:#fff;font-size:15px;flex:1}}
.badge{{font-size:12px;color:#38bdf8;background:rgba(56,189,248,0.14);padding:5px 12px;border-radius:8px;border:1px solid rgba(56,189,248,0.25)}}
.res{{background:#111827;border:1px solid #1e293b;border-radius:12px;padding:13px 18px;margin-bottom:10px}}
.meta{{font-size:11.5px;color:#94a3b8;margin-bottom:4px}}
.url{{color:#64748b}}
h3{{margin:0 0 6px 0;font-size:15.5px}}
h3 a{{color:#38bdf8;text-decoration:none}}
p{{margin:0;font-size:13px;color:#cbd5e1;line-height:1.5}}
</style></head><body>
<div class="bar"><div class="qbox">🔍 {q_esc}</div><div class="badge">WARP 多源搜索结果 ({len(items)} 条)</div></div>
{body_rows}
</body></html>"""
    out_file = cwd / '.last_search_results.html'
    out_file.write_text(html_doc, encoding='utf-8')
    return out_file


def _wait_for_browser_window(run_env: Dict[str, str], timeout_s: float = 4.8) -> bool:
    """Wait until an actual browser window (`chromium` or `luakit`, not `tint2`) maps on X11."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        wins = list_x11_windows(run_env)
        for w in wins:
            wclass = str(w.get('wm_class', '')).lower()
            wtitle = str(w.get('title', '')).lower()
            if any(k in wclass or k in wtitle for k in ('chromium', 'chrome', 'luakit', 'webkit', 'browser', 'welcome', 'cloud')):
                return True
        if wins:
            return True
        time.sleep(0.18)
    return False


def launch_modern_browser(
    url: str,
    env: Dict[str, str],
    cwd: Path,
    width: int = 1280,
    height: int = 720,
) -> Tuple[str, Dict[str, object]]:
    """
    Open or navigate `url` in our persistent CDP-enabled Chromium (or fallback `luakit` with WebKit fixes),
    guaranteeing the X11 window is mapped and extracting live rendered DOM + `@b1..@bN` screen coordinates.
    """
    target_url = (url or '').strip()
    if not target_url.startswith(('http://', 'https://', 'file://')):
        target_url = 'https://' + target_url

    run_env = dict(os.environ)
    run_env.update(env)
    home_dir = Path(run_env.get('HOME') or str(cwd.parent))
    run_env['HOME'] = str(home_dir)
    run_env['DBUS_SESSION_BUS_ADDRESS'] = 'disabled:'
    run_env['DBUS_SYSTEM_BUS_ADDRESS'] = 'disabled:'
    run_env['WEBKIT_DISABLE_SANDBOX_THIS_IS_DANGEROUS'] = '1'
    run_env['WEBKIT_DISABLE_COMPOSITING_MODE'] = '1'
    run_env['WEBKIT_DISABLE_DMABUF_RENDERER'] = '1'

    warp_url = _warp_proxy_url()
    # Check if URL is a domestic CN site that works faster/cleaner directly without WARP
    parsed_host = (urllib.parse.urlsplit(target_url).hostname or '').lower()
    is_domestic_direct = any(
        parsed_host.endswith(suf)
        for suf in ('.qq.com', '.baidu.com', '.bilibili.com', '.zhihu.com', '.feishu.cn', '.163.com', '.taobao.com', '.jd.com', '.weibo.com', '.csdn.net', '.aliyun.com')
    )
    if warp_url and not is_domestic_direct:
        run_env['http_proxy'] = warp_url
        run_env['https_proxy'] = warp_url
        run_env['HTTP_PROXY'] = warp_url
        run_env['HTTPS_PROXY'] = warp_url

    # Pre-check search engine URLs so blocked search pages get healed before navigation
    search_q = _extract_search_query_from_url(target_url)
    display_url = target_url
    pre_snapshot: Optional[Dict[str, object]] = None
    if search_q:
        pre_snapshot = fetch_semantic_dom_snapshot(target_url, width=width, height=height, cwd=cwd)
        display_url = str(pre_snapshot.get('_local_render_uri') or target_url)
        pre_snapshot.pop('_local_render_uri', None)

    browser_used = 'chromium-cdp'
    chrom_bin = (
        '/usr/lib/chromium/chromium'
        if Path('/usr/lib/chromium/chromium').exists()
        else (shutil.which('chromium') or shutil.which('chromium-browser'))
    )

    if os.name != 'nt' and run_env.get('DISPLAY'):
        # Fast-path 1: Reuse already-running Chromium via CDP `Page.navigate`!
        targets = _cdp_get_targets(timeout=0.45)
        if targets and chrom_bin and _wait_for_browser_window(run_env, timeout_s=0.25):
            ws_url = str(targets[0].get('webSocketDebuggerUrl') or '')
            try:
                _ws_cdp_call(ws_url, [('Page.navigate', {'url': display_url})], timeout=3.5)
                # Wait briefly for SPA/DOM render
                wait_t = 1.6 if is_domestic_direct or not display_url.startswith('file://') else 0.35
                time.sleep(wait_t)
                live_snap = extract_live_cdp_dom_snapshot(wait_ms=250)
                if live_snap and (live_snap.get('page_text') or live_snap.get('elements')):
                    live_snap['browser'] = 'chromium-cdp'
                    return 'chromium-cdp', live_snap
            except Exception:
                pass

        # Cold-start Chromium with CDP enabled on port 9222
        if chrom_bin:
            reap_idle_browsers(run_env)
            profile_dir = home_dir / '.local' / 'chromium_profile'
            profile_dir.mkdir(parents=True, exist_ok=True)
            for lock_name in ('SingletonLock', 'SingletonCookie', 'SingletonSocket'):
                try:
                    (profile_dir / lock_name).unlink(missing_ok=True)
                except Exception:
                    pass
            pref_file = profile_dir / 'Default' / 'Preferences'
            if pref_file.exists():
                try:
                    ptxt = pref_file.read_text(encoding='utf-8', errors='ignore')
                    ptxt = ptxt.replace('"exit_type":"Crashed"', '"exit_type":"Normal"').replace('"exited_cleanly":false', '"exited_cleanly":true')
                    pref_file.write_text(ptxt, encoding='utf-8')
                except Exception:
                    pass
            for cache_sub in (
                profile_dir / 'component_crx_cache',
                profile_dir / 'WasmTtsEngine',
                profile_dir / 'BrowserMetrics-spare.pma',
                profile_dir / 'Default' / 'Service Worker' / 'CacheStorage',
                profile_dir / 'Default' / 'Service Worker' / 'ScriptCache',
            ):
                try:
                    if cache_sub.is_file():
                        cache_sub.unlink(missing_ok=True)
                    elif cache_sub.is_dir() and not cache_sub.is_symlink():
                        shutil.rmtree(cache_sub, ignore_errors=True)
                except Exception:
                    pass
            cmd = [
                str(chrom_bin),
                '--no-sandbox',
                '--test-type',
                '--disable-infobars',
                '--hide-crash-restore-bubble',
                '--disable-session-crashed-bubble',
                '--disable-gpu',
                '--disable-dev-shm-usage',
                '--disable-software-rasterizer',
                '--disable-blink-features=AutomationControlled',
                '--no-first-run',
                '--no-default-browser-check',
                '--disable-background-networking',
                '--disable-component-update',
                '--disable-sync',
                '--disable-translate',
                '--hide-scrollbars',
                '--disk-cache-size=33554432',
                '--media-cache-size=16777216',
                '--renderer-process-limit=1',
                '--js-flags=--max-old-space-size=192',
                f'--remote-debugging-port={CDP_PORT}',
                '--remote-allow-origins=*',
                f'--user-data-dir={profile_dir}',
                f'--window-size={width - 28},{height - 64}',
                '--window-position=14,10',
                display_url,
            ]
            if warp_url and not is_domestic_direct:
                cmd.insert(-1, f'--proxy-server={warp_url};<-loopback>;*.qq.com;*.baidu.com;*.bilibili.com;*.zhihu.com;*.feishu.cn')
            subprocess.Popen(
                cmd,
                env=run_env,
                cwd=str(cwd),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            browser_used = 'chromium-cdp'
            _wait_for_browser_window(run_env, timeout_s=4.5)
            # Wait for CDP target + initial page paint
            for _ in range(14):
                if _cdp_get_targets(timeout=0.25):
                    break
                time.sleep(0.2)
            time.sleep(1.2 if not display_url.startswith('file://') else 0.35)
            live_snap = extract_live_cdp_dom_snapshot(wait_ms=250)
            if live_snap and (live_snap.get('page_text') or live_snap.get('elements') or display_url.startswith('file://')):
                live_snap['browser'] = browser_used
                return browser_used, live_snap

        # Fallback: luakit with WebKit2 sandbox/DMABUF disabled
        if shutil.which('luakit') and not _wait_for_browser_window(run_env, timeout_s=0.2):
            subprocess.Popen(
                ['/usr/bin/luakit', display_url],
                env=run_env,
                cwd=str(cwd),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            browser_used = 'luakit-webkit2'
            _wait_for_browser_window(run_env, timeout_s=4.0)
            time.sleep(0.8)

    snapshot = pre_snapshot or fetch_semantic_dom_snapshot(target_url, width=width, height=height, cwd=cwd)
    snapshot.pop('_local_render_uri', None)
    snapshot['browser'] = browser_used
    return browser_used, snapshot


def fetch_semantic_dom_snapshot(url: str, width: int = 1280, height: int = 720, cwd: Optional[Path] = None) -> Dict[str, object]:
    """
    Fallback static/CDP DOM snapshot fetcher when live CDP extraction isn't used directly.
    """
    live = extract_live_cdp_dom_snapshot(wait_ms=100)
    if live and str(live.get('url') or '').rstrip('/') == url.rstrip('/') and len(str(live.get('page_text') or '')) > 40:
        return live

    html_text = ''
    final_url = url
    warp_url = _warp_proxy_url()
    parsed_host = (urllib.parse.urlsplit(url).hostname or '').lower()
    is_domestic_direct = any(
        parsed_host.endswith(suf)
        for suf in ('.qq.com', '.baidu.com', '.bilibili.com', '.zhihu.com', '.feishu.cn', '.163.com', '.taobao.com', '.jd.com')
    )
    ua = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'

    handlers = []
    if warp_url and not url.startswith('file://') and not is_domestic_direct:
        handlers.append(urllib.request.ProxyHandler({'http': warp_url, 'https': warp_url}))
    opener = urllib.request.build_opener(*handlers)
    opener.addheaders = [
        ('User-Agent', ua),
        ('Accept', 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'),
        ('Accept-Language', 'zh-CN,zh;q=0.9,en;q=0.8'),
    ]

    try:
        with opener.open(url, timeout=8) as resp:
            raw_bytes = resp.read(450_000)
            final_url = resp.geturl() or url
            charset = resp.headers.get_content_charset() or 'utf-8'
            html_text = raw_bytes.decode(charset, errors='replace')
    except Exception:
        try:
            req = urllib.request.Request(url, headers={'User-Agent': ua, 'Accept-Language': 'zh-CN,zh;q=0.9'})
            with urllib.request.urlopen(req, timeout=7) as resp:
                raw_bytes = resp.read(450_000)
                final_url = resp.geturl() or url
                charset = resp.headers.get_content_charset() or 'utf-8'
                html_text = raw_bytes.decode(charset, errors='replace')
        except Exception:
            html_text = ''

    parser = _SemanticDOMParser(base_url=final_url)
    if html_text:
        try:
            parser.feed(html_text)
        except Exception:
            pass

    title_str = re.sub(r'\s+', ' ', parser.title).strip()[:140]
    page_text = re.sub(r'\s+', ' ', ' '.join(parser.text_chunks)).strip()[:3200]
    elements = parser.elements[:30]

    search_q = _extract_search_query_from_url(url)
    local_render_uri = None
    if search_q and _is_poisoned_or_blocked_search(search_q, title_str, page_text):
        s_data = _run_fallback_multi_search_sync(search_q)
        s_items = s_data.get('results') or []
        if s_items:
            title_str = f'{search_q} - 搜索结果'
            lines = []
            new_elements = []
            for idx, it in enumerate(s_items[:12], 1):
                it_title = str(it.get('title') or '')
                it_url = str(it.get('url') or '')
                it_snip = str(it.get('snippet') or '')
                lines.append(f'[{idx}] {it_title} ({it_url}) — {it_snip}')
                new_elements.append({
                    'ref': f'@b{idx}',
                    'tag': 'a',
                    'type': 'a',
                    'label': it_title[:90],
                    'href': it_url,
                })
            page_text = '\n'.join(lines)[:3200]
            elements = new_elements
            if cwd is not None:
                try:
                    rendered_file = _render_search_results_html(search_q, url, s_data, cwd)
                    local_render_uri = f'file://{rendered_file}'
                except Exception:
                    pass

    res: Dict[str, object] = {
        'url': final_url,
        'title': title_str,
        'page_text': page_text or '[动态网页已打开，请结合桌面截图或 @bN 元素交互]',
        'elements': elements,
    }
    if local_render_uri:
        res['_local_render_uri'] = local_render_uri
    return res
