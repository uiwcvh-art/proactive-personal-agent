from __future__ import annotations
import asyncio, json, os, re, secrets, signal, sys, time, contextvars
from datetime import datetime, timedelta
from pathlib import Path
import httpx
from vault_manager import TZ, now_iso
from assistants import ensure_assistants, get_assistant
from harness import TOOLS
from storage import compact_trace_list
from core.llm_client import LLMClient
from core.memory_engine import MemoryEngine
from core.vision_engine import VisionEngine
from core.scheduler_engine import SchedulerEngine
from core.context_search import ContextSearchEngine

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
        self.call_chain_var = contextvars.ContextVar('call_chain', default=())
        self.turn_id_var = contextvars.ContextVar('turn_id', default='')
        self.stream_prefix_var = contextvars.ContextVar('stream_prefix', default='')
        self.choices_var = contextvars.ContextVar('choices', default=None)
        self.choices_prompt_var = contextvars.ContextVar('choices_prompt', default='')
        self.stream_seq: int = 0
        self.last_completed_stream: dict | None = None
        self.execution = None
        self.live_stream = None
        self.active_task = None
        self.computer = None
        self.bg_tasks: dict[str, dict] = {}
        self.watchdogs: dict[str, dict] = {}
        self.last_prompt_tokens: dict[str, int] = {}
        self.phone_pending: dict[str, asyncio.Future] = {}
        self._models_cache: tuple[float, str, list[str]] | None = None
        self._used_phone_in_turn: bool = False
        self._last_vision_nodes: list[dict] = []
        self._last_phone_node_count: int = -1
        self._last_phone_size: tuple[int, int] = (720, 1600)
        self._vision_cache: dict[str, str] = {}
        self._recent_client_msg_ids: dict[str, float] = {}
        self._stopped_turn_ids: set[str] = set()
        self._last_scheduled_fire_by_bot: dict[str, float] = {}
        self._last_emergency_alarm_at: float = 0.0

        # Domain 1 Core Engines
        self.context_search = ContextSearchEngine(self.harness.root / '.config' / 'context_index.db')
        try:
            self.context_search.sync_store_messages(self.store.state.get('messages', []))
        except Exception:
            pass
        self.llm = LLMClient(store, get_assistant, self.last_prompt_tokens)
        self.memory_engine = MemoryEngine(store, vault, harness, self.last_prompt_tokens, context_search=self.context_search)
        self.vision_engine = VisionEngine(store, get_assistant, self.llm.fetch_available_models)
        self.scheduler_engine = SchedulerEngine(store, self.wake)

        self.harness.store = store
        self.harness.vault = vault
        self.harness.vision_reader = self.read_image_file_with_vision
        self.harness.custom_handlers = {
            'send_message': self._tool_send_message,
            'manage_bot': self._tool_manage_bot,
            'list_models': self._tool_list_models,
            'schedule_task': self._tool_schedule_task,
            'bg_task': self._tool_bg_task,
            'watchdog_task': self._tool_watchdog_task,
            'conversation_search': self._tool_conversation_search,
            'ctx_search': self._tool_ctx_search,
            'present_choices': self._tool_present_choices,
            'memory_pin': self._tool_memory_pin,
            'blackboard': self._tool_blackboard,
            'custom_tool': self._tool_custom_tool,
            'phone_macro': self._tool_phone_macro,
            'cloud_desktop': self._tool_cloud_desktop,
            'notify_user': self._tool_notify_user,
            'emergency_alarm': self._tool_emergency_alarm,
            'phone_workspace': self._tool_phone_workspace,
        }

    def _clear_bot_stream(self, bot_id: str | None = None) -> None:
        if bot_id:
            self.streams_by_bot.pop(bot_id, None)
            self.executions_by_bot.pop(bot_id, None)
            self.active_tasks_by_bot.pop(bot_id, None)
        else:
            self.streams_by_bot.clear()
            self.executions_by_bot.clear()
            self.active_tasks_by_bot.clear()
        self.live_stream = next(iter(self.streams_by_bot.values()), None)
        self.execution = next(iter(self.executions_by_bot.values()), None)
        self.active_task = next(iter(self.active_tasks_by_bot.values()), None)
        self.activity = 'thinking' if self.executions_by_bot else self.ready_state()

    async def stop_immediately(self, bot_id: str | None = None) -> dict:
        """
        Instant 0ms Stop:
        1. Captures any partial stream text already generated (`streams_by_bot`) and commits it immediately
           as a real message so the user's chat window freezes cleanly on the generated content.
        2. Cancels the running asyncio task(s) and clears `streams_by_bot` / `executions_by_bot`.
        """
        target_bots = [bot_id] if bot_id else list(set(list(self.streams_by_bot.keys()) + list(self.active_tasks_by_bot.keys()) + ([self.execution.get('bot_id')] if isinstance(self.execution, dict) and self.execution.get('bot_id') else [])))
        committed_msg = None
        for b_id in target_bots:
            if not b_id:
                continue
            cur_stream = self.streams_by_bot.get(b_id) or (self.live_stream if (self.live_stream or {}).get('bot_id') == b_id else None)
            cur_exec = self.executions_by_bot.get(b_id) or (self.execution if (self.execution or {}).get('bot_id') == b_id else None)
            partial_text = ''
            if isinstance(cur_stream, dict):
                raw_t = str(cur_stream.get('text') or '').strip()
                if raw_t and not self._is_placeholder_text(raw_t):
                    partial_text = raw_t
            trace_list = list(cur_exec.get('trace') or []) if isinstance(cur_exec, dict) else []
            turn_id = str((cur_stream or {}).get('turn_id') or '')
            if turn_id:
                self._stopped_turn_ids.add(turn_id)
                if len(self._stopped_turn_ids) > 200:
                    self._stopped_turn_ids.clear()
                    self._stopped_turn_ids.add(turn_id)
            if partial_text or trace_list:
                try:
                    bot_obj = get_assistant(self.store, b_id)
                    b_name = bot_obj['name']
                except Exception:
                    b_name = b_id
                final_txt = partial_text or '（已停止执行）'
                committed_msg = self.store.message(
                    'assistant',
                    final_txt,
                    'chat',
                    bot_id=b_id,
                    from_bot_id=b_id,
                    from_bot_name=b_name,
                    trace=trace_list,
                    notify=False,
                    stopped_early=True,
                )
                self.stream_seq += 1
                committed_msg['seq'] = self.stream_seq
                committed_msg['turn_id'] = turn_id or committed_msg['id']
                self.last_completed_stream = {
                    'seq': self.stream_seq,
                    'turn_id': committed_msg['turn_id'],
                    'bot_id': b_id,
                    'speaker_id': b_id,
                    'speaker_name': b_name,
                    'text': final_txt,
                    'final_message_id': committed_msg['id'],
                    'done': True,
                    'completed_at': time.time(),
                }
                self.store.save()
                await self.publish({'type': 'message', 'message': committed_msg, 'seq': self.stream_seq})
            self._clear_bot_stream(b_id)
            t = self.active_tasks_by_bot.get(b_id)
            if t and not t.done():
                t.cancel()

        if self.active_task and not self.active_task.done():
            self.active_task.cancel()
        if not bot_id:
            self._clear_bot_stream(None)
        await self.publish({'type': 'stopped', 'bot_id': bot_id or 'all', 'message': committed_msg, 'seq': self.stream_seq})
        return {'stopped': True, 'committed_message': committed_msg, 'seq': self.stream_seq}

    def _get_bot_lock(self, bot_id: str) -> asyncio.Lock:
        b = (bot_id or 'companion').strip()
        if b not in self.bot_locks:
            self.bot_locks[b] = asyncio.Lock()
        return self.bot_locks[b]

    def is_any_bot_busy(self) -> bool:
        return self.lock.locked() or any(lk.locked() for lk in self.bot_locks.values())

    def persona(self, bot_id: str | None = None):
        target = bot_id or self.bot_context.get()
        bot = get_assistant(self.store, target)
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
        prev = self.streams_by_bot.get(bot_id) or self.live_stream or {}
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
        sub_agents = []
        todos = []
        cur_exec = self.executions_by_bot.get(bot_id) or self.execution
        if cur_exec:
            if isinstance(cur_exec.get('trace'), list):
                trace_tail = compact_trace_list(cur_exec['trace'][-16:])
            if isinstance(cur_exec.get('sub_agents'), list):
                sub_agents = list(cur_exec['sub_agents'])
            elif isinstance(cur_exec.get('sub_agents'), dict):
                sub_agents = list(cur_exec['sub_agents'].values())
            if isinstance(cur_exec.get('todos'), list):
                todos = list(cur_exec['todos'])
        same_stream = (prev.get('bot_id') == bot_id and prev.get('turn_id') == turn_id and prev.get('speaker_id') == sp_id)
        if not trace_tail and same_stream and isinstance(prev.get('trace'), list):
            trace_tail = list(prev['trace'])
        # If LLM is currently streaming arguments for a tool call (`tool` is a real tool name),
        # anchor it in `trace_tail` immediately as a running entry so it NEVER pops in and out!
        is_real_tool_name = bool(
            tool and isinstance(tool, str)
            and not tool.startswith(('正在', '已执行', '并行执行'))
        )
        if is_real_tool_name:
            if not trace_tail or trace_tail[-1].get('tool') != tool or trace_tail[-1].get('status') != 'running':
                trace_tail.append({
                    'tool': tool,
                    'arguments': {},
                    'duration_ms': 0,
                    'status': 'running',
                })
        elif same_stream and isinstance(prev.get('trace'), list) and len(prev['trace']) > len(trace_tail):
            trace_tail = list(prev['trace'])
        # Never allow `tool` to drop to None mid-turn while tools have been called or are running!
        effective_tool = tool
        if not effective_tool:
            if isinstance(cur_exec, dict) and cur_exec.get('tool'):
                effective_tool = str(cur_exec['tool'])
            elif same_stream and prev.get('tool'):
                effective_tool = str(prev['tool'])
            elif trace_tail:
                effective_tool = f'已执行 {len(trace_tail)} 步'
        raw_thought = str(prev.get('thought') or '') if thought is None else thought
        if trace_tail:
            step_lines = []
            for idx_s, st_item in enumerate(trace_tail[-6:], max(1, len(trace_tail) - 5)):
                st_name = st_item.get('tool', 'tool')
                st_args = st_item.get('arguments') or {}
                st_op = st_args.get('op') or st_args.get('action') or st_args.get('path') or st_args.get('query') or ''
                st_label = f"{st_name}({st_op})" if st_op else st_name
                is_run = st_item.get('status') == 'running' or st_item.get('duration_ms', 0) == 0
                mark = '▸' if is_run else '✓'
                dur_txt = '' if is_run else f" · {st_item.get('duration_ms', 0)}ms"
                step_lines.append(f"{mark} 第{idx_s}步 {st_label}{dur_txt}")
            steps_summary = '\n'.join(step_lines)
            if raw_thought and not raw_thought.startswith(('✓ 第', '▸ 第')):
                effective_thought = f"{steps_summary}\n{raw_thought}"
            else:
                effective_thought = steps_summary
        else:
            effective_thought = raw_thought
        delta_text = ''
        if same_stream and prev_text and not self._is_placeholder_text(prev_text) and effective_text.startswith(prev_text):
            delta_text = effective_text[len(prev_text):]
        live_choices = list(self.choices_var.get() or [])
        if not live_choices and same_stream and isinstance(prev.get('choices'), list):
            live_choices = list(prev['choices'])
        self.stream_seq += 1
        self.live_stream = {
            'seq': self.stream_seq,
            'turn_id': turn_id,
            'bot_id': bot_id,
            'speaker_id': sp_id,
            'speaker_name': sp_name,
            'text': effective_text,
            'delta': delta_text,
            'thought': effective_thought,
            'tool': effective_tool,
            'trace': trace_tail,
            'sub_agents': sub_agents,
            'todos': todos,
            'choices': live_choices,
            'updated_at': time.time(),
        }
        self.streams_by_bot[bot_id] = self.live_stream
        await self.publish({'type': 'stream', 'stream': self.live_stream})

    async def complete(self, messages, tools=None, big=False):
        bot_id = self.bot_context.get()
        try:
            bot = get_assistant(self.store, bot_id)
        except ValueError:
            bot = {}
        return await self.llm.complete_stream(
            messages=messages,
            bot_id=bot_id,
            bot_model=str(bot.get('model') or ''),
            tools=tools,
            big=big,
            stream_prefix=self.stream_prefix_var.get() or '',
            on_stream=lambda text='', tool=None, thought=None: self._set_stream(text=text, tool=tool, thought=thought),
        )

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
                    {k: v for k, v in info.items() if k != 'proc'}
                    for info in self.bg_tasks.values()
                ],
                'watchdogs': [
                    {k: v for k, v in w.items() if k != 'task'}
                    for w in self.watchdogs.values()
                ],
            }
        if action == 'logs':
            tid = str(args.get('task_id') or '').strip()
            info = self.bg_tasks.get(tid)
            if not info:
                return {'error': f'未找到后台任务 {tid}'}
            log_path = Path(info['log_file'])
            return {
                'task_id': tid,
                'status': info['status'],
                'exit_code': info.get('exit_code'),
                'logs': log_path.read_text('utf-8', errors='replace')[-12000:] if log_path.exists() else '',
            }
        if action == 'send_stdin':
            tid = str(args.get('task_id') or '').strip()
            input_text = str(args.get('input_text') or args.get('command') or '')
            info = self.bg_tasks.get(tid)
            if not info or info.get('status') != 'running':
                return {'error': f'未找到正在运行的后台任务 {tid}'}
            proc = info.get('proc')
            if not proc or proc.stdin is None:
                return {'error': f'任务 {tid} 不支持 stdin 输入'}
            try:
                line_bytes = (input_text if input_text.endswith('\n') else input_text + '\n').encode('utf-8')
                proc.stdin.write(line_bytes)
                await proc.stdin.drain()
                return {'sent_stdin': True, 'task_id': tid, 'bytes': len(line_bytes)}
            except Exception as e:
                return {'error': f'写入 stdin 失败: {e}'}
        if action == 'kill':
            tid = str(args.get('task_id') or '').strip()
            info = self.bg_tasks.get(tid)
            if not info:
                return {'error': f'未找到后台任务 {tid}'}
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
            return {'killed': True, 'task_id': tid}
        if action == 'start':
            cmd = str(args.get('command') or '').strip()
            if not cmd:
                return {'error': 'command 不能为空'}
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
                cmd, cwd=str(self.harness.root), stdin=asyncio.subprocess.PIPE, stdout=out_f, stderr=asyncio.subprocess.STDOUT,
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
                    self.stream_seq += 1
                    m['seq'] = self.stream_seq
                    self.context_search.index_message(m)
                    await self.publish({'type': 'message', 'message': m, 'seq': self.stream_seq})

            asyncio.create_task(watch())
            return {'started': True, 'task_id': tid, 'log_file': f'.bg_logs/{tid}.log'}
        return {'error': f'未知 bg_task action: {action}'}

    async def _tool_watchdog_task(self, args: dict):
        """Zero-token self-written watchdog scripts that silently monitor targets and wake the agent ONLY on TRIGGER:"""
        action = str(args.get('action') or 'list').strip().lower()
        if action == 'list':
            return {
                'watchdogs': [
                    {k: v for k, v in w.items() if k != 'task'}
                    for w in self.watchdogs.values()
                ]
            }
        if action in ('stop', 'kill', 'remove'):
            tid = str(args.get('task_id') or args.get('name') or '').strip()
            w = self.watchdogs.get(tid)
            if not w:
                for k, cand in list(self.watchdogs.items()):
                    if cand.get('name') == tid:
                        w = cand
                        tid = k
                        break
            if not w:
                return {'error': f'未找到盯盘哨兵 {tid}'}
            w['status'] = 'stopped'
            t = w.get('task')
            if t and not t.done():
                t.cancel()
            self.watchdogs.pop(tid, None)
            return {'stopped': True, 'task_id': tid}

        if action == 'logs':
            tid = str(args.get('task_id') or args.get('name') or '').strip()
            w = self.watchdogs.get(tid)
            if not w:
                return {'error': f'未找到盯盘哨兵 {tid}'}
            return {k: v for k, v in w.items() if k != 'task'}

        if action == 'run_now':
            tid = str(args.get('task_id') or args.get('name') or '').strip()
            w = self.watchdogs.get(tid)
            if not w:
                return {'error': f'未找到盯盘哨兵 {tid}'}
            out = await self._exec_watchdog_once(w)
            return {'ran': True, 'task_id': tid, 'output': out}

        if action == 'start':
            name = str(args.get('name') or '监控哨兵').strip()[:80]
            script = str(args.get('script') or '').strip()
            if not script:
                return {'error': 'script 不能为空（请提供 Python 或 Bash 探针脚本，当检测到目标变化时打印 "TRIGGER: 详情"）'}
            interval_sec = max(15, min(86400, int(args.get('interval_sec') or 120)))
            owner_bot = str(args.get('bot_id') or self.bot_context.get() or 'companion').strip()
            tid = 'wd-' + secrets.token_hex(4)
            wd_dir = self.harness.root / '.watchdogs'
            wd_dir.mkdir(parents=True, exist_ok=True)
            is_py = ('import ' in script or 'print(' in script or script.startswith('#!/usr/bin/env python'))
            ext = '.py' if is_py else '.sh'
            script_path = wd_dir / f'{tid}{ext}'
            script_path.write_text(script, encoding='utf-8')
            w_info: dict = {
                'task_id': tid,
                'name': name,
                'bot_id': owner_bot,
                'interval_sec': interval_sec,
                'script_path': str(script_path),
                'status': 'running',
                'runs': 0,
                'triggers': 0,
                'last_run_at': 0.0,
                'last_output': '',
                'created_at': time.time(),
            }

            async def _loop():
                while w_info.get('status') == 'running':
                    try:
                        await self._exec_watchdog_once(w_info)
                    except asyncio.CancelledError:
                        break
                    except Exception as e:
                        w_info['last_output'] = f'Error: {e}'
                    await asyncio.sleep(interval_sec)

            w_info['task'] = asyncio.create_task(_loop())
            self.watchdogs[tid] = w_info
            return {
                'started': True,
                'task_id': tid,
                'name': name,
                'interval_sec': interval_sec,
                'note': '哨兵已在后台以 0 Token 模式静默运行；当脚本输出包含 "TRIGGER:" 时将自动唤醒你向主人汇报。',
            }
        return {'error': f'未知 watchdog_task action: {action}'}

    async def _exec_watchdog_once(self, w_info: dict) -> str:
        script_path = Path(w_info['script_path'])
        cmd = f'{sys.executable} "{script_path}"' if script_path.suffix == '.py' else f'bash "{script_path}"'
        proc = await asyncio.create_subprocess_shell(
            cmd,
            cwd=str(self.harness.root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            raw_out, _ = await asyncio.wait_for(proc.communicate(), timeout=30.0)
        except asyncio.TimeoutError:
            proc.kill()
            raw_out = b'Timeout after 30s'
        out_str = raw_out.decode('utf-8', errors='replace').strip()
        w_info['runs'] = int(w_info.get('runs') or 0) + 1
        w_info['last_run_at'] = time.time()
        w_info['last_output'] = out_str[-800:]
        if 'TRIGGER:' in out_str:
            w_info['triggers'] = int(w_info.get('triggers') or 0) + 1
            trig_detail = out_str.split('TRIGGER:', 1)[1].strip()[:1500]
            asyncio.create_task(
                self.run_scheduled(
                    f'【盯盘哨兵「{w_info["name"]}」命中触发条件】\n探针输出：{trig_detail}\n请立即向主人汇报此变化并给出处理建议。',
                    bot_id=w_info.get('bot_id', 'companion'),
                    label=f'哨兵触发 ({w_info["name"]})',
                )
            )
        return out_str[-800:]

    async def _tool_conversation_search(self, args: dict):
        query = str(args.get('query') or '').strip()
        bot_id = str(args.get('bot_id') or 'all').strip()
        limit = int(args.get('limit') or 10)
        cur_bot = self.room_context.get() or self.bot_context.get()
        try:
            self.context_search.sync_store_messages(self.store.state.get('messages', []))
        except Exception:
            pass
        # Exclude messages already in the current bot's active 200K window so we primarily search
        # pre-200K compacted history, compact_summary blocks, timeline/notes, and other bots' rooms!
        active_ids = {
            str(m.get('id'))
            for m in self.store.state.get('messages', [])
            if m.get('bot_id', 'companion') == cur_bot and m.get('kind') != 'compact_summary'
        }
        return self.context_search.conversation_search(
            query=query,
            bot_id=bot_id,
            limit=limit,
            exclude_msg_ids=active_ids if bot_id in ('all', cur_bot, '') else set(),
            vault=self.vault,
        )

    async def _tool_ctx_search(self, args: dict):
        query = str(args.get('query') or '').strip()
        ctx_id = str(args.get('ctx_id') or '').strip()
        limit = int(args.get('limit') or 8)
        return self.context_search.ctx_search(query=query, ctx_id=ctx_id, limit=limit)

    async def _tool_present_choices(self, args: dict):
        raw_choices = args.get('choices') or args.get('options') or args.get('items')
        prompt_txt = str(args.get('prompt') or args.get('question') or args.get('title') or '').strip()
        if isinstance(raw_choices, str):
            raw_choices = [ln.strip() for ln in re.split(r'[\n;|]+', raw_choices) if ln.strip()]
        if not isinstance(raw_choices, list):
            return {'error': 'choices 必须是列表（如 ["选项A", "选项B"]）'}
        cleaned: list[dict] = []
        for c in raw_choices[:6]:
            if isinstance(c, dict):
                lbl = str(c.get('label') or c.get('title') or c.get('text') or c.get('value') or '').strip()[:72]
                val = str(c.get('value') or c.get('prompt') or c.get('text') or lbl).strip()[:240]
                if lbl:
                    cleaned.append({'label': lbl, 'value': val or lbl})
            else:
                s = str(c).strip()
                if s:
                    cleaned.append({'label': s[:72], 'value': s[:240]})
        if not cleaned:
            return {'error': 'choices 不能为空'}
        self.choices_var.set(cleaned)
        if prompt_txt:
            self.choices_prompt_var.set(prompt_txt)
        bot_id = self.room_context.get() or self.bot_context.get()
        cur_exec = self.executions_by_bot.get(bot_id) or self.execution
        if isinstance(cur_exec, dict):
            cur_exec['choices'] = cleaned
        cur_prefix = (self.stream_prefix_var.get() or '').strip()
        if cur_prefix and prompt_txt and prompt_txt not in cur_prefix:
            combined_stream_txt = cur_prefix + '\n\n' + prompt_txt
        else:
            combined_stream_txt = cur_prefix or prompt_txt or '正在思考…'
        await self._set_stream(
            text=combined_stream_txt,
            tool='present_choices',
        )
        return {
            'ok': True,
            'choices_attached': [item['label'] for item in cleaned],
            'note': '可点击选项按钮已附加到回复气泡底部。若前面已说明清楚，无需再重复生成多余总结。',
        }

    @staticmethod
    def _extract_implicit_choices(text: str) -> list[dict]:
        """
        When the assistant asks the user to choose among numbered/bulleted options in prose
        without explicitly invoking `present_choices`, automatically extract 2..5 clickable choices!
        """
        if not text or len(text) < 18:
            return []
        tail = text[-260:]
        asks_choice = (
            '?' in tail or '？' in tail
            or any(k in tail for k in ('哪一个', '哪个', '哪种', '怎么选', '选哪', '想先', '你想', '觉得呢', '怎么搞', '还是', '要不', '方向', '方案', '先试'))
        )
        if not asks_choice:
            return []
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        numbered: list[dict] = []
        rx = re.compile(r'^(?:(\d+)[\.、\)]|([①②③④⑤⑥])|(?:[-*•]\s*(?:方案|选项|方向)\s*[A-Za-z0-9一二三四五]?[：:\s]))\s*(.+)$')
        for ln in lines:
            m = rx.match(ln)
            if not m:
                continue
            body = m.group(3).strip()
            body_clean = re.sub(r'\*\*([^*]+)\*\*', r'\1', body).strip()
            if len(body_clean) < 2 or len(body_clean) > 180:
                continue
            # Split title vs description at colon / dash / parenthesis
            short_title = re.split(r'[：:—–(（。；;]', body_clean, maxsplit=1)[0].strip()
            if len(short_title) < 2 or len(short_title) > 42:
                short_title = body_clean[:34]
            idx_num = m.group(1) or m.group(2) or str(len(numbered) + 1)
            label = f"{idx_num}. {short_title}" if not short_title.startswith(str(idx_num)) else short_title
            value = body_clean[:120]
            numbered.append({'label': label[:64], 'value': value})
        if 2 <= len(numbered) <= 6:
            return numbered
        return []

    async def _tool_memory_pin(self, args: dict):
        action = str(args.get('action') or 'list').strip().lower()
        if action == 'list':
            return {'pins': self.memory_engine.list_pins()}
        if action in ('pin', 'add'):
            return self.memory_engine.add_pin(str(args.get('text') or ''))
        if action in ('unpin', 'remove', 'delete'):
            return self.memory_engine.remove_pin(str(args.get('pin_id') or args.get('text') or ''))
        return {'error': f'未知 memory_pin action: {action}'}

    async def _tool_blackboard(self, args: dict):
        action = str(args.get('action') or 'list').strip().lower()
        bot_id = self.bot_context.get()
        room_id = self.room_context.get() or bot_id
        if action == 'write':
            return self.context_search.blackboard_write(
                key=str(args.get('key') or ''),
                value=str(args.get('value') or ''),
                author_bot=bot_id,
                summary=str(args.get('summary') or ''),
            )
        if action == 'read':
            return self.context_search.blackboard_read(str(args.get('key') or ''))
        if action == 'delete':
            return self.context_search.blackboard_delete(str(args.get('key') or ''))
        if action == 'set_todos':
            raw_steps = args.get('steps') if isinstance(args.get('steps'), list) else []
            todos = []
            for idx, st in enumerate(raw_steps[:12], 1):
                if isinstance(st, dict):
                    todos.append({
                        'title': str(st.get('title') or st.get('text') or f'步骤 {idx}')[:120],
                        'status': str(st.get('status') or ('done' if st.get('done') else 'pending')),
                    })
                elif isinstance(st, str):
                    todos.append({'title': st[:120], 'status': 'pending'})
            cur_exec = self.executions_by_bot.get(room_id) or self.execution
            if isinstance(cur_exec, dict):
                cur_exec['todos'] = todos
                await self.publish({'type': 'execution', 'execution': cur_exec})
            return {'ok': True, 'todos': todos}
        return self.context_search.blackboard_list()

    async def _tool_custom_tool(self, args: dict):
        action = str(args.get('action') or 'list').strip().lower()
        plugins_dir = self.harness.root / 'plugins'
        plugins_dir.mkdir(parents=True, exist_ok=True)
        if action == 'list':
            return self.harness.extension_registry.summary()
        if action == 'delete':
            t_name = re.sub(r'[^a-zA-Z0-9_]', '', str(args.get('name') or '')).strip()
            p_file = plugins_dir / f'{t_name}.py'
            if p_file.exists():
                p_file.unlink()
            self.harness.extension_registry.tool_plugins.pop(t_name, None)
            return {'deleted': True, 'name': t_name}
        if action == 'create':
            t_name = re.sub(r'[^a-zA-Z0-9_]', '', str(args.get('name') or '')).strip().lower()
            desc = str(args.get('description') or f'Custom tool {t_name}').strip()
            code = str(args.get('code') or '').strip()
            if not t_name or not code:
                return {'error': 'create 需要提供 name 和 code (包含 async def run(args, context) -> dict)'}
            try:
                params_schema = json.loads(str(args.get('parameters_json') or '{"type":"object","properties":{}}'))
            except Exception:
                params_schema = {'type': 'object', 'properties': {}}
            indented_code = '\n'.join('        ' + ln for ln in code.splitlines())
            plugin_src = (
                f'"""Dynamic Custom Tool: {t_name}"""\n'
                f'import asyncio, json, os, re, time, httpx\n'
                f'from pathlib import Path\n\n'
                f'class DynamicPlugin_{t_name}:\n'
                f'    name = {json.dumps(t_name)}\n'
                f'    description = {json.dumps(desc, ensure_ascii=False)}\n'
                f'    parameters = {repr(params_schema)}\n\n'
                f'    async def execute(self, args: dict, context: dict) -> dict:\n'
                f'{indented_code}\n'
                f'        if "run" in locals() and callable(locals()["run"]):\n'
                f'            res = locals()["run"](args, context)\n'
                f'            return (await res) if asyncio.iscoroutine(res) else res\n'
                f'        return {{"ok": True}}\n\n'
                f'def register(registry):\n'
                f'    registry.register_tool(DynamicPlugin_{t_name}())\n'
            )
            p_file = plugins_dir / f'{t_name}.py'
            p_file.write_text(plugin_src, encoding='utf-8')
            loaded = self.harness.extension_registry.discover_plugins_dir(plugins_dir)
            return {'created': True, 'name': t_name, 'loaded_plugins': loaded}
        return {'error': f'未知 custom_tool action: {action}'}

    async def _tool_phone_macro(self, args: dict):
        action = str(args.get('action') or 'list').strip().lower()
        cfg_dir = self.harness.root / '.config'
        cfg_dir.mkdir(parents=True, exist_ok=True)
        macro_file = cfg_dir / 'phone_macros.json'
        macros: dict = {}
        if macro_file.exists():
            try:
                macros = json.loads(macro_file.read_text('utf-8'))
            except Exception:
                macros = {}
        if action == 'list':
            return {'macros': macros}
        name = str(args.get('name') or '').strip()
        if not name:
            return {'error': '请提供宏名称 name'}
        if action == 'save':
            steps = args.get('steps') if isinstance(args.get('steps'), list) else []
            if not steps:
                return {'error': 'steps 不能为空'}
            macros[name] = {
                'name': name,
                'description': str(args.get('description') or ''),
                'steps': steps[:30],
                'updated_at': time.time(),
            }
            macro_file.write_text(json.dumps(macros, ensure_ascii=False, indent=2), encoding='utf-8')
            return {'saved': True, 'name': name, 'steps_count': len(steps[:30])}
        if action == 'delete':
            macros.pop(name, None)
            macro_file.write_text(json.dumps(macros, ensure_ascii=False, indent=2), encoding='utf-8')
            return {'deleted': True, 'name': name}
        if action == 'run':
            item = macros.get(name)
            if not item:
                return {'error': f'未找到手机动作宏 "{name}"'}
            return await self._tool_phone_workspace({'op': 'batch', 'steps': item.get('steps', [])})
        return {'error': f'未知 phone_macro action: {action}'}

    async def _broadcast_desktop_frame(self, res_or_frame: dict, op: str):
        if not isinstance(res_or_frame, dict):
            return
        b64 = res_or_frame.get('image')
        if b64:
            import base64
            try:
                (self.harness.root / 'desktop-latest.jpg').write_bytes(base64.b64decode(b64))
            except Exception:
                pass
        room_id = self.room_context.get() or self.bot_context.get()
        # Only broadcast lightweight metadata (no heavy base64 image in chat stream) to save bandwidth;
        # the client pulls /api/computer/frame only when the user actually opens the Cloud Computer view!
        await self.publish({
            'type': 'cloud_desktop_frame',
            'bot_id': room_id,
            'speaker_id': self.bot_context.get(),
            'op': op,
            'frame': {
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
            if op in ('search', 'web_search'):
                q = str(args.get('query') or args.get('text') or args.get('goal') or args.get('url') or '').strip()
                engine = str(args.get('engine') or args.get('target') or 'auto').strip()
                res = await self.computer.client.call('POST', '/search', body={'query': q, 'engine': engine, 'include_frame': True})
                if isinstance(res, dict) and res.get('image'):
                    await self._broadcast_desktop_frame(res, 'search')
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
            if op in ('click', 'double_click', 'right_click', 'type', 'key', 'hotkey', 'scroll', 'move', 'hover', 'drag', 'focus_window', 'close_window', 'tile_windows', 'clipboard', 'click_ref', 'type_ref'):
                payload: dict = {'kind': op, 'include_frame': True}
                if op in ('click', 'double_click', 'right_click', 'move', 'hover'):
                    payload.update(
                        x=int(args.get('x') or 0),
                        y=int(args.get('y') or 0),
                        nx=int(args.get('nx') if args.get('nx') is not None else -1),
                        ny=int(args.get('ny') if args.get('ny') is not None else -1),
                        grid=str(args.get('grid') or ''),
                    )
                elif op == 'drag':
                    payload.update(
                        x=int(args.get('x') or 0),
                        y=int(args.get('y') or 0),
                        x2=int(args.get('x2') or 0),
                        y2=int(args.get('y2') or 0),
                    )
                elif op in ('type', 'clipboard'):
                    payload.update(text=str(args.get('text') or ''))
                elif op in ('key', 'hotkey'):
                    payload.update(key=str(args.get('key') or args.get('text') or 'Return'))
                elif op == 'scroll':
                    payload.update(x=int(args.get('x') or 640), y=int(args.get('y') or 400), direction=str(args.get('text') or 'down'), amount=4)
                elif op in ('focus_window', 'close_window'):
                    payload.update(target=str(args.get('target') or args.get('app') or args.get('text') or ''))
                elif op in ('click_ref', 'type_ref'):
                    payload.update(ref=str(args.get('ref') or args.get('target') or ''), text=str(args.get('text') or ''))
                res = await self.computer.client.call('POST', '/input', body=payload)
                if isinstance(res, dict) and res.get('image'):
                    await self._broadcast_desktop_frame(res, op)
                    res = {k: v for k, v in res.items() if k != 'image'}
                    res['saved_screenshot'] = 'desktop-latest.jpg'
                return res
            return {'error': f'未知 cloud_desktop op: {op}'}
        except Exception as e:
            return {'error':str(e)[:260]}

    async def emit(self, text, kind='curiosity', track=True, **extra):
        bot_id = extra.setdefault('bot_id', self.bot_context.get())
        try:
            bot_notify = get_assistant(self.store, bot_id).get('notify', True)
        except ValueError:
            bot_notify = True
        extra.setdefault('notify', bot_notify)
        pending_choices = self.choices_var.get()
        if pending_choices and 'choices' not in extra:
            extra['choices'] = list(pending_choices)
            self.choices_var.set(None)
            self.choices_prompt_var.set('')
        elif 'choices' not in extra and kind in ('chat', 'curiosity', 'result'):
            implicit_choices = self._extract_implicit_choices(str(text or ''))
            if implicit_choices:
                extra['choices'] = implicit_choices
        m = self.store.message('assistant', text, kind, **extra)
        self.stream_seq += 1
        turn_id = self.turn_id_var.get() or m['id']
        m['seq'] = self.stream_seq
        m['turn_id'] = turn_id
        try:
            self.context_search.index_message(m)
        except Exception:
            pass
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
        await self.publish({'type': 'message', 'message': m, 'seq': self.stream_seq})
        if track and extra['notify']:
            await self.escalation.track(m)
        return m

    async def _tool_notify_user(self, args: dict):
        title = str(args.get('title') or '助理提醒').strip()[:120]
        body = str(args.get('body') or '').strip()[:2000]
        urgent = bool(args.get('urgent', False))
        if not body:
            return {'error': '通知内容不能为空'}
        bot_id = self.room_context.get() or self.bot_context.get()
        bot = get_assistant(self.store, self.bot_context.get())
        nid = 'notif-' + secrets.token_hex(6)
        await self.publish({
            'type': 'user_notification',
            'id': nid,
            'bot_id': bot_id,
            'bot_name': bot['name'],
            'title': title,
            'body': body,
            'urgent': urgent,
        })
        return {
            'notified': True,
            'id': nid,
            'title': title,
            'urgent': urgent,
            'note': '手机系统通知栏推送已发出；请直接将提醒正文作为本轮最终回复输出即可，禁止再额外说“推送已经发了”。',
        }

    async def _tool_emergency_alarm(self, args: dict):
        title = str(args.get('title') or '紧急呼叫').strip()[:120]
        reason = str(args.get('reason') or '').strip()[:1000]
        duration_sec = max(5, min(45, int(args.get('duration_sec') or 18)))
        if not reason:
            return {'error': '必须提供触发紧急响铃的具体原因 reason'}
        now = time.time()
        if self.store.state.get('snooze_until', 0) > now:
            return {'suppressed': True, 'reason': '当前处于主人设定的免打扰时段，已拦截紧急闹钟'}
        if now - self._last_emergency_alarm_at < 180.0:
            return {'suppressed': True, 'reason': '3 分钟内已触发过一次紧急响铃，避免重复打扰'}
        self._last_emergency_alarm_at = now
        bot_id = self.room_context.get() or self.bot_context.get()
        bot = get_assistant(self.store, self.bot_context.get())
        aid = 'alarm-' + secrets.token_hex(6)
        await self.publish({
            'type': 'emergency_alarm',
            'id': aid,
            'bot_id': bot_id,
            'bot_name': bot['name'],
            'title': f'🚨 {title}',
            'body': reason,
            'duration_sec': duration_sec,
            'urgent': True,
        })
        return {'alarm_triggered': True, 'id': aid, 'duration_sec': duration_sec, 'title': title}

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

    @staticmethod
    def _resolve_phone_grid_or_norm(args: dict, width: int, height: int) -> tuple[int, int] | None:
        """
        Convert coarse grid (`A1..F10`), normalized 0-1000 (`nx`, `ny`), or float 0..1 (`x`, `y`)
        into exact virtual display pixel coordinates `(px, py)`.
        """
        grid_raw = str(args.get('grid') or '').strip().upper()
        if grid_raw and len(grid_raw) >= 2 and grid_raw[0].isalpha() and grid_raw[1:].replace('.', '', 1).isdigit():
            col_idx = max(0, min(5, ord(grid_raw[0]) - ord('A')))
            row_val = max(0.0, min(9.9, float(grid_raw[1:]) - 1.0))
            px = int(((col_idx + 0.5) / 6.0) * width)
            py = int(((row_val + 0.5) / 10.0) * height)
            return max(0, min(width - 1, px)), max(0, min(height - 1, py))
        nx = args.get('nx')
        ny = args.get('ny')
        if nx is not None and ny is not None:
            try:
                nx_f, ny_f = float(nx), float(ny)
                if 0 <= nx_f <= 1000 and 0 <= ny_f <= 1000:
                    return (
                        max(0, min(width - 1, int(round(nx_f * width / 1000.0)))),
                        max(0, min(height - 1, int(round(ny_f * height / 1000.0)))),
                    )
            except (ValueError, TypeError):
                pass
        return None

    async def _tool_phone_workspace(self, args: dict):
        import base64
        op = str(args.get('op') or 'status').strip().lower()
        if op not in ('status', 'release', 'auto_release', 'notifications', 'get_notifications', 'recent_notifications', 'notifs', 'notification'):
            self._used_phone_in_turn = True

        # Never delegate to local phone sub-agent op="run" from cloud tool calls (prevents fake user messages & 10-min loops)
        if op in ('run', 'task'):
            op = 'ui_tree'
            args = {'op': 'ui_tree', 'refresh': True}

        cur_w, cur_h = self._last_phone_size
        resolved_pt = self._resolve_phone_grid_or_norm(args, cur_w, cur_h)
        if resolved_pt is not None and op in ('tap', 'tap_node', 'click_node', 'tap_and_type'):
            args = {**args, 'x': resolved_pt[0], 'y': resolved_pt[1]}
            if op in ('tap_node', 'click_node') and not args.get('ref') and not args.get('text'):
                op = 'tap'
                args['op'] = 'tap'

        # Coarse combo action for WeChat / no-accessibility-tree screens: tap input box -> paste text -> optional send/enter
        if op == 'tap_and_type':
            tx = int(args.get('x') if args.get('x') is not None else (cur_w // 2))
            ty = int(args.get('y') if args.get('y') is not None else int(cur_h * 0.94))
            content_txt = str(args.get('text') or '').strip()
            tap_r = await self._raw_phone_rpc('tap', {'op': 'tap', 'x': tx, 'y': ty})
            await asyncio.sleep(0.22)
            type_r = await self._raw_phone_rpc('text', {'op': 'text', 'text': content_txt}) if content_txt else {}
            if args.get('submit'):
                await asyncio.sleep(0.18)
                # Tap WeChat right-bottom send button area or press Enter
                await self._raw_phone_rpc('key', {'op': 'key', 'key': 'enter'})
            return {
                **(tap_r if isinstance(tap_r, dict) else {}),
                **(type_r if isinstance(type_r, dict) else {}),
                'ok': True,
                'op': 'tap_and_type',
                'x': tx,
                'y': ty,
                'typed': bool(content_txt),
                'submitted': bool(args.get('submit')),
            }

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
            return {'error': 'to_bot_id 和 text 不能为空'}
        self.store.sync_from_workspace()
        target_bot = self._resolve_bot(raw_target)
        if target_bot is None:
            available = [f"{b['id']} ({b['name']})" for b in self.store.state.get('assistants', [])]
            return {
                'error': f'未找到目标 Bot "{raw_target}"。当前可用: {", ".join(available)}。可先调用 manage_bot(action="create", name="{raw_target}") 创建。'
            }
        target_id = target_bot['id']
        from_id = self.bot_context.get()
        room_id = self.room_context.get() or from_id
        if target_id == from_id:
            return {'error': '不能给自己发送 send_message'}
        chain = list(self.call_chain_var.get() or (from_id,))
        if target_id in chain:
            return {'error': f'检测到 Bot 循环调用链路 ({" -> ".join(chain + [target_id])})，已自动拦截以防死锁；请直接汇总当前信息。'}
        depth = self.hop_depth.get()
        if depth >= 5:
            return {'error': 'Bot 间连续转发已达单次上限 (5 跳)，请先总结当前进展'}
        from_bot = get_assistant(self.store, from_id)
        parent_turn_id = self.turn_id_var.get() or ('turn-' + secrets.token_hex(6))
        parent_exec = self.executions_by_bot.get(room_id) or self.execution
        if isinstance(parent_exec, dict):
            subs = parent_exec.setdefault('sub_agents', {})
            subs[target_id] = {'bot_id': target_id, 'name': target_bot['name'], 'status': 'thinking', 'goal': text[:120], 'updated_at': time.time()}

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
            self.stream_seq += 1
            room_delegation['seq'] = self.stream_seq
            room_delegation['turn_id'] = parent_turn_id
            room_delegation['is_sub_turn'] = True
            await self.publish({'type': 'message', 'message': room_delegation, 'seq': self.stream_seq})
        delegated_msg = self.store.message(
            'user',
            f'@{target_bot["name"]} [来自 {from_bot["name"]}]:\n{text}',
            kind='bot_message',
            bot_id=target_id,
            from_bot_id=from_id,
            from_bot_name=from_bot['name'],
            notify=False,
        )
        self.stream_seq += 1
        delegated_msg['seq'] = self.stream_seq
        delegated_msg['turn_id'] = parent_turn_id
        delegated_msg['is_sub_turn'] = True
        await self.publish({'type': 'message', 'message': delegated_msg, 'seq': self.stream_seq})

        # 2. Execute target_bot with live streaming into room_id
        depth_token = self.hop_depth.set(depth + 1)
        chain_token = self.call_chain_var.set(tuple(chain + [target_id]))
        bot_token = self.bot_context.set(target_id)
        room_token = self.room_context.set(room_id)
        sub_turn_token = self.turn_id_var.set(parent_turn_id)
        sub_prefix_token = self.stream_prefix_var.set('')
        try:
            await self._set_stream(text='正在思考…', speaker_id=target_id, speaker_name=target_bot['name'])
            allowed = target_bot.get('tools') or [t['function']['name'] for t in TOOLS]
            async def sub_progress(event):
                if isinstance(parent_exec, dict):
                    subs = parent_exec.setdefault('sub_agents', {})
                    if event['type'] == 'tool_start':
                        subs[target_id] = {'bot_id': target_id, 'name': target_bot['name'], 'status': f"调用 {event['tool']}", 'goal': text[:120], 'updated_at': time.time()}
                        if isinstance(parent_exec.get('trace'), list):
                            parent_exec['trace'].append({
                                'tool': event['tool'],
                                'arguments': event.get('arguments') or {},
                                'duration_ms': 0,
                                'status': 'running',
                            })
                        parent_exec['tool'] = event['tool']
                    elif event['type'] == 'tool_end':
                        if isinstance(parent_exec.get('trace'), list):
                            entry_done = dict(event['entry'])
                            entry_done['status'] = 'done'
                            if parent_exec['trace'] and parent_exec['trace'][-1].get('status') == 'running' and parent_exec['trace'][-1].get('tool') == entry_done.get('tool'):
                                parent_exec['trace'][-1] = entry_done
                            else:
                                parent_exec['trace'].append(entry_done)
                        parent_exec['tool'] = f"正在分析 {event['entry'].get('tool', '工具')} 结果…"
                    elif event['type'] == 'turn_text':
                        subs[target_id] = {'bot_id': target_id, 'name': target_bot['name'], 'status': '生成回复中', 'goal': text[:120], 'updated_at': time.time()}
                if event['type'] == 'turn_text':
                    self.stream_prefix_var.set(event['text'])
                    await self._set_stream(text=event['text'], speaker_id=target_id, speaker_name=target_bot['name'])
                elif event['type'] == 'tool_start':
                    await self._set_stream(text=self.stream_prefix_var.get() or f"正在调用 {event['tool']}…", tool=event['tool'], speaker_id=target_id, speaker_name=target_bot['name'])
                elif event['type'] == 'tool_end':
                    await self._set_stream(text=self.stream_prefix_var.get() or '正在思考…', tool=f"正在分析 {event['entry'].get('tool', '工具')} 结果…", speaker_id=target_id, speaker_name=target_bot['name'])
            result = await self.harness.run(
                f'来自同事 Bot「{from_bot["name"]} ({from_id})」的协作请求：\n{text}',
                lambda msgs, tools=None: self.complete(msgs, tools=tools, big=(target_bot.get('engine') == 'big')),
                self.context() + '\n角色设定：' + self.persona(target_id),
                allowed_tools=allowed,
                on_event=sub_progress,
            )
            reply_text = result['text']
            if isinstance(parent_exec, dict) and 'sub_agents' in parent_exec and target_id in parent_exec['sub_agents']:
                parent_exec['sub_agents'][target_id]['status'] = 'done'
            target_reply = self.store.message(
                'assistant',
                reply_text,
                kind='bot_reply',
                bot_id=target_id,
                from_bot_id=target_id,
                from_bot_name=target_bot['name'],
                to_bot_id=from_id,
                trace=result['trace'],
                notify=False,
            )
            self.stream_seq += 1
            target_reply['seq'] = self.stream_seq
            target_reply['turn_id'] = parent_turn_id
            target_reply['is_sub_turn'] = True
            await self.publish({'type': 'message', 'message': target_reply, 'seq': self.stream_seq})
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
                self.stream_seq += 1
                caller_copy['seq'] = self.stream_seq
                caller_copy['turn_id'] = parent_turn_id
                caller_copy['is_sub_turn'] = True
                await self.publish({'type': 'message', 'message': caller_copy, 'seq': self.stream_seq})
            return {
                'status': 'replied',
                'from_bot_id': target_id,
                'from_bot_name': target_bot['name'],
                'reply': reply_text,
                'trace_count': len(result['trace']),
            }
        except Exception as e:
            err_str = str(e)[:280]
            if isinstance(parent_exec, dict) and 'sub_agents' in parent_exec and target_id in parent_exec['sub_agents']:
                parent_exec['sub_agents'][target_id]['status'] = 'error'
            configured_model = target_bot.get('model') or self.store.config.effective_model(big=(target_bot.get('engine') == 'big')) or '未配置'
            avail_models = (await self._fetch_available_models())[:18]
            models_hint = f'当前可用模型：{", ".join(avail_models)}' if avail_models else '请调用 list_models() 查看可用模型'
            err_msg_text = (
                f'⚠️ **[{target_bot["name"]} 报错]**: {err_str}\n'
                f'当前模型: `{configured_model}`\n'
                f'💡 {models_hint}'
            )
            err_target = self.store.message(
                'assistant', err_msg_text, kind='error',
                bot_id=target_id, from_bot_id=target_id, from_bot_name=target_bot['name'], notify=False
            )
            self.stream_seq += 1
            err_target['seq'] = self.stream_seq
            err_target['turn_id'] = parent_turn_id
            err_target['is_sub_turn'] = True
            await self.publish({'type': 'message', 'message': err_target, 'seq': self.stream_seq})
            if room_id != target_id:
                err_caller = self.store.message(
                    'assistant', err_msg_text, kind='error',
                    bot_id=room_id, from_bot_id=target_id, from_bot_name=target_bot['name'], notify=False
                )
                self.stream_seq += 1
                err_caller['seq'] = self.stream_seq
                err_caller['turn_id'] = parent_turn_id
                err_caller['is_sub_turn'] = True
                await self.publish({'type': 'message', 'message': err_caller, 'seq': self.stream_seq})
            return {
                'error': err_str,
                'from_bot_id': target_id,
                'from_bot_name': target_bot['name'],
                'current_model': configured_model,
                'available_models_wheelchair': avail_models,
                'self_heal_hint': f'目标 Bot 报错，可调用 manage_bot(action="update", bot_id="{target_id}", model="有效模型名") 修复后重试。',
            }
        finally:
            self._clear_bot_stream(target_id)
            self.stream_prefix_var.reset(sub_prefix_token)
            self.turn_id_var.reset(sub_turn_token)
            self.room_context.reset(room_token)
            self.bot_context.reset(bot_token)
            self.call_chain_var.reset(chain_token)
            self.hop_depth.reset(depth_token)

    AUTO_COMPACT_TOKENS = 200_000
    KEEP_RECENT_TOKENS = 64_000

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        return MemoryEngine.estimate_tokens(text)

    def _estimate_messages_tokens(self, messages: list[dict]) -> int:
        return MemoryEngine.estimate_messages_tokens(messages)

    @staticmethod
    def _prune_tool_loop_messages(messages: list[dict]):
        MemoryEngine.prune_tool_loop_messages(messages)

    def context(self):
        return self.memory_engine.build_prompt_context()

    async def _auto_compact_if_needed(self, bot_id: str, force: bool = False) -> dict | None:
        """Silent background 200K compaction via MemoryEngine (never pollutes live_stream, emits compact_summary card to chat)."""
        sys_tokens = self._estimate_tokens(self.harness.system_instructions(self.context())) + 800
        res = await self.memory_engine.auto_compact_if_needed(
            bot_id=bot_id,
            sys_tokens=sys_tokens,
            silent_complete_fn=lambda msgs: self.llm.complete_silent(msgs, tools=None, big=False, bot_id=bot_id),
            force=force,
        )
        if res and isinstance(res.get('marker'), dict):
            marker = res['marker']
            self.stream_seq += 1
            marker['seq'] = self.stream_seq
            self.store.save()
            await self.publish({'type': 'message', 'message': marker, 'seq': self.stream_seq})
        return res

    async def curiosity(self, manual=False, bot_id=None):
        if not manual and (self.store.state['snooze_until'] > time.time() or not self.store.config.enabled):
            return {'suppressed': True}
        async with self.lock:
            target_id = bot_id or self.store.state['primary_bot_id']
            if not manual:
                bot_history = [m for m in self.store.state['messages'] if m.get('bot_id', 'companion') == target_id]
                if bot_history and bot_history[-1].get('role') == 'assistant':
                    # Unanswered backoff: never talk to ourselves when the last message in the room is already from the assistant
                    self.store.state['last_curiosity'] = time.time()
                    self.store.save()
                    return {'suppressed': True, 'reason': 'unanswered_backoff'}
            marker = self.bot_context.set(target_id)
            room_marker = self.room_context.set(target_id)
            cur_turn_token = self.turn_id_var.set('turn-' + secrets.token_hex(6))
            cur_prefix_token = self.stream_prefix_var.set('')
            cur_choices_token = self.choices_var.set(None)
            cur_choices_prompt_token = self.choices_prompt_var.set('')
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
                        'read_file', 'write_file', 'edit_file', 'memory_search', 'web_search', 'web_fetch', 'schedule_task', 'present_choices'
                    }
                    available_tools = [t for t in TOOLS if t['function']['name'] in allowed]
                    opps = self.vault.read('tasks/sniffed_opportunities.md')[:3000]
                    deadlines = self.vault.read('tasks/active_deadlines.md')[:3000]
                    routines_md = self.vault.read('playbooks/routines.md')[:3000]
                    raw_imp, missing_imp = self.memory_engine.get_personal_impression()
                    schedule_md = ''
                    try:
                        schedule_md = self.vault.read('profile/course_schedule.md')[:4000]
                    except Exception:
                        pass
                    missing_hint = ('\n- ' + '\n- '.join(missing_imp[:8])) if missing_imp else '（主要维度已初步记录，可深入细节）'
                    sys_prompt = (
                        self.harness.static_system_instructions(self.memory_engine.build_static_pinned_block()) + '\n\n'
                        '你的设定：' + self.persona() + '\n\n'
                        '当前时间：' + self._fmt_bj(time.time()) + ' (' + now_iso() + ')\n'
                        '主人立体个人印象档案 (memory_vault/profile/personal_impression.md)：\n' + raw_imp[:2200] + '\n\n'
                        '尚未了解的个人印象维度：' + missing_hint + '\n\n'
                        '课表：\n' + (schedule_md or '（未记录）') + '\n\n'
                        '待确认截止事项：\n' + deadlines + '\n\n'
                        '常驻例行：\n' + routines_md + '\n\n'
                        '待办机会池：\n' + opps + '\n\n'
                        '请主动向主人发起一次简短、真诚、自然的搭话（2~4句话）：\n'
                        '- 重要原则：不要每次只问工作或任务！请优先从「尚未了解的个人印象维度」（如主人的出生地/家乡、成长经历、当前身份与理想、家庭氛围与重要的人/宠物、生活饮食喜好、性格与相处偏好）中挑一个还没问过的小话题，像亲近的伙伴一样自然地问一句，逐渐补全对主人的个人印象；或者结合当前时间/课表/情报分享一件具体有用的事。\n'
                        '- 严禁生硬查户口，每次只轻轻问一个具体而温暖的小问题，不要顺着最近报错复读。\n'
                        '- 务必调用 `present_choices(choices=[...])` 提供 3~4 个贴心选项，方便主人一键点选回答（主人也可以自己打字）。\n'
                    )
                    messages = [
                        {'role':'system', 'content':sys_prompt},
                        {'role':'user', 'content':'最近对话摘要（仅用于防重复，严禁顺着里面的闲聊或报错继续尬聊）：\n' + json.dumps(recent_digest, ensure_ascii=False)}
                    ]
                    msg = {'content': ''}
                    cur_accumulated: list[str] = []
                    for _ in range(6):
                        msg = await self.llm.complete_silent(messages, tools=available_tools or None, big=False, bot_id=target_id)
                        step_txt = (msg.get('content') or '').strip()
                        if step_txt and (not cur_accumulated or (step_txt != cur_accumulated[-1] and step_txt not in cur_accumulated[-1])):
                            cur_accumulated.append(step_txt)
                        calls = msg.get('tool_calls') or []
                        if not calls:
                            break
                        messages.append(msg)
                        for c in calls[:4]:
                            try:
                                args = json.loads(c['function']['arguments'])
                            except (ValueError, KeyError):
                                args = {}
                            name = c['function']['name']
                            started = asyncio.get_running_loop().time()
                            result = await self.harness.execute(name, args) if name in allowed else {'error':'此助手未启用该工具'}
                            trace.append({'tool':name, 'arguments':args, 'result':result, 'duration_ms':round((asyncio.get_running_loop().time()-started)*1000)})
                            messages.append({'role':'tool', 'tool_call_id':c['id'], 'content':json.dumps(result, ensure_ascii=False)})
                        if all(c['function']['name'] == 'present_choices' for c in calls[:4]) and cur_accumulated:
                            break
                    choice_prompt = (self.choices_prompt_var.get() or '').strip()
                    if choice_prompt and all(choice_prompt not in p for p in cur_accumulated):
                        cur_accumulated.append(choice_prompt)
                    text = '\n\n'.join(cur_accumulated).strip()
                    if not text and trace:
                        summary = await self.llm.complete_silent(
                            messages + [{'role':'user', 'content':'请基于以上探索结果，直接用自然亲切的语气对主人说一句主动搭话并带一个有趣的小问题，不要再调用工具。'}],
                            tools=None,
                            big=False,
                            bot_id=target_id,
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
                self._clear_bot_stream(target_id)
                self.choices_var.reset(cur_choices_token)
                self.choices_prompt_var.reset(cur_choices_prompt_token)
                self.stream_prefix_var.reset(cur_prefix_token)
                self.turn_id_var.reset(cur_turn_token)
                self.room_context.reset(room_marker)
                self.bot_context.reset(marker)
                self.activity = 'thinking' if self.executions_by_bot else self.ready_state()

    def ready_state(self):
        has_key = bool(self.store.config.effective_api_key(big=False) or self.store.config.effective_api_key(big=True))
        has_model = bool(self.store.config.effective_model(big=False) or self.store.config.effective_model(big=True))
        return 'curiosity_idle' if (has_key and has_model) else 'awaiting_config'

    async def enqueue_chat(self, text: str, bot_id: str = 'companion', mode: str = 'auto', client_msg_id: str | None = None) -> dict:
        if client_msg_id:
            now_ts = time.time()
            self._recent_client_msg_ids = {k: v for k, v in self._recent_client_msg_ids.items() if now_ts - v < 120.0}
            if client_msg_id in self._recent_client_msg_ids:
                return {
                    'queued': True,
                    'deduplicated': True,
                    'recent_tail': self.store.state['messages'][-10:],
                    'seq': self.stream_seq,
                }
            self._recent_client_msg_ids[client_msg_id] = now_ts

        bot = get_assistant(self.store, bot_id)
        turn_id = 'turn-' + secrets.token_hex(6)
        self.store.state['last_user'] = time.time()
        m = self.store.message('user', text, bot_id=bot_id)
        if client_msg_id:
            m['client_msg_id'] = client_msg_id
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

    async def chat(self, text, bot_id='companion', mode='auto', pre_recorded_user_msg=None, turn_id=None, client_msg_id: str | None = None):
        if client_msg_id and pre_recorded_user_msg is None:
            now_ts = time.time()
            self._recent_client_msg_ids = {k: v for k, v in self._recent_client_msg_ids.items() if now_ts - v < 120.0}
            if client_msg_id in self._recent_client_msg_ids:
                return self.store.state['messages'][-1] if self.store.state['messages'] else {'text': ''}
            self._recent_client_msg_ids[client_msg_id] = now_ts
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

                # 100% static system prompt (messages[0]) so upstream LLM Prefix Cache hits 100% across turns & tool steps
                system = (
                    self.harness.static_system_instructions(self.memory_engine.build_static_pinned_block()) + '\n\n'
                    '你的设定：' + self.persona(bot_id)
                )
                all_schemas = self.harness.all_tools()
                plugin_names = set(self.harness.extension_registry.tool_plugins.keys())
                allowed = set(bot.get('tools') or [t['function']['name'] for t in all_schemas]) | plugin_names
                available_tools = [t for t in all_schemas if t['function']['name'] in allowed]
                ctx_snapshot = self.context()
                if recent and recent[-1]['role'] == 'user' and isinstance(recent[-1].get('content'), str) and ctx_snapshot:
                    recent[-1]['content'] = f"[记忆快照]\n{ctx_snapshot[:3000]}\n\n{recent[-1]['content']}"
                messages = [{'role': 'system', 'content': system}, *recent]
                trace = []
                accumulated_texts: list[str] = []
                final_turn_text: str = ''
                parsed_meta: dict = {}
                choices_reset_token = self.choices_var.set(None)
                choices_prompt_token = self.choices_prompt_var.set('')
                recent_call_sigs: list[str] = []
                await self._set_stream(text='正在思考…', speaker_id=bot_id, speaker_name=bot['name'])
                msg = {'content': '收到。'}
                use_big = (bot.get('engine') == 'big')
                for _ in range(40):
                    # NEVER mutate already-sent messages in `messages` (preserves 100% Prefix Cache!)
                    msg = await self.complete(messages, tools=available_tools or None, big=use_big)
                    turn_content = (msg.get('content') or '').strip()
                    if turn_content:
                        clean_piece, piece_meta = self._extract_reply_piece(turn_content)
                        if piece_meta:
                            parsed_meta.update(piece_meta)
                        piece_to_add = (clean_piece or turn_content).strip()
                        if piece_to_add:
                            if not accumulated_texts:
                                accumulated_texts.append(piece_to_add)
                            elif piece_to_add != accumulated_texts[-1] and piece_to_add not in accumulated_texts[-1]:
                                if accumulated_texts[-1] in piece_to_add:
                                    accumulated_texts[-1] = piece_to_add
                                else:
                                    accumulated_texts.append(piece_to_add)
                            final_turn_text = '\n\n'.join(accumulated_texts)
                            self.stream_prefix_var.set(final_turn_text)
                    calls = msg.get('tool_calls') or []
                    if not calls:
                        break
                    messages.append(msg)
                    batch_calls = calls[:24]
                    # Check if we have multiple independent send_message or read-only calls that can run concurrently
                    can_parallel = (
                        len(batch_calls) > 1
                        and all(c['function']['name'] in ('send_message', 'web_search', 'web_fetch', 'read_file', 'memory_search', 'conversation_search', 'ctx_search') for c in batch_calls)
                    )
                    if can_parallel:
                        names = [c['function']['name'] for c in batch_calls]
                        running_entries = []
                        for c_item in batch_calls:
                            try:
                                c_args_init = json.loads(c_item['function']['arguments'])
                            except (ValueError, KeyError):
                                c_args_init = {}
                            r_ent = compact_trace_list([{'tool': c_item['function']['name'], 'arguments': c_args_init, 'duration_ms': 0, 'status': 'running'}])[0]
                            my_exec['trace'].append(r_ent)
                            running_entries.append(r_ent)
                        my_exec['tool'] = f"并行执行 ({', '.join(names[:3])})"
                        self.execution = my_exec
                        await self._set_stream(
                            text=final_turn_text or f"正在并行执行 {len(batch_calls)} 项任务…",
                            tool=my_exec['tool'],
                            speaker_id=bot_id,
                            speaker_name=bot['name'],
                        )
                        await self.publish({'type': 'execution', 'execution': my_exec})
                        async def _run_one(c_item):
                            try:
                                c_args = json.loads(c_item['function']['arguments'])
                            except (ValueError, KeyError):
                                c_args = {}
                            c_name = c_item['function']['name']
                            t0 = asyncio.get_running_loop().time()
                            res = await self.harness.execute(c_name, c_args) if c_name in allowed else {'error': '此助手未启用该工具'}
                            dur = round((asyncio.get_running_loop().time() - t0) * 1000)
                            return c_item, c_name, c_args, res, dur
                        parallel_results = await asyncio.gather(*[_run_one(c_item) for c_item in batch_calls])
                        for idx_p, (c_item, c_name, c_args, result, dur) in enumerate(parallel_results):
                            entry = compact_trace_list([{'tool': c_name, 'arguments': c_args, 'result': result, 'duration_ms': dur, 'status': 'done'}])[0]
                            trace.append(entry)
                            if idx_p < len(running_entries):
                                running_entries[idx_p].clear()
                                running_entries[idx_p].update(entry)
                            else:
                                my_exec['trace'].append(entry)
                            tool_content_str = self.context_search.maybe_offload_tool_output(
                                c_name, c_args, result, bot_id=bot_id, turn_id=self.turn_id_var.get()
                            )
                            messages.append({'role': 'tool', 'tool_call_id': c_item['id'], 'content': tool_content_str})
                        my_exec['tool'] = f'已执行 {len(trace)} 步'
                        self.execution = my_exec
                        await self._set_stream(
                            text=final_turn_text or '正在思考…',
                            tool=my_exec['tool'],
                            speaker_id=bot_id,
                            speaker_name=bot['name'],
                            thought=f'已完成 {len(trace)} 步操作，正在继续…'
                        )
                        await self.publish({'type': 'execution', 'execution': my_exec})
                    else:
                        for c in batch_calls:
                            try:
                                args = json.loads(c['function']['arguments'])
                            except (ValueError, KeyError):
                                args = {}
                            name = c['function']['name']
                            sig = f"{name}:{json.dumps(args, sort_keys=True, ensure_ascii=False)}"
                            recent_call_sigs.append(sig)
                            running_entry = compact_trace_list([{'tool': name, 'arguments': args, 'duration_ms': 0, 'status': 'running'}])[0]
                            my_exec['trace'].append(running_entry)
                            my_exec['tool'] = name
                            self.execution = my_exec
                            stream_txt = final_turn_text if final_turn_text else f'正在调用 {name}…'
                            await self._set_stream(
                                text=stream_txt,
                                tool=name,
                                speaker_id=bot_id,
                                speaker_name=bot['name'],
                                thought=self._summarize_streaming_tool(name, c['function'].get('arguments') or '')
                            )
                            await self.publish({'type': 'execution', 'execution': my_exec})
                            started = asyncio.get_running_loop().time()
                            if len(recent_call_sigs) >= 3 and recent_call_sigs[-1] == recent_call_sigs[-2] == recent_call_sigs[-3]:
                                result = {'error': f'检测到连续 3 次重复调用相同工具与参数 ({name})，已自动拦截。请更换方法或直接基于已有信息回复。'}
                            else:
                                result = await self.harness.execute(name, args) if name in allowed else {'error': '此助手未启用该工具'}
                            entry = compact_trace_list([{'tool': name, 'arguments': args, 'result': result, 'duration_ms': max(1, round((asyncio.get_running_loop().time() - started) * 1000)), 'status': 'done'}])[0]
                            trace.append(entry)
                            running_entry.clear()
                            running_entry.update(entry)
                            # Keep clean non-null step count between consecutive tool steps so client UI never renders a fake extra tool row!
                            my_exec['tool'] = f'已执行 {len(trace)} 步'
                            self.execution = my_exec
                            await self._set_stream(
                                text=final_turn_text if final_turn_text else '正在思考…',
                                tool=my_exec['tool'],
                                speaker_id=bot_id,
                                speaker_name=bot['name'],
                                thought=f'已完成 {len(trace)} 步操作，正在继续…'
                            )
                            await self.publish({'type': 'execution', 'execution': my_exec})
                            # Offload >12K outputs into SQLite FTS5 BEFORE appending so `messages` stays immutable!
                            tool_content_str = self.context_search.maybe_offload_tool_output(
                                name, args, result, bot_id=bot_id, turn_id=self.turn_id_var.get()
                            )
                            messages.append({'role': 'tool', 'tool_call_id': c['id'], 'content': tool_content_str})
                        # If this tool step only attached `present_choices` and we already have the assistant's reply text,
                        # stop immediately so a second post-tool turn never overwrites or tacks on redundant text!
                        if all(c['function']['name'] == 'present_choices' for c in batch_calls) and len(final_turn_text.strip()) >= 12:
                            break

                choice_prompt_str = (self.choices_prompt_var.get() or '').strip()
                if choice_prompt_str and all(choice_prompt_str not in p for p in accumulated_texts):
                    accumulated_texts.append(choice_prompt_str)
                    final_turn_text = '\n\n'.join(accumulated_texts)
                    self.stream_prefix_var.set(final_turn_text)

                if not accumulated_texts and trace:
                    await self._set_stream(text=final_turn_text or '正在整理回复…', speaker_id=bot_id, speaker_name=bot['name'])
                    final_msg = await self.complete(
                        messages + [{'role': 'user', 'content': '请基于以上工具执行结果，直接用自然、清晰的话回复主人，不要再调用工具。'}],
                        tools=None,
                        big=use_big,
                    )
                    content_str = (final_msg.get('content') or '').strip()
                    if content_str:
                        clean_piece, piece_meta = self._extract_reply_piece(content_str)
                        if piece_meta:
                            parsed_meta.update(piece_meta)
                        piece_to_add = (clean_piece or content_str).strip()
                        if piece_to_add and (not accumulated_texts or accumulated_texts[-1] != piece_to_add):
                            accumulated_texts.append(piece_to_add)
                        final_turn_text = '\n\n'.join(accumulated_texts)
                        self.stream_prefix_var.set(final_turn_text)
                joined_reply = '\n\n'.join(accumulated_texts).strip() or final_turn_text.strip()
                if not joined_reply and trace:
                    last_tool = trace[-1]
                    joined_reply = f"已执行 `{last_tool['tool']}`，结果：{json.dumps(last_tool['result'], ensure_ascii=False)[:400]}"
                data = dict(parsed_meta)
                data['reply'] = joined_reply or '收到，已处理完毕。'
                for trait in data.get('traits', [])[:5]:
                    if isinstance(trait, str):
                        self.vault.append('profile/user_traits.md', f'- {now_iso()}（用户陈述）{trait}')
                op = data.get('opportunity')
                if isinstance(op, dict) and op.get('title') and op.get('goal'):
                    op = {'title': str(op['title'])[:200], 'goal': str(op['goal'])[:3000], 'ready': op.get('ready') is True, 'done': False, 'created_at': time.time()}
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
                try:
                    return await self.emit(reply_out, 'chat', engine='small_brain', track=False, **extra)
                finally:
                    self.choices_var.reset(choices_reset_token)
                    self.choices_prompt_var.reset(choices_prompt_token)
            except asyncio.CancelledError:
                cur_turn = self.turn_id_var.get()
                if cur_turn and cur_turn in self._stopped_turn_ids:
                    # `stop_immediately()` already committed the partial message for this turn; do NOT emit a duplicate!
                    return {'stopped': True}
                partial = (final_turn_text or '\n\n'.join(accumulated_texts)).strip()
                if partial:
                    return await self.emit(
                        partial,
                        'chat',
                        engine='small_brain',
                        track=False,
                        trace=list(my_exec['trace']),
                        from_bot_id=bot_id,
                        from_bot_name=bot['name'],
                    )
                return {'stopped': True}
            finally:
                await self._auto_release_phone_if_used()
                self._clear_bot_stream(bot_id)
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
            cur_task = asyncio.current_task()
            self.active_task = cur_task
            if cur_task:
                self.active_tasks_by_bot[bot_id] = cur_task
            my_exec = {'bot_id':bot_id, 'goal':goal or '整理任务池', 'trace':[], 'tool':None, 'started_at':time.time()}
            self.executions_by_bot[bot_id] = my_exec
            self.execution = my_exec
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
                            my_exec['trace'].append({
                                'tool': event['tool'],
                                'arguments': event.get('arguments') or {},
                                'duration_ms': 0,
                                'status': 'running',
                            })
                            my_exec['tool'] = event['tool']
                            self.execution = my_exec
                            await self._set_stream(text=self.stream_prefix_var.get() or f"正在调用 {event['tool']}…", tool=event['tool'], speaker_id=bot_id, speaker_name=bot['name'])
                            await self.publish({'type':'execution', 'execution':my_exec})
                        else:
                            entry_done = dict(event['entry'])
                            entry_done['status'] = 'done'
                            if my_exec['trace'] and my_exec['trace'][-1].get('status') == 'running' and my_exec['trace'][-1].get('tool') == entry_done.get('tool'):
                                my_exec['trace'][-1] = entry_done
                            else:
                                my_exec['trace'].append(entry_done)
                            my_exec['tool'] = f"正在分析 {entry_done.get('tool', '工具')} 结果…"
                            self.execution = my_exec
                            await self._set_stream(text=self.stream_prefix_var.get() or '正在思考…', tool=my_exec['tool'], speaker_id=bot_id, speaker_name=bot['name'])
                            await self.publish({'type':'execution', 'execution':my_exec})
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
                return await self.emit(result['text'], 'chat', trace=result['trace'], goal=goal or '整理任务池快照', from_bot_id=bot_id, from_bot_name=bot['name'])
            except asyncio.CancelledError:
                partial = (self.stream_prefix_var.get() or '').strip()
                if partial:
                    return await self.emit(partial, 'chat', track=False, trace=list(my_exec['trace']), from_bot_id=bot_id, from_bot_name=bot['name'])
                return {'stopped': True}
            finally:
                await self._auto_release_phone_if_used()
                self._clear_bot_stream(bot_id)
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
                now_ts = time.time()
                self._last_scheduled_fire_by_bot[target_id] = now_ts
                # Prevent immediate curiosity follow-up right after a scheduled reminder fires!
                self.store.state['last_curiosity'] = now_ts
                self.activity = 'deep_research'
                cur_task = asyncio.current_task()
                self.active_task = cur_task
                if cur_task:
                    self.active_tasks_by_bot[target_id] = cur_task
                my_exec = {'bot_id': target_id, 'goal': goal[:200], 'trace': [], 'tool': None, 'started_at': now_ts}
                self.executions_by_bot[target_id] = my_exec
                self.execution = my_exec
                sched_turn_token = self.turn_id_var.set('turn-' + secrets.token_hex(6))
                sched_prefix_token = self.stream_prefix_var.set('')
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
                        self.harness.static_system_instructions(self.memory_engine.build_static_pinned_block()) + '\n\n'
                        '你的设定：' + self.persona(target_id) + '\n\n'
                        '当前时间：' + self._fmt_bj(time.time()) + ' (' + now_iso() + ')\n'
                        f'[{label}触发]：请按任务目标直接执行，并直接输出一句给主人的提醒内容（不要调用 notify_user 或 schedule_task，你的回复本身就会直接作为提醒推送给主人，不要再附加“推送已发送”之类的自我汇报）。'
                    )
                    # Exclude notify_user and schedule_task from scheduled execution so the LLM never double-notifies or re-schedules the same reminder!
                    allowed = (set(bot.get('tools') or [t['function']['name'] for t in TOOLS])) - {'notify_user', 'schedule_task'}
                    available_tools = [t for t in TOOLS if t['function']['name'] in allowed]
                    messages = [
                        {'role': 'system', 'content': system},
                        *recent,
                        {'role': 'user', 'content': f'⏰ [{label}到点触发]：{goal}'}
                    ]
                    trace = []
                    accumulated_texts: list[str] = []
                    final_turn_text: str = ''
                    await self._set_stream(text=f'⏰ 正在执行{label}…', speaker_id=target_id, speaker_name=bot['name'])
                    msg = {'content': ''}
                    use_big = (bot.get('engine') == 'big')
                    for _ in range(30):
                        msg = await self.complete(messages, tools=available_tools or None, big=use_big)
                        turn_txt = (msg.get('content') or '').strip()
                        if turn_txt:
                            final_turn_text = turn_txt
                            if not accumulated_texts or accumulated_texts[-1] != turn_txt:
                                accumulated_texts.append(turn_txt)
                            self.stream_prefix_var.set(final_turn_text)
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
                            running_entry = {'tool': name, 'arguments': args, 'duration_ms': 0, 'status': 'running'}
                            my_exec['trace'].append(running_entry)
                            my_exec['tool'] = name
                            self.execution = my_exec
                            await self._set_stream(text=final_turn_text or f'正在调用 {name}…', tool=name, speaker_id=target_id, speaker_name=bot['name'])
                            await self.publish({'type': 'execution', 'execution': my_exec})
                            started = asyncio.get_running_loop().time()
                            result = await self.harness.execute(name, args) if name in allowed else {'error': '此助手未启用该工具'}
                            entry = {'tool': name, 'arguments': args, 'result': result, 'duration_ms': max(1, round((asyncio.get_running_loop().time() - started) * 1000)), 'status': 'done'}
                            trace.append(entry)
                            running_entry.update(entry)
                            my_exec['tool'] = f'正在分析 {name} 结果…'
                            self.execution = my_exec
                            await self._set_stream(text=final_turn_text or '正在思考…', tool=my_exec['tool'], speaker_id=target_id, speaker_name=bot['name'])
                            await self.publish({'type': 'execution', 'execution': my_exec})
                            tool_content_str = self.context_search.maybe_offload_tool_output(
                                name, args, result, bot_id=target_id, turn_id=self.turn_id_var.get()
                            )
                            messages.append({'role': 'tool', 'tool_call_id': c['id'], 'content': tool_content_str})
                    final_text = final_turn_text.strip() or '\n\n'.join(accumulated_texts).strip() or (msg.get('content') or '').strip()
                    if not final_text and trace:
                        sum_msg = await self.complete(
                            messages + [{'role': 'user', 'content': '请基于以上定时任务执行结果，直接用自然简练的话输出给主人的提醒内容，不要再调用工具。'}],
                            tools=None, big=use_big
                        )
                        sum_txt = (sum_msg.get('content') or '').strip()
                        if sum_txt:
                            final_text = sum_txt
                    final_text = final_text or f'⏰ {goal}'
                    self.vault.log(f'完成{label}：{goal[:120]}')
                    self.store.state['last_curiosity'] = time.time()
                    self.store.save()
                    emitted = await self.emit(
                        final_text, 'curiosity', track=False,
                        trace=trace, goal=goal, from_bot_id=target_id, from_bot_name=bot['name']
                    )
                    if bot.get('notify', True):
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
                    self._clear_bot_stream(target_id)
                    try:
                        self.stream_prefix_var.reset(sched_prefix_token)
                        self.turn_id_var.reset(sched_turn_token)
                    except Exception:
                        pass
        finally:
            self.room_context.reset(room_marker)
            self.bot_context.reset(marker)

    async def edit_and_rerun(self, message_id: str, new_text: str, bot_id: str = 'companion') -> dict:
        """Edit an existing user message in `bot_id` room, archive/remove subsequent messages in that room, and re-run."""
        new_text = (new_text or '').strip()
        if not new_text:
            raise ValueError('新消息内容不能为空')
        msgs = self.store.state.get('messages', [])
        target_idx = -1
        for idx, m in enumerate(msgs):
            if m.get('id') == message_id and m.get('bot_id', 'companion') == bot_id:
                target_idx = idx
                break
        if target_idx < 0:
            raise ValueError('未找到该条消息')
        # Index removed tail messages into FTS5 before trimming so nothing is ever lost
        kept = []
        for idx, m in enumerate(msgs):
            if idx < target_idx or m.get('bot_id', 'companion') != bot_id:
                kept.append(m)
            else:
                self.context_search.index_message(m)
        self.store.state['messages'] = kept
        self.store.save()
        return await self.enqueue_chat(new_text, bot_id=bot_id)

    async def reroll_last(self, bot_id: str = 'companion', message_id: str | None = None) -> dict:
        """Re-generate the assistant reply for the last user message (or the user message preceding `message_id`)."""
        msgs = self.store.state.get('messages', [])
        room_indices = [i for i, m in enumerate(msgs) if m.get('bot_id', 'companion') == bot_id]
        if not room_indices:
            raise ValueError('当前房间暂无消息可重答')
        last_user_idx = -1
        for i in reversed(room_indices):
            if msgs[i].get('role') == 'user':
                last_user_idx = i
                break
        if last_user_idx < 0:
            raise ValueError('未找到上一条用户消息')
        user_text = str(msgs[last_user_idx].get('text') or '').strip()
        return await self.edit_and_rerun(msgs[last_user_idx]['id'], user_text, bot_id=bot_id)

    def quiet(self):
        return self.scheduler_engine.is_quiet_hour()

    async def _sleep_time_consolidate(self):
        """Silent background Sleep-Time Compute via MemoryEngine (never touches live_stream)."""
        async with self.lock:
            await self.memory_engine.sleep_time_consolidate(
                silent_complete_fn=lambda msgs: self.llm.complete_silent(msgs, tools=None, big=False)
            )

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
                        r_bot = r.get('bot_id', 'companion')
                        r['last_run'] = now
                        r['next_run'] = self._compute_next_routine_run(r, now + 2)
                        r['next_run_bj'] = self._fmt_bj(r['next_run'])
                        self.store.save()
                        # Skip duplicate fire if a one-shot timer already fired for the same bot in the last 55s
                        if now - self._last_scheduled_fire_by_bot.get(r_bot, 0.0) < 55.0:
                            break
                        if has_api:
                            if is_clock_routine:
                                await self.run_scheduled(r['goal'], bot_id=r_bot, label=f"定点例行 ({r['daily_time']})")
                            else:
                                await self.research(r['goal'], bot_id=r_bot)
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
