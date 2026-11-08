"""Unified Third-Party App Integration Hub (`server/plugins/integration_hub.py`).

Gives all Bots a single, zero-context-bloat meta-tool (`integration_hub`) that unlocks:
1. Direct Protocol & REST Connectors (works immediately with App Passwords / PAT / Webhooks):
   - Email (`gmail`, `outlook`, `qq`, `163`, `icloud`, `imap`): list/search inbox, read full email, send email via SMTP.
   - Calendar (`ics`, `google_calendar`, `outlook_calendar`, `icloud_calendar`): fetch upcoming events from ICS/CalDAV.
   - GitHub (`github`): notifications, repos, issues, create issue, PRs, or arbitrary GitHub REST calls.
   - Notion (`notion`): search pages/databases, read page blocks, append blocks, create page.
   - Todoist (`todoist`): list active tasks, create task, close task.
   - Push & IM (`telegram`, `feishu`, `dingtalk`, `discord`, `slack`, `bark`): send instant messages/cards.
2. 500+ Third-Party App Ecosystem Bridge (`Composio` & `ACI.dev`):
   - Dynamic discovery (`op="search_actions"`), 1-click OAuth2 link generation (`op="connect_oauth"`),
     and execution (`op="execute"`) across 500+ apps (Gmail, Outlook, Google Calendar, Drive, Sheets,
     Slack, Linear, Jira, Trello, X/Twitter, Reddit, YouTube, Spotify, Vercel, Cloudflare, etc.)
     without bloating the LLM prompt context window.
"""
from __future__ import annotations

import asyncio
import email
from email.header import decode_header
from email.message import EmailMessage
import imaplib
import json
from pathlib import Path
import re
import smtplib
import ssl
import time
from typing import Any, Dict

import httpx

IMAP_PRESETS = {
    'gmail': {'imap_host': 'imap.gmail.com', 'imap_port': 993, 'smtp_host': 'smtp.gmail.com', 'smtp_port': 465},
    'outlook': {'imap_host': 'outlook.office365.com', 'imap_port': 993, 'smtp_host': 'smtp.office365.com', 'smtp_port': 587, 'starttls': True},
    'qq': {'imap_host': 'imap.qq.com', 'imap_port': 993, 'smtp_host': 'smtp.qq.com', 'smtp_port': 465},
    '163': {'imap_host': 'imap.163.com', 'imap_port': 993, 'smtp_host': 'smtp.163.com', 'smtp_port': 465},
    'icloud': {'imap_host': 'imap.mail.me.com', 'imap_port': 993, 'smtp_host': 'smtp.mail.me.com', 'smtp_port': 587, 'starttls': True},
}

CATALOG_SUMMARY = {
    'direct_builtin': [
        {
            'app': 'email (gmail / outlook / qq / 163 / icloud)',
            'actions': ['email_list', 'email_read', 'email_send'],
            'config_keys': ['provider (gmail|outlook|qq|163|icloud)', 'address', 'password (应用专用密码/授权码)', 'imap_host', 'smtp_host'],
        },
        {
            'app': 'calendar (google / outlook / icloud ICS)',
            'actions': ['calendar_list'],
            'config_keys': ['ics_url (日历私密 ICS 订阅链接)'],
        },
        {
            'app': 'github',
            'actions': ['github_notifications', 'github_repos', 'github_issues', 'github_create_issue', 'github_request'],
            'config_keys': ['token (GitHub PAT)'],
        },
        {
            'app': 'notion',
            'actions': ['notion_search', 'notion_read_page', 'notion_append', 'notion_create_page'],
            'config_keys': ['token (Notion Internal Integration Secret)'],
        },
        {
            'app': 'todoist',
            'actions': ['todoist_tasks', 'todoist_add_task', 'todoist_complete_task'],
            'config_keys': ['token (Todoist API Token)'],
        },
        {
            'app': 'messaging (telegram / feishu / dingtalk / discord / slack / bark)',
            'actions': ['send_message'],
            'config_keys': ['telegram_bot_token', 'telegram_chat_id', 'feishu_webhook', 'dingtalk_webhook', 'discord_webhook', 'slack_webhook', 'bark_url'],
        },
    ],
    'ecosystem_bridges_500_plus': [
        {
            'bridge': 'composio',
            'apps_count': '500+ (Gmail, Outlook, Google Calendar/Drive/Sheets/Docs, Slack, Notion, GitHub, Linear, Jira, Trello, X, Reddit, Spotify, YouTube, Stripe, Vercel, Cloudflare...)',
            'actions': ['search_actions', 'connect_oauth', 'execute'],
            'config_keys': ['api_key (Composio API Key, https://app.composio.dev)'],
        },
        {
            'bridge': 'aci',
            'apps_count': '600+ (ACI.dev Open-Source Tool Platform)',
            'actions': ['search_actions', 'execute'],
            'config_keys': ['api_key (ACI.dev API Key, https://platform.aci.dev)'],
        },
    ],
}


