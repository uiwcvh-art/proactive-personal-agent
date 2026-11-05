"""
Low-Memory On-Demand Browser & Semantic DOM Engine (`server/cloud_os/agent_browser.py`).

Designed for the 1C 1G 10GB Cloud Computer:
1. **Zero Idle Memory**: Browsers are launched on-demand only when requested, using ultra-lightweight
   `luakit` (WebKit2GTK, ~45MB RAM) and automatically reaped after 180s of inactivity (`reap_idle_browsers`).
2. **WARP Anti-Bot Egress (`127.0.0.1:40001`)**: Uses the local Cloudflare WARP tunnel when available so
   search engines and websites do not block or poison requests from the datacenter IP.
3. **One-Step Semantic DOM Snapshot (`@b1..@bN`)**: Every navigation or search directly returns the full
   `title`, `url`, readable `page_text` (up to 3200 chars), and clickable `elements` (`@b1..@b30`).
"""

from __future__ import annotations

import asyncio
import html as html_mod
import json
import os
import re
import shutil
import socket
import subprocess
import time
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Dict, List, Optional, Tuple


CDP_PORT = 9222


def _warp_proxy_url() -> Optional[str]:
    try:
        with socket.create_connection(('127.0.0.1', 40001), timeout=0.2):
            return 'http://127.0.0.1:40001'
    except OSError:
        return None


