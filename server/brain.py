"""
[Module Responsibility]
DSB 多设备中枢大脑与对话/主动性主编排器 (`server/brain.py`)。
负责将提示词构建 (`BrainPromptMixin`)、工具循环与流式引擎 (`BrainToolsMixin`)、主动关怀与夜间整理 (`BrainProactiveMixin`)
以及手机/桌面/跨端路由控制组合为统一的 `Brain` 运行时实例。

[Public Interface]
- `Brain`: 核心中枢类，聚合所有领域 Mixin，维护多设备节点会话锁与实时推流状态
  - `.enqueue_chat(...)`, `.chat(...)`: 异步入队与执行单轮对话
  - `.stop_immediately(...)`: 0ms 立即中断当前生成与工具链
  - `.edit_and_rerun(...)`, `.reroll_last(...)`: 编辑重发与重新生成
  - `.emit(...)`: 向客户端推送状态与增量消息

[Dependencies]
- `core.brain_prompt_mixin.BrainPromptMixin`
- `core.brain_tools_mixin.BrainToolsMixin`
- `core.brain_proactive_mixin.BrainProactiveMixin`
- `core.phone_controller`, `core.desktop_controller`, `core.interconnect_router`, `core.tool_handlers`
"""
from __future__ import annotations
import asyncio, contextlib, contextvars, json, re, secrets, time
from typing import Any
from assistants import ensure_assistants, get_assistant, resolve_bot_for_source, resolve_bots_for_source
from core.blackboard_engine import BlackboardEngine
from core.brain_proactive_mixin import BrainProactiveMixin
from core.brain_prompt_mixin import BrainPromptMixin
from core.brain_tools_mixin import BrainToolsMixin
from core.context_search import ContextSearchEngine
from core.desktop_controller import DesktopControllerMixin
from core.interconnect_router import InterconnectRouterMixin
from core.llm_client import LLMClient
from core.memory_engine import MemoryEngine
from core.notification_hub import NotificationHub
from core.phone_controller import PhoneControllerMixin
from core.scheduler_engine import SchedulerEngine
from core.tool_handlers import ToolHandlersMixin
from core.turn_ledger import TurnLedgerManager
from core.vfs_router import PerNodePhoneRegistry
from core.vision_engine import VisionEngine
from harness import TOOLS
from storage import compact_trace_list, node_display_name, normalize_node_source
from vault_manager import TZ, now_iso


