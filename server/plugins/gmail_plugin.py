"""Gmail & Email Connector Plugin (`server/plugins/gmail_plugin.py`).

Registered automatically via `ExtensionRegistry`.
Supports:
- Google Gmail OAuth2 (`access_token` / `refresh_token`) AND standard IMAP/SMTP (`imap.gmail.com:993`)
- Searching billing notifications (e.g., Bybit, OpenAI, Stripe), unread emails, and extracting OTP verification codes
- Direct Gmail web links (`https://mail.google.com/mail/u/0/#all/<id>`)
"""
from __future__ import annotations

import asyncio
import base64
import email
import imaplib
import json
import re
import smtplib
from email.header import decode_header
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Dict

import httpx


class GmailToolPlugin:
    name = 'gmail_connector'
    description = (
        'Gmail 与通用邮箱插件工具（action: status|configure|search|read|latest_codes|send；'
        '支持 Gmail OAuth2 Token 或 IMAP 授权，可自动搜索账单通知如 Bybit/OpenAI、提取最新验证码、阅读或发送邮件）'
    )
    parameters = {
        'type': 'object',
        'properties': {
            'action': {'type': 'string'},
            'query': {'type': 'string'},
            'message_id': {'type': 'string'},
            'to': {'type': 'string'},
            'subject': {'type': 'string'},
            'body': {'type': 'string'},
            'email_address': {'type': 'string'},
            'app_password': {'type': 'string'},
            'oauth_token': {'type': 'string'},
            'refresh_token': {'type': 'string'},
            'client_id': {'type': 'string'},
            'client_secret': {'type': 'string'},
            'imap_host': {'type': 'string'},
            'limit': {'type': 'integer'},
        },
        'required': ['action'],
        'additionalProperties': False,
    }

    @staticmethod
    def _config_path(root: Path) -> Path:
        cfg_dir = root / '.config'
        cfg_dir.mkdir(parents=True, exist_ok=True)
        return cfg_dir / 'gmail_auth.json'

    @classmethod
    def _load_cfg(cls, root: Path) -> dict:
        p = cls._config_path(root)
        if not p.exists():
            return {}
        try:
            return json.loads(p.read_text('utf-8'))
        except Exception:
            return {}

    @classmethod
    def _save_cfg(cls, root: Path, data: dict) -> None:
        p = cls._config_path(root)
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

    @staticmethod
    def _decode_mime_str(val: str | None) -> str:
        if not val:
            return ''
        parts = decode_header(val)
        out = []
        for content, enc in parts:
            if isinstance(content, bytes):
                out.append(content.decode(enc or 'utf-8', errors='replace'))
            else:
                out.append(str(content))
        return ''.join(out)

    async def _refresh_oauth_if_needed(self, root: Path, cfg: dict) -> str:
        token = str(cfg.get('oauth_token') or '')
        if cfg.get('refresh_token') and cfg.get('client_id') and cfg.get('client_secret'):
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    r = await client.post(
                        'https://oauth2.googleapis.com/token',
                        data={
                            'client_id': cfg['client_id'],
                            'client_secret': cfg['client_secret'],
                            'refresh_token': cfg['refresh_token'],
                            'grant_type': 'refresh_token',
                        },
                    )
                if r.status_code == 200:
                    new_tok = r.json().get('access_token')
                    if new_tok:
                        cfg['oauth_token'] = new_tok
                        self._save_cfg(root, cfg)
                        return new_tok
            except Exception:
                pass
        return token

    async def execute(self, args: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        root = Path(context.get('root') or '.')
        action = str(args.get('action') or 'status').strip().lower()
        cfg = self._load_cfg(root)

        if action == 'status':
            has_oauth = bool(cfg.get('oauth_token') or cfg.get('refresh_token'))
            has_imap = bool(cfg.get('email_address') and cfg.get('app_password'))
            return {
                'configured': has_oauth or has_imap,
                'mode': 'gmail_oauth' if has_oauth else ('imap' if has_imap else 'unconfigured'),
                'email_address': cfg.get('email_address', ''),
                'setup_hint': (
                    '可通过 gmail_connector(action="configure", oauth_token="...") 配置 Google OAuth2 授权，'
                    '或传入 email_address="xxx@gmail.com", app_password="16位应用专用密码" 启用 IMAP/SMTP。'
                ),
            }

        if action == 'configure':
            for k in ('email_address', 'app_password', 'oauth_token', 'refresh_token', 'client_id', 'client_secret', 'imap_host'):
                if args.get(k):
                    cfg[k] = str(args[k]).strip()
            self._save_cfg(root, cfg)
            return {'configured': True, 'email_address': cfg.get('email_address', ''), 'mode': 'gmail_oauth' if cfg.get('oauth_token') else 'imap'}

        limit = max(1, min(20, int(args.get('limit') or 8)))
        query = str(args.get('query') or '').strip()

        # Mode A: Gmail Official REST API (OAuth2)
        if cfg.get('oauth_token') or cfg.get('refresh_token'):
            token = await self._refresh_oauth_if_needed(root, cfg)
            headers = {'Authorization': f'Bearer {token}'}
            async with httpx.AsyncClient(timeout=20) as client:
                if action in ('search', 'list', 'latest_codes'):
                    q_str = query or ('verification OR code OR 验证码 OR 账单 OR Bybit OR OpenAI' if action == 'latest_codes' else 'in:inbox')
                    r = await client.get(
                        'https://gmail.googleapis.com/gmail/v1/users/me/messages',
                        headers=headers,
                        params={'q': q_str, 'maxResults': limit},
                    )
                    if r.status_code >= 400:
                        return {'error': f'Gmail API 返回 HTTP {r.status_code}: {r.text[:240]}'}
                    msgs = r.json().get('messages') or []
                    results = []
                    for m_meta in msgs[:limit]:
                        mid = m_meta['id']
                        mr = await client.get(
                            f'https://gmail.googleapis.com/gmail/v1/users/me/messages/{mid}',
                            headers=headers,
                            params={'format': 'full'},
                        )
                        if mr.status_code != 200:
                            continue
                        m_data = mr.json()
                        hdrs = {h['name'].lower(): h['value'] for h in (m_data.get('payload', {}).get('headers') or [])}
                        snippet = m_data.get('snippet', '')
                        codes = re.findall(r'\b\d{4,8}\b', snippet)
                        results.append({
                            'id': mid,
                            'subject': hdrs.get('subject', ''),
                            'from': hdrs.get('from', ''),
                            'date': hdrs.get('date', ''),
                            'snippet': snippet,
                            'extracted_codes': codes[:3],
                            'web_link': f'https://mail.google.com/mail/u/0/#all/{mid}',
                        })
                    return {'count': len(results), 'query': q_str, 'emails': results}
                if action == 'read':
                    mid = str(args.get('message_id') or query or '').strip()
                    if not mid:
                        return {'error': 'read 动作需要提供 message_id'}
                    mr = await client.get(
                        f'https://gmail.googleapis.com/gmail/v1/users/me/messages/{mid}',
                        headers=headers,
                        params={'format': 'full'},
                    )
                    if mr.status_code >= 400:
                        return {'error': f'Gmail API 返回 HTTP {mr.status_code}: {mr.text[:240]}'}
                    m_data = mr.json()
                    hdrs = {h['name'].lower(): h['value'] for h in (m_data.get('payload', {}).get('headers') or [])}
                    snippet = m_data.get('snippet', '')
                    return {
                        'id': mid,
                        'subject': hdrs.get('subject', ''),
                        'from': hdrs.get('from', ''),
                        'date': hdrs.get('date', ''),
                        'snippet': snippet,
                        'web_link': f'https://mail.google.com/mail/u/0/#all/{mid}',
                    }
                if action == 'send':
                    to_addr = str(args.get('to') or '').strip()
                    subj = str(args.get('subject') or '').strip()
                    body = str(args.get('body') or '').strip()
                    if not to_addr or not body:
                        return {'error': '发送邮件需提供 to 和 body'}
                    msg = EmailMessage()
                    msg['To'] = to_addr
                    msg['Subject'] = subj
                    msg.set_content(body)
                    raw_b64 = base64.urlsafe_b64encode(msg.as_bytes()).decode('ascii')
                    sr = await client.post(
                        'https://gmail.googleapis.com/gmail/v1/users/me/messages/send',
                        headers=headers,
                        json={'raw': raw_b64},
                    )
                    if sr.status_code >= 400:
                        return {'error': f'Gmail 发送失败 HTTP {sr.status_code}: {sr.text[:240]}'}
                    return {'sent': True, 'to': to_addr, 'subject': subj, 'id': sr.json().get('id', '')}

        # Mode B: Standard IMAP / SMTP
        if cfg.get('email_address') and cfg.get('app_password'):
            imap_host = str(cfg.get('imap_host') or 'imap.gmail.com')
            email_addr = str(cfg['email_address'])
            app_pw = str(cfg['app_password'])

            if action == 'send':
                to_addr = str(args.get('to') or '').strip()
                subj = str(args.get('subject') or '').strip()
                body = str(args.get('body') or '').strip()
                if not to_addr or not body:
                    return {'error': '发送邮件需提供 to 和 body'}

                def _smtp_send():
                    msg = EmailMessage()
                    msg['Subject'] = subj
                    msg['From'] = email_addr
                    msg['To'] = to_addr
                    msg.set_content(body)
                    smtp_host = imap_host.replace('imap.', 'smtp.')
                    with smtplib.SMTP_SSL(smtp_host, 465, timeout=15) as s:
                        s.login(email_addr, app_pw)
                        s.send_message(msg)
                    return {'sent': True, 'to': to_addr, 'subject': subj}

                return await asyncio.to_thread(_smtp_send)

            def _imap_fetch():
                with imaplib.IMAP4_SSL(imap_host, 993) as M:
                    M.login(email_addr, app_pw)
                    M.select('INBOX', readonly=True)
                    target_mid = str(args.get('message_id') or '').strip()
                    if action == 'read' and target_mid:
                        ids = [target_mid.encode('ascii', errors='ignore')]
                    else:
                        typ, data = M.search(None, 'ALL')
                        scan_cap = max(limit, min(60, limit * 5)) if query else limit
                        ids = (data[0] or b'').split()[-scan_cap:]
                    items = []
                    for num in reversed(ids):
                        _, msg_data = M.fetch(num, '(RFC822)')
                        if not msg_data or not isinstance(msg_data[0], tuple):
                            continue
                        em = email.message_from_bytes(msg_data[0][1])
                        subj = self._decode_mime_str(em.get('Subject'))
                        sender = self._decode_mime_str(em.get('From'))
                        date_s = str(em.get('Date') or '')
                        body_txt = ''
                        if em.is_multipart():
                            for part in em.walk():
                                if part.get_content_type() == 'text/plain':
                                    payload = part.get_payload(decode=True)
                                    if payload:
                                        body_txt = payload.decode(part.get_content_charset() or 'utf-8', errors='replace')
                                        break
                        else:
                            payload = em.get_payload(decode=True)
                            if payload:
                                body_txt = payload.decode(em.get_content_charset() or 'utf-8', errors='replace')
                        hay = f'{subj}\n{sender}\n{body_txt}'
                        if query and action != 'read' and query.lower() not in hay.lower():
                            continue
                        codes = re.findall(r'\b\d{4,8}\b', f'{subj} {body_txt[:600]}')
                        items.append({
                            'id': num.decode('ascii', errors='ignore'),
                            'subject': subj,
                            'from': sender,
                            'date': date_s,
                            'snippet': body_txt.strip()[:2000 if action == 'read' else 400],
                            'extracted_codes': codes[:3],
                        })
                        if len(items) >= limit:
                            break
                    return {'count': len(items), 'query': query, 'emails': items}

            return await asyncio.to_thread(_imap_fetch)

        return {
            'configured': False,
            'error': '邮箱插件尚未配置授权凭证。请先调用 gmail_connector(action="configure", ...) 配置 Google OAuth2 Token 或 IMAP 应用专用密码。',
        }


def register(registry) -> None:
    registry.register_tool(GmailToolPlugin())