def _decode_mime_words(raw: str) -> str:
    if not raw:
        return ''
    parts = []
    for chunk, enc in decode_header(raw):
        if isinstance(chunk, bytes):
            parts.append(chunk.decode(enc or 'utf-8', errors='replace'))
        else:
            parts.append(str(chunk))
    return ''.join(parts).strip()


def _extract_email_body(msg: email.message.Message) -> str:
    plain_parts: list[str] = []
    html_parts: list[str] = []
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = str(part.get('Content-Disposition') or '')
            if 'attachment' in disp.lower():
                continue
            payload = part.get_payload(decode=True)
            if not payload:
                continue
            charset = part.get_content_charset() or 'utf-8'
            text = payload.decode(charset, errors='replace')
            if ctype == 'text/plain':
                plain_parts.append(text)
            elif ctype == 'text/html':
                html_parts.append(re.sub(r'<[^>]+>', ' ', text))
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or 'utf-8'
            text = payload.decode(charset, errors='replace')
            if msg.get_content_type() == 'text/html':
                html_parts.append(re.sub(r'<[^>]+>', ' ', text))
            else:
                plain_parts.append(text)
    body = '\n'.join(plain_parts).strip() or '\n'.join(html_parts).strip()
    return re.sub(r'\s+\n', '\n', body)[:8000]


