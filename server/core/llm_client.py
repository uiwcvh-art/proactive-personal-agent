"""LLM Client Engine with strict separation between UI-Streaming and Silent Background Calls.

Features:
1. Prevents background tasks (sleep-time memory consolidation, 200K context compaction,
   vision UI tree extraction) from ever leaking internal JSON or state into foreground chat streams.
2. Fixes the Python empty-dict `{}` falsy trap on SSE final chunks (`delta: {}` must NEVER fall back to `message`).
3. Automatic transient error retry & model failover (handles 429 / 502 / 503 / 504 / network jitter).
4. Requests `stream_options: {"include_usage": True}` on SSE streams so upstream gateways (GOAT / DeepSeek / OpenAI)
   emit real `prompt_tokens` and `cached_tokens` (`prompt_tokens_details.cached_tokens` / `prompt_cache_hit_tokens`),
   and prevents small background calls from overwriting the foreground conversation's real context token count.
"""
from __future__ import annotations

import asyncio
import json
import re
import secrets
import time
import httpx


class LLMClient:
    def __init__(
        self,
        store,
        get_assistant_fn=None,
        last_prompt_tokens: dict[str, int] | None = None,
        last_cached_tokens: dict[str, int] | None = None,
    ):
        self.store = store
        self.get_assistant_fn = get_assistant_fn
        self.last_prompt_tokens: dict[str, int] = last_prompt_tokens if last_prompt_tokens is not None else {}
        self.last_cached_tokens: dict[str, int] = last_cached_tokens if last_cached_tokens is not None else {}
        self.last_metrics: dict[str, dict] = {}
        try:
            saved_m = self.store.state.get('last_metrics')
            if isinstance(saved_m, dict):
                for k, v in saved_m.items():
                    if isinstance(v, dict):
                        self.last_metrics[str(k)] = dict(v)
        except Exception:
            pass
        self._models_cache: tuple[float, str, list[str]] | None = None

    KNOWN_XML_TOOLS = (
        'cloud_desktop', 'phone_workspace', 'read_file', 'write_file', 'edit_file',
        'list_dir', 'glob_files', 'grep_search', 'run_bash', 'run_command',
        'bg_task', 'watchdog_task', 'conversation_search', 'ctx_search',
        'present_choices', 'memory_pin', 'blackboard', 'custom_tool',
        'phone_macro', 'send_message', 'manage_bot', 'list_models',
        'schedule_task', 'memory_search', 'notify_user', 'emergency_alarm',
        'web_fetch', 'web_search', 'integration_hub',
    )
    _KNOWN_XML_TOOLS_ALT = '|'.join(KNOWN_XML_TOOLS)
    _STRING_ONLY_PARAMS = {
        'text', 'content', 'old', 'new', 'path', 'url', 'uri', 'query', 'goal',
        'target', 'ref', 'app', 'package', 'command', 'script', 'code', 'key',
        'name', 'title', 'body', 'reason', 'prompt', 'at_time', 'daily_time',
        'weekdays', 'task_id', 'bot_id', 'to_bot_id', 'ctx_id', 'pin_id',
        'label', 'direction', 'engine', 'source',
    }

    @classmethod
    def _coerce_xml_param(cls, p_name: str, raw_val: str, is_str_flag: str = ''):
        if is_str_flag == 'true':
            return raw_val
        if p_name in cls._STRING_ONLY_PARAMS and not raw_val.startswith(('[', '{')):
            return raw_val
        if (
            is_str_flag == 'false'
            or raw_val.startswith(('[', '{'))
            or raw_val in ('true', 'false', 'null')
            or re.fullmatch(r'-?\d+(?:\.\d+)?', raw_val)
        ):
            try:
                return json.loads(raw_val)
            except Exception:
                pass
        return raw_val

    @classmethod
    def _parse_xml_kv_and_children(cls, attrs_str: str, body_str: str) -> dict:
        args_dict: dict = {}
        if attrs_str:
            for m_attr in re.finditer(r'([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*(?:"([^"]*)"|\'([^\']*)\')', attrs_str):
                k = m_attr.group(1).strip()
                v = m_attr.group(2) if m_attr.group(2) is not None else (m_attr.group(3) or '')
                if k:
                    args_dict[k] = cls._coerce_xml_param(k, v.strip())
        if body_str:
            for m_child in re.finditer(r'<([a-zA-Z_][a-zA-Z0-9_]*)[^>]*>([\s\S]*?)</\s*\1\s*>', body_str):
                k = m_child.group(1).strip()
                v = (m_child.group(2) or '').strip()
                if k:
                    args_dict[k] = cls._coerce_xml_param(k, v)
        return args_dict

    @classmethod
    def strip_streaming_dsml_tail(cls, text: str) -> str:
        """Hides any in-progress DeepSeek DSML / XML function-call block from live UI stream."""
        if not text:
            return ''
        m = re.search(
            r'(?<!`)<(?:[|｜]{1,2}\s*DSML[|｜\s]*(?:function_calls|calls|invoke)\b|function_calls\b|antml:function_calls\b|invoke\s+name=)',
            text,
            flags=re.IGNORECASE,
        )
        if m:
            return text[:m.start()].rstrip()
        m_pseudo = re.search(
            rf'(?<!`)<(?:{cls._KNOWN_XML_TOOLS_ALT})(?:\s|["\'>/]|$)',
            text,
            flags=re.IGNORECASE,
        )
        if m_pseudo:
            return text[:m_pseudo.start()].rstrip()
        # Also hide trailing partial opening tag like `<｜` or `<｜｜` at the very end of stream
        m_tail = re.search(r'(?<!`)<[|｜]{1,2}(?:DSML[|｜\s]*)?[A-Za-z_]*$', text)
        if m_tail:
            return text[:m_tail.start()].rstrip()
        return text

    @classmethod
    def parse_and_strip_dsml_calls(cls, text: str) -> tuple[str, list[dict]]:
        """
        Parses leaked DeepSeek native DSML tool calls (`<｜DSML｜...>` or `<｜｜DSML｜｜ calls>`)
        AND pseudo-XML tool calls (`<cloud_desktop op="..."/>` or `<cloud_desktop">\\n<op>...</op>\\n</cloud_desktop>`)
        into standard OpenAI `tool_calls` dicts and strips the raw tags (and any post-tag hallucinated steps) from `content`.
        """
        if not text:
            return text, []
        has_dsml = ('DSML' in text or '<function_calls>' in text or '<invoke' in text)
        has_pseudo = bool(re.search(rf'(?<!`)<(?:{cls._KNOWN_XML_TOOLS_ALT})(?:\s|["\'>/]|$)', text, flags=re.IGNORECASE))
        if not has_dsml and not has_pseudo:
            return text, []

        extracted_calls: list[dict] = []
        first_tag_pos: int | None = None

        def _mark_pos(pos: int):
            nonlocal first_tag_pos
            if first_tag_pos is None or pos < first_tag_pos:
                first_tag_pos = pos

        if has_dsml:
            m_outer = re.search(
                r'(?<!`)<(?:[|｜\s]*DSML[|｜\s]*(?:function_calls|calls|invoke)\b|function_calls\b|invoke\s+name=)',
                text,
                flags=re.IGNORECASE,
            )
            if m_outer:
                _mark_pos(m_outer.start())
            inv_pattern = re.compile(
                r'<[|｜\s]*(?:DSML[|｜\s]*)?invoke\s+name=["\']([^"\']+)["\'][^>]*>([\s\S]*?)</[|｜\s]*(?:DSML[|｜\s]*)?invoke\s*>',
                flags=re.IGNORECASE,
            )
            param_pattern = re.compile(
                r'<[|｜\s]*(?:DSML[|｜\s]*)?parameter\s+name=["\']([^"\']+)["\'](?:\s+string=["\']([^"\']+)["\'])?[^>]*>([\s\S]*?)</[|｜\s]*(?:DSML[|｜\s]*)?parameter\s*>',
                flags=re.IGNORECASE,
            )
            for idx, m_inv in enumerate(inv_pattern.finditer(text)):
                _mark_pos(m_inv.start())
                fn_name = (m_inv.group(1) or '').strip()
                body = m_inv.group(2) or ''
                if not fn_name:
                    continue
                args_dict: dict = {}
                for m_p in param_pattern.finditer(body):
                    p_name = (m_p.group(1) or '').strip()
                    is_str_flag = (m_p.group(2) or '').strip().lower()
                    raw_val = (m_p.group(3) or '').strip()
                    if not p_name:
                        continue
                    args_dict[p_name] = cls._coerce_xml_param(p_name, raw_val, is_str_flag)
                extracted_calls.append({
                    'id': f'call_dsml_{idx}_{secrets.token_hex(4)}',
                    'type': 'function',
                    'function': {
                        'name': fn_name,
                        'arguments': json.dumps(args_dict, ensure_ascii=False),
                    },
                })

        if has_pseudo:
            # 1. Paired block tags: <cloud_desktop ...>...</cloud_desktop> or <cloud_desktop">\n<op>...</op>\n</cloud_desktop>
            paired_rx = re.compile(
                rf'(?<!`)<(?P<tname>{cls._KNOWN_XML_TOOLS_ALT})(?P<attrs>(?:\s+[^>]*?|["\'][^>]*?))?>(?P<body>[\s\S]*?)</\s*(?P=tname)\s*>',
                flags=re.IGNORECASE,
            )
            paired_spans: list[tuple[int, int]] = []
            for m_pair in paired_rx.finditer(text):
                paired_spans.append((m_pair.start(), m_pair.end()))
                t_name = m_pair.group('tname').lower()
                attrs_s = m_pair.group('attrs') or ''
                body_s = m_pair.group('body') or ''
                args_d = cls._parse_xml_kv_and_children(attrs_s, body_s)
                if args_d or t_name == 'list_models':
                    _mark_pos(m_pair.start())
                    extracted_calls.append({
                        'id': f'call_xml_{len(extracted_calls)}_{secrets.token_hex(4)}',
                        'type': 'function',
                        'function': {
                            'name': t_name,
                            'arguments': json.dumps(args_d, ensure_ascii=False),
                        },
                    })

            # 2. Self-closing tags: <cloud_desktop op="type" ... /> or <phone_workspace op="open" target="Outlook"/>
            self_close_rx = re.compile(
                rf'(?<!`)<(?P<tname>{cls._KNOWN_XML_TOOLS_ALT})(?P<attrs>\s+[^>]+?)/\s*>',
                flags=re.IGNORECASE,
            )
            for m_sc in self_close_rx.finditer(text):
                if any(s <= m_sc.start() < e for s, e in paired_spans):
                    continue
                t_name = m_sc.group('tname').lower()
                attrs_s = m_sc.group('attrs') or ''
                args_d = cls._parse_xml_kv_and_children(attrs_s, '')
                if args_d or t_name == 'list_models':
                    _mark_pos(m_sc.start())
                    extracted_calls.append({
                        'id': f'call_xml_{len(extracted_calls)}_{secrets.token_hex(4)}',
                        'type': 'function',
                        'function': {
                            'name': t_name,
                            'arguments': json.dumps(args_d, ensure_ascii=False),
                        },
                    })

            # 3. Unclosed trailing pseudo-XML tag at end of text (e.g. `<cloud_desktop">` or truncated `<cloud_desktop">\n<op>tap</op>`)
            unclosed_rx = re.compile(
                rf'(?<!`)<(?P<tname>{cls._KNOWN_XML_TOOLS_ALT})(?P<attrs>(?:\s+[^>]*?|["\'][^>]*?))?>(?P<body>[\s\S]*)$',
                flags=re.IGNORECASE,
            )
            m_unc = unclosed_rx.search(text)
            if m_unc and not any(s <= m_unc.start() < e for s, e in paired_spans):
                t_name = m_unc.group('tname').lower()
                attrs_s = (m_unc.group('attrs') or '').rstrip('/')
                body_s = m_unc.group('body') or ''
                args_d = cls._parse_xml_kv_and_children(attrs_s, body_s)
                if args_d and not extracted_calls:
                    _mark_pos(m_unc.start())
                    extracted_calls.append({
                        'id': f'call_xml_{len(extracted_calls)}_{secrets.token_hex(4)}',
                        'type': 'function',
                        'function': {
                            'name': t_name,
                            'arguments': json.dumps(args_d, ensure_ascii=False),
                        },
                    })

        if not extracted_calls and first_tag_pos is None:
            return text, []

        # Truncate any post-tag hallucinated steps written after an inline tool call tag!
        if first_tag_pos is not None and text[:first_tag_pos].strip():
            cleaned = text[:first_tag_pos].strip()
        else:
            cleaned = re.sub(
                r'(?<!`)<[|｜\s]*DSML[|｜\s]*(?:function_calls|calls)\s*>[\s\S]*?</[|｜\s]*DSML[|｜\s]*(?:function_calls|calls)\s*>',
                '',
                text,
                flags=re.IGNORECASE,
            )
            cleaned = re.sub(
                r'(?<!`)<[|｜\s]*DSML[|｜\s]*(?:function_calls|calls|invoke)\b[\s\S]*$',
                '',
                cleaned,
                flags=re.IGNORECASE,
            )
            cleaned = re.sub(r'(?<!`)<function_calls>[\s\S]*?</function_calls>', '', cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(
                rf'(?<!`)<(?P<tname>{cls._KNOWN_XML_TOOLS_ALT})(?:\s+[^>]*?|["\'][^>]*?)?>[\s\S]*?</\s*(?P=tname)\s*>',
                '',
                cleaned,
                flags=re.IGNORECASE,
            )
            cleaned = re.sub(
                rf'(?<!`)<(?:{cls._KNOWN_XML_TOOLS_ALT})\s+[^>]+?/\s*>',
                '',
                cleaned,
                flags=re.IGNORECASE,
            )
            cleaned = re.sub(
                rf'(?<!`)<(?:{cls._KNOWN_XML_TOOLS_ALT})(?:\s+[^>]*?|["\'][^>]*?)?>?[\s\S]*$',
                '',
                cleaned,
                flags=re.IGNORECASE,
            )
        cleaned = re.sub(r'\n*\[系统(?:真实记录|最后修正|修正|注):[^\]]*\]', '', cleaned).strip()
        return cleaned.strip(), extracted_calls

    @staticmethod
    def _estimate_text_tokens(text: str) -> int:
        if not text:
            return 0
        cjk = sum(1 for ch in text if '\u4e00' <= ch <= '\u9fff')
        other_bytes = len(text.encode('utf-8')) - cjk * 3
        return cjk + max(0, other_bytes) // 3

    @classmethod
    def _estimate_payload_tokens(cls, messages: list[dict], tools: list[dict] | None = None) -> int:
        total = 0
        for m in messages:
            if not isinstance(m, dict):
                continue
            c = m.get('content')
            if isinstance(c, list):
                for part in c:
                    if isinstance(part, dict):
                        if part.get('type') == 'text':
                            total += cls._estimate_text_tokens(str(part.get('text') or ''))
                        elif part.get('type') == 'image_url':
                            total += 1100
            else:
                total += 8 + cls._estimate_text_tokens(str(c or ''))
            if m.get('tool_calls'):
                total += cls._estimate_text_tokens(json.dumps(m['tool_calls'], ensure_ascii=False))
        if tools:
            total += cls._estimate_text_tokens(json.dumps(tools, ensure_ascii=False))
        return total

    def _record_usage(
        self,
        bot_id: str,
        usage: dict | None,
        is_foreground: bool = True,
        fallback_tokens: int = 0,
    ) -> tuple[bool, int, int, int]:
        if not bot_id:
            return False, 0, 0, 0
        pt = 0
        cached = 0
        comp = 0
        if isinstance(usage, dict):
            pt = int(usage.get('prompt_tokens') or usage.get('input_tokens') or 0)
            comp = int(usage.get('completion_tokens') or usage.get('output_tokens') or 0)
            details = usage.get('prompt_tokens_details') or usage.get('input_tokens_details') or {}
            cached = int(
                (details.get('cached_tokens') if isinstance(details, dict) else 0)
                or usage.get('prompt_cache_hit_tokens')
                or usage.get('cache_read_input_tokens')
                or usage.get('cached_tokens')
                or 0
            )
        if pt <= 0 and is_foreground and fallback_tokens > 0:
            prev_pt = int(self.last_prompt_tokens.get(bot_id, 0) or 0)
            pt = fallback_tokens
            # Even when `fallback_tokens < prev_pt` (because the previous turn had temporary tool loop outputs
            # that were not persisted into `state['messages']`), the shared base prefix `min(fallback_tokens, prev_pt)`
            # remains cached in KV Cache!
            if prev_pt > 0:
                shared_prefix = min(fallback_tokens, prev_pt)
                cached = int((max(0, shared_prefix - 64) // 64) * 64)
        if pt <= 0:
            return False, 0, 0, comp
        eff_cached = max(0, min(pt, cached))
        # Never let a small background vision/consolidation call overwrite the foreground conversation's real token count!
        if is_foreground or pt > int(self.last_prompt_tokens.get(bot_id, 0) or 0):
            self.last_prompt_tokens[bot_id] = pt
            if eff_cached > 0 or is_foreground:
                self.last_cached_tokens[bot_id] = eff_cached
            try:
                st_pt = self.store.state.setdefault('last_prompt_tokens', {})
                st_ct = self.store.state.setdefault('last_cached_tokens', {})
                st_pt[bot_id] = pt
                st_ct[bot_id] = self.last_cached_tokens.get(bot_id, 0)
            except Exception:
                pass
            return True, pt, eff_cached, comp
        return False, pt, eff_cached, comp

    def _record_perf_metrics(
        self,
        bot_id: str,
        ttft_ms: int,
        total_ms: int,
        prompt_tokens: int,
        cached_tokens: int,
        completion_tokens: int,
        model: str = '',
        is_foreground: bool = True,
    ) -> dict:
        gen_ms = max(60, total_ms - max(0, ttft_ms)) if ttft_ms > 0 and total_ms > ttft_ms else max(100, total_ms)
        tps = round((completion_tokens * 1000.0) / gen_ms, 1) if completion_tokens > 0 else 0.0
        eff_pt = prompt_tokens or int(self.last_prompt_tokens.get(bot_id, 0) or 0)
        eff_ct = cached_tokens if prompt_tokens > 0 else int(self.last_cached_tokens.get(bot_id, 0) or 0)
        eff_ct = max(0, min(eff_pt, eff_ct))
        hit_pct = int(round((eff_ct * 100.0) / max(1, eff_pt))) if eff_pt > 0 else 0
        m_obj = {
            'ttft_ms': max(0, int(ttft_ms)),
            'total_ms': max(1, int(total_ms)),
            'tps': tps,
            'prompt_tokens': eff_pt,
            'cached_tokens': eff_ct,
            'cache_hit_pct': hit_pct,
            'completion_tokens': max(0, int(completion_tokens)),
            'model': model,
            'updated_at': round(time.time(), 2),
        }
        if is_foreground and bot_id:
            self.last_metrics[bot_id] = m_obj
            try:
                st_m = self.store.state.setdefault('last_metrics', {})
                st_m[bot_id] = m_obj
            except Exception:
                pass
        return m_obj

    @staticmethod
    def summarize_streaming_tool(t_name: str, raw_args: str) -> str:
        if not raw_args:
            return f'正在准备调用 {t_name}…'
        path_m = re.search(r'"(?:path|url|query|app|package|to_bot_id|key|name)"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"', raw_args)
        target = path_m.group(1) if path_m else ''
        content_m = re.search(r'"(?:content|new_text|command|text|script|code)"\s*:\s*"((?:[^"\\]|\\.)*)', raw_args, re.DOTALL)
        snippet = ''
        if content_m:
            raw_body = content_m.group(1)[-320:]
            snippet = raw_body.replace('\\n', '\n').replace('\\t', '  ').replace('\\"', '"').strip()
            lines = [ln.strip() for ln in snippet.splitlines() if ln.strip()]
            snippet = '\n'.join(lines[-3:]) if lines else ''
        header = f'正在生成 {t_name}' + (f' · {target}' if target else '') + f' ({len(raw_args)} 字符)'
        return f'{header}\n{snippet}' if snippet else header

    async def fetch_available_models(self) -> list[str]:
        base_url = self.store.config.effective_base_url()
        api_key = self.store.config.effective_api_key(big=True) or self.store.config.effective_api_key(big=False)
        fb_url, fb_key, fb_model = (
            self.store.config.effective_fallback(big=True)
            if hasattr(self.store.config, 'effective_fallback')
            else ('', '', '')
        )
        cache_key = f"{(base_url or '').rstrip('/')}|{(api_key or '')[-8:]}|{fb_url}|{fb_model}"
        now = time.time()
        if self._models_cache and self._models_cache[1] == cache_key and (now - self._models_cache[0]) < 300:
            return list(self._models_cache[2])
        collected: list[str] = []
        for u, k in [(base_url, api_key), (fb_url, fb_key)]:
            if not u or not k:
                continue
            try:
                async with httpx.AsyncClient(timeout=12, trust_env=False) as client:
                    r = await client.get(u.rstrip('/') + '/models', headers={'Authorization': 'Bearer ' + k})
                if r.status_code < 400:
                    body = r.json()
                    raw_list = body.get('data') if isinstance(body, dict) else (body if isinstance(body, list) else [])
                    for item in (raw_list or []):
                        mid = str(item['id']) if isinstance(item, dict) and item.get('id') else str(item or '')
                        if mid and mid not in collected:
                            collected.append(mid)
            except Exception:
                pass
        for builtin_m in [
            fb_model,
            'commandcode/deepseek/deepseek-v4.1-flash',
            'commandcode/deepseek/deepseek-v4-flash',
            'commandcode/deepseek/deepseek-v4-pro',
            'deepseek-v4.1-flash-eco',
        ]:
            if builtin_m and builtin_m not in collected:
                collected.append(builtin_m)
        if collected:
            self._models_cache = (now, cache_key, collected)
        return collected

    def _resolve_endpoint_candidates(self, bot_id: str = '', bot_model: str = '', big: bool = False) -> list[tuple[str, str, str]]:
        resolved_bot_model = self._resolve_bot_model(bot_id, bot_model)
        base_url = self.store.config.effective_base_url().rstrip('/')
        api_key = self.store.config.effective_api_key(big=big) or self.store.config.effective_api_key(big=not big)
        model = self.store.config.effective_model(bot_model=resolved_bot_model, big=big) or self.store.config.effective_model(bot_model=resolved_bot_model, big=not big)
        candidates: list[tuple[str, str, str]] = []
        if base_url and api_key and model:
            candidates.append((base_url, api_key, model))
        if hasattr(self.store.config, 'effective_fallback'):
            fb_url, fb_key, fb_model = self.store.config.effective_fallback(big=big)
            if fb_url and fb_key and fb_model:
                fb_tuple = (fb_url.rstrip('/'), fb_key, fb_model)
                if not candidates or (fb_tuple[0], fb_tuple[2]) != (candidates[0][0], candidates[0][2]):
                    candidates.append(fb_tuple)
        return candidates

    def _resolve_bot_model(self, bot_id: str = '', bot_model: str = '') -> str:
        if bot_model:
            return bot_model
        if bot_id and self.get_assistant_fn:
            try:
                bot = self.get_assistant_fn(self.store, bot_id)
                return str(bot.get('model') or '')
            except Exception:
                return ''
        return ''

    @staticmethod
    def _normalize_messages(messages: list[dict]) -> list[dict]:
        normalized: list[dict] = []
        for m in messages:
            if not isinstance(m, dict):
                continue
            c = m.get('content')
            if m.get('role') == 'tool' and not isinstance(c, str):
                m = {**m, 'content': json.dumps(c, ensure_ascii=False) if c is not None else ''}
            elif isinstance(c, dict):
                m = {**m, 'content': json.dumps(c, ensure_ascii=False)}
            normalized.append(m)
        return normalized

    async def complete_silent(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        big: bool = False,
        bot_id: str = 'companion',
        bot_model: str = '',
        max_tokens: int | None = None,
        tool_choice: str | dict | None = None,
    ) -> dict:
        """Pure background LLM call (for memory consolidation, compaction, curiosity, vision). NEVER touches UI streams."""
        candidates = self._resolve_endpoint_candidates(bot_id, bot_model, big=big)
        if not candidates:
            raise ValueError('请先在设置中配置 API Key 和 Model')
        base_url, api_key, model = candidates[0]
        norm_msgs = self._normalize_messages(messages)
        payload: dict = {'model': model, 'messages': norm_msgs, 'stream': False}
        if tools:
            eff_choice = tool_choice or 'auto'
            payload.update(tools=tools, tool_choice=eff_choice, parallel_tool_calls=True)
        if max_tokens:
            payload['max_tokens'] = max_tokens

        last_err: Exception | None = None
        for attempt in range(3):
            if attempt > 0 and len(candidates) > 1:
                base_url, api_key, model = candidates[1]
                payload['model'] = model
            try:
                t_req = time.perf_counter()
                async with httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=15.0), trust_env=False) as client:
                    r = await client.post(
                        base_url + '/chat/completions',
                        headers={'Authorization': 'Bearer ' + api_key},
                        json=payload,
                    )
                if r.status_code == 400 and 'parallel_tool_calls' in payload and 'parallel_tool_calls' in r.text:
                    payload.pop('parallel_tool_calls', None)
                    continue
                if r.status_code == 400 and payload.get('tool_choice') == 'none':
                    payload.pop('tools', None)
                    payload.pop('tool_choice', None)
                    continue
                if r.status_code >= 400 and attempt == 0 and len(candidates) > 1:
                    continue
                if r.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                    await asyncio.sleep(1.2 * (attempt + 1))
                    continue
                if r.status_code >= 400:
                    detail = r.text[:300].strip()
                    raise ValueError(f'HTTP {r.status_code}: {detail}')
                total_ms = int(round((time.perf_counter() - t_req) * 1000))
                body = r.json()
                usage = body.get('usage') or {}
                _, pt, ct, comp = self._record_usage(bot_id, usage, is_foreground=False)
                msg = body['choices'][0]['message']
                raw_content = str(msg.get('content') or '')
                clean_content, dsml_calls = self.parse_and_strip_dsml_calls(raw_content)
                merged_calls = list(msg.get('tool_calls') or []) + dsml_calls
                if comp <= 0:
                    comp = self._estimate_text_tokens(raw_content)
                metrics = self._record_perf_metrics(bot_id, total_ms, total_ms, pt, ct, comp, model=model, is_foreground=False)
                out: dict = {'role': 'assistant', 'content': clean_content, '_metrics': metrics}
                if merged_calls:
                    out['tool_calls'] = merged_calls
                return out
            except (httpx.TransportError, httpx.TimeoutException) as e:
                last_err = e
                if attempt < 2:
                    await asyncio.sleep(1.2 * (attempt + 1))
                    continue
                raise
        raise last_err or ValueError('complete_silent 请求失败')

    async def complete_stream(
        self,
        messages: list[dict],
        bot_id: str,
        bot_model: str = '',
        tools: list[dict] | None = None,
        big: bool = False,
        stream_prefix: str = '',
        on_stream: callable | None = None,
        tool_choice: str | dict | None = None,
    ) -> dict:
        """Foreground streaming LLM call with real-time token & tool-call delta callbacks and transient retry."""
        candidates = self._resolve_endpoint_candidates(bot_id, bot_model, big=big)
        if not candidates:
            raise ValueError('请先在设置中配置 API Key 和 Model')
        base_url, api_key, model = candidates[0]
        norm_msgs = self._normalize_messages(messages)
        est_payload_tokens = self._estimate_payload_tokens(norm_msgs, tools)
        payload: dict = {
            'model': model,
            'messages': norm_msgs,
            'stream': True,
            'stream_options': {'include_usage': True},
        }
        if tools:
            eff_choice = tool_choice or 'auto'
            payload.update(tools=tools, tool_choice=eff_choice, parallel_tool_calls=True)

        for attempt in range(3):
            if attempt > 0 and len(candidates) > 1:
                base_url, api_key, model = candidates[1]
                payload['model'] = model
            try:
                t_req = time.perf_counter()
                ttft_ms = 0
                async with httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=30.0), trust_env=False) as client:
                    async with client.stream('POST', base_url + '/chat/completions', headers={'Authorization': 'Bearer ' + api_key}, json=payload) as r:
                        if r.status_code == 400 and ('stream_options' in payload or 'parallel_tool_calls' in payload or payload.get('tool_choice') == 'none'):
                            err_peek = (await r.aread()).decode('utf-8', errors='replace')
                            retried_400 = False
                            if 'stream_options' in payload and ('stream_options' in err_peek or 'include_usage' in err_peek):
                                payload.pop('stream_options', None)
                                retried_400 = True
                            if 'parallel_tool_calls' in payload and 'parallel_tool_calls' in err_peek:
                                payload.pop('parallel_tool_calls', None)
                                retried_400 = True
                            if payload.get('tool_choice') == 'none':
                                payload.pop('tools', None)
                                payload.pop('tool_choice', None)
                                retried_400 = True
                            if retried_400:
                                continue
                            if attempt == 0 and len(candidates) > 1:
                                continue
                            detail = err_peek[:400].strip()
                            raise ValueError(f'模型网关返回 HTTP {r.status_code} (model={model})：{detail or "请检查 Base URL、Key、Model 或额度"}')
                        if r.status_code >= 400 and attempt == 0 and len(candidates) > 1:
                            await r.aread()
                            continue
                        if r.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                            await r.aread()
                            await asyncio.sleep(1.2 * (attempt + 1))
                            continue
                        if r.status_code >= 400:
                            err_bytes = await r.aread()
                            detail = err_bytes.decode('utf-8', errors='replace')[:400].strip()
                            raise ValueError(f'模型网关返回 HTTP {r.status_code} (model={model})：{detail or "请检查 Base URL、Key、Model 或额度"}')
                        ctype = r.headers.get('content-type', '')
                        if 'text/event-stream' not in ctype:
                            raw_bytes = await r.aread()
                            total_ms = int(round((time.perf_counter() - t_req) * 1000))
                            body = json.loads(raw_bytes.decode('utf-8', errors='replace'))
                            usage = body.get('usage') or {}
                            _, pt, ct, comp = self._record_usage(bot_id, usage, is_foreground=True, fallback_tokens=est_payload_tokens)
                            msg = body['choices'][0]['message']
                            raw_c = str(msg.get('content') or '')
                            clean_c, dsml_calls = self.parse_and_strip_dsml_calls(raw_c)
                            merged_calls = list(msg.get('tool_calls') or []) + dsml_calls
                            if comp <= 0:
                                comp = self._estimate_text_tokens(raw_c)
                            metrics = self._record_perf_metrics(bot_id, total_ms, total_ms, pt, ct, comp, model=model, is_foreground=True)
                            if clean_c and on_stream:
                                await on_stream(text=clean_c)
                            out_msg: dict = {'role': 'assistant', 'content': clean_c, '_metrics': metrics}
                            if merged_calls:
                                out_msg['tool_calls'] = merged_calls
                            return out_msg

                        content_parts: list[str] = []
                        reasoning_parts: list[str] = []
                        tool_calls_map: dict[int, dict] = {}
                        last_emit = 0.0
                        next_auto_idx = 0
                        saw_usage = False
                        rep_pt, rep_ct, rep_comp = 0, 0, 0
                        unflushed_content = False
                        async for line in r.aiter_lines():
                            line = line.strip()
                            if not line or not line.startswith('data:'):
                                continue
                            data_str = line[5:].strip()
                            if data_str == '[DONE]':
                                break
                            try:
                                chunk = json.loads(data_str)
                            except ValueError:
                                continue
                            usage = chunk.get('usage') or {}
                            if usage:
                                ok_u, u_pt, u_ct, u_comp = self._record_usage(bot_id, usage, is_foreground=True)
                                if ok_u:
                                    saw_usage = True
                                    rep_pt, rep_ct, rep_comp = u_pt, u_ct, u_comp
                            choices = chunk.get('choices') or []
                            if not choices:
                                continue
                            choice0 = choices[0]
                            # CRITICAL FIX: Never use `choice0.get('delta') or choice0.get('message')`!
                            # An empty dict `{}` in `delta` on the final finish_reason chunk is falsy in Python
                            # and would fall back to `message`, duplicating the entire response!
                            if 'delta' in choice0 and choice0['delta'] is not None:
                                delta = choice0['delta']
                            elif not content_parts and not tool_calls_map:
                                delta = choice0.get('message') or {}
                            else:
                                delta = {}

                            r_delta = delta.get('reasoning_content') or delta.get('reasoning')
                            c_delta = delta.get('content')
                            tc_deltas = delta.get('tool_calls') or []
                            if ttft_ms == 0 and (r_delta or c_delta or tc_deltas):
                                ttft_ms = int(round((time.perf_counter() - t_req) * 1000))

                            if r_delta:
                                reasoning_parts.append(r_delta)
                                if not content_parts and on_stream:
                                    now = time.time()
                                    if now - last_emit >= 0.08:
                                        last_emit = now
                                        tail_thought = ''.join(reasoning_parts).strip()[-360:]
                                        await on_stream(text=stream_prefix or '正在思考…', thought=tail_thought)
                            if c_delta:
                                content_parts.append(c_delta)
                                unflushed_content = True
                                if on_stream:
                                    now = time.time()
                                    if now - last_emit >= 0.035:
                                        last_emit = now
                                        unflushed_content = False
                                        active_tc = tool_calls_map[max(tool_calls_map.keys())]['function'].get('name') if tool_calls_map else None
                                        safe_live_txt = self.strip_streaming_dsml_tail(''.join(content_parts))
                                        if safe_live_txt or not stream_prefix:
                                            await on_stream(text=safe_live_txt or stream_prefix or '正在思考…', tool=active_tc or None, thought=None)
                            if tc_deltas and unflushed_content and on_stream and content_parts:
                                # Flush any short sentence spoken right before tool calls immediately so the user sees it BEFORE the tool starts!
                                unflushed_content = False
                                last_emit = time.time()
                                safe_live_txt = self.strip_streaming_dsml_tail(''.join(content_parts))
                                if safe_live_txt:
                                    await on_stream(text=safe_live_txt, tool=None, thought=None)
                            for tc in tc_deltas:
                                raw_idx = tc.get('index')
                                tc_id = tc.get('id')
                                if raw_idx is None:
                                    idx = next_auto_idx
                                    next_auto_idx += 1
                                else:
                                    idx = int(raw_idx)
                                    if tc_id and idx in tool_calls_map and tool_calls_map[idx].get('id') and tool_calls_map[idx]['id'] != tc_id:
                                        idx = max(tool_calls_map.keys()) + 1
                                entry = tool_calls_map.setdefault(idx, {
                                    'id': tc_id or f'call_{idx}_{secrets.token_hex(4)}',
                                    'type': 'function',
                                    'function': {'name': '', 'arguments': ''}
                                })
                                if tc_id:
                                    entry['id'] = tc_id
                                fn = tc.get('function') or {}
                                if fn.get('name'):
                                    entry['function']['name'] += fn['name']
                                if fn.get('arguments'):
                                    entry['function']['arguments'] += fn['arguments']
                                if on_stream:
                                    now = time.time()
                                    if now - last_emit >= 0.08:
                                        last_emit = now
                                        cur_fn = entry['function']
                                        t_name = cur_fn.get('name') or 'tool'
                                        r_tail = ''.join(reasoning_parts).strip()[-360:] if reasoning_parts else None
                                        safe_live_txt = self.strip_streaming_dsml_tail(''.join(content_parts)).strip()
                                        cur_text = safe_live_txt or stream_prefix or f'正在调用 {t_name}…'
                                        await on_stream(text=cur_text, tool=t_name, thought=r_tail)

                        total_ms = int(round((time.perf_counter() - t_req) * 1000))
                        if ttft_ms == 0:
                            ttft_ms = total_ms
                        if not saw_usage:
                            _, rep_pt, rep_ct, rep_comp = self._record_usage(bot_id, None, is_foreground=True, fallback_tokens=est_payload_tokens)
                        raw_full_content = ''.join(content_parts)
                        full_content, dsml_calls = self.parse_and_strip_dsml_calls(raw_full_content)
                        for d_idx, d_call in enumerate(dsml_calls):
                            tool_calls_map[1000 + d_idx] = d_call
                        if rep_comp <= 0:
                            tc_str = json.dumps([tool_calls_map[k] for k in sorted(tool_calls_map.keys())], ensure_ascii=False) if tool_calls_map else ''
                            rep_comp = max(1, self._estimate_text_tokens(raw_full_content + ''.join(reasoning_parts) + tc_str))
                        metrics = self._record_perf_metrics(bot_id, ttft_ms, total_ms, rep_pt, rep_ct, rep_comp, model=model, is_foreground=True)

                        r_full = ''.join(reasoning_parts).strip() or None
                        if full_content and on_stream and not tool_calls_map:
                            await on_stream(text=full_content, thought=r_full)
                        elif tool_calls_map and on_stream:
                            last_fn = tool_calls_map[max(tool_calls_map.keys())]['function']
                            t_name = last_fn.get('name') or 'tool'
                            cur_text = full_content.strip() or stream_prefix or f'正在调用 {t_name}…'
                            await on_stream(text=cur_text, tool=t_name, thought=r_full)
                        result_msg: dict = {'role': 'assistant', 'content': full_content or '', '_metrics': metrics}
                        if reasoning_parts:
                            result_msg['thought'] = ''.join(reasoning_parts).strip()
                        if tool_calls_map:
                            result_msg['tool_calls'] = [tool_calls_map[k] for k in sorted(tool_calls_map.keys())]
                        return result_msg
            except (httpx.TransportError, httpx.TimeoutException):
                if attempt < 2:
                    await asyncio.sleep(1.2 * (attempt + 1))
                    continue
                raise
        raise ValueError('complete_stream 请求失败')