class Brain(
    BrainPromptMixin,
    BrainToolsMixin,
    BrainProactiveMixin,
    PhoneControllerMixin,
    DesktopControllerMixin,
    InterconnectRouterMixin,
    ToolHandlersMixin,
):
    def __init__(self, store, vault, harness, publish, escalation):
        self.store, self.vault, self.harness = store, vault, harness
        self.publish, self.escalation = publish, escalation
        self.lock = asyncio.Lock()
        self.bot_locks: dict[str, asyncio.Lock] = {}
        self.executions_by_bot: dict[str, dict] = {}
        self.streams_by_bot: dict[str, dict] = {}
        self.executions_by_node_bot: dict[str, dict] = {}
        self.streams_by_node_bot: dict[str, dict] = {}
        self.active_tasks_by_bot: dict[str, asyncio.Task] = {}
        self.active_tasks_by_node_bot: dict[str, asyncio.Task] = {}
        self.wake = asyncio.Event()
        self.activity = 'awaiting_config'
        self.task = None
        ensure_assistants(store)
        self.bot_context = contextvars.ContextVar('assistant', default='companion')
        self.room_context = contextvars.ContextVar('room', default=None)
        self.source_context = contextvars.ContextVar('source', default='phone')
        self.hop_depth = contextvars.ContextVar('hop_depth', default=0)
        self.call_chain_var = contextvars.ContextVar('call_chain', default=())
        self.turn_id_var = contextvars.ContextVar('turn_id', default='')
        self.stream_prefix_var = contextvars.ContextVar('stream_prefix', default='')
        self.thought_prefix_var = contextvars.ContextVar('thought_prefix', default='')
        self.choices_var = contextvars.ContextVar('choices', default=None)
        self.choices_prompt_var = contextvars.ContextVar('choices_prompt', default='')
        self.pending_sticker_var = contextvars.ContextVar('pending_sticker', default='')
        self.pending_stickers_var = contextvars.ContextVar('pending_stickers', default=())
        self.parent_stream_room_var = contextvars.ContextVar('parent_stream_room', default=None)
        self.stream_seq: int = 0
        self.last_completed_stream: dict | None = None
        self.execution = None
        self.live_stream = None
        self.active_task = None
        self.computer = None
        self.bg_tasks: dict[str, dict] = {}
        self.watchdogs: dict[str, dict] = {}
        self.last_prompt_tokens: dict[str, int] = {}
        self.last_cached_tokens: dict[str, int] = {}
        self._cached_vault_context: str = ''
        self._cached_pinned_block: str = ''
        self.phone_state_registry = PerNodePhoneRegistry()
        self._last_phone_tap: tuple[int, int] | None = None
        self._recent_phone_taps: list[tuple[int, int, str]] = []
        self.phone_pending: dict[str, asyncio.Future] = {}
        self.dsh_pending: dict[str, asyncio.Future] = {}
        self.dsh_bridge_info: dict[str, Any] = {'online': False, 'last_seen': 0.0, 'active_device_id': '', 'status': {}, 'peers': {}, 'inbox': []}
        self.dsh_dispatch: Any = None
        self.phone_online_checker: Any = None
        self._models_cache: tuple[float, str, list[str]] | None = None
        self._used_phone_in_turn: bool = False
        self._vdisplay_currently_open: bool = False
        self._phone_to_foreground_in_turn: bool = False
        self.store.state.setdefault('messages_epoch', 1)
        self._last_vision_nodes: list[dict] = []
        self._last_phone_node_count: int = -1
        self._last_phone_size: tuple[int, int] = (720, 1600)
        self._last_phone_pkg: str = ''
        self._last_phone_tree_hash: str = ''
        self._last_a11y_valid: bool = False
        self._last_a11y_reason: str = ''
        self._verified_a11y_hashes: dict[str, tuple[bool, str, dict]] = {}
        self._vision_cache: dict[str, str] = {}
        self._recent_client_msg_ids: dict[str, float] = {}
        self._stopped_turn_ids: set[str] = set()
        self.queued_tasks_by_bot: dict[str, set[asyncio.Task]] = {}
        self.queued_tasks_by_node_bot: dict[str, set[asyncio.Task]] = {}
        self._abort_epoch_by_bot: dict[str, int] = {}
        self._abort_epoch_by_node_bot: dict[str, int] = {}
        self._current_user_text_by_bot: dict[str, str] = {}
        self._phone_blocked_by_user_by_bot: dict[str, bool] = {}
        self._last_scheduled_fire_by_bot: dict[str, float] = {}
        self._last_emergency_alarm_at: float = 0.0
        self._reply_box_by_sig: dict[str, list[dict]] = {}

        # Domain 1 Core Engines
        self.harness.active_bot_getter = lambda: str(self.room_context.get() or self.bot_context.get() or '')
        self.context_search = ContextSearchEngine(self.harness.root / '.config' / 'context_index.db')
        try:
            self.context_search.sync_store_messages(self.store.state.get('messages', []))
        except Exception:
            pass
        self.llm = LLMClient(store, get_assistant, self.last_prompt_tokens, self.last_cached_tokens)
        self.memory_engine = MemoryEngine(store, vault, harness, self.last_prompt_tokens, context_search=self.context_search, last_cached_tokens=self.last_cached_tokens)
        self.vision_engine = VisionEngine(store, get_assistant, self.llm.fetch_available_models)
        self.scheduler_engine = SchedulerEngine(store, self.wake)
        self.notification_hub = NotificationHub(store, vault)
        self.blackboard_engine = BlackboardEngine(self)
        self._last_notif_bg_poll: float = 0.0

        self.harness.store = store
        self.harness.vault = vault
        self.harness.vision_reader = self.read_image_file_with_vision
        self.harness.custom_handlers = {
            'send_message': self._tool_send_message,
            'message_colleague': self._tool_send_message,
            'manage_bot': self._tool_manage_bot,
            'list_models': self._tool_list_models,
            'schedule_task': self._tool_schedule_task,
            'bg_task': self._tool_bg_task,
            'watchdog_task': self._tool_watchdog_task,
            'memory_search': self._tool_memory_search,
            'conversation_search': self._tool_conversation_search,
            'ctx_search': self._tool_ctx_search,
            'present_choices': self._tool_present_choices,
            'memory_pin': self._tool_memory_pin,
            'blackboard': self._tool_blackboard,
            'peek_bots': self._tool_peek_bots,
            'custom_tool': self._tool_custom_tool,
            'phone_macro': self._tool_phone_macro,
            'cloud_desktop': self._tool_cloud_desktop,
            'notify_user': self._tool_notify_user,
            'stay_silent': self._tool_stay_silent,
            'do_not_disturb': self._tool_stay_silent,
            'emergency_alarm': self._tool_emergency_alarm,
            'phone_workspace': self._tool_phone_workspace,
            'dsh_workspace': self._tool_dsh_workspace,
            'message_phone_bot': self._tool_dsh_workspace,
            'system_status': self._tool_system_status,
            'context_status': self._tool_system_status,
            'doctor': self._tool_system_status,
            'complete_goal': self._tool_complete_goal,
            'finish_goal': self._tool_complete_goal,
        }

    def is_phone_online(self) -> bool:
        try:
            return bool(self.phone_online_checker and self.phone_online_checker())
        except Exception:
            return False

    def _clear_bot_stream(self, bot_id: str | None = None, source: str | None = None) -> None:
        norm_src = normalize_node_source(source) if source else None
        if bot_id:
            if norm_src:
                nb_key = f'{norm_src}:{bot_id}'
                self.streams_by_node_bot.pop(nb_key, None)
                self.executions_by_node_bot.pop(nb_key, None)
                self.active_tasks_by_node_bot.pop(nb_key, None)
            else:
                for k in [k for k in self.streams_by_node_bot if k.endswith(f':{bot_id}')]:
                    self.streams_by_node_bot.pop(k, None)
                for k in [k for k in self.executions_by_node_bot if k.endswith(f':{bot_id}')]:
                    self.executions_by_node_bot.pop(k, None)
                for k in [k for k in self.active_tasks_by_node_bot if k.endswith(f':{bot_id}')]:
                    self.active_tasks_by_node_bot.pop(k, None)
            if norm_src is None or normalize_node_source((self.streams_by_bot.get(bot_id) or {}).get('source')) == norm_src:
                other_st = next((v for k, v in self.streams_by_node_bot.items() if k.endswith(f':{bot_id}')), None)
                if other_st:
                    self.streams_by_bot[bot_id] = other_st
                else:
                    self.streams_by_bot.pop(bot_id, None)
            if norm_src is None or normalize_node_source((self.executions_by_bot.get(bot_id) or {}).get('source')) == norm_src:
                other_ex = next((v for k, v in self.executions_by_node_bot.items() if k.endswith(f':{bot_id}')), None)
                if other_ex:
                    self.executions_by_bot[bot_id] = other_ex
                else:
                    self.executions_by_bot.pop(bot_id, None)
            if not any(k.endswith(f':{bot_id}') for k in self.executions_by_node_bot):
                self.active_tasks_by_bot.pop(bot_id, None)
        else:
            if norm_src:
                for k in [k for k in self.streams_by_node_bot if k.startswith(f'{norm_src}:')]:
                    self.streams_by_node_bot.pop(k, None)
                for k in [k for k in self.executions_by_node_bot if k.startswith(f'{norm_src}:')]:
                    self.executions_by_node_bot.pop(k, None)
            else:
                self.streams_by_node_bot.clear()
                self.executions_by_node_bot.clear()
                self.streams_by_bot.clear()
                self.executions_by_bot.clear()
                self.active_tasks_by_bot.clear()
        self.live_stream = next(iter(self.streams_by_node_bot.values()), None) or next(iter(self.streams_by_bot.values()), None)
        self.execution = next(iter(self.executions_by_node_bot.values()), None) or next(iter(self.executions_by_bot.values()), None)
        self.active_task = next(iter(self.active_tasks_by_bot.values()), None)
        self.activity = 'thinking' if (self.executions_by_node_bot or self.executions_by_bot) else self.ready_state()

    @staticmethod
    def _summarize_trace_brief(trace_list: list[dict], limit: int = 6) -> str:
        items: list[str] = []
        for t in (trace_list or [])[:limit]:
            if not isinstance(t, dict) or not t.get('tool'):
                continue
            tname = str(t['tool'])
            targs = t.get('arguments') if isinstance(t.get('arguments'), dict) else {}
            hint = ''
            for k in ('op', 'action', 'url', 'query', 'target', 'path', 'to_bot_id', 'command', 'text'):
                v = targs.get(k)
                if v and isinstance(v, (str, int, float)):
                    sv = str(v).strip().replace('\n', ' ')[:36]
                    if sv:
                        hint = f'{hint}:{sv}' if (hint and k not in ('op', 'action')) else sv
                        if len(hint) >= 30:
                            break
            items.append(f'{tname}({hint})' if hint else tname)
        return ', '.join(items)

    async def stop_immediately(self, bot_id: str | None = None, reason: str = 'user_stop', commit_partial: bool = True, source: str | None = None) -> dict:
        """
        0ms Hard Interrupt:
        1. Increment abort epoch and record turn_id in `_stopped_turn_ids`.
        2. If `commit_partial` and there is any streamed text or tool trace, immediately commit it to `state['messages']`
           (including intermediate `thought` / stage progress and tool argument summary so the Bot never loses memory of what it was just doing!)
        3. Cancel ONLY the target bot's active/queued tasks (`active_tasks_by_bot[b_id]`), never killing independent peer Bots!
        """
        req_src = normalize_node_source(source or self.source_context.get())
        target_bots = [bot_id] if bot_id else list(set(
            list(self.streams_by_bot.keys())
            + list(self.executions_by_bot.keys())
            + list(self.active_tasks_by_bot.keys())
            + list(self.queued_tasks_by_bot.keys())
            + ([self.execution.get('bot_id')] if isinstance(self.execution, dict) and self.execution.get('bot_id') else [])
        ))
        committed_msg = None
        had_phone_activity = (req_src != 'pc') and (bool(self._vdisplay_currently_open) or bool(self.phone_pending))
        cur_global_task = self.active_task
        for b_id in target_bots:
            if not b_id:
                continue
            if source:
                nb_target = f'{req_src}:{b_id}'
                cur_stream = self.streams_by_node_bot.get(nb_target)
                cur_exec = self.executions_by_node_bot.get(nb_target)
                if cur_stream is None and (self.streams_by_bot.get(b_id) or {}).get('source') == req_src:
                    cur_stream = self.streams_by_bot.get(b_id)
                if cur_exec is None and (self.executions_by_bot.get(b_id) or {}).get('source') == req_src:
                    cur_exec = self.executions_by_bot.get(b_id)
                active_src = req_src
            else:
                cur_stream = self.streams_by_bot.get(b_id) or (self.live_stream if (self.live_stream or {}).get('bot_id') == b_id else None)
                cur_exec = self.executions_by_bot.get(b_id) or (self.execution if (self.execution or {}).get('bot_id') == b_id else None)
                active_src = normalize_node_source((cur_stream or cur_exec or {}).get('source') or req_src)
            nb_active = f'{active_src}:{b_id}'
            self._abort_epoch_by_node_bot[nb_active] = self._abort_epoch_by_node_bot.get(nb_active, 0) + 1
            if not source:
                self._abort_epoch_by_bot[b_id] = self._abort_epoch_by_bot.get(b_id, 0) + 1
                for k_ep in list(self._abort_epoch_by_node_bot.keys()):
                    if k_ep.endswith(f':{b_id}'):
                        self._abort_epoch_by_node_bot[k_ep] = self._abort_epoch_by_node_bot.get(k_ep, 0) + 1
            if reason == 'user_stop':
                if active_src != 'pc':
                    self._phone_blocked_by_user_by_bot[b_id] = True
                active_goals = self.store.state.get('active_goals')
                if isinstance(active_goals, dict):
                    stopped_g = active_goals.pop(nb_active, None)
                    if isinstance(stopped_g, dict) and stopped_g.get('goal'):
                        self.store.state['last_goal_run'] = {
                            'goal': str(stopped_g.get('goal'))[:200],
                            'status': 'stopped_by_user',
                            'steps': int(stopped_g.get('total_steps') or 0),
                            'wakeups': int(stopped_g.get('wakeups') or 0),
                            'updated_at': now_iso(),
                        }
            # MUST grab tasks BEFORE _clear_bot_stream(b_id) pops active_tasks_by_node_bot!
            t_active = self.active_tasks_by_node_bot.get(nb_active) or (None if source else self.active_tasks_by_bot.get(b_id))
            q_tasks = list(self.queued_tasks_by_node_bot.get(nb_active) or (() if source else (self.queued_tasks_by_bot.get(b_id) or ())))
            partial_text = ''
            cur_thought = ''
            if isinstance(cur_stream, dict):
                raw_t = str(cur_stream.get('text') or '').strip()
                if raw_t and not self._is_placeholder_text(raw_t):
                    partial_text = raw_t
                raw_th = str(cur_stream.get('thought') or '').strip()
                if raw_th and not raw_th.startswith(('正在生成 ', '正在准备调用 ')):
                    cur_thought = raw_th
            trace_list = list(cur_exec.get('trace') or []) if isinstance(cur_exec, dict) else []
            if not partial_text and cur_thought:
                prog_lines = [
                    ln.split(']', 1)[-1].strip()
                    for ln in cur_thought.splitlines()
                    if ln.strip().startswith('💬 [') and ']' in ln
                ]
                if prog_lines:
                    partial_text = prog_lines[-1]
            turn_id = str((cur_stream or {}).get('turn_id') or '')
            if turn_id:
                self._stopped_turn_ids.add(turn_id)
                if len(self._stopped_turn_ids) > 200:
                    self._stopped_turn_ids.clear()
                    self._stopped_turn_ids.add(turn_id)
            TurnLedgerManager.finish_turn(
                self.store, active_src, b_id, turn_id, status='interrupted', reason=reason, steps_count=len(trace_list)
            )
            if commit_partial and (partial_text or trace_list or cur_thought):
                try:
                    bot_obj = get_assistant(self.store, b_id)
                    b_name = bot_obj['name']
                except Exception:
                    b_name = b_id
                trace_brief = self._summarize_trace_brief(trace_list)
                trace_suffix = f'：已中断第 {len(trace_list)} 步 {trace_brief}' if (trace_brief and not partial_text) else ''
                if reason == 'user_preempt':
                    final_txt = (
                        f'{partial_text}\n\n（主人插话，已立即中断当前操作并优先响应新消息）'
                        if partial_text
                        else f'（主人插话，已立即中断当前操作{trace_suffix}，优先响应新消息）'
                    )
                else:
                    final_txt = partial_text or f'（已停止执行{trace_suffix}）'
                recent_bot_msgs = [m for m in self.store.state.get('messages', [])[-6:] if m.get('bot_id') == b_id and normalize_node_source(m.get('source')) == active_src]
                already_committed = bool(
                    turn_id and any(m.get('turn_id') == turn_id and m.get('role') == 'assistant' for m in recent_bot_msgs)
                )
                if not already_committed:
                    partial_steps = list(cur_exec.get('turn_steps') or []) if isinstance(cur_exec, dict) else []
                    committed_msg = self.store.message(
                        'assistant', final_txt, 'chat', bot_id=b_id, from_bot_id=b_id,
                        from_bot_name=b_name, thought=cur_thought, trace=trace_list,
                        turn_steps=partial_steps, notify=False, stopped_early=True, source=active_src,
                    )
                    self.stream_seq += 1
                    committed_msg['seq'] = self.stream_seq
                    committed_msg['turn_id'] = turn_id or committed_msg['id']
                    self.last_completed_stream = {
                        'seq': self.stream_seq, 'turn_id': committed_msg['turn_id'], 'bot_id': b_id, 'source': active_src,
                        'speaker_id': b_id, 'speaker_name': b_name, 'text': final_txt,
                        'final_message_id': committed_msg['id'], 'done': True, 'completed_at': time.time(),
                    }
                    self.store.save()
                    await self.publish({'type': 'message', 'source': active_src, 'message': committed_msg, 'seq': self.stream_seq})
            self._clear_bot_stream(b_id, source=active_src)
            with contextlib.suppress(Exception):
                await self.publish({'type': 'stream', 'source': active_src, 'bot_id': b_id, 'turn_id': turn_id, 'stream': None, 'seq': self.stream_seq})
                await self.publish({'type': 'execution', 'source': active_src, 'bot_id': b_id, 'execution': None, 'seq': self.stream_seq})
            if t_active and not t_active.done():
                t_active.cancel()
            for qt in q_tasks:
                if qt and not qt.done():
                    qt.cancel()
            self.queued_tasks_by_node_bot.pop(nb_active, None)
            if not source:
                self.queued_tasks_by_bot.pop(b_id, None)

        if not bot_id and cur_global_task and not cur_global_task.done() and not source:
            cur_global_task.cancel()
        if not bot_id:
            self._clear_bot_stream(None, source=req_src if source else None)
        if had_phone_activity:
            for req_id, fut in list(self.phone_pending.items()):
                self.phone_pending.pop(req_id, None)
                if fut and not fut.done():
                    fut.cancel()
            self._used_phone_in_turn = False
            self._vdisplay_currently_open = False
            try:
                await self.publish({
                    'type': 'phone_workspace_command',
                    'request_id': 'ph-stop-' + secrets.token_hex(4),
                    'bot_id': bot_id or 'companion',
                    'source': req_src,
                    'op': 'release',
                    'args': {'op': 'release', 'force': True, 'source': req_src},
                    'timestamp': time.time(),
                })
            except Exception:
                pass
        await self.publish({'type': 'stopped', 'source': req_src, 'bot_id': bot_id or 'all', 'message': committed_msg, 'seq': self.stream_seq})
        return {'stopped': True, 'released_phone': had_phone_activity, 'committed_message': committed_msg, 'seq': self.stream_seq}

    def _get_bot_lock(self, bot_id: str, source: str | None = None) -> asyncio.Lock:
        b = (bot_id or 'companion').strip()
        s = normalize_node_source(source or self.source_context.get())
        key = f'{s}::{b}'
        if key not in self.bot_locks:
            self.bot_locks[key] = asyncio.Lock()
        return self.bot_locks[key]

    def is_node_bot_busy(self, bot_id: str, source: str = 'phone') -> bool:
        s = normalize_node_source(source)
        b = (bot_id or 'companion').strip()
        nb = f'{s}:{b}'
        return (
            self._get_bot_lock(b, s).locked()
            or nb in self.executions_by_node_bot
            or bool(self.queued_tasks_by_node_bot.get(nb))
        )

    def is_any_bot_busy(self) -> bool:
        return (
            self.lock.locked()
            or any(lk.locked() for lk in self.bot_locks.values())
            or bool(self.executions_by_bot)
            or any(bool(q) for q in self.queued_tasks_by_bot.values())
        )

    async def emit(self, text, kind='curiosity', track=True, **extra):
        bot_id = extra.setdefault('bot_id', self.bot_context.get())
        cur_src = normalize_node_source(extra.get('source') or self.source_context.get())
        extra['source'] = cur_src
        try:
            bot_notify = get_assistant(self.store, bot_id).get('notify', True)
        except ValueError:
            bot_notify = True
        extra.setdefault('notify', bot_notify)
        text, tag_choices, tag_thought, tag_sticker = self._extract_all_tags(str(text or ''))
        if extra.get('llm_text'):
            clean_llm_txt, _, _, _ = self._extract_all_tags(str(extra['llm_text']))
            extra['llm_text'] = clean_llm_txt or text
        else:
            extra['llm_text'] = text
        pending_choices = self.choices_var.get()
        if pending_choices and 'choices' not in extra:
            extra['choices'] = list(pending_choices)
            self.choices_var.set(None)
            self.choices_prompt_var.set('')
        elif tag_choices and 'choices' not in extra and kind in ('chat', 'curiosity', 'result'):
            extra['choices'] = tag_choices

        queued_stickers: list[str] = list(self.pending_stickers_var.get() or ())
        if extra.get('sticker') and str(extra['sticker']) not in queued_stickers:
            queued_stickers.append(str(extra['sticker']))
        elif tag_sticker and tag_sticker not in queued_stickers:
            queued_stickers.append(tag_sticker)
        elif self.pending_sticker_var.get() and self.pending_sticker_var.get() not in queued_stickers:
            queued_stickers.append(self.pending_sticker_var.get())
        self.pending_sticker_var.set('')
        self.pending_stickers_var.set(())

        if len(queued_stickers) > 1:
            turn_id_base = self.turn_id_var.get() or ('turn-' + secrets.token_hex(6))
            for idx_img, s_url in enumerate(queued_stickers[:-1], 1):
                sub_extra: dict[str, Any] = {
                    'bot_id': bot_id,
                    'source': cur_src,
                    'notify': False,
                    'sticker': s_url,
                    'stickers': [s_url],
                    'image_index': idx_img,
                    'image_total': len(queued_stickers),
                    'is_sub_turn': True,
                }
                for k_copy in ('from_bot_id', 'from_bot_name', 'from_node', 'to_bot_id', 'to_bot_name', 'to_node', 'direction'):
                    if extra.get(k_copy):
                        sub_extra[k_copy] = extra[k_copy]
                sub_m = self.store.message('assistant', '', kind, **sub_extra)
                self.stream_seq += 1
                sub_m['seq'] = self.stream_seq
                sub_m['turn_id'] = turn_id_base
                await self.publish({'type': 'message', 'source': cur_src, 'message': sub_m, 'seq': self.stream_seq})
            extra['sticker'] = queued_stickers[-1]
            extra['stickers'] = queued_stickers
            extra['image_index'] = len(queued_stickers)
            extra['image_total'] = len(queued_stickers)
        elif len(queued_stickers) == 1:
            extra['sticker'] = queued_stickers[0]
            extra['stickers'] = queued_stickers
            extra['image_index'] = 1
            extra['image_total'] = 1
        if 'thought' not in extra:
            prev_st = self.streams_by_node_bot.get(f'{cur_src}:{bot_id}') or self.streams_by_bot.get(bot_id) or self.live_stream or {}
            combined_th = str(prev_st.get('thought') or '').strip()
            if combined_th.startswith(('正在生成 ', '正在准备调用 ')):
                combined_th = ''
            if tag_thought and tag_thought not in combined_th:
                combined_th = (combined_th + '\n' + tag_thought).strip()
            if combined_th:
                extra['thought'] = combined_th
        m = self.store.message('assistant', text, kind, **extra)
        self.stream_seq += 1
        turn_id = self.turn_id_var.get() or m['id']
        m['seq'] = self.stream_seq
        m['turn_id'] = turn_id
        try:
            self.context_search.index_message(m)
        except Exception:
            pass
        self._clear_bot_stream(bot_id, source=cur_src)
        self.last_completed_stream = {
            'seq': self.stream_seq, 'turn_id': turn_id, 'bot_id': bot_id, 'source': cur_src,
            'speaker_id': extra.get('from_bot_id') or bot_id, 'speaker_name': extra.get('from_bot_name') or '',
            'text': m['text'], 'final_message_id': m['id'], 'done': True, 'completed_at': time.time(),
        }
        await self.publish({'type': 'message', 'source': cur_src, 'message': m, 'seq': self.stream_seq})
        if track and extra.get('escalate') and extra['notify'] and cur_src != 'pc':
            await self.escalation.track(m)
        return m

    def ready_state(self):
        has_key = bool(self.store.config.effective_api_key(big=False) or self.store.config.effective_api_key(big=True))
        has_model = bool(self.store.config.effective_model(big=False) or self.store.config.effective_model(big=True))
        return 'curiosity_idle' if (has_key and has_model) else 'awaiting_config'

    def _reuse_unanswered_user_message_if_match(
        self,
        text: str,
        bot_id: str,
        src: str,
        client_msg_id: str | None = None,
    ) -> tuple[dict | None, list[str]]:
        """
        If the user re-sends the exact same message (or same `client_msg_id`) when the last user message
        in `(bot_id, src)` never received a normal assistant reply (e.g. had no reply, errored, or was stopped early),
        reuse that existing user message in-place and strip any trailing error/stopped bubbles!
        """
        clean_t = (text or '').strip()
        if not clean_t:
            return None, []
        norm_src = normalize_node_source(src)
        msgs = self.store.state.get('messages', [])
        room_indices = [
            i for i, m in enumerate(msgs)
            if m.get('bot_id', 'companion') == bot_id and normalize_node_source(m.get('source')) == norm_src
        ]
        if not room_indices:
            return None, []
        target_u_idx = -1
        if client_msg_id:
            for i in reversed(room_indices):
                if msgs[i].get('role') == 'user' and msgs[i].get('client_msg_id') == client_msg_id:
                    target_u_idx = i
                    break
        if target_u_idx < 0:
            last_u_idx = -1
            for i in reversed(room_indices):
                if msgs[i].get('role') == 'user' and msgs[i].get('kind') != 'bot_message':
                    last_u_idx = i
                    break
            if last_u_idx >= 0 and str(msgs[last_u_idx].get('text') or '').strip() == clean_t:
                after_in_room = [msgs[i] for i in room_indices if i > last_u_idx]
                has_normal_reply = any(
                    m.get('role') == 'assistant'
                    and m.get('kind') not in ('error', 'stopped')
                    and not m.get('stopped_early')
                    and str(m.get('text') or '').strip() not in ('', '（已停止执行）')
                    and '主人插话，已立即中断当前操作' not in str(m.get('text') or '')
                    for m in after_in_room
                )
                if not has_normal_reply:
                    target_u_idx = last_u_idx
        if target_u_idx < 0:
            return None, []
        u_msg = msgs[target_u_idx]
        removed_ids: list[str] = []
        kept: list[dict] = []
        for idx, m in enumerate(msgs):
            if idx > target_u_idx and m.get('bot_id', 'companion') == bot_id and normalize_node_source(m.get('source')) == norm_src:
                self.context_search.index_message(m)
                if m.get('id'):
                    removed_ids.append(str(m['id']))
            else:
                kept.append(m)
        if removed_ids:
            self.store.state['messages'] = kept
            self.store.state['messages_epoch'] = int(self.store.state.get('messages_epoch') or 1) + 1
        return u_msg, removed_ids

    async def enqueue_chat(self, text: str, bot_id: str = 'companion', mode: str = 'auto', client_msg_id: str | None = None, source: str = 'phone') -> dict:
        src = normalize_node_source(source)
        nb_enq = f'{src}:{bot_id}'
        same_src_exec = bool(self.executions_by_node_bot.get(nb_enq))
        is_bot_busy = (
            same_src_exec
            or self._get_bot_lock(bot_id, src).locked()
        )
        if client_msg_id:
            now_ts = time.time()
            self._recent_client_msg_ids = {k: v for k, v in self._recent_client_msg_ids.items() if now_ts - v < 120.0}
            if client_msg_id in self._recent_client_msg_ids and is_bot_busy:
                tail = [x for x in self.store.state['messages'] if normalize_node_source(x.get('source')) == src][-10:]
                existing_um = next((x for x in reversed(tail) if x.get('client_msg_id') == client_msg_id or (x.get('kind') == 'user' and x.get('text') == text)), None)
                return {
                    'queued': True,
                    'deduplicated': True,
                    'turn_id': (existing_um or {}).get('turn_id', ''),
                    'user_message': existing_um,
                    'recent_tail': tail,
                    'messages_epoch': int(self.store.state.get('messages_epoch') or 1),
                    'seq': self.stream_seq,
                }
            self._recent_client_msg_ids[client_msg_id] = now_ts

        # User Input is ALWAYS Priority #1 on the SAME node (`src`):
        if is_bot_busy:
            await self.stop_immediately(bot_id=bot_id, reason='user_preempt', source=src)
            for _ in range(20):
                if not self._get_bot_lock(bot_id, src).locked():
                    break
                await asyncio.sleep(0.01)

        bot = get_assistant(self.store, bot_id)
        turn_id = 'turn-' + secrets.token_hex(6)
        self.store.state['last_user'] = time.time()
        reused_msg, removed_ids = self._reuse_unanswered_user_message_if_match(text, bot_id, src, client_msg_id=client_msg_id)
        if reused_msg is not None:
            m = reused_msg
            if client_msg_id:
                m['client_msg_id'] = client_msg_id
            self.stream_seq += 1
            m['seq'] = self.stream_seq
            m['turn_id'] = turn_id
            self.store.save()
            if removed_ids:
                await self.publish({
                    'type': 'messages_truncated',
                    'bot_id': bot_id,
                    'source': src,
                    'removed_message_ids': removed_ids,
                    'messages_epoch': int(self.store.state.get('messages_epoch') or 1),
                    'seq': self.stream_seq,
                })
        else:
            m = self.store.message('user', text, bot_id=bot_id, source=src)
            if client_msg_id:
                m['client_msg_id'] = client_msg_id
            self.stream_seq += 1
            m['seq'] = self.stream_seq
            m['turn_id'] = turn_id
            self.vault.log('用户：' + text, bot_id=bot_id)
            self.store.save()
            await self.publish({'type': 'message', 'source': src, 'message': m, 'seq': self.stream_seq})
        visible = {msg['id'] for msg in self.store.state['messages'] if msg.get('bot_id', 'companion') == bot_id}
        await self.escalation.ack([mid for mid in self.store.state['pending'] if mid in visible])

        marker = self.bot_context.set(bot_id)
        room_marker = self.room_context.set(bot_id)
        src_marker = self.source_context.set(src)
        turn_token = self.turn_id_var.set(turn_id)
        prefix_token = self.stream_prefix_var.set('')
        try:
            self.activity = 'thinking'
            exec_obj = {'bot_id': bot_id, 'source': src, 'goal': text[:200], 'trace': [], 'tool': None, 'started_at': time.time()}
            self.executions_by_node_bot[f'{src}:{bot_id}'] = exec_obj
            self.executions_by_bot[bot_id] = exec_obj
            self.execution = exec_obj
            await self._set_stream(text='正在思考…', speaker_id=bot_id, speaker_name=bot['name'])
        finally:
            self.stream_prefix_var.reset(prefix_token)
            self.turn_id_var.reset(turn_token)
            self.source_context.reset(src_marker)
            self.room_context.reset(room_marker)
            self.bot_context.reset(marker)

        async def _bg():
            try:
                await self.chat(text, bot_id=bot_id, mode=mode, pre_recorded_user_msg=m, turn_id=turn_id, source=src)
            except asyncio.CancelledError:
                pass
            except Exception as e:
                err_msg = f'⚠️ [来自 {bot["name"]} ({bot_id}) 的报错]: {str(e)[:260]}'
                await self.emit(err_msg, 'error', track=False, bot_id=bot_id, source=src)

        bg_task = asyncio.create_task(_bg())
        self.queued_tasks_by_bot.setdefault(bot_id, set()).add(bg_task)
        self.queued_tasks_by_node_bot.setdefault(nb_enq, set()).add(bg_task)
        bg_task.add_done_callback(lambda t, bid=bot_id, nbk=nb_enq: (self.queued_tasks_by_bot.get(bid, set()).discard(t), self.queued_tasks_by_node_bot.get(nbk, set()).discard(t)))
        has_api = bool(
            (self.store.config.effective_api_key(big=False) or self.store.config.effective_api_key(big=True))
            and (self.store.config.effective_model(big=False) or self.store.config.effective_model(big=True))
        )
        if not has_api:
            await bg_task
        else:
            await asyncio.sleep(0)
        tail = [x for x in self.store.state['messages'] if normalize_node_source(x.get('source')) == src][-10:]
        return {
            'ok': True, 'queued': True, 'reused': reused_msg is not None, 'reused_existing_user_message': reused_msg is not None,
            'removed_message_ids': removed_ids, 'turn_id': turn_id, 'user_message': m,
            'recent_tail': tail, 'messages_epoch': int(self.store.state.get('messages_epoch') or 1), 'seq': self.stream_seq,
        }

    async def chat(self, text, bot_id='companion', mode='auto', pre_recorded_user_msg=None, turn_id=None, client_msg_id: str | None = None, source: str = 'phone'):
        src = normalize_node_source(source or (pre_recorded_user_msg or {}).get('source'))
        if client_msg_id and pre_recorded_user_msg is None:
            now_ts = time.time()
            self._recent_client_msg_ids = {k: v for k, v in self._recent_client_msg_ids.items() if now_ts - v < 120.0}
            if client_msg_id in self._recent_client_msg_ids:
                same_src = [x for x in self.store.state['messages'] if normalize_node_source(x.get('source')) == src]
                return same_src[-1] if same_src else {'text': ''}
            self._recent_client_msg_ids[client_msg_id] = now_ts
        if pre_recorded_user_msg is None and self.hop_depth.get() == 0:
            cur_exec_for_bot = self.executions_by_bot.get(bot_id)
            same_src_exec = bool(cur_exec_for_bot and normalize_node_source(cur_exec_for_bot.get('source')) == src)
            is_bot_busy = (
                same_src_exec
                or self._get_bot_lock(bot_id, src).locked()
            )
            if is_bot_busy:
                await self.stop_immediately(bot_id=bot_id, reason='user_preempt', source=src)
                for _ in range(20):
                    if not self._get_bot_lock(bot_id, src).locked():
                        break
                    await asyncio.sleep(0.01)
            reused_msg, _ = self._reuse_unanswered_user_message_if_match(text, bot_id, src, client_msg_id=client_msg_id)
            if reused_msg is not None:
                pre_recorded_user_msg = reused_msg
        bot = get_assistant(self.store, bot_id)
        has_api = bool((self.store.config.effective_api_key(big=False) or self.store.config.effective_api_key(big=True)) and (self.store.config.effective_model(big=False) or self.store.config.effective_model(big=True)))
        if mode == 'execute' or (mode == 'auto' and bot.get('engine') == 'big' and not has_api):
            return await self.research(text, manual=True, bot_id=bot_id, record_user=(pre_recorded_user_msg is None))
        marker = self.bot_context.set(bot_id)
        room_marker = self.room_context.set(bot_id)
        src_marker = self.source_context.set(src)
        try:
            return await self._chat(text, pre_recorded_user_msg=pre_recorded_user_msg, turn_id=turn_id, source=src, client_msg_id=client_msg_id)
        finally:
            self.source_context.reset(src_marker)
            self.room_context.reset(room_marker)
            self.bot_context.reset(marker)

    async def _spawn_turn_for_existing_user_message(
        self,
        user_msg: dict,
        bot_id: str,
        src: str,
        removed_ids: list[str],
        is_edit: bool = False,
    ) -> dict:
        """
        Increment `messages_epoch`, broadcast truncation/edit metadata, and launch an async turn
        for an already-existing `user_msg` in-place (NEVER appending a duplicate user message!).
        """
        src = normalize_node_source(src)
        nb_enq = f'{src}:{bot_id}'
        bot = get_assistant(self.store, bot_id)
        text = str(user_msg.get('text') or '').strip()
        turn_id = 'turn-' + secrets.token_hex(6)
        self.store.state['last_user'] = time.time()
        self.stream_seq += 1
        user_msg['seq'] = self.stream_seq
        user_msg['turn_id'] = turn_id
        self.store.state['messages_epoch'] = int(self.store.state.get('messages_epoch') or 1) + 1
        self.store.save()
        await self.publish({
            'type': 'messages_truncated',
            'bot_id': bot_id,
            'source': src,
            'edited_message': user_msg if is_edit else None,
            'user_message': user_msg,
            'removed_message_ids': removed_ids,
            'messages_epoch': self.store.state['messages_epoch'],
            'seq': self.stream_seq,
        })
        visible = {msg['id'] for msg in self.store.state['messages'] if msg.get('bot_id', 'companion') == bot_id}
        await self.escalation.ack([mid for mid in self.store.state['pending'] if mid in visible])

        marker = self.bot_context.set(bot_id)
        room_marker = self.room_context.set(bot_id)
        src_marker = self.source_context.set(src)
        turn_token = self.turn_id_var.set(turn_id)
        prefix_token = self.stream_prefix_var.set('')
        try:
            self.activity = 'thinking'
            exec_obj = {'bot_id': bot_id, 'source': src, 'goal': text[:200], 'trace': [], 'tool': None, 'started_at': time.time()}
            self.executions_by_node_bot[f'{src}:{bot_id}'] = exec_obj
            self.executions_by_bot[bot_id] = exec_obj
            self.execution = exec_obj
            await self._set_stream(text='正在思考…', speaker_id=bot_id, speaker_name=bot['name'])
        finally:
            self.stream_prefix_var.reset(prefix_token)
            self.turn_id_var.reset(turn_token)
            self.source_context.reset(src_marker)
            self.room_context.reset(room_marker)
            self.bot_context.reset(marker)

        async def _bg():
            try:
                await self.chat(text, bot_id=bot_id, mode='chat', pre_recorded_user_msg=user_msg, turn_id=turn_id, source=src)
            except asyncio.CancelledError:
                pass
            except Exception as e:
                err_msg = f'⚠️ [来自 {bot["name"]} ({bot_id}) 的报错]: {str(e)[:260]}'
                await self.emit(err_msg, 'error', track=False, bot_id=bot_id, source=src)

        bg_task = asyncio.create_task(_bg())
        self.queued_tasks_by_bot.setdefault(bot_id, set()).add(bg_task)
        self.queued_tasks_by_node_bot.setdefault(nb_enq, set()).add(bg_task)
        bg_task.add_done_callback(lambda t, bid=bot_id, nbk=nb_enq: (self.queued_tasks_by_bot.get(bid, set()).discard(t), self.queued_tasks_by_node_bot.get(nbk, set()).discard(t)))
        has_api = bool(
            (self.store.config.effective_api_key(big=False) or self.store.config.effective_api_key(big=True))
            and (self.store.config.effective_model(big=False) or self.store.config.effective_model(big=True))
        )
        if not has_api:
            await bg_task
        else:
            await asyncio.sleep(0)
        tail = [x for x in self.store.state['messages'] if normalize_node_source(x.get('source')) == src][-10:]
        return {
            'ok': True, 'queued': True, 'turn_id': turn_id, 'edited_message': user_msg,
            'user_message': user_msg, 'rerolled_user_message_id': user_msg.get('id'),
            'removed_message_ids': removed_ids, 'messages_epoch': self.store.state['messages_epoch'],
            'recent_tail': tail, 'seq': self.stream_seq,
        }

    async def _preempt_if_bot_busy(self, bot_id: str, reason: str, source: str | None = None):
        norm_src = normalize_node_source(source or self.source_context.get())
        cur_exec_for_bot = self.executions_by_bot.get(bot_id)
        same_src_exec = bool(cur_exec_for_bot and normalize_node_source(cur_exec_for_bot.get('source')) == norm_src)
        is_bot_busy = (
            same_src_exec
            or self._get_bot_lock(bot_id, norm_src).locked()
        )
        if is_bot_busy:
            await self.stop_immediately(bot_id=bot_id, reason=reason, commit_partial=False, source=norm_src)
            for _ in range(20):
                if not self._get_bot_lock(bot_id, norm_src).locked():
                    break
                await asyncio.sleep(0.01)

    def _truncate_room_messages_after(self, msgs: list[dict], target_idx: int, bot_id: str, src: str) -> list[str]:
        norm_src = normalize_node_source(src)
        kept, removed_ids = [], []
        for idx, m in enumerate(msgs):
            if idx <= target_idx or m.get('bot_id', 'companion') != bot_id or normalize_node_source(m.get('source')) != norm_src:
                kept.append(m)
            else:
                self.context_search.index_message(m)
                if m.get('id'):
                    removed_ids.append(str(m['id']))
        self.store.state['messages'] = kept
        return removed_ids

    async def edit_and_rerun(self, message_id: str, new_text: str, bot_id: str = 'companion', source: str | None = None) -> dict:
        """
        Edit an existing user message in-place (preserving its `id` and list index!),
        truncate/archive only subsequent messages in the same `(bot_id, source)` room,
        increment `messages_epoch`, and re-run without creating a duplicate user message.
        """
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
            for idx, m in enumerate(msgs):
                if m.get('id') == message_id or (message_id and m.get('client_msg_id') == message_id):
                    target_idx = idx
                    break
        if target_idx < 0 and source:
            req_src = normalize_node_source(source)
            for idx in range(len(msgs) - 1, -1, -1):
                m = msgs[idx]
                if m.get('bot_id', 'companion') == bot_id and normalize_node_source(m.get('source')) == req_src and m.get('role') == 'user':
                    target_idx = idx
                    break
        if target_idx < 0:
            raise ValueError('未找到该条消息')
        target_msg = msgs[target_idx]
        bot_id = str(target_msg.get('bot_id') or bot_id or 'companion')
        src = normalize_node_source(source or target_msg.get('source'))

        await self._preempt_if_bot_busy(bot_id, 'user_edit', source=src)

        msgs = self.store.state.get('messages', [])
        target_idx = next((i for i, m in enumerate(msgs) if m.get('id') == target_msg.get('id')), -1)
        if target_idx < 0:
            raise ValueError('未找到该条消息')
        target_msg = msgs[target_idx]
        target_msg['text'] = new_text
        target_msg['edited_at'] = now_iso()
        target_msg.pop('llm_text', None)

        removed_ids = self._truncate_room_messages_after(msgs, target_idx, bot_id, src)
        self.vault.log(f'用户（编辑重发 {target_msg["id"]}）：' + new_text, bot_id=bot_id)
        return await self._spawn_turn_for_existing_user_message(
            target_msg,
            bot_id=bot_id,
            src=src,
            removed_ids=removed_ids,
            is_edit=True,
        )

    async def reroll_last(self, bot_id: str = 'companion', message_id: str | None = None, source: str | None = None) -> dict:
        """
        Re-generate the assistant reply for the target user message (or the user message preceding `message_id`)
        in-place, preserving the user message's `id` and list position without appending a duplicate!
        """
        msgs = self.store.state.get('messages', [])
        explicit_src = normalize_node_source(source) if source else None
        matched_idx = -1
        if message_id:
            for idx, m in enumerate(msgs):
                if m.get('id') == message_id or m.get('client_msg_id') == message_id:
                    matched_idx = idx
                    break
        if matched_idx >= 0:
            bot_id = str(msgs[matched_idx].get('bot_id') or bot_id or 'companion')
            src = explicit_src or normalize_node_source(msgs[matched_idx].get('source'))
        else:
            src = explicit_src or 'phone'

        await self._preempt_if_bot_busy(bot_id, 'user_reroll', source=src)

        msgs = self.store.state.get('messages', [])
        if message_id:
            matched_idx = next((i for i, m in enumerate(msgs) if m.get('id') == message_id or m.get('client_msg_id') == message_id), -1)
        room_indices = [
            i for i, m in enumerate(msgs)
            if m.get('bot_id', 'companion') == bot_id and normalize_node_source(m.get('source')) == src
        ]
        if not room_indices:
            room_indices = [i for i, m in enumerate(msgs) if m.get('bot_id', 'companion') == bot_id]
        if not room_indices:
            raise ValueError('当前房间暂无消息可重答')

        target_user_idx = -1
        if matched_idx >= 0:
            if msgs[matched_idx].get('role') == 'user':
                target_user_idx = matched_idx
            else:
                for i in reversed(room_indices):
                    if i < matched_idx and msgs[i].get('role') == 'user':
                        target_user_idx = i
                        break
        if target_user_idx < 0:
            for i in reversed(room_indices):
                if msgs[i].get('role') == 'user':
                    target_user_idx = i
                    break
        if target_user_idx < 0:
            raise ValueError('未找到上一条用户消息')

        u_msg = msgs[target_user_idx]
        src = normalize_node_source(u_msg.get('source') or src)
        removed_ids = self._truncate_room_messages_after(msgs, target_user_idx, bot_id, src)
        return await self._spawn_turn_for_existing_user_message(
            u_msg,
            bot_id=bot_id,
            src=src,
            removed_ids=removed_ids,
            is_edit=False,
        )