class IntegrationHubPlugin:
    name = 'integration_hub'
    description = (
        '第三方应用与几百个接口统一中枢（支持 Gmail/Outlook/QQ/163 邮件收发、Google/Outlook/iCloud 日历、GitHub、Notion、Todoist、Telegram/飞书/Discord 推送，'
        '以及通过 Composio / ACI.dev 动态检索与调用 500+ 第三方应用接口；op: catalog|status|configure|search_actions|connect_oauth|execute）'
    )
    parameters = {
        'type': 'object',
        'properties': {
            'op': {
                'type': 'string',
                'description': '操作类型：catalog(查看支持的应用目录) | status(查看已配置账号) | configure(保存某应用凭据/密码/Token) | search_actions(在500+应用库中按关键词搜索接口) | connect_oauth(生成OAuth一键登录绑定链接) | execute(执行具体应用接口动作)',
            },
            'app': {
                'type': 'string',
                'description': '应用名称：email | gmail | outlook | calendar | github | notion | todoist | telegram | feishu | dingtalk | discord | slack | bark | composio | aci 或任意第三方应用名',
            },
            'action': {
                'type': 'string',
                'description': '具体动作名，如 email_list | email_read | email_send | calendar_list | github_notifications | github_issues | github_create_issue | github_request | notion_search | notion_read_page | notion_append | todoist_tasks | todoist_add_task | send_message，或 Composio/ACI 的 action 标识符',
            },
            'query': {
                'type': 'string',
                'description': '搜索关键词（用于 search_actions、email_list 搜索条件、notion_search 等）',
            },
            'config': {
                'type': 'object',
                'description': '当 op="configure" 时传入的凭据字典（如 {"provider":"gmail","address":"...","password":"..."} 或 {"token":"..."}）',
            },
            'params': {
                'type': 'object',
                'description': '当 op="execute" 时传给目标接口的参数字典（如 {"to":"...","subject":"...","body":"..."} 或 {"limit":10,"unread_only":true}）',
            },
        },
        'required': ['op'],
    }

    def _config_path(self, root: Path) -> Path:
        cfg_dir = root / '.config'
        cfg_dir.mkdir(parents=True, exist_ok=True)
        return cfg_dir / 'integrations.json'

    def _load_config(self, root: Path) -> dict[str, Any]:
        p = self._config_path(root)
        if not p.exists():
            return {}
        try:
            return json.loads(p.read_text('utf-8'))
        except Exception:
            return {}

    def _save_config(self, root: Path, data: dict[str, Any]) -> None:
        p = self._config_path(root)
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

    async def execute(self, args: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        root = Path(context.get('root') or '.')
        op = str(args.get('op') or 'catalog').strip().lower()
        app_name = str(args.get('app') or '').strip().lower()
        action = str(args.get('action') or '').strip()
        query = str(args.get('query') or '').strip()
        params = args.get('params') if isinstance(args.get('params'), dict) else {}
        cfg_all = self._load_config(root)

        if op == 'catalog':
            return {
                'catalog': CATALOG_SUMMARY,
                'configured_apps': [k for k, v in cfg_all.items() if v],
                'usage_tip': (
                    '1) 直连模式：用 op="configure", app="email|github|notion|todoist|calendar|telegram|feishu", config={...} 保存凭据后即可直接 op="execute"；'
                    '2) 500+ 应用网关模式：配置 app="composio" 或 app="aci" 的 api_key 后，可用 op="search_actions", query="gmail/outlook/slack/..." 搜索几百个应用的现成动作并一键发起 OAuth 绑定或执行。'
                ),
            }

        if op == 'status':
            masked = {}
            for k, v in cfg_all.items():
                if isinstance(v, dict):
                    masked[k] = {
                        sk: ('***' + str(sv)[-4:] if any(s in sk.lower() for s in ('key', 'token', 'password', 'secret')) and len(str(sv)) > 4 else sv)
                        for sk, sv in v.items()
                    }
                else:
                    masked[k] = 'configured'
            return {'configured': masked}

        if op == 'configure':
            new_cfg = args.get('config')
            if not app_name or not isinstance(new_cfg, dict):
                return {'error': 'configure 需要提供 app 名称与 config 字典'}
            norm_app = 'email' if app_name in ('gmail', 'outlook', 'qq', '163', 'icloud', 'imap', 'smtp') and 'address' in new_cfg else app_name
            if app_name in IMAP_PRESETS and 'provider' not in new_cfg:
                new_cfg['provider'] = app_name
            merged = dict(cfg_all.get(norm_app) or {})
            merged.update(new_cfg)
            cfg_all[norm_app] = merged
            self._save_config(root, cfg_all)
            return {'saved': True, 'app': norm_app, 'keys': list(merged.keys())}

        if op == 'search_actions':
            return await self._search_actions(query or app_name, cfg_all)

        if op == 'connect_oauth':
            return await self._connect_oauth(app_name, params, cfg_all)

        if op == 'execute':
            return await self._execute_action(app_name, action, query, params, cfg_all)

        return {'error': f'未知 op: {op}，可选 catalog | status | configure | search_actions | connect_oauth | execute'}

    async def _search_actions(self, keyword: str, cfg_all: dict[str, Any]) -> dict[str, Any]:
        kw = (keyword or '').strip().lower()
        builtin_hits = []
        for item in CATALOG_SUMMARY['direct_builtin']:
            if not kw or kw in item['app'].lower() or any(kw in a.lower() for a in item['actions']):
                builtin_hits.append(item)

        remote_hits: list[dict] = []
        composio_key = str((cfg_all.get('composio') or {}).get('api_key') or '').strip()
        if composio_key:
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    r = await client.get(
                        'https://backend.composio.dev/api/v2/actions/list/all',
                        params={'apps': kw if kw else None, 'useCase': kw if kw else None, 'limit': 15},
                        headers={'x-api-key': composio_key},
                    )
                    if r.status_code == 200:
                        items = r.json().get('items') or []
                        for it in items[:15]:
                            remote_hits.append({
                                'bridge': 'composio',
                                'action': it.get('name'),
                                'app': it.get('appName') or it.get('appKey'),
                                'description': str(it.get('description') or '')[:200],
                                'parameters': it.get('parameters'),
                            })
            except Exception as e:
                remote_hits.append({'bridge': 'composio', 'warning': str(e)[:160]})

        aci_key = str((cfg_all.get('aci') or {}).get('api_key') or '').strip()
        if aci_key:
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    r = await client.get(
                        'https://api.aci.dev/v1/functions/search',
                        params={'intent': kw or 'email calendar github notion', 'limit': 15},
                        headers={'Authorization': f'Bearer {aci_key}'},
                    )
                    if r.status_code == 200:
                        for it in (r.json() or [])[:15]:
                            remote_hits.append({
                                'bridge': 'aci',
                                'action': it.get('name'),
                                'description': str(it.get('description') or '')[:200],
                                'parameters': it.get('parameters'),
                            })
            except Exception as e:
                remote_hits.append({'bridge': 'aci', 'warning': str(e)[:160]})

        return {
            'query': keyword,
            'builtin_connectors': builtin_hits,
            'cloud_bridge_actions': remote_hits,
            'note': '如果尚未配置 Composio/ACI API Key，可直接使用 builtin_connectors（原生支持 Gmail/Outlook/QQ/163 邮箱、日历、GitHub、Notion、Todoist、Telegram/飞书/Discord），或填入 Composio/ACI Key 解锁 500+ 应用 OAuth 托管。',
        }

    async def _connect_oauth(self, app_name: str, params: dict[str, Any], cfg_all: dict[str, Any]) -> dict[str, Any]:
        composio_key = str((cfg_all.get('composio') or {}).get('api_key') or '').strip()
        if not composio_key:
            return {
                'error': '尚未配置 Composio API Key。',
                'alternative': (
                    f'如果要绑定 {app_name or "邮箱/应用"}：\n'
                    '方案 A（免 OAuth 直连，推荐）：直接提供邮箱地址 + 应用专用密码（Gmail App Password / Outlook / QQ / 163 授权码）或 GitHub/Notion Token，调用 op="configure" 保存即可立刻使用；\n'
                    '方案 B（500+ 应用 OAuth 托管）：在 https://app.composio.dev 免费获取 API Key 后调用 op="configure", app="composio", config={"api_key":"..."}，即可一键生成 Gmail/Outlook/Slack/Notion OAuth 登录链接。'
                ),
            }
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                r = await client.post(
                    'https://backend.composio.dev/api/v1/connectedAccounts',
                    headers={'x-api-key': composio_key},
                    json={'appName': app_name, **(params or {})},
                )
                return {'status_code': r.status_code, 'data': r.json()}
        except Exception as e:
            return {'error': str(e)[:240]}

    async def _execute_action(
        self, app_name: str, action: str, query: str, params: dict[str, Any], cfg_all: dict[str, Any]
    ) -> dict[str, Any]:
        act = action.lower().strip()
        # 1. Email (Gmail / Outlook / QQ / 163 / iCloud / Custom IMAP & SMTP)
        if app_name in ('email', 'gmail', 'outlook', 'qq', '163', 'icloud', 'imap', 'smtp') or act.startswith('email_'):
            email_cfg = dict(cfg_all.get(app_name) or cfg_all.get('email') or {})
            email_cfg.update({k: v for k, v in params.items() if k in ('provider', 'address', 'password', 'imap_host', 'imap_port', 'smtp_host', 'smtp_port')})
            if not email_cfg.get('address') or not email_cfg.get('password'):
                return {
                    'error': '邮箱凭据尚未配置。请先调用 integration_hub(op="configure", app="email", config={"provider":"gmail|outlook|qq|163", "address":"你的邮箱", "password":"应用专用密码/授权码"})。'
                }
            if act in ('email_list', 'list', ''):
                return await asyncio.to_thread(self._imap_list, email_cfg, params, query)
            if act in ('email_read', 'read'):
                return await asyncio.to_thread(self._imap_read, email_cfg, params)
            if act in ('email_send', 'send'):
                return await asyncio.to_thread(self._smtp_send, email_cfg, params)

        # 2. Calendar (ICS feed for Google Calendar / Outlook / Apple iCloud)
        if app_name in ('calendar', 'google_calendar', 'outlook_calendar', 'icloud_calendar') or act.startswith('calendar_'):
            cal_cfg = dict(cfg_all.get('calendar') or {})
            ics_url = str(params.get('ics_url') or cal_cfg.get('ics_url') or '').strip()
            if not ics_url:
                return {'error': '请先通过 op="configure", app="calendar", config={"ics_url":"https://..."} 配置日历 ICS 订阅链接（Google 日历/Outlook/iCloud 均支持导出私密 ICS 链接）。'}
            return await self._fetch_ics_events(ics_url, int(params.get('limit') or 15))

        # 3. GitHub REST API
        if app_name == 'github' or act.startswith('github_'):
            gh_cfg = dict(cfg_all.get('github') or {})
            token = str(params.get('token') or gh_cfg.get('token') or '').strip()
            return await self._github_action(act, token, params, query)

        # 4. Notion API
        if app_name == 'notion' or act.startswith('notion_'):
            nt_cfg = dict(cfg_all.get('notion') or {})
            token = str(params.get('token') or nt_cfg.get('token') or '').strip()
            return await self._notion_action(act, token, params, query)

        # 5. Todoist API
        if app_name == 'todoist' or act.startswith('todoist_'):
            td_cfg = dict(cfg_all.get('todoist') or {})
            token = str(params.get('token') or td_cfg.get('token') or '').strip()
            return await self._todoist_action(act, token, params)

        # 6. Instant Messaging / Webhooks (Telegram / Feishu / DingTalk / Discord / Slack / Bark)
        if app_name in ('telegram', 'feishu', 'dingtalk', 'discord', 'slack', 'bark', 'messaging') or act == 'send_message':
            return await self._webhook_or_im_send(app_name, params, cfg_all)

        # 7. Composio 500+ App Action Execution
        composio_key = str((cfg_all.get('composio') or {}).get('api_key') or '').strip()
        if composio_key and action:
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    r = await client.post(
                        f'https://backend.composio.dev/api/v2/actions/{action}/execute',
                        headers={'x-api-key': composio_key},
                        json={'input': params, 'appName': app_name or None},
                    )
                    return {'bridge': 'composio', 'action': action, 'status_code': r.status_code, 'result': r.json()}
            except Exception as e:
                return {'error': f'Composio 执行失败: {str(e)[:240]}'}

        # 8. ACI.dev 600+ App Function Execution
        aci_key = str((cfg_all.get('aci') or {}).get('api_key') or '').strip()
        if aci_key and action:
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    r = await client.post(
                        f'https://api.aci.dev/v1/functions/{action}/execute',
                        headers={'Authorization': f'Bearer {aci_key}'},
                        json={'function_input': params},
                    )
                    return {'bridge': 'aci', 'action': action, 'status_code': r.status_code, 'result': r.json()}
            except Exception as e:
                return {'error': f'ACI 执行失败: {str(e)[:240]}'}

        return {'error': f'未找到对应的内置执行器或尚未配置 {app_name}/{action} 的凭据。可先调用 op="catalog" 查看支持列表。'}

    def _resolve_mail_hosts(self, cfg: dict[str, Any]) -> dict[str, Any]:
        addr = str(cfg.get('address') or '').strip()
        domain = addr.split('@')[-1].lower() if '@' in addr else ''
        provider = str(cfg.get('provider') or '').strip().lower()
        if not provider:
            if 'gmail.com' in domain:
                provider = 'gmail'
            elif any(d in domain for d in ('outlook.', 'hotmail.', 'live.', 'office365.')):
                provider = 'outlook'
            elif 'qq.com' in domain or 'foxmail.com' in domain:
                provider = 'qq'
            elif '163.com' in domain or '126.com' in domain:
                provider = '163'
            elif 'icloud.com' in domain or 'me.com' in domain:
                provider = 'icloud'
        preset = dict(IMAP_PRESETS.get(provider, {}))
        preset.update({k: v for k, v in cfg.items() if v})
        return preset

    def _imap_list(self, cfg: dict[str, Any], params: dict[str, Any], query: str) -> dict[str, Any]:
        resolved = self._resolve_mail_hosts(cfg)
        host = resolved.get('imap_host')
        port = int(resolved.get('imap_port') or 993)
        if not host:
            return {'error': '无法识别 IMAP 服务器地址，请在 config 中指定 imap_host'}
        folder = str(params.get('folder') or 'INBOX')
        limit = max(1, min(30, int(params.get('limit') or 10)))
        unread_only = bool(params.get('unread_only', False))
        conn = imaplib.IMAP4_SSL(host, port, timeout=20)
        try:
            conn.login(str(resolved['address']), str(resolved['password']))
            conn.select(folder, readonly=True)
            criterion = 'UNSEEN' if unread_only else 'ALL'
            _, data = conn.search(None, criterion)
            ids = (data[0] or b'').split()
            scan_cap = max(limit, min(80, limit * 5)) if query else limit
            recent_ids = list(reversed(ids[-scan_cap:]))
            emails = []
            for mid in recent_ids:
                _, msg_data = conn.fetch(mid, '(BODY.PEEK[HEADER.FIELDS (SUBJECT FROM DATE)])')
                raw_hdr = b''
                for item in msg_data:
                    if isinstance(item, tuple) and len(item) >= 2:
                        raw_hdr += item[1]
                msg = email.message_from_bytes(raw_hdr)
                subj = _decode_mime_words(msg.get('Subject', ''))
                sender = _decode_mime_words(msg.get('From', ''))
                date_str = str(msg.get('Date', '')).strip()
                if query and query.lower() not in (subj + ' ' + sender).lower():
                    continue
                emails.append({
                    'id': mid.decode('ascii', errors='ignore'),
                    'subject': subj,
                    'from': sender,
                    'date': date_str,
                })
                if len(emails) >= limit:
                    break
            return {'address': resolved['address'], 'total_in_box': len(ids), 'messages': emails}
        finally:
            try:
                conn.logout()
            except Exception:
                pass

    def _imap_read(self, cfg: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
        resolved = self._resolve_mail_hosts(cfg)
        host = resolved.get('imap_host')
        port = int(resolved.get('imap_port') or 993)
        msg_id = str(params.get('id') or params.get('message_id') or '').strip()
        if not msg_id:
            return {'error': 'email_read 需要传入 params={"id": "邮件ID"}'}
        conn = imaplib.IMAP4_SSL(host, port, timeout=20)
        try:
            conn.login(str(resolved['address']), str(resolved['password']))
            conn.select(str(params.get('folder') or 'INBOX'), readonly=True)
            _, msg_data = conn.fetch(msg_id.encode('ascii'), '(RFC822)')
            raw_bytes = b''
            for item in msg_data:
                if isinstance(item, tuple) and len(item) >= 2:
                    raw_bytes += item[1]
            msg = email.message_from_bytes(raw_bytes)
            return {
                'id': msg_id,
                'subject': _decode_mime_words(msg.get('Subject', '')),
                'from': _decode_mime_words(msg.get('From', '')),
                'to': _decode_mime_words(msg.get('To', '')),
                'date': str(msg.get('Date', '')).strip(),
                'body': _extract_email_body(msg),
            }
        finally:
            try:
                conn.logout()
            except Exception:
                pass

    def _smtp_send(self, cfg: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
        resolved = self._resolve_mail_hosts(cfg)
        host = resolved.get('smtp_host')
        port = int(resolved.get('smtp_port') or 465)
        to_addr = str(params.get('to') or '').strip()
        subject = str(params.get('subject') or '').strip()
        body = str(params.get('body') or params.get('text') or '').strip()
        if not to_addr or not subject:
            return {'error': 'email_send 需要 params={"to": "收件人", "subject": "主题", "body": "正文"}'}
        msg = EmailMessage()
        msg['From'] = str(resolved['address'])
        msg['To'] = to_addr
        msg['Subject'] = subject
        msg.set_content(body)
        if resolved.get('starttls') or port == 587:
            with smtplib.SMTP(host, port, timeout=20) as server:
                server.starttls(context=ssl.create_default_context())
                server.login(str(resolved['address']), str(resolved['password']))
                server.send_message(msg)
        else:
            with smtplib.SMTP_SSL(host, port, timeout=20, context=ssl.create_default_context()) as server:
                server.login(str(resolved['address']), str(resolved['password']))
                server.send_message(msg)
        return {'sent': True, 'from': resolved['address'], 'to': to_addr, 'subject': subject}

    async def _fetch_ics_events(self, ics_url: str, limit: int = 15) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(ics_url)
            r.raise_for_status()
            text = r.text
        events = []
        for block in text.split('BEGIN:VEVENT')[1:]:
            end_idx = block.find('END:VEVENT')
            chunk = block[:end_idx] if end_idx >= 0 else block
            summary = re.search(r'^SUMMARY(?:;[^:]*)?:(.*)$', chunk, re.M)
            dtstart = re.search(r'^DTSTART(?:;[^:]*)?:(.*)$', chunk, re.M)
            dtend = re.search(r'^DTEND(?:;[^:]*)?:(.*)$', chunk, re.M)
            loc = re.search(r'^LOCATION(?:;[^:]*)?:(.*)$', chunk, re.M)
            events.append({
                'summary': summary.group(1).strip() if summary else '（无标题）',
                'start': dtstart.group(1).strip() if dtstart else '',
                'end': dtend.group(1).strip() if dtend else '',
                'location': loc.group(1).strip() if loc else '',
            })
        events.sort(key=lambda x: x.get('start', ''), reverse=True)
        return {'events_count': len(events), 'events': events[:limit]}

    async def _github_action(self, act: str, token: str, params: dict[str, Any], query: str) -> dict[str, Any]:
        headers = {'Accept': 'application/vnd.github+json', 'User-Agent': 'ProactivePersonalAgent/2.8'}
        if token:
            headers['Authorization'] = f'Bearer {token}'
        repo = str(params.get('repo') or '').strip()
        async with httpx.AsyncClient(timeout=20, headers=headers) as client:
            if act in ('github_notifications', 'notifications'):
                r = await client.get('https://api.github.com/notifications', params={'per_page': int(params.get('limit') or 15)})
                return {'status_code': r.status_code, 'notifications': r.json()}
            if act in ('github_repos', 'repos'):
                url = f"https://api.github.com/users/{params['owner']}/repos" if params.get('owner') else 'https://api.github.com/user/repos'
                r = await client.get(url, params={'sort': 'updated', 'per_page': int(params.get('limit') or 15)})
                data = r.json()
                if isinstance(data, list):
                    return {'repos': [{'full_name': x.get('full_name'), 'stars': x.get('stargazers_count'), 'updated_at': x.get('updated_at'), 'description': x.get('description')} for x in data]}
                return {'status_code': r.status_code, 'data': data}
            if act in ('github_issues', 'issues') and repo:
                r = await client.get(f'https://api.github.com/repos/{repo}/issues', params={'state': params.get('state', 'open'), 'per_page': int(params.get('limit') or 15)})
                data = r.json()
                if isinstance(data, list):
                    return {'issues': [{'number': x.get('number'), 'title': x.get('title'), 'state': x.get('state'), 'user': (x.get('user') or {}).get('login'), 'url': x.get('html_url')} for x in data]}
                return {'status_code': r.status_code, 'data': data}
            if act in ('github_create_issue', 'create_issue') and repo:
                r = await client.post(f'https://api.github.com/repos/{repo}/issues', json={'title': params.get('title'), 'body': params.get('body', '')})
                return {'status_code': r.status_code, 'issue': r.json()}
            method = str(params.get('method') or 'GET').upper()
            endpoint = str(params.get('endpoint') or '/user').strip()
            if not endpoint.startswith('http'):
                endpoint = 'https://api.github.com/' + endpoint.lstrip('/')
            r = await client.request(method, endpoint, json=params.get('body') if method != 'GET' else None, params=params.get('query'))
            return {'status_code': r.status_code, 'data': r.json() if 'json' in r.headers.get('content-type', '') else r.text[:2000]}

    async def _notion_action(self, act: str, token: str, params: dict[str, Any], query: str) -> dict[str, Any]:
        if not token:
            return {'error': '请先配置 Notion Token：op="configure", app="notion", config={"token":"ntn_..."}'}
        headers = {
            'Authorization': f'Bearer {token}',
            'Notion-Version': '2022-06-28',
            'Content-Type': 'application/json',
        }
        async with httpx.AsyncClient(timeout=20, headers=headers) as client:
            if act in ('notion_search', 'search', ''):
                q = query or str(params.get('query') or '')
                r = await client.post('https://api.notion.com/v1/search', json={'query': q, 'page_size': int(params.get('limit') or 10)})
                return {'status_code': r.status_code, 'data': r.json()}
            if act in ('notion_read_page', 'read_page'):
                block_id = str(params.get('page_id') or params.get('block_id') or '').strip()
                r = await client.get(f'https://api.notion.com/v1/blocks/{block_id}/children')
                return {'status_code': r.status_code, 'data': r.json()}
            if act in ('notion_append', 'append'):
                block_id = str(params.get('page_id') or params.get('block_id') or '').strip()
                text = str(params.get('text') or '').strip()
                children = [{'object': 'block', 'type': 'paragraph', 'paragraph': {'rich_text': [{'type': 'text', 'text': {'content': text}}]}}]
                r = await client.patch(f'https://api.notion.com/v1/blocks/{block_id}/children', json={'children': children})
                return {'status_code': r.status_code, 'data': r.json()}
            if act in ('notion_create_page', 'create_page'):
                parent_id = str(params.get('parent_page_id') or params.get('page_id') or params.get('database_id') or '').strip()
                title = str(params.get('title') or query or '新建页面').strip()
                text = str(params.get('text') or params.get('content') or '').strip()
                if not parent_id:
                    return {'error': 'notion_create_page 需要提供 parent_page_id 或 database_id'}
                parent_obj = {'database_id': parent_id} if params.get('database_id') else {'page_id': parent_id}
                children = [{'object': 'block', 'type': 'paragraph', 'paragraph': {'rich_text': [{'type': 'text', 'text': {'content': text}}]}}] if text else []
                body_json = {
                    'parent': parent_obj,
                    'properties': {'title': {'title': [{'type': 'text', 'text': {'content': title}}]}},
                    'children': children,
                }
                r = await client.post('https://api.notion.com/v1/pages', json=body_json)
                return {'status_code': r.status_code, 'data': r.json()}
            return {'error': f'未知 Notion 动作: {act}'}

    async def _todoist_action(self, act: str, token: str, params: dict[str, Any]) -> dict[str, Any]:
        if not token:
            return {'error': '请先配置 Todoist API Token：op="configure", app="todoist", config={"token":"..."}'}
        headers = {'Authorization': f'Bearer {token}'}
        async with httpx.AsyncClient(timeout=20, headers=headers) as client:
            if act in ('todoist_tasks', 'list', ''):
                r = await client.get('https://api.todoist.com/rest/v2/tasks')
                return {'status_code': r.status_code, 'tasks': r.json()[:30] if isinstance(r.json(), list) else r.json()}
            if act in ('todoist_add_task', 'add'):
                r = await client.post('https://api.todoist.com/rest/v2/tasks', json={'content': params.get('content') or params.get('title'), 'due_string': params.get('due_string')})
                return {'status_code': r.status_code, 'task': r.json()}
            if act in ('todoist_complete_task', 'complete'):
                tid = params.get('task_id') or params.get('id')
                r = await client.post(f'https://api.todoist.com/rest/v2/tasks/{tid}/close')
                return {'status_code': r.status_code, 'completed': r.status_code == 204}
            return {'error': f'未知 Todoist 动作: {act}'}

    async def _webhook_or_im_send(self, app_name: str, params: dict[str, Any], cfg_all: dict[str, Any]) -> dict[str, Any]:
        im_cfg = dict(cfg_all.get('messaging') or {})
        im_cfg.update(cfg_all.get(app_name) or {})
        text = str(params.get('text') or params.get('message') or '').strip()
        if not text:
            return {'error': 'send_message 需要 params={"text": "消息内容"}'}
        async with httpx.AsyncClient(timeout=15) as client:
            if app_name == 'telegram' or im_cfg.get('telegram_bot_token'):
                bot_tok = str(params.get('bot_token') or im_cfg.get('telegram_bot_token') or '').strip()
                chat_id = str(params.get('chat_id') or im_cfg.get('telegram_chat_id') or '').strip()
                r = await client.post(f'https://api.telegram.org/bot{bot_tok}/sendMessage', json={'chat_id': chat_id, 'text': text})
                return {'status_code': r.status_code, 'result': r.json()}
            bark_url = str(params.get('bark_url') or im_cfg.get('bark_url') or '').strip()
            if app_name == 'bark' or bark_url:
                if not bark_url:
                    return {'error': '尚未配置 bark_url'}
                title = str(params.get('title') or 'DSB 通知').strip()
                r = await client.post(bark_url.rstrip('/'), json={'title': title, 'body': text})
                return {'status_code': r.status_code, 'text': r.text[:400]}
            webhook = str(
                params.get('webhook')
                or im_cfg.get(f'{app_name}_webhook')
                or im_cfg.get('webhook')
                or ''
            ).strip()
            if webhook:
                if app_name == 'discord' or 'discord.com/api/webhooks' in webhook:
                    payload = {'content': text}
                elif app_name == 'dingtalk' or 'oapi.dingtalk.com' in webhook:
                    payload = {'msgtype': 'text', 'text': {'content': text}}
                elif app_name == 'feishu' or 'open.feishu.cn' in webhook or 'open.larksuite.com' in webhook:
                    payload = {'msg_type': 'text', 'content': {'text': text}}
                else:
                    payload = {'msg_type': 'text', 'msgtype': 'text', 'content': {'text': text}, 'text': text}
                r = await client.post(webhook, json=payload)
                return {'status_code': r.status_code, 'text': r.text[:400]}
            return {'error': f'尚未配置 {app_name} 的 webhook 或 bot_token'}


def register(registry) -> None:
    registry.register_tool(IntegrationHubPlugin())