def reap_idle_browsers(env: Optional[Dict[str, str]] = None) -> int:
    """Terminate any running browser windows/processes to reclaim 100% of browser RAM when idle."""
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
    """Lightweight HTML parser extracting clean readable text and interactive `@b1..@bN` elements."""

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
    """If `url` is a search engine query URL (Google, Bing, Baidu, DDG, Sogou, Yahoo), extract the query string."""
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
    """Detect if a search engine returned a CAPTCHA, empty shell, or datacenter-poisoned result page."""
    combined = f'{title} {page_text}'.lower()
    if len(page_text.strip()) < 120:
        return True
    if any(bad in combined for bad in ('anomaly-modal', 'unusual traffic', '安全验证', '百度安全验证', 'there are no results for')):
        return True
    # Check if at least one non-trivial token/hanzi from `query` appears in the result text
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
body{{margin:0;font-family:'Noto Sans CJK SC',system-ui,sans-serif;background:#0f1115;color:#f3f4f6;padding:18px 26px}}
.bar{{display:flex;align-items:center;gap:12px;padding-bottom:14px;border-bottom:1px solid #262b36;margin-bottom:16px}}
.qbox{{background:#181b22;border:1px solid #2e3440;border-radius:8px;padding:8px 14px;color:#fff;font-size:15px;flex:1}}
.badge{{font-size:12px;color:#60a5fa;background:rgba(59,130,246,0.15);padding:4px 10px;border-radius:6px}}
.res{{background:#161920;border:1px solid #232834;border-radius:10px;padding:12px 16px;margin-bottom:10px}}
.meta{{font-size:11.5px;color:#9ca3af;margin-bottom:4px}}
.url{{color:#6b7280}}
h3{{margin:0 0 6px 0;font-size:15.5px}}
h3 a{{color:#60a5fa;text-decoration:none}}
p{{margin:0;font-size:13px;color:#d1d5db;line-height:1.45}}
</style></head><body>
<div class="bar"><div class="qbox">🔍 {q_esc}</div><div class="badge">WARP 多源真实搜索结果 ({len(items)} 条)</div></div>
{body_rows}
</body></html>"""
    out_file = cwd / '.last_search_results.html'
    out_file.write_text(html_doc, encoding='utf-8')
    return out_file


def launch_modern_browser(
    url: str,
    env: Dict[str, str],
    cwd: Path,
    width: int = 1280,
    height: int = 720,
) -> Tuple[str, Dict[str, object]]:
    """
    Open `url` on-demand in ultra-lightweight `luakit` (~45MB RAM), clean up any heavy stray
    browser instances, and return a complete semantic DOM snapshot (`title`, `url`, `page_text`, `elements`).
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
    warp_url = _warp_proxy_url()
    if warp_url:
        run_env['http_proxy'] = warp_url
        run_env['https_proxy'] = warp_url
        run_env['HTTP_PROXY'] = warp_url
        run_env['HTTPS_PROXY'] = warp_url

    # 1. Fetch semantic DOM snapshot first (and detect if a search engine poisoned the datacenter IP)
    snapshot = fetch_semantic_dom_snapshot(target_url, width=width, height=height, cwd=cwd)
    display_url = str(snapshot.get('_local_render_uri') or target_url)
    snapshot.pop('_local_render_uri', None)

    # 2. Reap any old browser processes so memory never accumulates across navigations
    reap_idle_browsers(run_env)

    browser_used = 'luakit-on-demand'
    if os.name != 'nt' and run_env.get('DISPLAY'):
        if shutil.which('luakit'):
            subprocess.Popen(
                ['/usr/bin/luakit', display_url],
                env=run_env,
                cwd=str(cwd),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            browser_used = 'luakit-webkit2'
        elif shutil.which('chromium') or shutil.which('chromium-browser'):
            chrom_bin = '/usr/lib/chromium/chromium' if Path('/usr/lib/chromium/chromium').exists() else (shutil.which('chromium') or shutil.which('chromium-browser'))
            profile_dir = home_dir / '.local' / 'chromium_profile'
            profile_dir.mkdir(parents=True, exist_ok=True)
            for lock_name in ('SingletonLock', 'SingletonCookie', 'SingletonSocket'):
                try:
                    (profile_dir / lock_name).unlink(missing_ok=True)
                except Exception:
                    pass
            cmd = [
                str(chrom_bin),
                '--no-sandbox',
                '--no-zygote',
                '--disable-gpu',
                '--disable-dev-shm-usage',
                '--disable-software-rasterizer',
                '--disable-blink-features=AutomationControlled',
                '--renderer-process-limit=1',
                '--process-per-site',
                '--no-first-run',
                '--no-default-browser-check',
                f'--user-data-dir={profile_dir}',
                f'--window-size={width - 48},{height - 72}',
                '--window-position=24,20',
                display_url,
            ]
            if warp_url:
                cmd.insert(-1, f'--proxy-server={warp_url}')
            subprocess.Popen(
                cmd,
                env=run_env,
                cwd=str(cwd),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            browser_used = 'chromium-ondemand'

        if shutil.which('xprop'):
            for _ in range(8):
                time.sleep(0.2)
                try:
                    xp = subprocess.check_output(
                        ['xprop', '-root', '_NET_CLIENT_LIST_STACKING'],
                        env=run_env, text=True, stderr=subprocess.DEVNULL, timeout=1,
                    )
                    if len(re.findall(r'0x[0-9a-fA-F]+', xp)) >= 1:
                        break
                except Exception:
                    pass
        else:
            time.sleep(0.8)

    snapshot['browser'] = browser_used
    return browser_used, snapshot


def fetch_semantic_dom_snapshot(url: str, width: int = 1280, height: int = 720, cwd: Optional[Path] = None) -> Dict[str, object]:
    """
    Fetch `url` through WARP proxy (or direct fallback), parse semantic DOM (`@b1..@bN`),
    and automatically heal datacenter-poisoned search engine pages so every search engine works cleanly.
    """
    html_text = ''
    final_url = url
    warp_url = _warp_proxy_url()
    ua = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'

    handlers = []
    if warp_url and not url.startswith('file://'):
        handlers.append(urllib.request.ProxyHandler({'http': warp_url, 'https': warp_url}))
    opener = urllib.request.build_opener(*handlers)
    opener.addheaders = [
        ('User-Agent', ua),
        ('Accept', 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'),
        ('Accept-Language', 'zh-CN,zh;q=0.9,en;q=0.8'),
    ]

    try:
        with opener.open(url, timeout=9) as resp:
            raw_bytes = resp.read(450_000)
            final_url = resp.geturl() or url
            charset = resp.headers.get_content_charset() or 'utf-8'
            html_text = raw_bytes.decode(charset, errors='replace')
    except Exception:
        # Fallback to direct connection if WARP timed out on a domestic CN site
        try:
            req = urllib.request.Request(url, headers={'User-Agent': ua, 'Accept-Language': 'zh-CN,zh;q=0.9'})
            with urllib.request.urlopen(req, timeout=8) as resp:
                raw_bytes = resp.read(450_000)
                final_url = resp.geturl() or url
                charset = resp.headers.get_content_charset() or 'utf-8'
                html_text = raw_bytes.decode(charset, errors='replace')
        except Exception:
            html_text = ''

    # If lightweight HTTP fetch got an empty JS shell, run transient Chromium `--dump-dom` (~1.5s, 0 persistent RAM)
    if len(html_text.strip()) < 400 and os.name != 'nt' and not url.startswith('file://'):
        chrom_bin = '/usr/lib/chromium/chromium' if Path('/usr/lib/chromium/chromium').exists() else (shutil.which('chromium') or shutil.which('chromium-browser'))
        if chrom_bin:
            try:
                c_env = dict(os.environ)
                c_env['DBUS_SESSION_BUS_ADDRESS'] = 'disabled:'
                c_env['DBUS_SYSTEM_BUS_ADDRESS'] = 'disabled:'
                cmd = [
                    str(chrom_bin),
                    '--headless=new',
                    '--no-sandbox',
                    '--disable-gpu',
                    '--disable-dev-shm-usage',
                    '--disable-blink-features=AutomationControlled',
                    '--no-first-run',
                    '--no-default-browser-check',
                    f'--user-agent={ua}',
                    '--dump-dom',
                    url,
                ]
                if warp_url:
                    cmd.insert(-1, f'--proxy-server={warp_url}')
                raw_dom = subprocess.check_output(cmd, env=c_env, stderr=subprocess.DEVNULL, timeout=8).decode('utf-8', errors='replace')
                if len(raw_dom.strip()) > 120:
                    html_text = raw_dom
            except Exception:
                pass

    parser = _SemanticDOMParser(base_url=final_url)
    if html_text:
        try:
            parser.feed(html_text)
        except Exception:
            pass

    title_str = re.sub(r'\s+', ' ', parser.title).strip()[:140]
    page_text = re.sub(r'\s+', ' ', ' '.join(parser.text_chunks)).strip()[:3200]
    elements = parser.elements[:30]

    # Check if this was a search engine URL that returned poisoned/CAPTCHA results
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
        'page_text': page_text or '[页面已打开，未提取到正文文本]',
        'elements': elements,
    }
    if local_render_uri:
        res['_local_render_uri'] = local_render_uri
    return res
