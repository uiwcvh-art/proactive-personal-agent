"""LLM Client Engine with strict separation between UI-Streaming and Silent Background Calls.

Features:
1. Prevents background tasks (sleep-time memory consolidation, 200K context compaction,
   vision UI tree extraction) from ever leaking internal JSON or state into foreground chat streams.
2. Fixes the Python empty-dict `{}` falsy trap on SSE final chunks (`delta: {}` must NEVER fall back to `message`).
3. Automatic transient error retry & model failover (handles 429 / 502 / 503 / 504 / network jitter).
"""
from __future__ import annotations

import asyncio
import json
import re
import secrets
import time
import httpx


class LLMClient:
    def __init__(self, store, get_assistant_fn=None, last_prompt_tokens: dict[str, int] | None = None):
        self.store = store
        self.get_assistant_fn = get_assistant_fn
        self.last_prompt_tokens: dict[str, int] = last_prompt_tokens if last_prompt_tokens is not None else {}
        self._models_cache: tuple[float, str, list[str]] | None = None

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
        if not base_url or not api_key:
            return []
        cache_key = base_url.rstrip('/') + '|' + api_key[-8:]
        now = time.time()
        if self._models_cache and self._models_cache[1] == cache_key and (now - self._models_cache[0]) < 300:
            return list(self._models_cache[2])
        try:
            async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
                r = await client.get(base_url.rstrip('/') + '/models', headers={'Authorization': 'Bearer ' + api_key})
            if r.status_code >= 400:
                return []
            body = r.json()
            raw_list = body.get('data') if isinstance(body, dict) else (body if isinstance(body, list) else [])
            models = [str(item['id']) if isinstance(item, dict) and item.get('id') else str(item) for item in (raw_list or []) if item]
            sorted_models = sorted(list(dict.fromkeys(models)))
            if sorted_models:
                self._models_cache = (now, cache_key, sorted_models)
            return sorted_models
        except Exception:
            return []

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
    ) -> dict:
        """Pure background LLM call (for memory consolidation, compaction, curiosity, vision). NEVER touches UI streams."""
        resolved_bot_model = self._resolve_bot_model(bot_id, bot_model)
        base_url = self.store.config.effective_base_url().rstrip('/')
        api_key = self.store.config.effective_api_key(big=big) or self.store.config.effective_api_key(big=not big)
        model = self.store.config.effective_model(bot_model=resolved_bot_model, big=big) or self.store.config.effective_model(bot_model=resolved_bot_model, big=not big)
        if not api_key or not model:
            raise ValueError('请先在设置中配置 API Key 和 Model')
        payload: dict = {'model': model, 'messages': self._normalize_messages(messages), 'stream': False}
        if tools:
            payload.update(tools=tools, tool_choice='auto')
        if max_tokens:
            payload['max_tokens'] = max_tokens

        last_err: Exception | None = None
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=15.0), trust_env=False) as client:
                    r = await client.post(
                        base_url + '/chat/completions',
                        headers={'Authorization': 'Bearer ' + api_key},
                        json=payload,
                    )
                if r.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                    await asyncio.sleep(1.2 * (attempt + 1))
                    continue
                if r.status_code >= 400:
                    detail = r.text[:300].strip()
                    raise ValueError(f'HTTP {r.status_code}: {detail}')
                body = r.json()
                usage = body.get('usage') or {}
                if usage.get('prompt_tokens') and bot_id:
                    self.last_prompt_tokens[bot_id] = int(usage['prompt_tokens'])
                msg = body['choices'][0]['message']
                out: dict = {'role': 'assistant', 'content': str(msg.get('content') or '')}
                if msg.get('tool_calls'):
                    out['tool_calls'] = msg['tool_calls']
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
    ) -> dict:
        """Foreground streaming LLM call with real-time token & tool-call delta callbacks and transient retry."""
        resolved_bot_model = self._resolve_bot_model(bot_id, bot_model)
        base_url = self.store.config.effective_base_url().rstrip('/')
        api_key = self.store.config.effective_api_key(big=big) or self.store.config.effective_api_key(big=not big)
        model = self.store.config.effective_model(bot_model=resolved_bot_model, big=big) or self.store.config.effective_model(bot_model=resolved_bot_model, big=not big)
        if not api_key or not model:
            raise ValueError('请先在设置中配置 API Key 和 Model')
        payload: dict = {'model': model, 'messages': self._normalize_messages(messages), 'stream': True}
        if tools:
            payload.update(tools=tools, tool_choice='auto')

        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=30.0), trust_env=False) as client:
                    async with client.stream('POST', base_url + '/chat/completions', headers={'Authorization': 'Bearer ' + api_key}, json=payload) as r:
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
                            body = json.loads(raw_bytes.decode('utf-8', errors='replace'))
                            usage = body.get('usage') or {}
                            if usage.get('prompt_tokens'):
                                self.last_prompt_tokens[bot_id] = int(usage['prompt_tokens'])
                            msg = body['choices'][0]['message']
                            if msg.get('content') and on_stream:
                                await on_stream(text=msg['content'])
                            return {k: v for k, v in msg.items() if k in ('role', 'content', 'tool_calls')}

                        content_parts: list[str] = []
                        reasoning_parts: list[str] = []
                        tool_calls_map: dict[int, dict] = {}
                        last_emit = 0.0
                        next_auto_idx = 0
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
                            if usage.get('prompt_tokens'):
                                self.last_prompt_tokens[bot_id] = int(usage['prompt_tokens'])
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
                            if r_delta:
                                reasoning_parts.append(r_delta)
                                if not content_parts and on_stream:
                                    now = time.time()
                                    if now - last_emit >= 0.08:
                                        last_emit = now
                                        tail_thought = ''.join(reasoning_parts).strip()[-360:]
                                        await on_stream(text=stream_prefix or '正在思考…', thought=tail_thought)
                            c_delta = delta.get('content')
                            if c_delta:
                                content_parts.append(c_delta)
                                if on_stream:
                                    now = time.time()
                                    if now - last_emit >= 0.04:
                                        last_emit = now
                                        active_tc = tool_calls_map[max(tool_calls_map.keys())]['function'].get('name') if tool_calls_map else None
                                        await on_stream(text=''.join(content_parts), tool=active_tc or None, thought='')
                            tc_deltas = delta.get('tool_calls') or []
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
                                        t_args = cur_fn.get('arguments') or ''
                                        live_preview = self.summarize_streaming_tool(t_name, t_args)
                                        cur_text = ''.join(content_parts).strip() or stream_prefix or f'正在调用 {t_name}…'
                                        await on_stream(text=cur_text, tool=t_name, thought=live_preview)

                        full_content = ''.join(content_parts)
                        if full_content and on_stream and not tool_calls_map:
                            await on_stream(text=full_content, thought='')
                        elif tool_calls_map and on_stream:
                            last_fn = tool_calls_map[max(tool_calls_map.keys())]['function']
                            t_name = last_fn.get('name') or 'tool'
                            t_args = last_fn.get('arguments') or ''
                            cur_text = full_content.strip() or stream_prefix or f'正在调用 {t_name}…'
                            await on_stream(text=cur_text, tool=t_name, thought=self.summarize_streaming_tool(t_name, t_args))
                        result_msg: dict = {'role': 'assistant', 'content': full_content or ''}
                        if tool_calls_map:
                            result_msg['tool_calls'] = [tool_calls_map[k] for k in sorted(tool_calls_map.keys())]
                        return result_msg
            except (httpx.TransportError, httpx.TimeoutException):
                if attempt < 2:
                    await asyncio.sleep(1.2 * (attempt + 1))
                    continue
                raise
        raise ValueError('complete_stream 请求失败')
