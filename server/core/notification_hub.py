"""
Notification Convergence Hub (`server/core/notification_hub.py`).

Capabilities:
1. **Notification Convergence & Session-Grouped Persistent Inbox (`NotificationHub`)**:
   - Passively & actively collects Android status-bar notifications before they are swiped away.
   - Deduplicates and groups messages by `(package, title)` so multi-message group chats (e.g., WeChat groups, QQ)
     accumulate a chronological message trail (`history`) even when newer notifications overwrite older ones.
   - Persists meaningful notifications into `store.state['notification_inbox']` AND appends a searchable log
     to `memory_vault/logs/notifications_inbox.md`.
2. **AI-Writable Notification Filter Engine (`notification_filters`)**:
   - Ships with sensible built-in filters blocking noisy ongoing VPNs (Clash, FlClash, v2rayNG, NekoBox, sing-box, etc.),
     live network speed meters (`↑/↓ KB/s`), pedometers, and Android system foreground banners.
   - Allows the AI to inspect, add, and remove custom filter rules (`action="add_filter" | "remove_filter" | "list_filters"`)
     persisted in `store.state['notification_filters']` and `memory_vault/config/notification_filters.json`.
3. **Curiosity Piggyback (`build_curiosity_digest`)**:
   - Packages unseen accumulated notifications into a clean digest injected automatically whenever `curiosity()` runs.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime
from typing import Any

from vault_manager import TZ

DEFAULT_NOTIFICATION_FILTERS: list[dict[str, Any]] = [
    {
        'id': 'builtin-vpn-proxy',
        'package': 'clash|flclash|v2ray|nekobox|sing-box|singbox|hiddify|shadowrocket|surfboard|wireguard|openvpn|tailscale|zerotier|proxifier|tun2socks|karing|matsuri|anxray|sager|ssr|trojan',
        'app': 'Clash|FlClash|v2ray|NekoBox|sing-box|Hiddify|WireGuard|OpenVPN|VPN|代理|加速器',
        'keyword': r'(?:VPN\s*已连接|代理已开启|正在通过.*代理|Clash\s*for\s*Android|FlClash|v2rayNG|NekoBox|sing-box|规则模式|全局模式|节点选择|tun2socks)',
        'reason': '默认过滤 VPN 与代理工具常驻状态通知',
        'created_by': 'system',
    },
    {
        'id': 'builtin-speed-traffic',
        'package': '',
        'app': '',
        'keyword': r'(?:(?:↑|↓|▲|▼|上传|下载|上行|下行)\s*[:：]?\s*\d+(?:\.\d+)?\s*(?:B|KB|MB|GB|K|M)/s|已用流量|今日流量|本月已用|实时网速)',
        'reason': '默认过滤实时网速跳动与流量统计常驻通知',
        'created_by': 'system',
    },
    {
        'id': 'builtin-system-ongoing',
        'package': 'com.android.systemui|com.miui.securitycenter|com.coloros.safecenter|com.huawei.systemmanager|com.vivo.abe',
        'app': '系统界面|手机管家|安全中心',
        'keyword': r'(?:正在后台运行|应用正在后台运行|手机工作区正在执行|点击查看执行记录|USB\s*调试已连接|正在通过\s*USB\s*充电|选择输入法|切换输入法|今日步数|正在运行的服务)',
        'reason': '默认过滤系统后台服务、USB 调试、输入法与计步器常驻通知',
        'created_by': 'system',
    },
]


class NotificationHub:
    def __init__(self, store, vault):
        self.store = store
        self.vault = vault
        self._ensure_initialized()

    def _ensure_initialized(self) -> None:
        s = self.store.state
        if not isinstance(s.get('notification_filters'), list) or not s['notification_filters']:
            loaded = None
            try:
                raw_cfg = self.vault.read('config/notification_filters.json')
                if raw_cfg:
                    parsed = json.loads(raw_cfg)
                    if isinstance(parsed, list) and parsed:
                        loaded = parsed
            except Exception:
                loaded = None
            s['notification_filters'] = loaded or [dict(f) for f in DEFAULT_NOTIFICATION_FILTERS]
            self._sync_filters_to_vault()
        if not isinstance(s.get('notification_inbox'), list):
            s['notification_inbox'] = []
        if not isinstance(s.get('notification_filtered_stats'), dict):
            s['notification_filtered_stats'] = {'total_filtered': 0, 'recent_blocked': []}

    def _sync_filters_to_vault(self) -> None:
        try:
            filters = self.store.state.get('notification_filters', [])
            self.vault.write('config/notification_filters.json', json.dumps(filters, ensure_ascii=False, indent=2))
        except Exception:
            pass

    def get_filters(self) -> list[dict[str, Any]]:
        self._ensure_initialized()
        return list(self.store.state.get('notification_filters', []))

    def match_filter(self, item: dict[str, Any]) -> dict[str, Any] | None:
        """Return the matching filter rule dict if `item` should be ignored, else None."""
        pkg = str(item.get('package') or '').strip().lower()
        app_name = str(item.get('app') or '').strip()
        title = str(item.get('title') or '').strip()
        text = str(item.get('text') or '').strip()
        combined_text = f'{app_name}\n{title}\n{text}'

        if not title and not text:
            return {'id': 'empty', 'reason': '空通知'}
        if pkg == 'dev.bubuki.proactive' or '手机工作区正在执行' in combined_text:
            return {'id': 'self-service', 'reason': '自身前台服务通知'}

        for rule in self.get_filters():
            if not isinstance(rule, dict) or rule.get('enabled', True) is False:
                continue
            r_pkg = str(rule.get('package') or '').strip()
            r_app = str(rule.get('app') or '').strip()
            r_kw = str(rule.get('keyword') or '').strip()

            pkg_matched = False
            if r_pkg:
                try:
                    pkg_matched = bool(re.search(r_pkg, pkg, flags=re.IGNORECASE))
                except re.error:
                    pkg_matched = r_pkg.lower() in pkg

            app_matched = False
            if r_app:
                try:
                    app_matched = bool(re.search(r_app, app_name, flags=re.IGNORECASE))
                except re.error:
                    app_matched = r_app.lower() in app_name.lower()

            kw_matched = False
            if r_kw:
                try:
                    kw_matched = bool(re.search(r_kw, combined_text, flags=re.IGNORECASE))
                except re.error:
                    kw_matched = r_kw.lower() in combined_text.lower()

            if str(rule.get('id') or '') == 'builtin-vpn-proxy':
                if pkg_matched or app_matched or kw_matched:
                    return rule
                continue

            if str(rule.get('id') or '').startswith('builtin-'):
                if kw_matched:
                    return rule
                continue

            checks = []
            if r_pkg:
                checks.append(pkg_matched)
            if r_app:
                checks.append(app_matched)
            if r_kw:
                checks.append(kw_matched)
            if checks and all(checks):
                return rule
        return None

    def add_filter(
        self,
        package: str = '',
        app: str = '',
        keyword: str = '',
        reason: str = '',
        created_by: str = 'ai',
    ) -> dict[str, Any]:
        self._ensure_initialized()
        package = (package or '').strip()
        app = (app or '').strip()
        keyword = (keyword or '').strip()
        if not package and not app and not keyword:
            return {'ok': False, 'error': 'add_filter 需要至少提供 filter_package、filter_app 或 filter_keyword 之一'}

        raw_sig = f'{package.lower()}|{app.lower()}|{keyword.lower()}'
        rule_id = 'filter-' + hashlib.sha1(raw_sig.encode('utf-8')).hexdigest()[:8]
        filters = self.store.state['notification_filters']
        existing = next((f for f in filters if f.get('id') == rule_id), None)
        new_rule = {
            'id': rule_id,
            'package': package,
            'app': app,
            'keyword': keyword,
            'reason': (reason or f'自动忽略 {app or package or keyword} 垃圾/常驻通知').strip(),
            'created_by': created_by,
            'created_at': time.time(),
            'enabled': True,
        }
        if existing:
            existing.update(new_rule)
        else:
            filters.append(new_rule)

        inbox = self.store.state.get('notification_inbox', [])
        kept = []
        removed_count = 0
        for item in inbox:
            if self.match_filter(item) is not None:
                removed_count += 1
            else:
                kept.append(item)
        self.store.state['notification_inbox'] = kept
        self.store.save()
        self._sync_filters_to_vault()
        return {
            'ok': True,
            'action': 'add_filter',
            'rule': new_rule,
            'removed_from_inbox': removed_count,
            'total_filters': len(filters),
        }

    def remove_filter(self, filter_id_or_kw: str) -> dict[str, Any]:
        self._ensure_initialized()
        q = (filter_id_or_kw or '').strip().lower()
        if not q:
            return {'ok': False, 'error': '请提供要移除的 filter_id 或关键词'}
        filters = self.store.state['notification_filters']
        before = len(filters)
        kept = [
            f for f in filters
            if str(f.get('id') or '').lower() != q
            and q not in str(f.get('package') or '').lower()
            and q not in str(f.get('app') or '').lower()
            and q not in str(f.get('keyword') or '').lower()
        ]
        self.store.state['notification_filters'] = kept
        self.store.save()
        self._sync_filters_to_vault()
        return {
            'ok': True,
            'action': 'remove_filter',
            'removed_count': before - len(kept),
            'remaining_filters': kept,
        }

    def ingest_raw_notifications(self, raw_items: list[dict[str, Any]]) -> dict[str, Any]:
        """
        Filter, deduplicate, and converge incoming phone notifications into `notification_inbox`
        and `memory_vault/logs/notifications_inbox.md`.
        """
        self._ensure_initialized()
        if not isinstance(raw_items, list):
            return {'added': 0, 'filtered': 0}

        inbox: list[dict[str, Any]] = self.store.state['notification_inbox']
        stats: dict[str, Any] = self.store.state['notification_filtered_stats']
        now_ts = time.time()
        added_count = 0
        updated_count = 0
        filtered_count = 0
        new_vault_lines: list[str] = []

        for raw in raw_items:
            if not isinstance(raw, dict):
                continue
            pkg = str(raw.get('package') or '').strip()
            app_name = str(raw.get('app') or pkg).strip()
            title = str(raw.get('title') or '').strip()
            text = str(raw.get('text') or '').strip()
            post_ms = raw.get('timestamp_ms') or raw.get('postTime') or raw.get('timestamp') or raw.get('time') or 0
            try:
                ts = float(post_ms) / 1000.0 if float(post_ms) > 1e11 else (float(post_ms) if float(post_ms) > 1e9 else now_ts)
            except (ValueError, TypeError):
                ts = now_ts
            time_bj = datetime.fromtimestamp(ts, TZ).strftime('%Y-%m-%d %H:%M:%S')
            short_time = datetime.fromtimestamp(ts, TZ).strftime('%H:%M')

            candidate = {
                'package': pkg,
                'app': app_name,
                'title': title,
                'text': text,
                'timestamp': ts,
                'time_bj': time_bj,
            }
            matched_rule = self.match_filter(candidate)
            if matched_rule is not None:
                filtered_count += 1
                stats['total_filtered'] = int(stats.get('total_filtered') or 0) + 1
                recent_b = stats.setdefault('recent_blocked', [])
                summary_b = f"[{app_name or pkg}] {title}: {text[:40]} (规则: {matched_rule.get('id')})"
                if summary_b not in recent_b:
                    recent_b.append(summary_b)
                    if len(recent_b) > 15:
                        del recent_b[:-15]
                continue

            msg_fp = hashlib.sha1(f'{pkg}|{title}|{text}'.encode('utf-8')).hexdigest()[:12]
            conv_key = f'{pkg}::{title}'

            existing_conv = next((it for it in reversed(inbox) if it.get('conv_key') == conv_key), None)
            if existing_conv is not None:
                fps = existing_conv.setdefault('fingerprints', [])
                if msg_fp in fps:
                    continue
                fps.append(msg_fp)
                if len(fps) > 15:
                    del fps[:-15]
                hist = existing_conv.setdefault('history', [])
                hist_line = f'[{short_time}] {text[:400]}'
                if not hist or hist[-1] != hist_line:
                    hist.append(hist_line)
                    if len(hist) > 12:
                        del hist[:-12]
                existing_conv['text'] = text[:500]
                existing_conv['timestamp'] = max(float(existing_conv.get('timestamp') or 0), ts)
                existing_conv['time_bj'] = time_bj
                existing_conv['seen_by_curiosity'] = False
                existing_conv['seen_by_ai'] = False
                existing_conv['unread_count'] = int(existing_conv.get('unread_count') or 0) + 1
                updated_count += 1
                new_vault_lines.append(f'- `{time_bj}` **[{app_name}] {title}**：{text[:400]}')
            else:
                entry = {
                    'id': 'ntf-' + msg_fp,
                    'conv_key': conv_key,
                    'package': pkg,
                    'app': app_name,
                    'title': title,
                    'text': text[:500],
                    'history': [f'[{short_time}] {text[:400]}'],
                    'fingerprints': [msg_fp],
                    'timestamp': ts,
                    'time_bj': time_bj,
                    'seen_by_curiosity': False,
                    'seen_by_ai': False,
                    'unread_count': 1,
                }
                inbox.append(entry)
                added_count += 1
                new_vault_lines.append(f'- `{time_bj}` **[{app_name}] {title}**：{text[:400]}')

        if added_count or updated_count:
            cutoff_ts = now_ts - 7 * 86400.0
            inbox[:] = [it for it in inbox if float(it.get('timestamp') or 0) >= cutoff_ts]
            inbox.sort(key=lambda x: float(x.get('timestamp') or 0))
            if len(inbox) > 100:
                del inbox[:-100]
            self.store.save()
            if new_vault_lines:
                try:
                    self.vault.append('logs/notifications_inbox.md', '\n'.join(new_vault_lines))
                except Exception:
                    pass
        elif filtered_count:
            self.store.save()

        return {
            'added': added_count,
            'updated': updated_count,
            'filtered': filtered_count,
            'inbox_total': len(inbox),
        }

    def query_inbox(self, app_query: str = '', limit: int = 30, mark_seen: bool = True) -> dict[str, Any]:
        self._ensure_initialized()
        inbox: list[dict[str, Any]] = self.store.state.get('notification_inbox', [])
        q = (app_query or '').strip().lower()
        matched = []
        for item in reversed(inbox):
            if self.match_filter(item) is not None:
                continue
            hay = f"{item.get('app', '')} {item.get('package', '')} {item.get('title', '')} {item.get('text', '')} {' '.join(item.get('history') or [])}".lower()
            if not q or q in hay:
                if mark_seen:
                    item['seen_by_ai'] = True
                    item['unread_count'] = 0
                matched.append({
                    'id': item.get('id'),
                    'app': item.get('app'),
                    'package': item.get('package'),
                    'title': item.get('title'),
                    'latest_text': item.get('text'),
                    'history': item.get('history', [])[-8:],
                    'time_bj': item.get('time_bj'),
                    'timestamp': item.get('timestamp'),
                })
                if len(matched) >= max(1, min(100, limit)):
                    break
        if mark_seen and matched:
            self.store.save()
        stats = self.store.state.get('notification_filtered_stats', {})
        return {
            'count': len(matched),
            'notifications': matched,
            'filtered_out_total': stats.get('total_filtered', 0),
            'recent_blocked_samples': stats.get('recent_blocked', [])[-5:],
            'filter_management_hint': (
                '若发现某款 VPN/加速器/常驻服务仍在产生噪音通知，可随时调用 '
                '`phone_workspace(op="notifications", action="add_filter", filter_app="应用名", filter_keyword="关键词", reason="原因")` 永久自动屏蔽。'
            ),
        }

    def clear_inbox(self, app_query: str = '') -> dict[str, Any]:
        self._ensure_initialized()
        q = (app_query or '').strip().lower()
        inbox = self.store.state.get('notification_inbox', [])
        before = len(inbox)
        if not q:
            self.store.state['notification_inbox'] = []
        else:
            self.store.state['notification_inbox'] = [
                it for it in inbox
                if q not in f"{it.get('app', '')} {it.get('package', '')} {it.get('title', '')}".lower()
            ]
        self.store.save()
        return {'ok': True, 'cleared': before - len(self.store.state['notification_inbox']), 'remaining': len(self.store.state['notification_inbox'])}

    def build_curiosity_digest(self, mark_seen: bool = True) -> str:
        """
        Build a concise, clean notification digest for `curiosity()` so the AI naturally sees
        accumulated messages (even if swiped away on the phone) and can act on or filter them.
        """
        self._ensure_initialized()
        inbox: list[dict[str, Any]] = self.store.state.get('notification_inbox', [])
        now_ts = time.time()
        unseen: list[dict[str, Any]] = []
        recent_context: list[dict[str, Any]] = []

        for item in inbox:
            if self.match_filter(item) is not None:
                continue
            age_hours = (now_ts - float(item.get('timestamp') or 0)) / 3600.0
            if not item.get('seen_by_curiosity'):
                unseen.append(item)
                if mark_seen:
                    item['seen_by_curiosity'] = True
            elif age_hours <= 6.0:
                recent_context.append(item)

        if mark_seen and unseen:
            self.store.save()

        stats = self.store.state.get('notification_filtered_stats', {})
        total_filtered = int(stats.get('total_filtered') or 0)

        if not unseen and not recent_context:
            return f'（最近暂无新的有效手机通知；通知收敛器已自动静默拦截 {total_filtered} 条 VPN/常驻噪音通知）'

        lines = [
            f'（通知栏收敛器已自动静默过滤 {total_filtered} 条 VPN/网速/系统常驻噪音；若你在下方看到新的垃圾常驻通知，可顺手调用 `phone_workspace(op="notifications", action="add_filter", ...)` 将其永久加入黑名单）'
        ]
        if unseen:
            lines.append('### 🆕 新积攒的未读手机通知（哪怕主人已从状态栏划走也已为你完整保留）：')
            for it in unseen[-15:]:
                app_lbl = it.get('app') or it.get('package') or '应用'
                title_lbl = it.get('title') or '通知'
                hist = it.get('history') or [it.get('text') or '']
                if len(hist) > 1:
                    joined_h = ' ； '.join(hist[-5:])
                    lines.append(f'- **[{app_lbl}] {title_lbl}**（共积攒 {len(hist)} 条）：{joined_h}')
                else:
                    lines.append(f'- **[{app_lbl}] {title_lbl}** ({it.get("time_bj", "")[-8:]})：{it.get("text", "")}')
        if recent_context and len(unseen) < 6:
            lines.append('### 🕒 最近 6 小时内已读通知留档：')
            for it in recent_context[-6:]:
                app_lbl = it.get('app') or it.get('package') or '应用'
                title_lbl = it.get('title') or '通知'
                lines.append(f'- [{app_lbl}] {title_lbl}：{it.get("text", "")[:100]}')
        return '\n'.join(lines)
