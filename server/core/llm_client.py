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
from core.xml_tool_parser import (
    KNOWN_XML_TOOLS as _PARSER_KNOWN_XML_TOOLS,
    coerce_xml_param,
    extract_inline_tool_calls,
    strip_inline_tool_markup,
)


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

    KNOWN_XML_TOOLS = _PARSER_KNOWN_XML_TOOLS
    _KNOWN_XML_TOOLS_ALT = '|'.join(KNOWN_XML_TOOLS)

    @classmethod
    def _coerce_xml_param(cls, p_name: str, raw_val: str, is_str_flag: str = ''):
        return coerce_xml_param(p_name, raw_val, is_str_flag)

    @classmethod
    def _extract_dsml_tool_calls(cls, text: str) -> list[dict]:
        return extract_inline_tool_calls(text)

    @classmethod
    def _strip_dsml(cls, text: str) -> str:
        return strip_inline_tool_markup(text)

    @classmethod
    def strip_streaming_dsml_tail(cls, text: str) -> str:
        """Hides any in-progress DeepSeek DSML / XML function-call block from live UI stream."""
        if not text:
            return ''
        m = re.search(
            r'(?<!`)<(?:[|｜]{1,2}\s*DSML[|｜\s]*(?:function_calls|calls|invoke)\b|function_calls\b|functions_tool_call\b|functions\b|tool_call\b|call\b|function(?:=|\s+name=)|antml:function_calls\b|invoke\s+name=)',
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
        m_tail = re.search(r'(?<!`)<[|｜]{1,2}(?:DSML[|｜\s]*)?[A-Za-z_]*$', text)
        if m_tail:
            return text[:m_tail.start()].rstrip()
        return text

    @classmethod
    def parse_and_strip_dsml_calls(cls, text: str) -> tuple[str, list[dict]]:
        """
        Parses all 5 inline tool call formats (DeepSeek DSML, <function=...>, <functions_tool_call>,
        <tool_call>, <call>, <functions>, and direct tool tags) via `xml_tool_parser` and strips markup.
        """
        if not text:
            return text, []
        extracted = extract_inline_tool_calls(text)
        if extracted:
            pre_text = cls.strip_streaming_dsml_tail(text)
            cleaned = strip_inline_tool_markup(pre_text) if pre_text.strip() else strip_inline_tool_markup(text)
        else:
            cleaned = strip_inline_tool_markup(text)
        return cleaned, extracted

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
        source: str = 'phone',
    ) -> tuple[bool, int, int, int]:
        if not bot_id:
            return False, 0, 0, 0
        norm_src = 'pc' if str(source or '').strip().lower() in ('pc', 'desktop', 'computer', 'dsh') else 'phone'
        nb_key = f'{norm_src}:{bot_id}'
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
        prev_pt = int(self.last_prompt_tokens.get(nb_key, self.last_prompt_tokens.get(bot_id, 0) if norm_src == 'phone' else 0) or 0)
        if pt <= 0 and is_foreground and fallback_tokens > 0:
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
        if is_foreground or pt > prev_pt:
            self.last_prompt_tokens[nb_key] = pt
            if norm_src == 'phone':
                self.last_prompt_tokens[bot_id] = pt
            if eff_cached > 0 or is_foreground:
                self.last_cached_tokens[nb_key] = eff_cached
                if norm_src == 'phone':
                    self.last_cached_tokens[bot_id] = eff_cached
            try:
                st_pt = self.store.state.setdefault('last_prompt_tokens', {})
                st_ct = self.store.state.setdefault('last_cached_tokens', {})
                st_pt[nb_key] = pt
                st_ct[nb_key] = self.last_cached_tokens.get(nb_key, 0)
                if norm_src == 'phone':
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
        source: str = 'phone',
    ) -> dict:
        norm_src = 'pc' if str(source or '').strip().lower() in ('pc', 'desktop', 'computer', 'dsh') else 'phone'
        nb_key = f'{norm_src}:{bot_id}'
        gen_ms = max(60, total_ms - max(0, ttft_ms)) if ttft_ms > 0 and total_ms > ttft_ms else max(100, total_ms)
        tps = round((completion_tokens * 1000.0) / gen_ms, 1) if completion_tokens > 0 else 0.0
        fallback_pt = int(self.last_prompt_tokens.get(nb_key, self.last_prompt_tokens.get(bot_id, 0) if norm_src == 'phone' else 0) or 0)
        fallback_ct = int(self.last_cached_tokens.get(nb_key, self.last_cached_tokens.get(bot_id, 0) if norm_src == 'phone' else 0) or 0)
        eff_pt = prompt_tokens or fallback_pt
        eff_ct = cached_tokens if prompt_tokens > 0 else fallback_ct
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
            self.last_metrics[nb_key] = m_obj
            if norm_src == 'phone':
                self.last_metrics[bot_id] = m_obj
            try:
                st_m = self.store.state.setdefault('last_metrics', {})
                st_m[nb_key] = m_obj
                if norm_src == 'phone':
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

    @staticmethod
    def _clean_error_detail(raw_text: str, limit: int = 240) -> str:
        s = str(raw_text or '').strip()
        if not s:
            return ''
        low = s.lower()
        if '<html' in low or '<!doctype' in low or '<title>' in low:
            title_m = re.search(r'<title>(.*?)</title>', s, flags=re.I | re.S)
            title = re.sub(r'\s+', ' ', title_m.group(1)).strip() if title_m else ''
            stripped = re.sub(r'<[^>]+>', ' ', s)
            stripped = re.sub(r'\s+', ' ', stripped).strip()
            msg = f'{title} | {stripped}' if title and title not in stripped else (title or stripped)
            return msg[:limit]
        return re.sub(r'\s+', ' ', s)[:limit]

    def _resolve_endpoint_candidates(self, bot_id: str = '', bot_model: str = '', big: bool = False, source: str = 'phone') -> list[tuple[str, str, str]]:
        resolved_bot_model = self._resolve_bot_model(bot_id, bot_model, source=source)
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

    def _resolve_bot_model(self, bot_id: str = '', bot_model: str = '', source: str = 'phone') -> str:
        if bot_model:
            return bot_model
        if bot_id and self.get_assistant_fn:
            try:
                try:
                    bot = self.get_assistant_fn(self.store, bot_id, source=source)
                except TypeError:
                    bot = self.get_assistant_fn(self.store, bot_id)
                return str(bot.get('model') or '')
            except Exception:
                return ''
        return ''

    @staticmethod
    def _sanitize_tools(tools: list[dict] | None) -> list[dict] | None:
        if not tools:
            return None
        import hashlib
        sanitized: list[dict] = []
        for t in tools:
            if not isinstance(t, dict) or not isinstance(t.get('function'), dict):
                continue
            fn = dict(t['function'])
            raw_name = str(fn.get('name') or '').strip()
            if not raw_name:
                continue
            if not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', raw_name):
                ascii_part = re.sub(r'[^a-zA-Z0-9_-]', '', raw_name)[:32].strip('_-')
                digest = hashlib.md5(raw_name.encode('utf-8')).hexdigest()[:8]
                safe_name = f'custom_{ascii_part}_{digest}' if ascii_part else f'custom_tool_{digest}'
                desc = str(fn.get('description') or '')
                if raw_name not in desc:
                    fn['description'] = f'[{raw_name}] {desc}'.strip()
                fn['name'] = safe_name
            sanitized.append({**t, 'function': fn})
        return sanitized or None

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
        clean_tools = self._sanitize_tools(tools)
        payload: dict = {'model': model, 'messages': norm_msgs, 'stream': False}
        if clean_tools:
            eff_choice = tool_choice or 'auto'
            payload.update(tools=clean_tools, tool_choice=eff_choice, parallel_tool_calls=True)
        if max_tokens:
            payload['max_tokens'] = max_tokens

        _TRANSIENT_CODES = (408, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524)
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
                if r.status_code in _TRANSIENT_CODES and attempt < 2:
                    await asyncio.sleep(1.2 * (attempt + 1))
                    continue
                if r.status_code >= 400:
                    detail = self._clean_error_detail(r.text, limit=240)
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
        source: str = 'phone',
        is_foreground: bool = True,
    ) -> dict:
        """Foreground streaming LLM call with real-time token & tool-call delta callbacks and transient retry."""
        candidates = self._resolve_endpoint_candidates(bot_id, bot_model, big=big, source=source)
        if not candidates:
            raise ValueError('请先在设置中配置 API Key 和 Model')
        base_url, api_key, model = candidates[0]
        norm_msgs = self._normalize_messages(messages)
        clean_tools = self._sanitize_tools(tools)
        est_payload_tokens = self._estimate_payload_tokens(norm_msgs, clean_tools)
        payload: dict = {
            'model': model,
            'messages': norm_msgs,
            'stream': True,
            'stream_options': {'include_usage': True},
        }
        if clean_tools:
            eff_choice = tool_choice or 'auto'
            payload.update(tools=clean_tools, tool_choice=eff_choice, parallel_tool_calls=True)

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
                        if r.status_code in (408, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524) and attempt < 2:
                            await r.aread()
                            await asyncio.sleep(1.2 * (attempt + 1))
                            continue
                        if r.status_code >= 400:
                            err_bytes = await r.aread()
                            detail = self._clean_error_detail(err_bytes.decode('utf-8', errors='replace'), limit=280)
                            raise ValueError(f'模型网关返回 HTTP {r.status_code} (model={model})：{detail or "请检查 Base URL、Key、Model 或额度"}')
                        ctype = r.headers.get('content-type', '')
                        if 'text/event-stream' not in ctype:
                            raw_bytes = await r.aread()
                            total_ms = int(round((time.perf_counter() - t_req) * 1000))
                            body = json.loads(raw_bytes.decode('utf-8', errors='replace'))
                            usage = body.get('usage') or {}
                            _, pt, ct, comp = self._record_usage(bot_id, usage, is_foreground=is_foreground, fallback_tokens=est_payload_tokens, source=source)
                            msg = body['choices'][0]['message']
                            raw_c = str(msg.get('content') or '')
                            clean_c, dsml_calls = self.parse_and_strip_dsml_calls(raw_c)
                            merged_calls = list(msg.get('tool_calls') or []) + dsml_calls
                            if comp <= 0:
                                comp = self._estimate_text_tokens(raw_c)
                            metrics = self._record_perf_metrics(bot_id, total_ms, total_ms, pt, ct, comp, model=model, is_foreground=is_foreground, source=source)
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
                        first_thought_emitted = False
                        first_content_emitted = False
                        first_tc_emitted = False
                        next_auto_idx = 0
                        saw_usage = False
                        saw_done = False
                        last_finish_reason = ''
                        rep_pt, rep_ct, rep_comp = 0, 0, 0
                        unflushed_content = False
                        async for line in r.aiter_lines():
                            line = line.strip()
                            if not line or not line.startswith('data:'):
                                continue
                            data_str = line[5:].strip()
                            if data_str == '[DONE]':
                                saw_done = True
                                break
                            try:
                                chunk = json.loads(data_str)
                            except ValueError:
                                continue
                            usage = chunk.get('usage') or {}
                            if usage:
                                ok_u, u_pt, u_ct, u_comp = self._record_usage(bot_id, usage, is_foreground=is_foreground, source=source)
                                if ok_u:
                                    saw_usage = True
                                    rep_pt, rep_ct, rep_comp = u_pt, u_ct, u_comp
                            choices = chunk.get('choices') or []
                            if not choices:
                                continue
                            choice0 = choices[0]
                            if choice0.get('finish_reason'):
                                last_finish_reason = str(choice0['finish_reason'])
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
                                    if (not first_thought_emitted) or (now - last_emit >= 0.05):
                                        first_thought_emitted = True
                                        last_emit = now
                                        tail_thought = ''.join(reasoning_parts).strip()[-1200:]
                                        await on_stream(text=stream_prefix or '正在思考…', thought=tail_thought)
                            if c_delta:
                                content_parts.append(c_delta)
                                unflushed_content = True
                                if on_stream:
                                    now = time.time()
                                    if (not first_content_emitted) or (now - last_emit >= 0.035):
                                        first_content_emitted = True
                                        last_emit = now
                                        unflushed_content = False
                                        active_tc = tool_calls_map[max(tool_calls_map.keys())]['function'].get('name') if tool_calls_map else None
                                        r_tail = ''.join(reasoning_parts).strip()[-1200:] if reasoning_parts else None
                                        safe_live_txt = self.strip_streaming_dsml_tail(''.join(content_parts))
                                        if safe_live_txt or not stream_prefix:
                                            await on_stream(text=safe_live_txt or stream_prefix or '正在思考…', tool=active_tc or None, thought=r_tail)
                            if tc_deltas and unflushed_content and on_stream and content_parts:
                                # Flush any short sentence spoken right before tool calls immediately so the user sees it BEFORE the tool starts!
                                unflushed_content = False
                                last_emit = time.time()
                                r_tail = ''.join(reasoning_parts).strip()[-1200:] if reasoning_parts else None
                                safe_live_txt = self.strip_streaming_dsml_tail(''.join(content_parts))
                                if safe_live_txt:
                                    await on_stream(text=safe_live_txt, tool=None, thought=r_tail)
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
                                    if (not first_tc_emitted and entry['function'].get('name')) or (now - last_emit >= 0.06):
                                        if entry['function'].get('name'):
                                            first_tc_emitted = True
                                        last_emit = now
                                        cur_fn = entry['function']
                                        t_name = cur_fn.get('name') or 'tool'
                                        r_tail = ''.join(reasoning_parts).strip()[-1200:] if reasoning_parts else None
                                        safe_live_txt = self.strip_streaming_dsml_tail(''.join(content_parts)).strip()
                                        cur_text = safe_live_txt or stream_prefix or f'正在调用 {t_name}…'
                                        await on_stream(text=cur_text, tool=t_name, thought=r_tail)

                        total_ms = int(round((time.perf_counter() - t_req) * 1000))
                        if ttft_ms == 0:
                            ttft_ms = total_ms
                        if not saw_usage:
                            _, rep_pt, rep_ct, rep_comp = self._record_usage(bot_id, None, is_foreground=is_foreground, fallback_tokens=est_payload_tokens, source=source)
                        raw_full_content = ''.join(content_parts)
                        full_content, dsml_calls = self.parse_and_strip_dsml_calls(raw_full_content)
                        for d_idx, d_call in enumerate(dsml_calls):
                            tool_calls_map[1000 + d_idx] = d_call
                        if rep_comp <= 0:
                            tc_str = json.dumps([tool_calls_map[k] for k in sorted(tool_calls_map.keys())], ensure_ascii=False) if tool_calls_map else ''
                            rep_comp = max(1, self._estimate_text_tokens(raw_full_content + ''.join(reasoning_parts) + tc_str))
                        metrics = self._record_perf_metrics(bot_id, ttft_ms, total_ms, rep_pt, rep_ct, rep_comp, model=model, is_foreground=is_foreground, source=source)

                        r_full = ''.join(reasoning_parts).strip() or None
                        if full_content and on_stream and not tool_calls_map:
                            await on_stream(text=full_content, thought=r_full)
                        elif tool_calls_map and on_stream:
                            last_fn = tool_calls_map[max(tool_calls_map.keys())]['function']
                            t_name = last_fn.get('name') or 'tool'
                            cur_text = full_content.strip() or stream_prefix or f'正在调用 {t_name}…'
                            await on_stream(text=cur_text, tool=t_name, thought=r_full)
                        result_msg: dict = {
                            'role': 'assistant',
                            'content': full_content or '',
                            '_metrics': metrics,
                            '_finish_reason': last_finish_reason,
                            '_stream_incomplete': bool(not saw_done and not last_finish_reason and (reasoning_parts or content_parts)),
                        }
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

    async def stream_with_tools(
        self,
        messages: list[dict],
        system_prompt: str = '',
        on_delta: callable | None = None,
        tools: list[dict] | None = None,
        bot_id: str = 'companion',
        big: bool = False,
        source: str = 'phone',
        is_foreground: bool = False,
    ) -> dict:
        full_msgs = list(messages or [])
        if system_prompt and (not full_msgs or full_msgs[0].get('role') != 'system'):
            full_msgs = [{'role': 'system', 'content': system_prompt}, *full_msgs]

        async def _bridge(text: str = '', tool: str | None = None, thought: str | None = None):
            if on_delta is not None:
                res = on_delta(text, thought or '')
                if asyncio.iscoroutine(res):
                    await res

        try:
            return await self.complete_stream(
                messages=full_msgs,
                bot_id=bot_id,
                tools=tools or None,
                big=big,
                on_stream=_bridge if on_delta else None,
                source=source,
                is_foreground=is_foreground,
            )
        except Exception:
            return await self.complete_silent(
                messages=full_msgs,
                tools=tools or None,
                big=big,
                bot_id=bot_id,
            )

