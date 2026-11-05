from __future__ import annotations
import asyncio, json, os, re, secrets, signal, sys, time, contextvars
from datetime import datetime, timedelta
from pathlib import Path
import httpx
from vault_manager import TZ, now_iso
from assistants import ensure_assistants, get_assistant
from harness import TOOLS

class Brain:
    def __init__(self, store, vault, harness, publish, escalation):
        self.store, self.vault, self.harness = store, vault, harness
        self.publish, self.escalation = publish, escalation
        self.lock = asyncio.Lock()
        self.bot_locks: dict[str, asyncio.Lock] = {}
        self.executions_by_bot: dict[str, dict] = {}
        self.streams_by_bot: dict[str, dict] = {}
        self.active_tasks_by_bot: dict[str, asyncio.Task] = {}
        self.wake = asyncio.Event()
        self.activity = 'awaiting_config'
        self.task = None
        ensure_assistants(store)
        self.bot_context = contextvars.ContextVar('assistant', default='companion')
        self.room_context = contextvars.ContextVar('room', default=None)
        self.hop_depth = contextvars.ContextVar('hop_depth', default=0)
        self.turn_id_var = contextvars.ContextVar('turn_id', default='')
        self.stream_prefix_var = contextvars.ContextVar('stream_prefix', default='')
        self.stream_seq: int = 0
        self.last_completed_stream: dict | None = None
        self.execution = None
        self.live_stream = None
        self.active_task = None
        self.computer = None
        self.bg_tasks: dict[str, dict] = {}
        self.last_prompt_tokens: dict[str, int] = {}
        self.phone_pending: dict[str, asyncio.Future] = {}
        self._models_cache: tuple[float, str, list[str]] | None = None
        self._used_phone_in_turn: bool = False
        self._last_vision_nodes: list[dict] = []
        self._last_phone_node_count: int = -1
        self._last_phone_size: tuple[int, int] = (1080, 2400)
        self._vision_cache: dict[str, str] = {}
        self.harness.store = store
        self.harness.vault = vault
        self.harness.vision_reader = self.read_image_file_with_vision
        self.harness.custom_handlers = {
            'send_message': self._tool_send_message,
            'manage_bot': self._tool_manage_bot,
            'list_models': self._tool_list_models,
            'schedule_task': self._tool_schedule_task,
            'bg_task': self._tool_bg_task,
            'cloud_desktop': self._tool_cloud_desktop,
            'notify_user': self._tool_notify_user,
            'phone_workspace': self._tool_phone_workspace,
        }

    def _get_bot_lock(self, bot_id: str) -> asyncio.Lock:
        b = (bot_id or 'companion').strip()
        if b not in self.bot_locks:
            self.bot_locks[b] = asyncio.Lock()
        return self.bot_locks[b]

    def is_any_bot_busy(self) -> bool:
        return self.lock.locked() or any(lk.locked() for lk in self.bot_locks.values())

    def persona(self):
        bot = get_assistant(self.store, self.bot_context.get())
        roster = [
            f"- id={b['id']}, 名字={b['name']}, 模型={b.get('model') or '默认'}, 简介={b.get('description','')}"
            for b in self.store.state.get('assistants', [])
        ]
        return bot['instructions'] + '\n\n当前团队 Bot 列表（可用 send_message(to_bot_id, text) 互相发消息，或用 manage_bot 创建/修改 Bot）：\n' + '\n'.join(roster)

    @staticmethod
    def _is_placeholder_text(t: str) -> bool:
        return (not t) or t in ('正在思考…', '正在思考与执行任务…', '正在整理回复…') or t.startswith('正在调用 ') or t.startswith('⏰ 正在执行') or t.startswith('📦 ')

    @staticmethod
    def _extract_reply_piece(raw_text: str) -> tuple[str, dict]:
        s = (raw_text or '').strip()
        if not s:
            return '', {}
        cleaned = re.sub(r'^```(?:json)?\s*|\s*```$', '', s).strip()
        if cleaned.startswith('{') and cleaned.endswith('}'):
            try:
                data = json.loads(cleaned)
                if isinstance(data, dict) and 'reply' in data:
                    return str(data.get('reply') or '').strip(), data
            except ValueError:
                pass
        return s, {}

    @staticmethod
    def _summarize_streaming_tool(t_name: str, raw_args: str) -> str:
        if not raw_args:
            return f'正在准备调用 {t_name}…'
        path_m = re.search(r'"(?:path|url|query|app|package)"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"', raw_args)
        target = path_m.group(1) if path_m else ''
        content_m = re.search(r'"(?:content|new_text|command|text)"\s*:\s*"((?:[^"\\]|\\.)*)', raw_args, re.DOTALL)
        snippet = ''
        if content_m:
            raw_body = content_m.group(1)[-320:]
            snippet = raw_body.replace('\\n', '\n').replace('\\t', '  ').replace('\\"', '"').strip()
            lines = [ln.strip() for ln in snippet.splitlines() if ln.strip()]
            snippet = '\n'.join(lines[-3:]) if lines else ''
        header = f'正在生成 {t_name}' + (f' · {target}' if target else '') + f' ({len(raw_args)} 字符)'
        return f'{header}\n{snippet}' if snippet else header

    async def _set_stream(self, text: str = '', tool: str | None = None, speaker_id: str | None = None, speaker_name: str | None = None, thought: str | None = None):
        bot_id = self.room_context.get() or self.bot_context.get()
        sp_id = speaker_id or self.bot_context.get()
        try:
            sp_name = speaker_name or get_assistant(self.store, sp_id)['name']
        except Exception:
            sp_name = sp_id
        prev = self.live_stream or {}
        prev_text = str(prev.get('text') or '')
        prefix = (self.stream_prefix_var.get() or '').strip()
        turn_id = self.turn_id_var.get() or str(prev.get('turn_id') or '')
        if self._is_placeholder_text(text):
            if prefix:
                effective_text = prefix
            elif prev.get('bot_id') == bot_id and prev.get('speaker_id') == sp_id and prev.get('turn_id') == turn_id and not self._is_placeholder_text(prev_text):
                effective_text = prev_text
            else:
                effective_text = text
        else:
            clean_piece, _ = self._extract_reply_piece(text)
            piece = clean_piece or text
            if prefix and piece != prefix and not piece.startswith(prefix):
                effective_text = prefix + '\n\n' + piece
            else:
                effective_text = piece
        trace_tail = []
        cur_exec = self.executions_by_bot.get(bot_id) or self.execution
        if cur_exec and isinstance(cur_exec.get('trace'), list):
            for step in cur_exec['trace'][-6:]:
                trace_tail.append({
                    'tool': step.get('tool', ''),
                    'arguments': step.get('arguments') or {},
                    'duration_ms': step.get('duration_ms', 0),
                })
        effective_thought = str(prev.get('thought') or '') if thought is None else thought
        self.stream_seq += 1
        self.live_stream = {
            'seq': self.stream_seq,
            'turn_id': turn_id,
            'bot_id': bot_id,
            'speaker_id': sp_id,
            'speaker_name': sp_name,
            'text': effective_text,
            'thought': effective_thought,
            'tool': tool,
            'trace': trace_tail,
            'updated_at': time.time(),
        }
        self.streams_by_bot[bot_id] = self.live_stream
        await self.publish({'type':'stream', 'stream':self.live_stream})

    async def complete(self, messages, tools=None, big=False):
        bot_id = self.bot_context.get()
        try:
            bot = get_assistant(self.store, bot_id)
        except ValueError:
            bot = {}
        base_url = self.store.config.effective_base_url()
        api_key = self.store.config.effective_api_key(big=big) or self.store.config.effective_api_key(big=not big)
        model = self.store.config.effective_model(bot_model=bot.get('model', ''), big=big) or self.store.config.effective_model(bot_model=bot.get('model', ''), big=not big)
        if not api_key or not model:
            raise ValueError('请先在设置中配置 API Key 和 Model')
        payload = {'model':model, 'messages':messages, 'stream':True}
        if tools:
            payload.update(tools=tools, tool_choice='auto')
        async with httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=30.0), trust_env=False) as client:
            async with client.stream('POST', base_url + '/chat/completions', headers={'Authorization':'Bearer ' + api_key}, json=payload) as r:
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
                    if msg.get('content'):
                        await self._set_stream(text=msg['content'])
                    return {k:v for k,v in msg.items() if k in ('role','content','tool_calls')}

                content_parts: list[str] = []
                reasoning_parts: list[str] = []
                tool_calls_map: dict[int, dict] = {}
                last_emit = 0.0
                first_content_at = 0.0
                emitted_content_count = 0
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
                    delta = choice0.get('delta') or choice0.get('message') or {}
                    r_delta = delta.get('reasoning_content') or delta.get('reasoning')
                    if r_delta:
                        reasoning_parts.append(r_delta)
                        if not content_parts:
                            now = time.time()
                            if now - last_emit >= 0.08:
                                last_emit = now
                                tail_thought = ''.join(reasoning_parts).strip()[-360:]
                                await self._set_stream(text=self.stream_prefix_var.get() or '正在思考…', thought=tail_thought)
                    c_delta = delta.get('content')
                    if c_delta:
                        if not first_content_at:
                            first_content_at = time.time()
                        content_parts.append(c_delta)
                        now = time.time()
                        if now - last_emit >= 0.04:
                            last_emit = now
                            emitted_content_count += 1
                            await self._set_stream(text=''.join(content_parts), thought='')
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
                        now = time.time()
                        if now - last_emit >= 0.08:
                            last_emit = now
                            cur_fn = entry['function']
                            t_name = cur_fn.get('name') or 'tool'
                            t_args = cur_fn.get('arguments') or ''
                            live_preview = self._summarize_streaming_tool(t_name, t_args)
                            cur_text = ''.join(content_parts).strip() or self.stream_prefix_var.get() or f'正在调用 {t_name}…'
                            await self._set_stream(text=cur_text, tool=t_name, thought=live_preview)

                full_content = ''.join(content_parts)
                if full_content:
                    burst_elapsed = (time.time() - first_content_at) if first_content_at else 0.0
                    # If upstream flash model burst all content in < 0.7s, pace out 5 progressive stream frames
                    # so high-latency clients see smooth progressive streaming instead of an abrupt wall of text
                    if not tool_calls_map and burst_elapsed < 0.75 and len(full_content) > 24 and emitted_content_count <= 4:
                        steps = 5
                        for step_i in range(1, steps):
                            cut = max(1, int(len(full_content) * step_i / steps))
                            await self._set_stream(text=full_content[:cut], thought='')
                            await asyncio.sleep(0.09)
                    await self._set_stream(text=full_content, thought='')
                result_msg: dict = {'role': 'assistant', 'content': full_content or ''}
                if tool_calls_map:
                    result_msg['tool_calls'] = [tool_calls_map[k] for k in sorted(tool_calls_map.keys())]
                return result_msg

    async def _fetch_available_models(self) -> list[str]:
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
                r = await client.get(base_url.rstrip('/') + '/models', headers={'Authorization':'Bearer ' + api_key})
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

    async def _tool_list_models(self, args: dict):
        models = await self._fetch_available_models()
        return {
            'models': models[:100],
            'total': len(models),
            'default_model': self.store.config.effective_model(),
            'api_base_url': self.store.config.effective_base_url(),
        }

    def _resolve_bot(self, query: str):
        q = query.strip()
        bots = self.store.state.get('assistants', [])
        for b in bots:
            if b['id'] == q or b['name'] == q:
                return b
        q_low = q.lower()
        for b in bots:
            if b['id'].lower() == q_low or b['name'].lower() == q_low or q_low in b['name'].lower():
                return b
        if q_low in ('b', 'bot b', 'bot_b', '助手b', '老二', '2') and len(bots) >= 2:
            primary = self.store.state.get('primary_bot_id', 'companion')
            return next((b for b in bots if b['id'] != primary), bots[1])
        return None

    async def _tool_manage_bot(self, args: dict):
        action = str(args.get('action') or 'list').strip().lower()
        self.store.sync_from_workspace()
        bots = self.store.state.get('assistants', [])
        if action == 'list':
            return {
                'primary_bot_id': self.store.state.get('primary_bot_id', 'companion'),
                'bots': [
                    {
                        'id': b['id'],
                        'name': b['name'],
                        'model': b.get('model') or self.store.config.effective_model(),
                        'raw_model_field': b.get('model', ''),
                        'description': b.get('description', ''),
                        'curiosity_enabled': b.get('curiosity_enabled', False),
                    }
                    for b in bots
                ]
            }
        if action == 'create':
            name = str(args.get('name') or args.get('bot_id') or '').strip()
            if not name:
                return {'error':'create 需要提供 name'}
            if len(bots) >= 30:
                return {'error':'最多创建 30 位 Bot'}
            requested_id = re.sub(r'[^a-zA-Z0-9_-]', '', str(args.get('bot_id') or '')).lower()
            existing_ids = {b['id'] for b in bots}
            new_id = requested_id if (requested_id and requested_id not in existing_ids) else secrets.token_hex(6)
            new_bot = {
                'id': new_id,
                'name': name[:40],
                'title': '',
                'description': str(args.get('description') or f'负责专项协作的 {name}')[:160],
                'instructions': str(args.get('instructions') or f'你是 {name}，专业、直接、善用工具完成任务并汇报结果。')[:12000],
                'model': str(args.get('model') or '').strip()[:200],
                'curiosity_enabled': bool(args.get('curiosity_enabled', False)),
                'engine': 'small',
                'color': '#28B9D1',
                'shape': 'circle',
                'notify': True,
                'tools': [t['function']['name'] for t in TOOLS],
                'pinned': False,
                'hidden': False,
                'pinned_at': 0,
                'avatar_version': '',
                'created_at': time.time(),
            }
            bots.append(new_bot)
            self.store.save()
            await self.publish({'type':'bots_changed', 'bot_id':new_id})
            return {'created':True, 'bot':new_bot}
        if action in ('update', 'update_model', 'update_prompt'):
            target_q = str(args.get('bot_id') or args.get('name') or '').strip()
            target = self._resolve_bot(target_q)
            if not target:
                return {'error':f'未找到 Bot "{target_q}"'}
            if 'name' in args and str(args['name']).strip():
                target['name'] = str(args['name']).strip()[:40]
            if 'model' in args:
                target['model'] = str(args['model']).strip()[:200]
            if 'description' in args:
                target['description'] = str(args['description']).strip()[:160]
            if 'instructions' in args and str(args['instructions']).strip():
                target['instructions'] = str(args['instructions']).strip()[:12000]
            if 'curiosity_enabled' in args:
                target['curiosity_enabled'] = bool(args['curiosity_enabled'])
            target['updated_at'] = time.time()
            self.store.save()
            await self.publish({'type':'bots_changed', 'bot_id':target['id']})
            return {'updated':True, 'bot':target}
        if action == 'delete':
            target_q = str(args.get('bot_id') or args.get('name') or '').strip()
            target = self._resolve_bot(target_q)
            if not target:
                return {'error':f'未找到 Bot "{target_q}"'}
            if target['id'] == self.store.state.get('primary_bot_id'):
                return {'error':'不能删除当前主助理'}
            self.store.state['assistants'] = [b for b in bots if b['id'] != target['id']]
            self.store.save()
            await self.publish({'type':'bots_changed', 'deleted_bot_id':target['id']})
            return {'deleted':True, 'bot_id':target['id']}
        return {'error':f'未知 manage_bot action: {action}'}

    @staticmethod
    def _fmt_bj(ts: float | None) -> str:
        if not ts:
            return ''
        dt = datetime.fromtimestamp(float(ts), TZ)
        wk = ('周一', '周二', '周三', '周四', '周五', '周六', '周日')[dt.weekday()]
        return f"{dt.strftime('%Y-%m-%d %H:%M:%S')} ({wk})"

    @staticmethod
    def _parse_wall_time(raw: str, now_ts: float | None = None) -> float:
        s = raw.strip()
        base_ts = now_ts if now_ts is not None else time.time()
        now_dt = datetime.fromtimestamp(base_ts, TZ)
        m_hm = re.fullmatch(r'(\d{1,2}):(\d{2})(?::(\d{2}))?', s)
        if m_hm:
            hh, mm, ss = int(m_hm.group(1)), int(m_hm.group(2)), int(m_hm.group(3) or 0)
            target = now_dt.replace(hour=hh, minute=mm, second=ss, microsecond=0)
            if target.timestamp() <= base_ts:
                target += timedelta(days=1)
            return target.timestamp()
        s_iso = s.replace(' ', 'T') if re.match(r'^\d{4}-\d{2}-\d{2}\s+\d{1,2}:\d{2}', s) else s
        dt = datetime.fromisoformat(s_iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ)
        return dt.timestamp()

    @classmethod
    def _extract_clock_from_goal(cls, goal: str) -> str:
        m = re.search(r'(?:每天|每早|每晚|早上|上午|中午|下午|晚上|凌晨|定点|北京时间)?\s*(\d{1,2}):(\d{2})', goal)
        if m:
            hh, mm = int(m.group(1)), int(m.group(2))
            if 0 <= hh <= 23 and 0 <= mm <= 59:
                if any(k in goal for k in ('下午', '晚上', '每晚')) and 1 <= hh <= 11:
                    hh += 12
                return f'{hh:02d}:{mm:02d}'
        return ''

    @classmethod
    def _compute_next_routine_run(cls, r: dict, now_ts: float | None = None) -> float:
        base_ts = now_ts if now_ts is not None else time.time()
        daily_raw = str(r.get('daily_time') or '').strip()
        if not daily_raw:
            inferred = cls._extract_clock_from_goal(str(r.get('goal') or ''))
            if inferred:
                daily_raw = inferred
                r['daily_time'] = inferred
        if daily_raw:
            slots = []
            for part in daily_raw.split(','):
                part = part.strip()
                m = re.fullmatch(r'(\d{1,2}):(\d{2})(?::(\d{2}))?', part)
                if m:
                    slots.append((int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)))
            if slots:
                wk_raw = str(r.get('weekdays') or '').strip().lower()
                allowed_days = set(range(1, 8))
                if wk_raw and wk_raw not in ('all', '*', 'everyday', '每天'):
                    parsed_days = {int(x) for x in re.findall(r'[1-7]', wk_raw)}
                    if parsed_days:
                        allowed_days = parsed_days
                now_dt = datetime.fromtimestamp(base_ts, TZ)
                candidates = []
                for day_offset in range(0, 9):
                    day_dt = now_dt + timedelta(days=day_offset)
                    if day_dt.isoweekday() not in allowed_days:
                        continue
                    for hh, mm, ss in slots:
                        cand = day_dt.replace(hour=hh, minute=mm, second=ss, microsecond=0)
                        if cand.timestamp() > base_ts + 1:
                            candidates.append(cand.timestamp())
                if candidates:
                    return min(candidates)
        last_run = float(r.get('last_run') or base_ts)
        hours = max(0.05, float(r.get('interval_hours') or 24))
        return last_run + hours * 3600

    def _decorate_schedule_view(self) -> dict:
        timers = self.store.state.setdefault('timers', [])
        routines = self.store.state.setdefault('routines', [])
        t_view = []
        for t in timers:
            t_view.append({
                **t,
                'due_at_bj': self._fmt_bj(t.get('due_at')),
                'remaining_seconds': max(0, round(float(t.get('due_at', 0)) - time.time())) if not t.get('done') else 0,
            })
        r_view = []
        changed = False
        for r in routines:
            if not r.get('next_run') or (r.get('daily_time') and r.get('next_run', 0) < time.time() - 86400):
                r['next_run'] = self._compute_next_routine_run(r)
                changed = True
            elif not r.get('daily_time') and self._extract_clock_from_goal(str(r.get('goal') or '')):
                r['next_run'] = self._compute_next_routine_run(r)
                changed = True
            mode_desc = f"每天 {r['daily_time']} (北京时间)" if r.get('daily_time') else f"每 {r.get('interval_hours', 24)} 小时"
            if r.get('weekdays') and str(r.get('weekdays')) not in ('', 'all', '*'):
                mode_desc += f" [星期 {r['weekdays']}]"
            r_view.append({
                **r,
                'schedule_mode': mode_desc,
                'next_run_bj': self._fmt_bj(r.get('next_run')),
                'last_run_bj': self._fmt_bj(r.get('last_run')),
            })
        if changed:
            self.store.save()
        return {'now_bj': self._fmt_bj(time.time()), 'timers': t_view, 'routines': r_view}

    async def _tool_schedule_task(self, args: dict):
        action = str(args.get('action') or 'list').strip().lower()
        timers = self.store.state.setdefault('timers', [])
        routines = self.store.state.setdefault('routines', [])
        if action == 'list':
            return self._decorate_schedule_view()
        if action == 'add_timer':
            goal = str(args.get('goal') or '').strip()
            if not goal:
                return {'error':'goal 不能为空'}
            bot_id = str(args.get('bot_id') or self.bot_context.get()).strip()
            at_time = str(args.get('at_time') or args.get('daily_time') or '').strip()
            if at_time:
                try:
                    due_at = self._parse_wall_time(at_time)
                except Exception as e:
                    return {'error': f'无法解析 at_time "{at_time}"（支持 "20:35" 或 "2026-11-05 07:30"）：{e}'}
            elif args.get('delay_seconds') is not None:
                due_at = time.time() + max(5, min(604800, int(args['delay_seconds'])))
            else:
                delay_min = max(1, min(10080, int(args.get('delay_minutes') or 5)))
                due_at = time.time() + delay_min * 60
            item = {
                'id': 'timer-' + secrets.token_hex(6),
                'goal': goal[:2000],
                'bot_id': bot_id,
                'at_time': at_time,
                'due_at': due_at,
                'due_at_bj': self._fmt_bj(due_at),
                'done': False,
                'created_at': time.time(),
            }
            timers.append(item)
            self.store.save()
            self.wake.set()
            return {'scheduled':True, 'timer':item}
        if action == 'add_routine':
            goal = str(args.get('goal') or '').strip()
            if not goal:
                return {'error':'goal 不能为空'}
            hours = max(1, min(720, int(args.get('interval_hours') or 24)))
            bot_id = str(args.get('bot_id') or self.bot_context.get()).strip()
            daily_time = str(args.get('daily_time') or args.get('at_time') or '').strip()
            if not daily_time:
                daily_time = self._extract_clock_from_goal(goal)
            weekdays = str(args.get('weekdays') or '').strip()
            item = {
                'id': secrets.token_hex(8),
                'goal': goal[:2000],
                'bot_id': bot_id,
                'daily_time': daily_time,
                'weekdays': weekdays,
                'interval_hours': hours,
                'enabled': True,
                'last_run': time.time(),
                'created_at': time.time(),
            }
            item['next_run'] = self._compute_next_routine_run(item)
            item['next_run_bj'] = self._fmt_bj(item['next_run'])
            routines.append(item)
            self.store.save()
            self.wake.set()
            return {'scheduled':True, 'routine':item}
        if action == 'update':
            tid = str(args.get('task_id') or '').strip()
            for r in routines:
                if r['id'] == tid or (not tid and len(routines) == 1):
                    if 'goal' in args and str(args['goal']).strip():
                        r['goal'] = str(args['goal']).strip()[:2000]
                    if 'bot_id' in args and str(args['bot_id']).strip():
                        r['bot_id'] = str(args['bot_id']).strip()
                    if 'daily_time' in args or 'at_time' in args:
                        r['daily_time'] = str(args.get('daily_time') or args.get('at_time') or '').strip()
                    if 'weekdays' in args:
                        r['weekdays'] = str(args['weekdays'] or '').strip()
                    if 'interval_hours' in args and args['interval_hours'] is not None:
                        r['interval_hours'] = max(1, min(720, int(args['interval_hours'])))
                    if 'enabled' in args and args['enabled'] is not None:
                        r['enabled'] = bool(args['enabled'])
                    r['next_run'] = self._compute_next_routine_run(r)
                    r['next_run_bj'] = self._fmt_bj(r['next_run'])
                    self.store.save()
                    self.wake.set()
                    return {'updated':True, 'routine':r}
            for t in timers:
                if t['id'] == tid:
                    if 'goal' in args and str(args['goal']).strip():
                        t['goal'] = str(args['goal']).strip()[:2000]
                    if 'at_time' in args or 'daily_time' in args:
                        raw_at = str(args.get('at_time') or args.get('daily_time') or '').strip()
                        t['at_time'] = raw_at
                        t['due_at'] = self._parse_wall_time(raw_at)
                        t['due_at_bj'] = self._fmt_bj(t['due_at'])
                    elif 'delay_minutes' in args and args['delay_minutes'] is not None:
                        t['due_at'] = time.time() + max(1, int(args['delay_minutes'])) * 60
                        t['due_at_bj'] = self._fmt_bj(t['due_at'])
                    t['done'] = False
                    self.store.save()
                    self.wake.set()
                    return {'updated':True, 'timer':t}
            return {'error':f'未找到定时任务 "{tid}"'}
        if action == 'remove':
            tid = str(args.get('task_id') or '').strip()
            self.store.state['timers'] = [t for t in timers if t['id'] != tid]
            self.store.state['routines'] = [r for r in routines if r['id'] != tid]
            self.store.save()
            return {'removed':True, 'task_id':tid}
        return {'error':f'未知 schedule_task action: {action}'}

    async def _tool_bg_task(self, args: dict):
        action = str(args.get('action') or 'list').strip().lower()
        if action == 'list':
            return {
                'tasks': [
                    {k:v for k,v in info.items() if k != 'proc'}
                    for info in self.bg_tasks.values()
                ]
            }
        if action == 'logs':
            tid = str(args.get('task_id') or '').strip()
            info = self.bg_tasks.get(tid)
            if not info:
                return {'error':f'未找到后台任务 {tid}'}
            log_path = Path(info['log_file'])
            return {
                'task_id': tid,
                'status': info['status'],
                'exit_code': info.get('exit_code'),
                'logs': log_path.read_text('utf-8', errors='replace')[-12000:] if log_path.exists() else '',
            }
        if action == 'kill':
            tid = str(args.get('task_id') or '').strip()
            info = self.bg_tasks.get(tid)
            if not info:
                return {'error':f'未找到后台任务 {tid}'}
            proc = info.get('proc')
            if proc and info['status'] == 'running':
                try:
                    if sys.platform == 'linux':
                        os.killpg(proc.pid, signal.SIGKILL)
                    else:
                        proc.kill()
                except Exception:
                    pass
                info['status'] = 'killed'
            return {'killed':True, 'task_id':tid}
        if action == 'start':
            cmd = str(args.get('command') or '').strip()
            if not cmd:
                return {'error':'command 不能为空'}
            tid = 'bg-' + secrets.token_hex(4)
            log_dir = self.harness.root / '.bg_logs'
            log_dir.mkdir(parents=True, exist_ok=True)
            log_file = log_dir / f'{tid}.log'
            owner_bot = self.bot_context.get()
            notify_exit = bool(args.get('notify_on_exit', True))
            env = os.environ.copy()
            env['TZ'] = 'Asia/Shanghai'
            env['API_BASE_URL'] = self.store.config.effective_base_url()
            env['API_KEY'] = self.store.config.effective_api_key(big=True) or self.store.config.effective_api_key(big=False)
            env['DEFAULT_MODEL'] = self.store.config.effective_model()
            out_f = log_file.open('w', encoding='utf-8')
            proc = await asyncio.create_subprocess_shell(
                cmd, cwd=str(self.harness.root), stdout=out_f, stderr=asyncio.subprocess.STDOUT,
                start_new_session=(sys.platform == 'linux'), env=env
            )
            info = {
                'task_id': tid,
                'command': cmd[:300],
                'bot_id': owner_bot,
                'status': 'running',
                'started_at': time.time(),
                'log_file': str(log_file),
                'proc': proc,
            }
            self.bg_tasks[tid] = info
            async def watch():
                try:
                    rc = await proc.wait()
                    info['status'] = 'done' if rc == 0 else 'failed'
                    info['exit_code'] = rc
                    info['finished_at'] = time.time()
                finally:
                    out_f.close()
                if notify_exit:
                    tail = log_file.read_text('utf-8', errors='replace')[-2000:] if log_file.exists() else ''
                    msg_text = f'⚙️ [后台任务 `{tid}` 已结束 (exit={info.get("exit_code")})]\n命令：`{cmd[:120]}`\n输出摘要：\n```\n{tail}\n```'
                    m = self.store.message('assistant', msg_text, kind='result', bot_id=owner_bot, notify=False)
                    await self.publish({'type':'message', 'message':m})
            asyncio.create_task(watch())
            return {'started':True, 'task_id':tid, 'log_file':f'.bg_logs/{tid}.log'}
        return {'error':f'未知 bg_task action: {action}'}

    async def _broadcast_desktop_frame(self, res_or_frame: dict, op: str):
        if not isinstance(res_or_frame, dict):
            return
        b64 = res_or_frame.get('image')
        if not b64:
            return
        import base64
        try:
            (self.harness.root / 'desktop-latest.jpg').write_bytes(base64.b64decode(b64))
        except Exception:
            pass
        room_id = self.room_context.get() or self.bot_context.get()
        await self.publish({
            'type': 'cloud_desktop_frame',
            'bot_id': room_id,
            'speaker_id': self.bot_context.get(),
            'op': op,
            'frame': {
                'image': b64,
                'hash': res_or_frame.get('hash', ''),
                'url': res_or_frame.get('url', ''),
                'title': res_or_frame.get('title', ''),
                'width': res_or_frame.get('width', 1280),
                'height': res_or_frame.get('height', 800),
            },
        })

    async def _tool_cloud_desktop(self, args: dict):
        if self.computer is None:
            return {'error':'云桌面组件未挂载'}
        op = str(args.get('op') or 'status').strip().lower()
        try:
            if op == 'status':
                st = await self.computer.client.status(force=True)
                try:
                    fr = await self.computer.client.call('GET', '/frame')
                    await self._broadcast_desktop_frame(fr, 'status')
                except Exception:
                    pass
                return st
            if op in ('screenshot', 'capture', 'frame'):
                frame = await self.computer.client.call('GET', '/frame')
                await self._broadcast_desktop_frame(frame, 'screenshot')
                st = await self.computer.client.status(force=True)
                return {
                    'captured': True,
                    'width': frame.get('width'),
                    'height': frame.get('height'),
                    'title': frame.get('title') or st.get('title', ''),
                    'windows': st.get('windows', []),
                    'saved_to': 'desktop-latest.jpg',
                }
            if op in ('bash', 'exec', 'shell', 'terminal') or (op == 'run' and (args.get('command') or args.get('goal'))):
                cmd = str(args.get('command') or '').strip()
                goal = str(args.get('goal') or args.get('text') or args.get('url') or '').strip()
                if not cmd and goal:
                    m_url = re.search(r'https?://[^\s"\'<>]+', goal)
                    if m_url and not any(k in goal for k in (';', '&&', '|', 'python', 'pip', 'gcc', 'ls ', 'cat ', 'echo ')):
                        res = await self.computer.client.call('POST', '/navigate', body={'url': m_url.group(0), 'include_frame': True})
                        await self._broadcast_desktop_frame(res, 'navigate')
                        res = {k: v for k, v in res.items() if k != 'image'}
                        res['saved_screenshot'] = 'desktop-latest.jpg'
                        return res
                    cmd = goal
                if not cmd:
                    return {'error': 'cloud_desktop bash 需要提供 command'}
                timeout_s = max(5, min(90, int(args.get('timeout') or 25)))
                res = await self.computer.client.call('POST', '/input', body={'kind': 'bash', 'command': cmd, 'timeout': timeout_s, 'include_frame': True})
                if isinstance(res, dict) and res.get('image'):
                    await self._broadcast_desktop_frame(res, 'bash')
                    res = {k: v for k, v in res.items() if k != 'image'}
                    res['saved_screenshot'] = 'desktop-latest.jpg'
                return res
            if op == 'navigate':
                res = await self.computer.client.call('POST', '/navigate', body={'url':str(args.get('url') or args.get('text') or ''), 'include_frame':True})
                if isinstance(res, dict) and res.get('image'):
                    await self._broadcast_desktop_frame(res, 'navigate')
                    res = {k: v for k, v in res.items() if k != 'image'}
                    res['saved_screenshot'] = 'desktop-latest.jpg'
                return res
            if op in ('launch_app', 'app', 'open_app'):
                app_name = str(args.get('app') or args.get('text') or 'lite_browser').strip()
                res = await self.computer.client.call('POST', '/input', body={'kind':'launch_app', 'app':app_name, 'text':str(args.get('url') or args.get('text') or ''), 'include_frame':True})
                if isinstance(res, dict) and res.get('image'):
                    await self._broadcast_desktop_frame(res, 'launch_app')
                    res = {k: v for k, v in res.items() if k != 'image'}
                    res['saved_screenshot'] = 'desktop-latest.jpg'
                return res
            if op == 'batch':
                steps = args.get('steps') if isinstance(args.get('steps'), list) else []
                res = await self.computer.client.call('POST', '/input', body={'kind':'batch', 'steps':steps[:20], 'include_frame':True})
                if isinstance(res, dict) and res.get('image'):
                    await self._broadcast_desktop_frame(res, 'batch')
                    res = {k: v for k, v in res.items() if k != 'image'}
                    res['saved_screenshot'] = 'desktop-latest.jpg'
                return res
            if op in ('ui_tree', 'read_screen', 'nodes'):
                frame = await self.computer.client.call('GET', '/frame')
                await self._broadcast_desktop_frame(frame, 'ui_tree')
                b64 = str(frame.get('image') or '')
                st = await self.computer.client.status(force=True)
                v_tree = await self._vision_extract_ui_tree(b64, 1280, 800, pkg='cloud-desktop', target_hint=str(args.get('text') or '')) if b64 else {}
                return {
                    'ok': True,
                    'width': 1280,
                    'height': 800,
                    'title': st.get('title', ''),
                    'windows': st.get('windows', []),
                    'ui_tree': v_tree.get('summary', ''),
                    'screen_text': v_tree.get('screen_text', ''),
                    'saved_screenshot': 'desktop-latest.jpg',
                }
            if op in ('click_text', 'tap_node', 'click_node') or (op == 'click' and not args.get('x') and (args.get('text') or args.get('ref'))):
                ref_q = str(args.get('ref') or '').strip()
                txt_q = str(args.get('text') or args.get('goal') or '').strip()
                matched = self._match_vision_node(ref_q, txt_q)
                if matched is None:
                    frame = await self.computer.client.call('GET', '/frame')
                    b64 = str(frame.get('image') or '')
                    if b64:
                        await self._vision_extract_ui_tree(b64, 1280, 800, pkg='cloud-desktop', target_hint=txt_q or ref_q)
                        matched = self._match_vision_node(ref_q, txt_q)
                if matched is None:
                    return {'ok': False, 'error': f'未在云电脑画面上找到匹配「{txt_q or ref_q}」的按钮或文字，请先调 cloud_desktop(op="ui_tree") 查看当前窗口'}
                cx, cy = int(matched['center_x']), int(matched['center_y'])
                res = await self.computer.client.call('POST', '/input', body={'kind': 'click', 'x': cx, 'y': cy, 'include_frame': True})
                if isinstance(res, dict) and res.get('image'):
                    await self._broadcast_desktop_frame(res, 'click')
                    res = {k: v for k, v in res.items() if k != 'image'}
                    res['saved_screenshot'] = 'desktop-latest.jpg'
                res.update({'clicked_text': matched.get('text', txt_q), 'x': cx, 'y': cy})
                return res
            if op in ('click', 'double_click', 'right_click', 'type', 'key', 'scroll'):
                payload: dict = {'kind':op, 'include_frame':True}
                if op in ('click', 'double_click', 'right_click'):
                    payload.update(x=int(args.get('x') or 0), y=int(args.get('y') or 0))
                elif op == 'type':
                    payload.update(text=str(args.get('text') or ''))
                elif op == 'key':
                    payload.update(key=str(args.get('key') or 'Return'))
                elif op == 'scroll':
                    payload.update(x=int(args.get('x') or 640), y=int(args.get('y') or 400), direction=str(args.get('text') or 'down'), amount=4)
                res = await self.computer.client.call('POST', '/input', body=payload)
                if isinstance(res, dict) and res.get('image'):
                    await self._broadcast_desktop_frame(res, op)
                    res = {k: v for k, v in res.items() if k != 'image'}
                    res['saved_screenshot'] = 'desktop-latest.jpg'
                return res
            return {'error':f'未知 cloud_desktop op: {op}'}
        except Exception as e:
            return {'error':str(e)[:260]}

    async def emit(self, text, kind='curiosity', track=True, **extra):
        bot_id = extra.setdefault('bot_id', self.bot_context.get())
        try:
            bot_notify = get_assistant(self.store, bot_id).get('notify', True)
        except ValueError:
            bot_notify = True
        extra.setdefault('notify', bot_notify)
        m = self.store.message('assistant', text, kind, **extra)
        self.stream_seq += 1
        turn_id = self.turn_id_var.get() or m['id']
        m['seq'] = self.stream_seq
        m['turn_id'] = turn_id
        self.last_completed_stream = {
            'seq': self.stream_seq,
            'turn_id': turn_id,
            'bot_id': bot_id,
            'speaker_id': extra.get('from_bot_id') or bot_id,
            'speaker_name': extra.get('from_bot_name') or '',
            'text': m['text'],
            'final_message_id': m['id'],
            'done': True,
            'completed_at': time.time(),
        }
        await self.publish({'type':'message', 'message':m, 'seq':self.stream_seq})
        if track and extra['notify']:
            await self.escalation.track(m)
        return m

    async def _tool_notify_user(self, args: dict):
        title = str(args.get('title') or '助理提醒').strip()[:120]
        body = str(args.get('body') or '').strip()[:2000]
        urgent = bool(args.get('urgent', False))
        if not body:
            return {'error':'通知内容不能为空'}
        bot_id = self.room_context.get() or self.bot_context.get()
        bot = get_assistant(self.store, self.bot_context.get())
        m = self.store.message(
            'assistant', f'🔔 **{title}**\n{body}', 'notification',
            bot_id=bot_id, from_bot_id=bot['id'], from_bot_name=bot['name'],
            title=title, urgent=urgent, notify=True
        )
        await self.publish({'type':'message', 'message':m})
        await self.escalation.track(m)
        if urgent and m['id'] in self.store.state['pending']:
            self.store.state['pending'][m['id']]['stage'] = 3
            self.store.save()
            await self.publish({'type':'escalation', 'stage':3, 'id':m['id'], 'text':m['text'], 'demo':False})
        await self.publish({'type':'user_notification', 'id':m['id'], 'bot_id':bot_id, 'bot_name':bot['name'], 'title':title, 'body':body, 'urgent':urgent})
        return {'notified':True, 'id':m['id'], 'title':title, 'urgent':urgent}

    @staticmethod
    def _is_vision_capable_model(model_id: str) -> bool:
        m = (model_id or '').lower()
        if not m or 'deepseek' in m:
            return False
        return any(k in m for k in ('gemini', 'claude', 'gpt-4', 'gpt-5', '4o', 'vl', 'vision', 'pixtral', 'qvq'))

    async def _pick_vision_model(self) -> str:
        bot_id = self.bot_context.get()
        try:
            bot = get_assistant(self.store, bot_id)
        except Exception:
            bot = {}
        cur_model = self.store.config.effective_model(bot_model=bot.get('model', ''), big=False) or self.store.config.effective_model(bot_model=bot.get('model', ''), big=True)
        if self._is_vision_capable_model(cur_model):
            return cur_model
        avail = await self._fetch_available_models()
        preferred = (
            'antigravity/gemini-3-flash',
            'antigravity-direct/gemini-3-flash',
            'gemini-3-flash',
            'gemini-2.5-flash',
            'antigravity/claude-sonnet-4-6',
            'antigravity-direct/claude-sonnet-4-6',
        )
        for p in preferred:
            if p in avail:
                return p
        for m in avail:
            if self._is_vision_capable_model(m):
                return m
        return 'antigravity/gemini-3-flash'

    async def analyze_image_b64(self, b64: str, mime: str = 'image/jpeg', prompt: str = '', max_tokens: int = 1400) -> str:
        import hashlib
        base_url = self.store.config.effective_base_url().rstrip('/')
        api_key = self.store.config.effective_api_key(big=True) or self.store.config.effective_api_key(big=False)
        if not base_url or not api_key or not b64:
            return ''
        effective_prompt = prompt or '请完整、准确地提取并描述这张图片中的所有可见文字（按从上到下顺序保留昵称、时间、气泡对话、标题与按钮文字）以及画面关键信息。'
        cache_key = hashlib.sha256((b64[:65536] + '|' + str(len(b64)) + '|' + effective_prompt[:160]).encode('utf-8')).hexdigest()
        if cache_key in self._vision_cache:
            return self._vision_cache[cache_key]
        v_model = await self._pick_vision_model()
        payload = {
            'model': v_model,
            'messages': [{
                'role': 'user',
                'content': [
                    {'type': 'text', 'text': effective_prompt},
                    {'type': 'image_url', 'image_url': {'url': f'data:{mime};base64,{b64}'}},
                ],
            }],
            'max_tokens': max_tokens,
            'stream': False,
        }
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(35.0, connect=10.0), trust_env=False) as client:
                r = await client.post(
                    base_url + '/chat/completions',
                    headers={'Authorization': 'Bearer ' + api_key},
                    json=payload,
                )
            if r.status_code >= 400:
                return ''
            body = r.json()
            out_txt = str(body['choices'][0]['message'].get('content') or '').strip()
            if out_txt:
                if len(self._vision_cache) > 80:
                    self._vision_cache.clear()
                self._vision_cache[cache_key] = out_txt
            return out_txt
        except Exception:
            return ''

    async def read_image_file_with_vision(self, file_path: Path, prompt: str = '') -> dict:
        import base64
        if not file_path.exists():
            return {'error': f'图片文件不存在: {file_path.name}'}
        raw_bytes = file_path.read_bytes()
        ext = file_path.suffix.lower()
        mime = {
            '.png': 'image/png',
            '.webp': 'image/webp',
            '.gif': 'image/gif',
            '.bmp': 'image/bmp',
        }.get(ext, 'image/jpeg')
        b64 = base64.b64encode(raw_bytes).decode('ascii')
        readout = await self.analyze_image_b64(b64, mime=mime, prompt=prompt)
        return {
            'image_file': file_path.name,
            'size_bytes': len(raw_bytes),
            'vision_content': readout or '（画面为空白或未识别到文字）',
            'note': '已由系统原生多模态视觉直接读取图片全部内容，无需调用任何外部 OCR 或脚本。',
        }

    async def _enrich_user_message_with_vision(self, raw_text: str, current_model: str, bot_id: str, bot_name: str):
        import base64
        matches = list(dict.fromkeys(re.findall(r'((?:uploads/|[A-Za-z0-9_.-]+\.)[^\s\n"\'<>]+?\.(?:png|jpg|jpeg|webp|gif|bmp))', raw_text, flags=re.IGNORECASE)))
        if not matches:
            return raw_text
        valid_files: list[tuple[str, Path, str, str]] = []
        for rel in matches[:3]:
            clean_rel = rel.removeprefix('workspace/').strip()
            try:
                p = self.harness.path(clean_rel)
                if p.exists() and p.is_file() and p.stat().st_size <= 8_000_000:
                    ext = p.suffix.lower()
                    mime = {'.png': 'image/png', '.webp': 'image/webp', '.gif': 'image/gif', '.bmp': 'image/bmp'}.get(ext, 'image/jpeg')
                    b64 = base64.b64encode(p.read_bytes()).decode('ascii')
                    valid_files.append((clean_rel, p, mime, b64))
            except Exception:
                continue
        if not valid_files:
            return raw_text

        await self._set_stream(
            text='正在思考…',
            speaker_id=bot_id,
            speaker_name=bot_name,
            thought=f'正在通过原生视觉读取图片 {valid_files[0][1].name}…',
        )
        # Single-pass fast vision extraction (cached by SHA-256). Avoids re-sending multi-MB Base64 on every tool loop turn!
        readouts = []
        for clean_rel, p, mime, b64 in valid_files:
            txt_out = await self.analyze_image_b64(b64, mime=mime)
            if txt_out:
                readouts.append(f'【系统原生多模态视觉已直接读取图片 `{clean_rel}` 内容（无需再调 read_file 或写 OCR 脚本）】：\n{txt_out}')
        enriched_text = raw_text + ('\n\n' + '\n\n'.join(readouts) if readouts else '')
        if not readouts and self._is_vision_capable_model(current_model):
            parts: list[dict] = [{'type': 'text', 'text': enriched_text}]
            for _, _, mime, b64 in valid_files:
                parts.append({'type': 'image_url', 'image_url': {'url': f'data:{mime};base64,{b64}'}})
            return parts
        return enriched_text

    def resolve_phone_command(self, event: dict):
        req_id = str(event.get('request_id') or '')
        fut = self.phone_pending.pop(req_id, None)
        if fut and not fut.done():
            fut.set_result(event.get('result') or {'ok': True})

    async def _raw_phone_rpc(self, op: str, args: dict, timeout: float = 32.0) -> dict:
        bot_id = self.bot_context.get()
        req_id = 'ph-' + secrets.token_hex(6)
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self.phone_pending[req_id] = fut
        await self.publish({
            'type': 'phone_workspace_command',
            'request_id': req_id,
            'bot_id': bot_id,
            'op': op,
            'args': args,
            'timestamp': time.time(),
        })
        try:
            res = await asyncio.wait_for(fut, timeout=timeout)
            return res if isinstance(res, dict) else {'ok': True}
        except asyncio.TimeoutError:
            self.phone_pending.pop(req_id, None)
            return {
                'ok': False,
                'timed_out': True,
                'op': op,
                'error': '手机端在超时时间内未返回执行结果或界面内容（该操作未确认完成，严禁假设成功或编造屏幕内容；请重试一次或提醒用户打开手机端 App 保持在线）',
            }

    async def _vision_extract_ui_tree(self, b64: str, width: int, height: int, pkg: str = '', target_hint: str = '') -> dict:
        prompt = (
            f'这是一张安卓手机独立虚拟屏的实时画面（逻辑坐标范围：宽 0..{width}，高 0..{height}，当前包名：{pkg or "unknown"}）。\n'
            + (f'特别注意：请务必找出与「{target_hint}」匹配的控件或联系人/文字及其精确中心坐标！\n' if target_hint else '')
            + '请提取画面中所有可见文字（如聊天记录、联系人、标题）以及所有可点击/可输入的 UI 元素，严格只输出一个 JSON 对象（不要加任何多余解释）：\n'
            f'{{"screen_text": "按从上到下顺序完整列出画面上的所有对话文字与界面信息", "nodes": [{{"role": "Row|Button|EditText|Tab|Text", "text": "元素上的文字或图标含义", "x": {width // 2}, "y": {height // 3}}}]}}\n'
            f'注意：x 必须在 0 到 {width} 之间，y 必须在 0 到 {height} 之间，代表该控件在 {width}x{height} 屏幕上的真实像素中心点！'
        )
        raw_out = await self.analyze_image_b64(b64, mime='image/jpeg', prompt=prompt, max_tokens=1600)
        if not raw_out:
            return {'nodes': [], 'summary': '', 'screen_text': ''}
        cleaned = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw_out.strip()).strip()
        m_json = re.search(r'\{.*\}', cleaned, flags=re.DOTALL)
        parsed = {}
        if m_json:
            try:
                parsed = json.loads(m_json.group(0))
            except Exception:
                parsed = {}
        raw_nodes = parsed.get('nodes') if isinstance(parsed.get('nodes'), list) else []
        screen_text = str(parsed.get('screen_text') or raw_out).strip()
        v_nodes = []
        lines = [f'displayId=virtual package={pkg or "unknown"} source=vision_ui_tree count={len(raw_nodes)}']
        for idx, item in enumerate(raw_nodes[:45], 1):
            if not isinstance(item, dict):
                continue
            ref = f'@v{idx}'
            role = str(item.get('role') or 'View').strip()
            txt = str(item.get('text') or '').strip()
            cx = max(0, min(width - 1, int(item.get('x') or (width // 2))))
            cy = max(0, min(height - 1, int(item.get('y') or (height // 2))))
            node_obj = {'ref': ref, 'role': role, 'text': txt, 'center_x': cx, 'center_y': cy, 'clickable': True}
            v_nodes.append(node_obj)
            lines.append(f'{ref} [{role}] "{txt}" center=({cx},{cy}) click')
        if screen_text:
            lines.append(f'--- 画面完整文字内容 ---\n{screen_text}')
        self._last_vision_nodes = v_nodes
        return {
            'nodes': v_nodes,
            'summary': '\n'.join(lines),
            'screen_text': screen_text,
        }

    def _match_vision_node(self, ref: str, text: str) -> dict | None:
        clean_ref = (ref or '').strip()
        if clean_ref and re.fullmatch(r'v\d+', clean_ref, flags=re.IGNORECASE):
            clean_ref = '@' + clean_ref.lower()
        if clean_ref.startswith('@v'):
            for n in self._last_vision_nodes:
                if n['ref'].lower() == clean_ref.lower():
                    return n
        q = (text or '').strip().lower()
        if q:
            for n in self._last_vision_nodes:
                if n['text'].lower() == q:
                    return n
            for n in self._last_vision_nodes:
                if q in n['text'].lower() or (n['text'] and n['text'].lower() in q):
                    return n
        return None

    async def _auto_release_phone_if_used(self):
        if not self._used_phone_in_turn:
            return
        self._used_phone_in_turn = False
        try:
            bot_id = self.bot_context.get()
            await self.publish({
                'type': 'phone_workspace_command',
                'request_id': 'ph-auto-' + secrets.token_hex(4),
                'bot_id': bot_id,
                'op': 'auto_release',
                'args': {'op': 'auto_release', 'delay_ms': 2500},
                'timestamp': time.time(),
            })
        except Exception:
            pass

    async def _tool_phone_workspace(self, args: dict):
        import base64
        op = str(args.get('op') or 'status').strip().lower()
        if op not in ('status', 'release', 'auto_release'):
            self._used_phone_in_turn = True

        # Never delegate to local phone sub-agent op="run" from cloud tool calls (prevents fake user messages & 10-min loops)
        if op in ('run', 'task'):
            op = 'ui_tree'
            args = {'op': 'ui_tree', 'refresh': True}

        if op == 'batch' and isinstance(args.get('steps'), list):
            steps_list = [s for s in args['steps'][:10] if isinstance(s, dict) and s.get('op') != 'batch']
            batch_results = []
            last_res: dict = {'ok': True}
            for idx_s, step_item in enumerate(steps_list):
                last_res = await self._tool_phone_workspace(step_item)
                batch_results.append({'step': idx_s + 1, 'op': step_item.get('op'), 'ok': not bool(isinstance(last_res, dict) and last_res.get('error'))})
                if isinstance(last_res, dict) and last_res.get('error'):
                    break
                if idx_s + 1 < len(steps_list):
                    await asyncio.sleep(0.28)
            if isinstance(last_res, dict):
                last_res['batch_steps_executed'] = batch_results
            return last_res

        ref_arg = str(args.get('ref') or '').strip()
        text_arg = str(args.get('text') or args.get('target') or args.get('label') or '').strip()

        # Fast-path: if tapping a vision node (@v1) or tapping by text when accessibility tree had 0 nodes (e.g. WeChat)
        if op in ('tap_node', 'click_node') and (ref_arg.lower().startswith('@v') or ref_arg.lower().startswith('v') or self._last_phone_node_count == 0):
            matched = self._match_vision_node(ref_arg, text_arg)
            if matched is None and text_arg:
                snap = await self._raw_phone_rpc('preview', {'op': 'preview', 'since': -1})
                if snap.get('image'):
                    b64 = str(snap['image']).split(',')[-1]
                    w = int(snap.get('width') or self._last_phone_size[0] or 1080)
                    h = int(snap.get('height') or self._last_phone_size[1] or 2400)
                    await self._vision_extract_ui_tree(b64, w, h, target_hint=text_arg)
                    matched = self._match_vision_node(ref_arg, text_arg)
            if matched is not None:
                cx, cy = int(matched['center_x']), int(matched['center_y'])
                res = await self._raw_phone_rpc('tap', {'op': 'tap', 'x': cx, 'y': cy})
                res.update({'clicked': True, 'method': 'vision_fast_tap', 'ref': matched['ref'], 'label': matched['text'], 'x': cx, 'y': cy})
            else:
                res = await self._raw_phone_rpc(op, args)
        else:
            res = await self._raw_phone_rpc(op, args)
            # If Android Accessibility couldn't find the node (e.g. WeChat custom canvas/view), auto-fallback to Vision Fast-Tap!
            if op in ('tap_node', 'click_node', 'type_node', 'input_node') and isinstance(res, dict) and '未找到目标节点' in str(res.get('error') or ''):
                matched = self._match_vision_node(ref_arg, text_arg)
                if matched is None:
                    snap = await self._raw_phone_rpc('preview', {'op': 'preview', 'since': -1})
                    if snap.get('image'):
                        b64 = str(snap['image']).split(',')[-1]
                        w = int(snap.get('width') or self._last_phone_size[0] or 1080)
                        h = int(snap.get('height') or self._last_phone_size[1] or 2400)
                        await self._vision_extract_ui_tree(b64, w, h, target_hint=text_arg or ref_arg)
                        matched = self._match_vision_node(ref_arg, text_arg)
                if matched is not None:
                    cx, cy = int(matched['center_x']), int(matched['center_y'])
                    tap_res = await self._raw_phone_rpc('tap', {'op': 'tap', 'x': cx, 'y': cy})
                    if op in ('type_node', 'input_node') and text_arg:
                        await asyncio.sleep(0.25)
                        type_res = await self._raw_phone_rpc('text', {'op': 'text', 'text': str(args.get('text') or '')})
                        res = {**tap_res, **type_res, 'typed': True, 'method': 'vision_fast_type', 'ref': matched['ref'], 'x': cx, 'y': cy}
                    else:
                        res = {**tap_res, 'clicked': True, 'method': 'vision_fast_tap', 'ref': matched['ref'], 'label': matched['text'], 'x': cx, 'y': cy}

        if not isinstance(res, dict):
            return res

        w = int(res.get('width') or self._last_phone_size[0] or 1080)
        h = int(res.get('height') or self._last_phone_size[1] or 2400)
        if w > 100 and h > 100:
            self._last_phone_size = (w, h)
        pkg = str(res.get('package') or '')
        node_count = res.get('node_count') if 'node_count' in res else res.get('nodeCount')
        if node_count is not None:
            self._last_phone_node_count = int(node_count)

        b64_img = ''
        if res.get('image'):
            b64_img = str(res['image']).split(',')[-1]
            (self.harness.root / 'phone-latest.jpg').write_bytes(base64.b64decode(b64_img))
            res = {k: v for k, v in res.items() if k != 'image'}
            res['saved_screenshot'] = 'phone-latest.jpg'

        # Whenever Accessibility returns 0 nodes (e.g. WeChat) or op == 'capture', automatically run 1s Vision UI Tree extraction!
        need_vision_tree = (
            op in ('launch', 'ui_tree', 'dump', 'tree', 'nodes', 'read_screen', 'capture', 'tap', 'tap_node', 'click_node', 'swipe', 'text', 'type_node', 'key')
            and (op == 'capture' or self._last_phone_node_count == 0 or pkg == 'com.tencent.mm')
        )
        if need_vision_tree:
            if not b64_img:
                snap = await self._raw_phone_rpc('preview', {'op': 'preview', 'since': -1})
                if snap.get('image'):
                    b64_img = str(snap['image']).split(',')[-1]
                    w = int(snap.get('width') or w)
                    h = int(snap.get('height') or h)
                    self._last_phone_size = (w, h)
                    (self.harness.root / 'phone-latest.jpg').write_bytes(base64.b64decode(b64_img))
                    res['saved_screenshot'] = 'phone-latest.jpg'
            if b64_img:
                v_tree = await self._vision_extract_ui_tree(b64_img, w, h, pkg=pkg)
                if v_tree.get('summary'):
                    existing_summary = str(res.get('ui_tree') or res.get('summary') or '').strip()
                    if self._last_phone_node_count == 0 or not existing_summary:
                        res['ui_tree'] = v_tree['summary']
                        res['summary'] = v_tree['summary']
                        res['node_count'] = len(v_tree.get('nodes') or [])
                    else:
                        res['ui_tree'] = existing_summary + '\n' + v_tree['summary']
                    res['screen_text'] = v_tree.get('screen_text', '')
                    res['vision_enhanced'] = True
        return res

    async def _tool_send_message(self, args: dict):
        raw_target = str(args.get('to_bot_id') or '').strip()
        text = str(args.get('text') or '').strip()
        if not raw_target or not text:
            return {'error':'to_bot_id 和 text 不能为空'}
        self.store.sync_from_workspace()
        target_bot = self._resolve_bot(raw_target)
        if target_bot is None:
            available = [f"{b['id']} ({b['name']})" for b in self.store.state.get('assistants', [])]
            return {
                'error':f'未找到目标 Bot "{raw_target}"。当前可用 Bot: {", ".join(available)}。可先调用 manage_bot(action="create", name="{raw_target}") 创建它，或发给现有的 Bot。'
            }
        target_id = target_bot['id']
        from_id = self.bot_context.get()
        room_id = self.room_context.get() or from_id
        if target_id == from_id:
            return {'error':'不能给自己发送 send_message'}
        depth = self.hop_depth.get()
        if depth >= 5:
            return {'error':'Bot 间连续转发已达单次上限 (5 跳)，请先总结当前进展'}
        from_bot = get_assistant(self.store, from_id)

        # 1. Show delegation bubble in the current active room AND in target_bot's private history
        if room_id != target_id:
            room_delegation = self.store.message(
                'assistant',
                f'@{target_bot["name"]} {text}',
                kind='bot_message',
                bot_id=room_id,
                from_bot_id=from_id,
                from_bot_name=from_bot['name'],
                notify=False,
            )
            await self.publish({'type':'message', 'message':room_delegation})
        delegated_msg = self.store.message(
            'user',
            f'@{target_bot["name"]} [来自 {from_bot["name"]}]:\n{text}',
            kind='bot_message',
            bot_id=target_id,
            from_bot_id=from_id,
            from_bot_name=from_bot['name'],
            notify=False,
        )
        await self.publish({'type':'message', 'message':delegated_msg})

        # 2. Execute target_bot with live streaming into room_id!
        depth_token = self.hop_depth.set(depth + 1)
        bot_token = self.bot_context.set(target_id)
        room_token = self.room_context.set(room_id)
        sub_turn_token = self.turn_id_var.set('turn-' + secrets.token_hex(6))
        sub_prefix_token = self.stream_prefix_var.set('')
        try:
            await self._set_stream(text='正在思考…', speaker_id=target_id, speaker_name=target_bot['name'])
            allowed = target_bot.get('tools') or [t['function']['name'] for t in TOOLS]
            async def sub_progress(event):
                if event['type'] == 'turn_text':
                    self.stream_prefix_var.set(event['text'])
                    await self._set_stream(text=event['text'], speaker_id=target_id, speaker_name=target_bot['name'])
                elif event['type'] == 'tool_start':
                    await self._set_stream(text=self.stream_prefix_var.get() or f"正在调用 {event['tool']}…", tool=event['tool'], speaker_id=target_id, speaker_name=target_bot['name'])
            result = await self.harness.run(
                f'来自同事 Bot「{from_bot["name"]} ({from_id})」的协作请求：\n{text}',
                lambda msgs, tools=None: self.complete(msgs, tools=tools, big=(target_bot.get('engine') == 'big')),
                self.context() + '\n助手的角色设定：' + self.persona(),
                allowed_tools=allowed,
                on_event=sub_progress,
            )
            reply_text = result['text']
            # Record in target_bot's own window
            target_reply = self.store.message(
                'assistant',
                reply_text,
                kind='result',
                bot_id=target_id,
                from_bot_id=target_id,
                from_bot_name=target_bot['name'],
                to_bot_id=from_id,
                trace=result['trace'],
                notify=False,
            )
            await self.publish({'type':'message', 'message':target_reply})
            # Also post directly as target_bot's bubble in room_id so user sees A & B chatting in the same window!
            if room_id != target_id:
                caller_copy = self.store.message(
                    'assistant',
                    reply_text,
                    kind='bot_reply',
                    bot_id=room_id,
                    from_bot_id=target_id,
                    from_bot_name=target_bot['name'],
                    trace=result['trace'],
                    notify=False,
                )
                await self.publish({'type':'message', 'message':caller_copy})
            return {
                'status':'replied',
                'from_bot_id':target_id,
                'from_bot_name':target_bot['name'],
                'reply':reply_text,
                'trace_count':len(result['trace']),
            }
        except Exception as e:
            err_str = str(e)[:280]
            configured_model = target_bot.get('model') or self.store.config.effective_model(big=(target_bot.get('engine') == 'big')) or '未配置'
            avail_models = (await self._fetch_available_models())[:18]
            models_hint = f'当前网关真实可用模型列表：{", ".join(avail_models)}' if avail_models else '请调用 list_models() 查看可用模型'
            err_msg_text = (
                f'⚠️ **[{target_bot["name"]} 报错]**: {err_str}\n'
                f'当前配置模型: `{configured_model}`\n'
                f'💡 {models_hint}'
            )
            err_target = self.store.message(
                'assistant', err_msg_text, kind='error',
                bot_id=target_id, from_bot_id=target_id, from_bot_name=target_bot['name'], notify=False
            )
            await self.publish({'type':'message', 'message':err_target})
            if room_id != target_id:
                err_caller = self.store.message(
                    'assistant', err_msg_text, kind='error',
                    bot_id=room_id, from_bot_id=target_id, from_bot_name=target_bot['name'], notify=False
                )
                await self.publish({'type':'message', 'message':err_caller})
            return {
                'error':err_str,
                'from_bot_id':target_id,
                'from_bot_name':target_bot['name'],
                'current_model':configured_model,
                'available_models_wheelchair':avail_models,
                'self_heal_hint':f'目标 Bot 模型或调用报错！请直接调用 manage_bot(action="update", bot_id="{target_id}", model="可用模型名") 将其修改为上述 available_models_wheelchair 中的有效模型，然后再次调用 send_message 重试！',
            }
        finally:
            self.stream_prefix_var.reset(sub_prefix_token)
            self.turn_id_var.reset(sub_turn_token)
            self.room_context.reset(room_token)
            self.bot_context.reset(bot_token)
            self.hop_depth.reset(depth_token)

    AUTO_COMPACT_TOKENS = 200_000
    KEEP_RECENT_TOKENS = 64_000

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        if not text:
            return 0
        cjk = sum(1 for ch in text if '\u4e00' <= ch <= '\u9fff')
        other_bytes = len(text.encode('utf-8')) - cjk * 3
        return cjk + max(0, other_bytes) // 3

    def _estimate_messages_tokens(self, messages: list[dict]) -> int:
        total = 0
        for m in messages:
            total += 6 + self._estimate_tokens(str(m.get('content') or ''))
            if m.get('tool_calls'):
                total += self._estimate_tokens(json.dumps(m['tool_calls'], ensure_ascii=False))
        return total

    @staticmethod
    def _prune_tool_loop_messages(messages: list[dict]):
        """Keep prefix cache 100% intact across tool steps; only fold older tool outputs if an individual output exceeds 16K chars."""
        tool_indices = [idx for idx, m in enumerate(messages) if m.get('role') == 'tool']
        if len(tool_indices) <= 2:
            return
        for idx in tool_indices[:-2]:
            content = str(messages[idx].get('content') or '')
            if len(content) > 16000 and '[早期工具输出已自动折叠]' not in content:
                messages[idx]['content'] = content[:1600] + f'\n...[早期超大工具输出已自动折叠，原长 {len(content)} 字符]...'

    def context(self):
        """Pi-style lean index + core traits; full notes are read on-demand via read_file / memory_search to save tokens & preserve prefix cache."""
        raw_idx = self.vault.read('INDEX.md')
        idx = raw_idx[:3500] if len(raw_idx) <= 3500 else (raw_idx[:2200] + '\n...\n' + raw_idx[-1200:])
        raw_traits = self.vault.read('profile/user_traits.md')
        traits = raw_traits[-2500:] if len(raw_traits) > 2500 else raw_traits
        return f'【记忆库目录 INDEX.md（可用 read_file("memory_vault/...") 或 memory_search 翻阅详情）】\n{idx}\n\n【用户核心画像】\n{traits}'

    async def _auto_compact_if_needed(self, bot_id: str, force: bool = False) -> dict | None:
        bot_msgs = [m for m in self.store.state['messages'] if m.get('bot_id', 'companion') == bot_id]
        if len(bot_msgs) <= (2 if force else 6):
            return None
        sys_tokens = self._estimate_tokens(self.harness.system_instructions(self.context())) + 800
        msg_tokens = sum(self._estimate_tokens(m.get('text', '')) + 12 for m in bot_msgs)
        reported_tokens = self.last_prompt_tokens.get(bot_id, 0)
        total_tokens = max(sys_tokens + msg_tokens, reported_tokens)
        if not force and total_tokens < self.AUTO_COMPACT_TOKENS:
            return None

        kept_tokens = 0
        split_idx = max(1, len(bot_msgs) - 2)
        for i in range(len(bot_msgs) - 1, 0, -1):
            kept_tokens += self._estimate_tokens(bot_msgs[i].get('text', '')) + 12
            if kept_tokens >= self.KEEP_RECENT_TOKENS:
                split_idx = i
                break
        older = bot_msgs[:max(1, split_idx)]
        if len(older) <= 1 and older[0].get('kind') == 'compact_summary' and not force:
            return None
        summary_lines = [f"- [{m.get('from_bot_name') or m['role']}]: {m.get('text', '')[:600]}" for m in older]
        raw_transcript = '\n'.join(f"[{m.get('from_bot_name') or m['role']}]: {m.get('text', '')[:1800]}" for m in older)
        llm_summary = ''
        try:
            await self._set_stream(text=f'📦 上下文已达 200K Tokens ({total_tokens})，正在自动压缩归档早期 {len(older)} 条对话…', speaker_id=bot_id)
            sum_msg = await self.complete([
                {
                    'role': 'system',
                    'content': '你是 200K 上下文自动压缩引擎。请将以下早期对话历史浓缩为高信息密度的结构化摘要，完整保留所有关键事实、用户偏好、时间节点、文件路径、代码/命令结论以及未决事项。'
                },
                {'role': 'user', 'content': raw_transcript[:150000]}
            ], tools=None, big=False)
            llm_summary = (sum_msg.get('content') or '').strip()
        except Exception:
            llm_summary = ''
        final_summary = llm_summary or '\n'.join(summary_lines[-40:])
        archive_text = f"## 200K 自动压缩归档 ({now_iso()} · bot={bot_id} · 压缩前约 {total_tokens} tokens / {len(older)} 条消息)\n" + final_summary
        self.vault.log(archive_text)
        older_ids = {m['id'] for m in older}
        compact_marker = {
            'id': secrets.token_hex(12),
            'role': 'assistant',
            'bot_id': bot_id,
            'kind': 'compact_summary',
            'text': f'📦 [上下文达 200K Tokens，已自动压缩早期 {len(older)} 条对话并归档至 memory_vault/timeline]：\n' + final_summary,
            'created_at': older[-1].get('created_at', time.time()),
            'notify': False,
        }
        new_all = []
        inserted = False
        for m in self.store.state['messages']:
            if m['id'] in older_ids:
                if not inserted:
                    new_all.append(compact_marker)
                    inserted = True
                continue
            new_all.append(m)
        self.store.state['messages'] = new_all
        new_est = sys_tokens + kept_tokens + self._estimate_tokens(final_summary)
        self.last_prompt_tokens[bot_id] = new_est
        self.store.save()
        return {'compacted_messages': len(older), 'before_tokens': total_tokens, 'after_tokens': new_est, 'summary': final_summary}

    async def curiosity(self, manual=False, bot_id=None):
        if not manual and (self.store.state['snooze_until'] > time.time() or not self.store.config.enabled):
            return {'suppressed':True}
        async with self.lock:
            target_id = bot_id or self.store.state['primary_bot_id']
            if not manual:
                bot_history = [m for m in self.store.state['messages'] if m.get('bot_id', 'companion') == target_id]
                if bot_history and bot_history[-1].get('kind') == 'curiosity':
                    # Unanswered backoff: do not burn quota repeating proactive outreach if the user hasn't replied yet
                    self.store.state['last_curiosity'] = time.time()
                    self.store.save()
                    return {'suppressed': True, 'reason': 'unanswered_backoff'}
            marker = self.bot_context.set(target_id)
            room_marker = self.room_context.set(target_id)
            self.activity = 'curiosity'
            try:
                bot = get_assistant(self.store, target_id)
                api_key = self.store.config.effective_api_key(big=False) or self.store.config.effective_api_key(big=True)
                model = self.store.config.effective_model(bot_model=bot.get('model',''), big=False) or self.store.config.effective_model(bot_model=bot.get('model',''), big=True)
                trace = []
                if api_key and model:
                    recent_items = [m for m in self.store.state['messages'] if m.get('bot_id','companion') == target_id][-8:]
                    recent_digest = [
                        {
                            'role': m.get('role', 'user'),
                            'speaker': '主人' if m.get('role') == 'user' else (m.get('from_bot_name') or bot['name']),
                            'kind': m.get('kind', 'chat'),
                            'text': m.get('text', '')[:240],
                        }
                        for m in recent_items
                    ]
                    allowed = set(bot.get('tools') or [t['function']['name'] for t in TOOLS]) & {
                        'read_file', 'write_file', 'edit_file', 'memory_search', 'web_search', 'web_fetch', 'schedule_task'
                    }
                    available_tools = [t for t in TOOLS if t['function']['name'] in allowed]
                    opps = self.vault.read('tasks/sniffed_opportunities.md')[:4000]
                    deadlines = self.vault.read('tasks/active_deadlines.md')[:4000]
                    routines_md = self.vault.read('playbooks/routines.md')[:4000]
                    schedule_md = ''
                    try:
                        schedule_md = self.vault.read('profile/course_schedule.md')[:6000]
                    except Exception:
                        pass
                    sys_prompt = (
                        self.harness.system_instructions(self.context()) + '\n\n'
                        '当前北京时间：' + self._fmt_bj(time.time()) + ' (' + now_iso() + ')\n'
                        '你的身份设定：' + self.persona() + '\n\n'
                        '【当前课表参考】\n' + (schedule_md or '（未记录）') + '\n\n'
                        '【当前待确认死线 active_deadlines.md】\n' + deadlines + '\n\n'
                        '【当前常驻自动化 routines.md】\n' + routines_md + '\n\n'
                        '【当前代劳机会池 sniffed_opportunities.md】\n' + opps + '\n\n'
                        '【✨ 云端主动嗅探与高价值提问（Actionable Curiosity）】\n'
                        '现在是你在云端主动发起一次「真正有用」的提问或情报投递的时刻！\n'
                        '⚠️ **绝对禁止的废话/逼话黑名单（违反即算严重失职）**：\n'
                        '- 严禁顺着最近几句聊天记录瞎延伸八卦或闲聊（例如：禁止问“这套系统你是怎么搭的”、“你那199个App常用几个/猜对了吗”、“杠精要不要试试”等毫无产出的废话）！\n'
                        '- 严禁在好奇心里复读或念叨刚才聊过的报错、手机连接状态、Root状态或上一条已经问过的话题！\n'
                        '- 严禁空手问“在忙什么呢”、“今天过得怎么样”等毫无信息量的寒暄！\n\n'
                        '✅ **你必须做的事（二选一，每次只聚焦一件事，2～4句话内说清）**：\n'
                        '1. **【先查硬核干货，再问要不要落地】**：先调用 `web_search` 搜索一条跟主人实际需求强相关的最新实用情报（例如：最新高性价比/免费大模型 API 动态、Claude/Grok/开源 Agent 实用工具或脚本、或者结合当前专业课的实用工程计算/资料工具），**第一句直接给出具体干货结论（带真实名称/关键细节），第二句问主人要不要你帮他在云端工作区配好、写成脚本或设为日常追踪**。\n'
                        '2. **【直击学业/日常工作流的具体代劳卡点】**：对照上面的课表、`active_deadlines.md` 和 `routines.md`，找出当前真正空白且能立刻帮主人省事的一项具体事务来问（例如：询问本周《高速铁路技术》《混凝土结构设计原理》《工程测量》等课程有没有近期要交的作业/实验报告/课程设计需要提前备资料和设死线；或者询问每天有没有固定要盯的网站/教务通知/GitHub项目/价格变动需要你写成后台定时巡检）。\n'
                        '**核心检验标准**：主人只需回你一句话（如“有，周四交XXX”或“帮我盯一下XXX”），你就能立刻动手替他干一件实事！'
                    )
                    messages = [
                        {'role':'system', 'content':sys_prompt},
                        {'role':'user', 'content':'最近对话摘要（仅用于防重复，严禁顺着里面的闲聊或报错继续尬聊）：\n' + json.dumps(recent_digest, ensure_ascii=False)}
                    ]
                    msg = {'content': ''}
                    for _ in range(8):
                        msg = await self.complete(messages, tools=available_tools or None)
                        calls = msg.get('tool_calls') or []
                        if not calls:
                            break
                        messages.append(msg)
                        for c in calls[:6]:
                            try:
                                args = json.loads(c['function']['arguments'])
                            except (ValueError, KeyError):
                                args = {}
                            name = c['function']['name']
                            started = asyncio.get_running_loop().time()
                            result = await self.harness.execute(name, args) if name in allowed else {'error':'此助手未启用该工具'}
                            trace.append({'tool':name, 'arguments':args, 'result':result, 'duration_ms':round((asyncio.get_running_loop().time()-started)*1000)})
                            messages.append({'role':'tool', 'tool_call_id':c['id'], 'content':json.dumps(result, ensure_ascii=False)})
                    text = (msg.get('content') or '').strip()
                    if not text and trace:
                        summary = await self.complete(
                            messages + [{'role':'user', 'content':'请基于以上探索结果，直接用自然亲切的语气对主人说一句主动搭话并带一个有趣的小问题，不要再调用工具。'}],
                            tools=None
                        )
                        text = (summary.get('content') or '').strip()
                    text = text or '主人这会儿在忙什么呢？有什么好玩的事跟余聊聊吗？'
                    mode = 'small_brain'
                else:
                    text = '这会儿在忙什么呢？挑一件有点卡住的小事告诉我，余先陪你把第一步捋清。'
                    mode = 'local_prompt'
                self.store.state['last_curiosity'] = time.time()
                self.store.save()
                extra = {'trace': trace, 'from_bot_id': target_id, 'from_bot_name': bot['name']} if trace else {'from_bot_id': target_id, 'from_bot_name': bot['name']}
                emitted = await self.emit(text, 'curiosity', engine=mode, **extra)
                if bot.get('notify', True):
                    await self.publish({
                        'type': 'user_notification',
                        'id': emitted['id'],
                        'bot_id': target_id,
                        'bot_name': bot['name'],
                        'title': f"{bot['name']} · 主动搭话",
                        'body': text[:300],
                        'urgent': False,
                    })
                return emitted
            finally:
                self.live_stream = None
                self.room_context.reset(room_marker)
                self.bot_context.reset(marker)
                self.activity = self.ready_state()

    def ready_state(self):
        has_key = bool(self.store.config.effective_api_key(big=False) or self.store.config.effective_api_key(big=True))
        has_model = bool(self.store.config.effective_model(big=False) or self.store.config.effective_model(big=True))
        return 'curiosity_idle' if (has_key and has_model) else 'awaiting_config'

    async def enqueue_chat(self, text: str, bot_id: str = 'companion', mode: str = 'auto') -> dict:
        bot = get_assistant(self.store, bot_id)
        turn_id = 'turn-' + secrets.token_hex(6)
        self.store.state['last_user'] = time.time()
        m = self.store.message('user', text, bot_id=bot_id)
        self.stream_seq += 1
        m['seq'] = self.stream_seq
        m['turn_id'] = turn_id
        self.vault.log('用户：' + text)
        self.store.save()
        await self.publish({'type': 'message', 'message': m, 'seq': self.stream_seq})
        visible = {msg['id'] for msg in self.store.state['messages'] if msg.get('bot_id', 'companion') == bot_id}
        await self.escalation.ack([mid for mid in self.store.state['pending'] if mid in visible])

        marker = self.bot_context.set(bot_id)
        room_marker = self.room_context.set(bot_id)
        turn_token = self.turn_id_var.set(turn_id)
        prefix_token = self.stream_prefix_var.set('')
        try:
            self.activity = 'thinking'
            exec_obj = {'bot_id': bot_id, 'goal': text[:200], 'trace': [], 'tool': None, 'started_at': time.time()}
            self.executions_by_bot[bot_id] = exec_obj
            self.execution = exec_obj
            await self._set_stream(text='正在思考…', speaker_id=bot_id, speaker_name=bot['name'])
        finally:
            self.stream_prefix_var.reset(prefix_token)
            self.turn_id_var.reset(turn_token)
            self.room_context.reset(room_marker)
            self.bot_context.reset(marker)

        async def _bg():
            try:
                await self.chat(text, bot_id=bot_id, mode=mode, pre_recorded_user_msg=m, turn_id=turn_id)
            except Exception as e:
                err_msg = f'⚠️ [来自 {bot["name"]} ({bot_id}) 的报错]: {str(e)[:260]}'
                await self.emit(err_msg, 'error', track=False, bot_id=bot_id)
                primary_id = self.store.state.get('primary_bot_id', 'companion')
                if bot_id != primary_id:
                    await self.emit(
                        err_msg + '\n（你可以在当前窗口让主助理读取 bots.json / settings.json 并修复该 Bot 的模型配置）',
                        'error', track=False, bot_id=primary_id, from_bot_id=bot_id, from_bot_name=bot['name']
                    )

        asyncio.create_task(_bg())
        return {
            'queued': True,
            'turn_id': turn_id,
            'user_message': m,
            'recent_tail': self.store.state['messages'][-10:],
            'seq': self.stream_seq,
        }

    async def chat(self, text, bot_id='companion', mode='auto', pre_recorded_user_msg=None, turn_id=None):
        bot = get_assistant(self.store, bot_id)
        has_api = bool((self.store.config.effective_api_key(big=False) or self.store.config.effective_api_key(big=True)) and (self.store.config.effective_model(big=False) or self.store.config.effective_model(big=True)))
        if mode == 'execute' or (mode == 'auto' and bot.get('engine') == 'big' and not has_api):
            return await self.research(text, manual=True, bot_id=bot_id, record_user=(pre_recorded_user_msg is None))
        marker = self.bot_context.set(bot_id)
        room_marker = self.room_context.set(bot_id)
        try:
            return await self._chat(text, pre_recorded_user_msg=pre_recorded_user_msg, turn_id=turn_id)
        finally:
            self.room_context.reset(room_marker)
            self.bot_context.reset(marker)

    async def _chat(self, text, pre_recorded_user_msg=None, turn_id=None):
        bot_id = self.bot_context.get()
        async with self._get_bot_lock(bot_id):
            bot = get_assistant(self.store, bot_id)
            turn_token = self.turn_id_var.set(turn_id or ('turn-' + secrets.token_hex(6)))
            prefix_token = self.stream_prefix_var.set('')
            if pre_recorded_user_msg is None:
                self.store.state['last_user'] = time.time()
                m = self.store.message('user', text, bot_id=bot_id)
                self.stream_seq += 1
                m['seq'] = self.stream_seq
                self.vault.log('用户：' + text)
                await self.publish({'type':'message', 'message':m, 'seq':self.stream_seq})
                visible = {m['id'] for m in self.store.state['messages'] if m.get('bot_id','companion') == bot_id}
                await self.escalation.ack([mid for mid in self.store.state['pending'] if mid in visible])
            else:
                m = pre_recorded_user_msg
            if any(k in text for k in ('别吵','免打扰','安静一小时')):
                await self.escalation.snooze()
                return await self.emit('好，余安静一小时。你先忙，需要时再来找我。', 'chat', track=False)
            self.activity = 'thinking'
            cur_task = asyncio.current_task()
            self.active_task = cur_task
            if cur_task:
                self.active_tasks_by_bot[bot_id] = cur_task
            my_exec = {'bot_id':bot_id, 'goal':text[:200], 'trace':[], 'tool':None, 'started_at':time.time()}
            self.executions_by_bot[bot_id] = my_exec
            self.execution = my_exec
            try:
                api_key = self.store.config.effective_api_key(big=False) or self.store.config.effective_api_key(big=True)
                model = self.store.config.effective_model(bot_model=bot.get('model',''), big=False) or self.store.config.effective_model(bot_model=bot.get('model',''), big=True)
                if not (api_key and model):
                    return await self.emit('记下了。具体最卡的是哪一步，理想结果又是什么？\n\n目前使用本地引导；在右上角「设置」中填入 API Key 并选择模型后，就能直接读写工作区、调工具和安排其它 Bot 干活啦。', 'curiosity', engine='local_prompt')

                await self._auto_compact_if_needed(bot_id)
                recent = []
                for msg_item in self.store.state['messages']:
                    if msg_item.get('bot_id', 'companion') == bot_id and msg_item['role'] in ('user', 'assistant'):
                        txt = (msg_item.get('text') or '').strip()
                        if msg_item['role'] == 'assistant' and txt in ('已完成操作。', '收到，已处理完毕。'):
                            continue
                        prefix = ''
                        if msg_item.get('from_bot_name') and msg_item.get('from_bot_id') != bot_id:
                            prefix = f"[{msg_item['from_bot_name']}]: "
                        recent.append({'role': msg_item['role'], 'content': prefix + txt})
                if recent and recent[-1]['role'] == 'user' and isinstance(recent[-1].get('content'), str):
                    enriched = await self._enrich_user_message_with_vision(
                        recent[-1]['content'], model, bot_id, bot['name']
                    )
                    if isinstance(enriched, str):
                        recent[-1]['content'] = f"{enriched}\n\n[当前时间: {self._fmt_bj(time.time())}]"
                    else:
                        recent[-1]['content'] = enriched

                # 100% static system prompt (NO per-second timestamp here!) so upstream LLM Prefix Cache hits 100% across turns & tool steps
                system = (
                    self.harness.system_instructions(self.context()) + '\n\n'
                    '你的角色设定：' + self.persona() + '\n\n'
                    '注意：请直接用自然、干脆的语言回复主人，并在需要时直接调用工具（如 phone_workspace 优先使用 ui_tree/tap_node/type_node 操作手机虚拟屏、send_message 给其它 Bot 发消息、manage_bot 创建或修改 Bot、list_models 查模型、read_file/write_file/run_bash 操作工作区）。'
                )
                allowed = set(bot.get('tools') or [t['function']['name'] for t in TOOLS])
                available_tools = [t for t in TOOLS if t['function']['name'] in allowed]
                messages = [{'role':'system', 'content':system}, *recent]
                trace = []
                accumulated_texts: list[str] = []
                parsed_meta: dict = {}
                await self._set_stream(text='正在思考…', speaker_id=bot_id, speaker_name=bot['name'])
                msg = {'content': '收到。'}
                use_big = (bot.get('engine') == 'big')
                for _ in range(40):
                    self._prune_tool_loop_messages(messages)
                    if self._estimate_messages_tokens(messages) >= self.AUTO_COMPACT_TOKENS:
                        for old_m in messages[1:-4]:
                            if old_m.get('role') == 'tool' and len(str(old_m.get('content') or '')) > 400:
                                old_m['content'] = str(old_m['content'])[:280] + '\n...[已触发上下文自动压缩]...'
                    msg = await self.complete(messages, tools=available_tools or None, big=use_big)
                    turn_content = (msg.get('content') or '').strip()
                    if turn_content:
                        clean_piece, piece_meta = self._extract_reply_piece(turn_content)
                        if piece_meta:
                            parsed_meta.update(piece_meta)
                        piece_to_add = clean_piece or turn_content
                        if not accumulated_texts or accumulated_texts[-1] != piece_to_add:
                            accumulated_texts.append(piece_to_add)
                        self.stream_prefix_var.set('\n\n'.join(accumulated_texts))
                    calls = msg.get('tool_calls') or []
                    if not calls:
                        break
                    messages.append(msg)
                    for c in calls[:24]:
                        try:
                            args = json.loads(c['function']['arguments'])
                        except (ValueError, KeyError):
                            args = {}
                        name = c['function']['name']
                        my_exec['tool'] = name
                        self.execution = my_exec
                        stream_txt = '\n\n'.join(accumulated_texts) if accumulated_texts else f'正在调用 {name}…'
                        await self._set_stream(text=stream_txt, tool=name, speaker_id=bot_id, speaker_name=bot['name'], thought=self._summarize_streaming_tool(name, c['function'].get('arguments') or ''))
                        await self.publish({'type':'execution', 'execution':my_exec})
                        started = asyncio.get_running_loop().time()
                        result = await self.harness.execute(name, args) if name in allowed else {'error':'此助手未启用该工具'}
                        entry = {'tool':name, 'arguments':args, 'result':result, 'duration_ms':round((asyncio.get_running_loop().time()-started)*1000)}
                        trace.append(entry)
                        my_exec['trace'].append(entry)
                        my_exec['tool'] = None
                        self.execution = my_exec
                        await self._set_stream(
                            text='\n\n'.join(accumulated_texts) if accumulated_texts else '正在思考…',
                            tool=None,
                            speaker_id=bot_id,
                            speaker_name=bot['name'],
                            thought=f'已完成 {len(trace)} 步操作，正在继续思考与生成…'
                        )
                        await self.publish({'type':'execution', 'execution':my_exec})
                        messages.append({'role':'tool', 'tool_call_id':c['id'], 'content':json.dumps(result, ensure_ascii=False)})

                content_str = (msg.get('content') or '').strip()
                if not content_str and trace:
                    await self._set_stream(text='\n\n'.join(accumulated_texts) if accumulated_texts else '正在整理回复…', speaker_id=bot_id, speaker_name=bot['name'])
                    final_msg = await self.complete(
                        messages + [{'role':'user', 'content':'请基于以上工具执行结果，直接用自然、清晰的话回复主人，不要再调用工具。'}],
                        tools=None
                    )
                    content_str = (final_msg.get('content') or '').strip()
                    if content_str:
                        clean_piece, piece_meta = self._extract_reply_piece(content_str)
                        if piece_meta:
                            parsed_meta.update(piece_meta)
                        piece_to_add = clean_piece or content_str
                        if not accumulated_texts or accumulated_texts[-1] != piece_to_add:
                            accumulated_texts.append(piece_to_add)
                        self.stream_prefix_var.set('\n\n'.join(accumulated_texts))
                joined_reply = '\n\n'.join(accumulated_texts).strip()
                if not joined_reply and trace:
                    last_tool = trace[-1]
                    joined_reply = f"已执行 `{last_tool['tool']}`，结果：{json.dumps(last_tool['result'], ensure_ascii=False)[:400]}"
                data = dict(parsed_meta)
                data['reply'] = joined_reply or content_str or '收到，已处理完毕。'
                for trait in data.get('traits', [])[:5]:
                    if isinstance(trait, str):
                        self.vault.append('profile/user_traits.md', f'- {now_iso()}（用户陈述）{trait}')
                op = data.get('opportunity')
                if isinstance(op, dict) and op.get('title') and op.get('goal'):
                    op = {'title':str(op['title'])[:200], 'goal':str(op['goal'])[:3000], 'ready':op.get('ready') is True, 'done':False, 'created_at':time.time()}
                    self.store.state['opportunities'].append(op)
                    self.vault.append('tasks/sniffed_opportunities.md', f'## {op["title"]}\n\n{op["goal"]}\n\n状态：' + ('上下文已对齐，可试做一步' if op['ready'] else '仍需追问'))
                deadline = data.get('deadline')
                if isinstance(deadline, dict):
                    try:
                        self.add_deadline(deadline['title'], deadline['due_at'])
                    except (ValueError, KeyError):
                        pass
                reply_out = str(data.get('reply', '收到。'))
                self.vault.log('助理：' + reply_out)
                self.vault.reindex()
                self.store.save()
                self.wake.set()
                extra = {'trace': trace, 'from_bot_id': bot_id, 'from_bot_name': bot['name']} if trace else {'from_bot_id': bot_id, 'from_bot_name': bot['name']}
                return await self.emit(reply_out, 'chat', engine='small_brain', track=False, **extra)
            except asyncio.CancelledError:
                return await self.emit('本次执行已停止。', 'stopped', track=False, trace=list(my_exec['trace']))
            finally:
                await self._auto_release_phone_if_used()
                self.streams_by_bot.pop(bot_id, None)
                self.executions_by_bot.pop(bot_id, None)
                self.active_tasks_by_bot.pop(bot_id, None)
                self.live_stream = next(iter(self.streams_by_bot.values()), None)
                self.execution = next(iter(self.executions_by_bot.values()), None)
                self.active_task = next(iter(self.active_tasks_by_bot.values()), None)
                self.activity = 'thinking' if self.executions_by_bot else self.ready_state()
                self.stream_prefix_var.reset(prefix_token)
                self.turn_id_var.reset(turn_token)

    async def research(self, goal=None, manual=False, bot_id=None, record_user=False):
        bot_id = bot_id or self.store.state['primary_bot_id']
        get_assistant(self.store, bot_id)
        marker = self.bot_context.set(bot_id)
        room_marker = self.room_context.set(bot_id)
        try:
            return await self._research(goal, manual, record_user)
        finally:
            self.room_context.reset(room_marker)
            self.bot_context.reset(marker)

    async def _research(self, goal=None, manual=False, record_user=False):
        async with self.lock:
            bot_id = self.bot_context.get()
            bot = get_assistant(self.store, bot_id)
            turn_token = self.turn_id_var.set('turn-' + secrets.token_hex(6))
            prefix_token = self.stream_prefix_var.set('')
            self.activity = 'deep_research'
            self.active_task = asyncio.current_task()
            self.execution = {'bot_id':bot_id, 'goal':goal or '整理任务池', 'trace':[], 'tool':None, 'started_at':time.time()}
            try:
                if record_user:
                    self.store.state['last_user'] = time.time()
                    message = self.store.message('user', goal, bot_id=bot_id)
                    self.stream_seq += 1
                    message['seq'] = self.stream_seq
                    self.vault.log('用户：' + goal)
                    await self.publish({'type':'message', 'message':message, 'seq':self.stream_seq})
                    visible = {m['id'] for m in self.store.state['messages'] if m.get('bot_id','companion') == bot_id}
                    await self.escalation.ack([mid for mid in self.store.state['pending'] if mid in visible])
                ready = next((x for x in reversed(self.store.state['opportunities']) if x['ready'] and not x['done']), None)
                goal = goal or (ready['goal'] if ready else None)
                api_key = self.store.config.effective_api_key(big=True) or self.store.config.effective_api_key(big=False)
                model = self.store.config.effective_model(bot_model=bot.get('model',''), big=True) or self.store.config.effective_model(bot_model=bot.get('model',''), big=False)
                if api_key and model and goal:
                    await self._set_stream(text='正在思考与执行任务…', speaker_id=bot_id, speaker_name=bot['name'])
                    async def progress(event):
                        if event['type'] == 'turn_text':
                            self.stream_prefix_var.set(event['text'])
                            await self._set_stream(text=event['text'], speaker_id=bot_id, speaker_name=bot['name'])
                        elif event['type'] == 'tool_start':
                            self.execution['tool'] = event['tool']
                            await self._set_stream(text=self.stream_prefix_var.get() or f"正在调用 {event['tool']}…", tool=event['tool'], speaker_id=bot_id, speaker_name=bot['name'])
                            await self.publish({'type':'execution', 'execution':self.execution})
                        else:
                            self.execution['trace'].append(event['entry'])
                            self.execution['tool'] = None
                            await self.publish({'type':'execution', 'execution':self.execution})
                    result = await self.harness.run(
                        goal,
                        lambda messages, tools=None: self.complete(messages, tools, big=True),
                        self.context() + '\n助手的角色设定：' + self.persona(),
                        allowed_tools=bot['tools'],
                        on_event=progress
                    )
                else:
                    content = self.vault.read('tasks/sniffed_opportunities.md')
                    allowed = bot['tools']
                    reason = '还没有上下文明确的代劳目标' if not goal else '大脑 API 尚未配置'
                    if {'write_file','read_file'} <= set(allowed):
                        write = await self.harness.execute('write_file', {'path':'research/opportunities-snapshot.md', 'content':content})
                        read = await self.harness.execute('read_file', {'path':'research/opportunities-snapshot.md'})
                        result = {
                            'text':f'余先把当前任务池整理成了一份工作区快照。\n\n{reason}，本轮执行了真实文件写入和回读，尚未调用外部模型或搜索。\n\n' + content[:2500],
                            'trace':[
                                {'tool':'write_file', 'arguments':{'path':'research/opportunities-snapshot.md'}, 'result':write},
                                {'tool':'read_file', 'arguments':{'path':'research/opportunities-snapshot.md'}, 'result':read}
                            ]
                        }
                    else:
                        result = {'text':reason + '。请在设置中配置执行模型，再开始这项任务。此助手没有启用完整的文件快照工具，本轮未执行任何工具。', 'trace':[]}
                if ready:
                    ready['done'] = True
                self.store.state['last_research'] = time.time()
                self.store.save()
                self.vault.log('完成一次任务执行：' + (goal or '本地任务池快照'))
                return await self.emit(result['text'] + '\n\n这是你需要的吗？以后要我接着干吗？', 'result', trace=result['trace'], goal=goal or '整理任务池快照', from_bot_id=bot_id, from_bot_name=bot['name'])
            except asyncio.CancelledError:
                return await self.emit('本次执行已停止。已完成的文件修改会保留，可以在工具记录中检查。', 'stopped', track=False, trace=list(self.execution['trace']))
            finally:
                await self._auto_release_phone_if_used()
                self.live_stream = None
                self.execution = None
                self.active_task = None
                self.activity = self.ready_state()
                self.stream_prefix_var.reset(prefix_token)
                self.turn_id_var.reset(turn_token)

    async def confirm(self, mid, accepted):
        m = next((m for m in self.store.state['messages'] if m['id'] == mid and m['kind'] == 'result'), None)
        if not m: raise ValueError('成果不存在')
        if m.get('decision'):
            return {'already_decided':True, 'decision':m['decision']}
        m['decision'] = 'accepted' if accepted else 'rejected'
        if accepted:
            routine = {'id':mid, 'goal':m.get('goal', m['text'][:1000]), 'bot_id':m.get('bot_id','companion'), 'interval_hours':24, 'last_run':time.time(), 'enabled':True}
            self.store.state['routines'].append(routine)
            self.vault.append('playbooks/routines.md', f'## 用户确认 · {now_iso()}\n\n- ID: {mid}\n- 职责: {routine["goal"]}\n- 触发: 每 24 小时（免打扰/夜间顺延）\n- 每次完成后仍需用户验收\n')
        else:
            self.vault.log('用户否决成果 ' + mid + '，不固化职责。')
        self.store.save()
        await self.escalation.ack([mid])
        await self.publish({'type':'decision', 'id':mid, 'accepted':accepted})
        return {'saved':True, 'routine_created':accepted}

    def add_deadline(self, title, due_at):
        dt = datetime.fromisoformat(due_at)
        if dt.tzinfo is None: raise ValueError('截止时间必须含时区')
        item = {'id':str(time.time_ns()), 'title':title[:300], 'due_at':dt.timestamp(), 'notified':False}
        self.store.state['deadlines'].append(item)
        self.store.save()
        self.vault.append('tasks/active_deadlines.md', f'- [ ] {title} · {dt.astimezone(TZ).isoformat()} · ID {item["id"]}')
        return item

    async def run_scheduled(self, goal: str, bot_id: str | None = None, label: str = '定时任务'):
        target_id = bot_id or self.store.state.get('primary_bot_id', 'companion')
        marker = self.bot_context.set(target_id)
        room_marker = self.room_context.set(target_id)
        try:
            async with self.lock:
                bot = get_assistant(self.store, target_id)
                self.activity = 'deep_research'
                self.active_task = asyncio.current_task()
                self.execution = {'bot_id': target_id, 'goal': goal[:200], 'trace': [], 'tool': None, 'started_at': time.time()}
                try:
                    recent = []
                    for msg_item in self.store.state['messages']:
                        if msg_item.get('bot_id', 'companion') == target_id and msg_item['role'] in ('user', 'assistant'):
                            txt = (msg_item.get('text') or '').strip()
                            if msg_item['role'] == 'assistant' and txt in ('已完成操作。', '收到，已处理完毕。'):
                                continue
                            recent.append({'role': msg_item['role'], 'content': txt[:1500]})
                    recent = recent[-24:]
                    system = (
                        self.harness.system_instructions(self.context()) + '\n\n'
                        '当前北京时间：' + self._fmt_bj(time.time()) + ' (' + now_iso() + ')\n'
                        '你的角色设定：' + self.persona() + '\n\n'
                        f'【⏰ 云端{label}准点触发】\n'
                        '请按以下定时任务目标直接执行（可按需调用 read_file 查阅 memory_vault、web_search、notify_user 等工具），然后用自然、简练的话直接向主人汇报结果。不要在结尾加“这是你需要的吗？以后要我接着干吗？”等机械套话。'
                    )
                    allowed = set(bot.get('tools') or [t['function']['name'] for t in TOOLS])
                    available_tools = [t for t in TOOLS if t['function']['name'] in allowed]
                    messages = [
                        {'role': 'system', 'content': system},
                        *recent,
                        {'role': 'user', 'content': f'⏰ [{label}到点触发]：{goal}'}
                    ]
                    trace = []
                    accumulated_texts: list[str] = []
                    notified_explicitly = False
                    sched_turn_token = self.turn_id_var.set('turn-' + secrets.token_hex(6))
                    sched_prefix_token = self.stream_prefix_var.set('')
                    await self._set_stream(text=f'⏰ 正在执行{label}…', speaker_id=target_id, speaker_name=bot['name'])
                    msg = {'content': ''}
                    use_big = (bot.get('engine') == 'big')
                    for _ in range(30):
                        self._prune_tool_loop_messages(messages)
                        msg = await self.complete(messages, tools=available_tools or None, big=use_big)
                        turn_txt = (msg.get('content') or '').strip()
                        if turn_txt:
                            if not accumulated_texts or accumulated_texts[-1] != turn_txt:
                                accumulated_texts.append(turn_txt)
                            self.stream_prefix_var.set('\n\n'.join(accumulated_texts))
                        calls = msg.get('tool_calls') or []
                        if not calls:
                            break
                        messages.append(msg)
                        for c in calls[:16]:
                            try:
                                args = json.loads(c['function']['arguments'])
                            except (ValueError, KeyError):
                                args = {}
                            name = c['function']['name']
                            if name == 'notify_user':
                                notified_explicitly = True
                            self.execution['tool'] = name
                            await self._set_stream(text='\n\n'.join(accumulated_texts) if accumulated_texts else f'正在调用 {name}…', tool=name, speaker_id=target_id, speaker_name=bot['name'])
                            await self.publish({'type': 'execution', 'execution': self.execution})
                            started = asyncio.get_running_loop().time()
                            result = await self.harness.execute(name, args) if name in allowed else {'error': '此助手未启用该工具'}
                            entry = {'tool': name, 'arguments': args, 'result': result, 'duration_ms': round((asyncio.get_running_loop().time() - started) * 1000)}
                            trace.append(entry)
                            self.execution['trace'].append(entry)
                            self.execution['tool'] = None
                            await self.publish({'type': 'execution', 'execution': self.execution})
                            messages.append({'role': 'tool', 'tool_call_id': c['id'], 'content': json.dumps(result, ensure_ascii=False)})
                    final_text = '\n\n'.join(accumulated_texts).strip() or (msg.get('content') or '').strip()
                    if not final_text and trace:
                        sum_msg = await self.complete(
                            messages + [{'role': 'user', 'content': '请基于以上定时任务执行结果，直接用自然简练的话输出给主人的提醒内容，不要再调用工具。'}],
                            tools=None, big=use_big
                        )
                        sum_txt = (sum_msg.get('content') or '').strip()
                        if sum_txt:
                            accumulated_texts.append(sum_txt)
                        final_text = '\n\n'.join(accumulated_texts).strip()
                    final_text = final_text or f'⏰ {goal}'
                    self.vault.log(f'完成{label}：{goal[:120]}')
                    self.store.save()
                    emitted = await self.emit(
                        final_text, 'curiosity', track=True,
                        trace=trace, goal=goal, from_bot_id=target_id, from_bot_name=bot['name']
                    )
                    if not notified_explicitly and bot.get('notify', True):
                        await self.publish({
                            'type': 'user_notification',
                            'id': emitted['id'],
                            'bot_id': target_id,
                            'bot_name': bot['name'],
                            'title': f"{bot['name']} · {label}",
                            'body': final_text[:300],
                            'urgent': True,
                        })
                    return emitted
                finally:
                    await self._auto_release_phone_if_used()
                    self.live_stream = None
                    self.execution = None
                    self.active_task = None
                    self.activity = self.ready_state()
                    try:
                        self.stream_prefix_var.reset(sched_prefix_token)
                        self.turn_id_var.reset(sched_turn_token)
                    except Exception:
                        pass
        finally:
            self.room_context.reset(room_marker)
            self.bot_context.reset(marker)

    def quiet(self):
        h = datetime.now(TZ).hour
        start, end = self.store.config.quiet_start_hour, self.store.config.quiet_end_hour
        return (start <= h < end) if start < end else (h >= start or h < end) if start != end else False

    async def _sleep_time_consolidate(self):
        """Letta & A-MEM inspired Sleep-Time Compute: silently consolidate recent conversation facts into Zettelkasten atomic notes during user idle periods."""
        s = self.store.state
        last_consolidated = float(s.get('last_sleep_consolidate', 0.0))
        last_user = float(s.get('last_user', 0.0))
        if last_user <= last_consolidated or (time.time() - last_user) < 1200:
            return
        new_msgs = [
            m for m in s.get('messages', [])
            if float(m.get('created_at', 0)) > last_consolidated and m.get('role') in ('user', 'assistant') and m.get('kind') not in ('curiosity', 'error', 'compact_summary')
        ]
        s['last_sleep_consolidate'] = time.time()
        self.store.save()
        if len(new_msgs) < 4:
            return
        async with self.lock:
            try:
                digest = '\n'.join(f"[{m.get('role')}]: {(m.get('text') or '')[:500]}" for m in new_msgs[-16:])
                existing_traits = self.vault.read('profile/user_traits.md')[-1800:]
                prompt = (
                    '你是 Sleep-Time Compute 睡眠期记忆整理器（参考 Letta / A-MEM Zettelkasten 原子记忆理论）。\n'
                    '请检查以下最近对话是否有值得长期记住的【新事实、用户偏好变更、重要决定、项目/课程进展】。\n'
                    '如果没有任何新的长期价值信息，严格只输出 `NONE`。\n'
                    '如果有，请严格输出 JSON 对象：{"new_traits": ["一句话新画像事实（若无则空数组）"], "note_slug": "英文短文件名如 project-update", "note_title": "卡片标题", "note_markdown": "包含 # 标题、关键事实列表与 [[关联双链]] 的精简原子卡片正文（不超过300字）"}\n\n'
                    f'【已有画像尾部（勿重复）】\n{existing_traits}\n\n【近期新增对话】\n{digest}'
                )
                res_msg = await self.complete([{'role': 'user', 'content': prompt}], tools=None, big=False)
                raw = (res_msg.get('content') or '').strip()
                if not raw or raw.upper() == 'NONE':
                    return
                m_json = re.search(r'\{.*\}', raw, flags=re.DOTALL)
                if not m_json:
                    return
                data = json.loads(m_json.group(0))
                changed = False
                for tr in (data.get('new_traits') or [])[:3]:
                    if isinstance(tr, str) and tr.strip() and tr.strip() not in existing_traits:
                        self.vault.append('profile/user_traits.md', f'- {now_iso()}（睡眠整理）{tr.strip()}')
                        changed = True
                slug = re.sub(r'[^a-zA-Z0-9_-]', '', str(data.get('note_slug') or ''))[:40].lower()
                md_body = str(data.get('note_markdown') or '').strip()
                if slug and md_body:
                    self.vault.write(f'notes/{slug}.md', md_body + f'\n\n> 更新时间：{now_iso()}\n')
                    changed = True
                if changed:
                    self.vault.reindex()
            except Exception:
                pass

    async def scheduler(self):
        while True:
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=1)
            except asyncio.TimeoutError:
                pass
            self.wake.clear()
            try:
                s, c = self.store.state, self.store.config
                if not c.enabled: continue
                now = time.time()
                if 0 < s['snooze_until'] <= now:
                    s['snooze_until'] = 0
                    self.store.save()
                    self.vault.write('profile/annoyance_valve.md', '# 烦人度阀门\n\n状态：好奇心开启（免打扰已到期）\n\n更新时间：' + now_iso() + '\n')
                await self.escalation.tick()
                if s['snooze_until'] > now: continue
                for d in s['deadlines']:
                    if not d['notified'] and d['due_at'] <= now:
                        marker = self.bot_context.set(s['primary_bot_id'])
                        try:
                            await self.emit('约好的截止时间到了：' + d['title'] + '。现在需要你拍板。', 'deadline')
                        finally:
                            self.bot_context.reset(marker)
                        d['notified'] = True
                        self.store.save()
                if self.lock.locked(): continue
                has_api = bool((c.effective_api_key(big=True) or c.effective_api_key(big=False)) and (c.effective_model(big=True) or c.effective_model(big=False)))
                # 1. Fire due one-shot timers (exact wall-clock or countdown; never blocked by 0-8 AM quiet hours!)
                for t in s.get('timers', []):
                    if not t.get('done') and t.get('due_at', 0) <= now:
                        t['done'] = True
                        self.store.save()
                        if has_api:
                            await self.run_scheduled(t['goal'], bot_id=t.get('bot_id', s['primary_bot_id']), label='定时闹钟')
                        break
                if self.lock.locked(): continue
                # 2. Fire due routines (supports exact daily_time like "07:30" or interval_hours)
                for r in s['routines']:
                    if not r.get('enabled', True):
                        continue
                    if not r.get('next_run'):
                        r['next_run'] = self._compute_next_routine_run(r, now)
                        r['next_run_bj'] = self._fmt_bj(r['next_run'])
                        self.store.save()
                    is_clock_routine = bool(r.get('daily_time'))
                    if not is_clock_routine and self.quiet():
                        continue
                    if now >= float(r['next_run']):
                        r['last_run'] = now
                        r['next_run'] = self._compute_next_routine_run(r, now + 2)
                        r['next_run_bj'] = self._fmt_bj(r['next_run'])
                        self.store.save()
                        if has_api:
                            if is_clock_routine:
                                await self.run_scheduled(r['goal'], bot_id=r.get('bot_id', 'companion'), label=f"定点例行 ({r['daily_time']})")
                            else:
                                await self.research(r['goal'], bot_id=r.get('bot_id', 'companion'))
                        break
                # 3. Sleep-Time Compute memory consolidation when user has been idle >= 20 min
                if has_api and not self.lock.locked() and (now - s.get('last_user', 0)) >= 1200 and s.get('last_user', 0) > s.get('last_sleep_consolidate', 0):
                    await self._sleep_time_consolidate()
                # 4. Background curiosity & idle research respect quiet hours (00:00 - 08:00)
                if self.quiet(): continue
                if len(s['pending']) < 3 and now - s['last_curiosity'] >= c.curiosity_interval_minutes * 60:
                    curiosity_bots = [b for b in s.get('assistants', []) if b.get('curiosity_enabled')]
                    target_bot_id = curiosity_bots[0]['id'] if curiosity_bots else s['primary_bot_id']
                    await self.curiosity(bot_id=target_bot_id)
                ready = next((x for x in s['opportunities'] if x['ready'] and not x['done']), None)
                if ready and has_api and now - s['last_user'] >= c.idle_research_minutes * 60:
                    await self.research()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.store.state['last_error'] = str(e)[:250]
                self.store.save()
                await self.publish({'type':'engine_error', 'message':str(e)[:250]})
