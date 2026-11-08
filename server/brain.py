from __future__ import annotations
import asyncio, contextlib, json, os, re, secrets, signal, sys, time, contextvars
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
import httpx
from vault_manager import TZ, now_iso
from assistants import ensure_assistants, get_assistant, resolve_bot_for_source, resolve_bots_for_source
from harness import TOOLS
from storage import compact_trace_list, normalize_node_source, node_display_name
from core.llm_client import LLMClient
from core.memory_engine import MemoryEngine
from core.vision_engine import VisionEngine
from core.scheduler_engine import SchedulerEngine
from core.context_search import ContextSearchEngine
from core.notification_hub import NotificationHub
from core.phone_controller import PhoneControllerMixin
from core.desktop_controller import DesktopControllerMixin
from core.interconnect_router import InterconnectRouterMixin
from core.tool_handlers import ToolHandlersMixin


class Brain(PhoneControllerMixin, DesktopControllerMixin, InterconnectRouterMixin, ToolHandlersMixin):
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
        self.choices_var = contextvars.ContextVar('choices', default=None)
        self.choices_prompt_var = contextvars.ContextVar('choices_prompt', default='')
        self.pending_sticker_var = contextvars.ContextVar('pending_sticker', default='')
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
        self._abort_epoch_by_bot: dict[str, int] = {}
        self._current_user_text_by_bot: dict[str, str] = {}
        self._phone_blocked_by_user_by_bot: dict[str, bool] = {}
        self._last_scheduled_fire_by_bot: dict[str, float] = {}
        self._last_emergency_alarm_at: float = 0.0
        self._reply_box_by_sig: dict[str, list[dict]] = {}

        # Domain 1 Core Engines
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
            'custom_tool': self._tool_custom_tool,
            'phone_macro': self._tool_phone_macro,
            'cloud_desktop': self._tool_cloud_desktop,
            'notify_user': self._tool_notify_user,
            'emergency_alarm': self._tool_emergency_alarm,
            'phone_workspace': self._tool_phone_workspace,
            'dsh_workspace': self._tool_dsh_workspace,
            'message_phone_bot': self._tool_dsh_workspace,
        }

    def _clear_bot_stream(self, bot_id: str | None = None, source: str | None = None) -> None:
        norm_src = normalize_node_source(source) if source else None
        if bot_id:
            if norm_src:
                nb_key = f'{norm_src}:{bot_id}'
                self.streams_by_node_bot.pop(nb_key, None)
                self.executions_by_node_bot.pop(nb_key, None)
            else:
                for k in [k for k in self.streams_by_node_bot if k.endswith(f':{bot_id}')]:
                    self.streams_by_node_bot.pop(k, None)
                for k in [k for k in self.executions_by_node_bot if k.endswith(f':{bot_id}')]:
                    self.executions_by_node_bot.pop(k, None)
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

    async def stop_immediately(self, bot_id: str | None = None, reason: str = 'user_stop', commit_partial: bool = True, source: str | None = None) -> dict:
        """
        Instant 0ms Stop & User Preemption:
        1. Captures any partial stream text already generated (`streams_by_bot`) and commits it immediately
           as a real message (when `commit_partial=True`) so the user's chat window freezes cleanly on the generated content.
        2. Cancels ALL running AND queued asyncio task(s), cancels pending phone RPC futures,
           releases the phone virtual display ONLY if it was actually opened (`_vdisplay_currently_open` or `phone_pending`),
           and clears `streams_by_bot` / `executions_by_bot`.
        """
        req_src = normalize_node_source(source or self.source_context.get())
        target_bots = [bot_id] if bot_id else list(set(
            list(self.streams_by_bot.keys())
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
            cur_stream = self.streams_by_bot.get(b_id) or (self.live_stream if (self.live_stream or {}).get('bot_id') == b_id else None)
            cur_exec = self.executions_by_bot.get(b_id) or (self.execution if (self.execution or {}).get('bot_id') == b_id else None)
            active_src = normalize_node_source((cur_stream or cur_exec or {}).get('source') or req_src)
            if source and active_src != req_src:
                continue
            self._abort_epoch_by_bot[b_id] = self._abort_epoch_by_bot.get(b_id, 0) + 1
            if reason == 'user_stop' and active_src != 'pc':
                self._phone_blocked_by_user_by_bot[b_id] = True
            # MUST grab tasks BEFORE _clear_bot_stream(b_id) pops active_tasks_by_bot!
            t_active = self.active_tasks_by_bot.get(b_id)
            q_tasks = list(self.queued_tasks_by_bot.get(b_id) or ())
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
            if commit_partial and (partial_text or trace_list):
                try:
                    bot_obj = get_assistant(self.store, b_id)
                    b_name = bot_obj['name']
                except Exception:
                    b_name = b_id
                if reason == 'user_preempt':
                    final_txt = (partial_text + '\n\n（主人插话，已立即中断当前操作并优先响应新消息）') if partial_text else '（主人插话，已立即中断当前操作并优先响应新消息）'
                else:
                    final_txt = partial_text or '（已停止执行）'
                recent_bot_msgs = [m for m in self.store.state.get('messages', [])[-6:] if m.get('bot_id') == b_id and normalize_node_source(m.get('source')) == active_src]
                already_committed = bool(
                    turn_id and any(m.get('turn_id') == turn_id and m.get('role') == 'assistant' for m in recent_bot_msgs)
                )
                if not already_committed:
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
                        source=active_src,
                    )
                    self.stream_seq += 1
                    committed_msg['seq'] = self.stream_seq
                    committed_msg['turn_id'] = turn_id or committed_msg['id']
                    self.last_completed_stream = {
                        'seq': self.stream_seq,
                        'turn_id': committed_msg['turn_id'],
                        'bot_id': b_id,
                        'source': active_src,
                        'speaker_id': b_id,
                        'speaker_name': b_name,
                        'text': final_txt,
                        'final_message_id': committed_msg['id'],
                        'done': True,
                        'completed_at': time.time(),
                    }
                    self.store.save()
                    await self.publish({'type': 'message', 'source': active_src, 'message': committed_msg, 'seq': self.stream_seq})
            self._clear_bot_stream(b_id, source=active_src)
            if t_active and not t_active.done():
                t_active.cancel()
            for qt in q_tasks:
                if qt and not qt.done():
                    qt.cancel()
            self.queued_tasks_by_bot.pop(b_id, None)

        if cur_global_task and not cur_global_task.done() and not source:
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

    def is_any_bot_busy(self) -> bool:
        return (
            self.lock.locked()
            or any(lk.locked() for lk in self.bot_locks.values())
            or bool(self.executions_by_bot)
            or any(bool(q) for q in self.queued_tasks_by_bot.values())
        )

    def _get_dsh_sessions_list(self) -> list[dict]:
        sessions: list[dict] = []
        seen_ids: set[str] = set()
        candidates: list[list] = []
        st = self.dsh_bridge_info.get('status')
        if isinstance(st, dict) and isinstance(st.get('sessions'), list):
            candidates.append(st['sessions'])
        peers = self.dsh_bridge_info.get('peers')
        if isinstance(peers, dict):
            for p in peers.values():
                if isinstance(p, dict):
                    if isinstance(p.get('sessions'), list):
                        candidates.append(p['sessions'])
                    pst = p.get('status')
                    if isinstance(pst, dict) and isinstance(pst.get('sessions'), list):
                        candidates.append(pst['sessions'])
        for c_list in candidates:
            for s in c_list:
                if not isinstance(s, dict):
                    continue
                raw_id = str(s.get('session_id') or s.get('id') or s.get('short_id') or '').strip()
                stitle = str(s.get('title') or '').strip()
                dedup_key = raw_id or stitle
                if not dedup_key or dedup_key in seen_ids:
                    continue
                seen_ids.add(dedup_key)
                clean_short = str(s.get('short_id') or '').strip()
                if not clean_short and raw_id:
                    clean_short = (raw_id[8:] if raw_id.startswith('session-') else raw_id)[:8]
                norm_s = dict(s)
                norm_s['session_id'] = raw_id or clean_short
                norm_s['short_id'] = clean_short
                norm_s['title'] = stitle or '未命名会话'
                norm_s['updated_ago'] = str(s.get('updated_ago') or s.get('updatedAgo') or '').strip()
                norm_s['last_prompt'] = str(s.get('last_prompt') or s.get('lastPrompt') or '').strip()
                sessions.append(norm_s)
        return sessions

    def _should_use_pc_fs(self, fs_op: str, args: dict) -> bool:
        if not self.is_dsh_online():
            return False
        if fs_op == 'grep_search':
            raw_p = str(args.get('path') or args.get('file_path') or '.').strip()
        elif fs_op == 'glob_files':
            raw_p = str(args.get('pattern') or args.get('path') or '**/*').strip()
        elif fs_op == 'list_dir':
            raw_p = str(args.get('path') or '.').strip() or '.'
        else:
            raw_p = str(args.get('path') or args.get('file_path') or args.get('target') or '').strip()
        if not raw_p:
            return False
        norm_p = raw_p.replace('\\', '/').lstrip('./')
        # Explicit Windows drive or home path -> local PC filesystem
        if re.match(r'^[A-Za-z]:[/\\]', raw_p) or raw_p.startswith(('\\\\', '~')):
            return True
        # Known Cloud Agent Memory/Config files/dirs -> ALWAYS use Cloud Harness (never hijack to local PC folder)
        cloud_prefixes = ('memory_vault', 'memory_vault/', 'notes/', 'skills/', 'uploads/', '.config/')
        cloud_files = {'settings.json', 'bots.json', 'routines.json', 'memory.md', 'user.md', 'soul.md', 'system.md', 'heartbeat.md', 'index.md'}
        if norm_p.lower() in cloud_files or any(norm_p.lower() == pref.rstrip('/') or norm_p.lower().startswith(pref) for pref in cloud_prefixes):
            return False
        return True

    def _sync_topology_delta_if_changed(self) -> None:
        """
        Records a time-invariant multi-device & workspace session topology delta into `memory_deltas`
        ONLY when `(pc_on, phone_on, session_titles)` actually changes!
        Because `inject_memory_deltas` anchors each delta permanently to the tail message ID (`anchors[consumer_key]`),
        `messages[0]` (`system`) and all prior turns `messages[1..N-1]` stay 100% immutable -> 100% KV Prefix Cache Hit!
        """
        phone_on = bool(self.phone_online_checker()) if callable(self.phone_online_checker) else False
        pc_on = self.is_dsh_online()
        dsh_sessions = self._get_dsh_sessions_list()
        sess_items = [
            f"{str(s.get('title') or '未命名').strip()}[{str(s.get('short_id') or s.get('session_id') or '')[:8]}]"
            for s in dsh_sessions[:12]
        ]
        topo_sig = json.dumps({'pc': pc_on, 'phone': phone_on, 'sessions': sess_items}, ensure_ascii=False, sort_keys=True)
        last_sig = str(self.store.state.get('last_topology_delta_sig') or '')
        if topo_sig == last_sig:
            return
        self.store.state['last_topology_delta_sig'] = topo_sig
        sess_str = '、'.join(sess_items) if sess_items else '（可调 dsh_workspace(op="list_sessions") 查看）'
        summary = (
            f"当前端在线状态：电脑端 `Windows一`={'🟢在线' if pc_on else '⚪离线'}，"
            f"手机端 `安卓一`={'🟢在线' if phone_on else '⚪后台待命'}（两端常驻Bot：白珂、研究助手、杠精、鲸鱼娘）；"
            f"电脑端底部默认工作区会话：{sess_str}。"
        )
        self.memory_engine.record_delta(
            kind='topology_sync',
            summary=summary,
            path='multi_device_topology',
            source_node='system',
        )

    def persona(self, bot_id: str | None = None):
        target = bot_id or self.bot_context.get()
        bot = get_assistant(self.store, target)
        roster = [
            f"- id={b['id']}, 名字={b['name']}, 模型={b.get('model') or '默认'}, 简介={b.get('description','')}"
            for b in self.store.state.get('assistants', [])
        ]
        static_comm_guide = (
            '\n\n【多端通信与工作区操控铁律（100% 静态头部，严禁为查在线状态去调 run_bash / glob_files / read_file 找配置文件！）】：\n'
            '- 常驻节点：`Windows一`（电脑端 DeepSeek Harness）、`安卓一`（手机端）；所有端共用常驻 Bot：白珂 (`companion`)、研究助手 (`research`)、杠精 (`contrarian`)、鲸鱼娘 (`whalechan`)。\n'
            '- 各端实时在线状态与电脑端底部「默认工作区」会话列表，请直接查看对话尾部的 `[System Memory Delta / 最新记忆与状态同步]`（或调 1 次 `dsh_workspace(op="status")`）。\n'
            '- 跨端/同端发消息只需 1 步调用 `send_message`：\n'
            '  · 发给同端其他 Bot：`send_message(to_bot_id="研究助手", text="...")`\n'
            '  · 发给手机端某 Bot：`send_message(to_bot_id="安卓一:白珂", text="...")` 或 `send_message(to_bot_id="安卓一:研究助手", text="...")`\n'
            '  · 发给电脑端某 Bot：`send_message(to_bot_id="Windows一:白珂", text="...")`\n'
            '  · 发给电脑端底部工作区会话：`send_message(to_bot_id="会话:<会话标题或ID>", text="...")`'
        )
        return (
            bot['instructions']
            + static_comm_guide
            + '\n\n当前团队 Bot 列表（可用 send_message 给同端或跨端任意 Bot 发消息，或用 manage_bot 创建/修改 Bot）：\n'
            + '\n'.join(roster)
        )

    @staticmethod
    def _is_placeholder_text(t: str) -> bool:
        return (
            (not t)
            or t in ('正在思考…', '正在思考与执行任务…', '正在整理回复…', '正在重新生成…')
            or t.startswith('正在调用 ')
            or t.startswith('正在并行执行 ')
            or t.startswith('⏰ 正在执行')
            or t.startswith('⏳ ')
            or t.startswith('📦 ')
        )

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
        cur_turn_check = self.turn_id_var.get()
        if cur_turn_check and cur_turn_check in self._stopped_turn_ids:
            raise asyncio.CancelledError()
        sp_id = speaker_id or self.bot_context.get()
        try:
            sp_name = speaker_name or get_assistant(self.store, sp_id)['name']
        except Exception:
            sp_name = sp_id
        cur_src = normalize_node_source(self.source_context.get())
        nb_key = f'{cur_src}:{bot_id}'
        prev = self.streams_by_node_bot.get(nb_key) or self.streams_by_bot.get(bot_id) or self.live_stream or {}
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
        cur_exec = self.executions_by_node_bot.get(nb_key) or self.executions_by_bot.get(bot_id) or self.execution
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
        effective_text, tag_choices, tag_thought, tag_sticker = self._extract_all_tags(effective_text)
        if tag_choices:
            self.choices_var.set(tag_choices)
        if tag_thought:
            raw_thought = (raw_thought + '\n' + tag_thought).strip() if raw_thought and tag_thought not in raw_thought else tag_thought
        effective_thought = raw_thought
        delta_text = ''
        if same_stream and prev_text and not self._is_placeholder_text(prev_text) and effective_text.startswith(prev_text):
            delta_text = effective_text[len(prev_text):]
        live_choices = list(self.choices_var.get() or [])
        if not live_choices and same_stream and isinstance(prev.get('choices'), list):
            live_choices = list(prev['choices'])
        cur_src = normalize_node_source(self.source_context.get())
        self.stream_seq += 1
        self.live_stream = {
            'seq': self.stream_seq,
            'turn_id': turn_id,
            'bot_id': bot_id,
            'source': cur_src,
            'speaker_id': sp_id,
            'speaker_name': sp_name,
            'speaker_node': cur_src,
            'text': effective_text,
            'delta': delta_text,
            'thought': effective_thought,
            'tool': effective_tool,
            'trace': trace_tail,
            'sub_agents': sub_agents,
            'todos': todos,
            'choices': live_choices,
            'context_stats': self.memory_engine.get_context_stats(bot_id),
            'updated_at': time.time(),
        }
        self.streams_by_node_bot[nb_key] = self.live_stream
        self.streams_by_bot[bot_id] = self.live_stream
        await self.publish({'type': 'stream', 'source': cur_src, 'stream': self.live_stream})
        parent_room_info = self.parent_stream_room_var.get()
        if isinstance(parent_room_info, tuple) and len(parent_room_info) == 3:
            p_src, p_room_id, p_speaker_label = parent_room_info
            if (p_src, p_room_id) != (cur_src, bot_id):
                mirrored_stream = {
                    **self.live_stream,
                    'bot_id': p_room_id,
                    'source': p_src,
                    'speaker_id': sp_id,
                    'speaker_name': p_speaker_label or sp_name,
                    'speaker_node': cur_src,
                }
                self.streams_by_node_bot[f'{p_src}:{p_room_id}'] = mirrored_stream
                self.streams_by_bot[p_room_id] = mirrored_stream
                await self.publish({'type': 'stream', 'source': p_src, 'stream': mirrored_stream})

    async def complete(self, messages, tools=None, big=False, tool_choice=None):
        eff_tc = tool_choice or getattr(self, '_next_tool_choice', None)
        self._next_tool_choice = None
        bot_id = self.bot_context.get()
        cur_src = normalize_node_source(self.source_context.get())
        try:
            bot = get_assistant(self.store, bot_id, source=cur_src)
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
            tool_choice=eff_tc,
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


    @staticmethod
    def _sanitize_choices_list(raw_choices: list) -> list[dict]:
        cleaned: list[dict] = []
        for c in raw_choices[:4]:
            if isinstance(c, dict):
                lbl = str(c.get('label') or c.get('title') or c.get('text') or c.get('value') or '').strip()
                val = str(c.get('value') or c.get('prompt') or c.get('text') or lbl).strip()
            else:
                s = str(c).strip()
                s = re.sub(r'^(?:[-*•]|\d+[\.、\)]|[①②③④⑤⑥])\s*', '', s).strip()
                if '|' in s:
                    parts = [p.strip() for p in s.split('|', 1)]
                    lbl, val = parts[0], (parts[1] or parts[0])
                else:
                    lbl, val = s, s
            lbl = re.sub(r'\*\*([^*]+)\*\*', r'\1', lbl).strip()[:36]
            val = re.sub(r'\*\*([^*]+)\*\*', r'\1', val).strip()[:160]
            if not lbl or len(lbl) < 2:
                continue
            cleaned.append({'label': lbl, 'value': val or lbl})
        return cleaned

    @classmethod
    def _strip_and_parse_choices(cls, text: str) -> tuple[str, list[dict]]:
        if not text:
            return '', []
        extracted: list[dict] = []
        # Also rescue any leaked DSML `present_choices` blocks!
        if 'DSML' in text or '<function_calls>' in text:
            clean_dsml, dsml_calls = LLMClient.parse_and_strip_dsml_calls(text)
            for dc in dsml_calls:
                fn = dc.get('function') or {}
                if fn.get('name') == 'present_choices':
                    try:
                        c_args = json.loads(fn.get('arguments') or '{}')
                        raw_c = c_args.get('choices') or c_args.get('options') or []
                        if isinstance(raw_c, list):
                            extracted.extend(cls._sanitize_choices_list(raw_c))
                    except Exception:
                        pass
            text = clean_dsml
        def _repl(m):
            block = m.group(1) or ''
            # Support <option send="..." value="...">Label</option> as well as "Label | Value" lines
            opt_matches = re.findall(r"""<option\s+(?:send|value)=["']([^"']+)["'][^>]*>([^<]+)</option>""", block, flags=re.IGNORECASE)
            if opt_matches:
                for val, lbl in opt_matches:
                    extracted.extend(cls._sanitize_choices_list([{'label': lbl.strip(), 'value': val.strip()}]))
            else:
                raw_lines = [ln.strip() for ln in block.splitlines() if ln.strip() and not ln.strip().startswith('<')]
                extracted.extend(cls._sanitize_choices_list(raw_lines))
            return ''
        cleaned_text = re.sub(r'<choices>([\s\S]*?)</choices>', _repl, text, flags=re.IGNORECASE)
        cleaned_text = re.sub(r'<choices>[\s\S]*$', '', cleaned_text, flags=re.IGNORECASE).rstrip()
        return cleaned_text, extracted[:4]

    def _prepare_chat_screenshot(
        self,
        raw_or_path: bytes | Path | str,
        crop_box: list | None = None,
        prefix: str = 'shot',
    ) -> str:
        """
        Save a clean (unmarked) screenshot or cropped QR/region into `workspace/uploads/<unique>.jpg`
        so the Android client renders it fresh in the chat bubble without hitting `stickerCache`
        or obscuring QR codes with ruler lines.
        """
        try:
            import io
            from PIL import Image, ImageOps
            uploads_dir = self.harness.root / 'uploads'
            uploads_dir.mkdir(parents=True, exist_ok=True)
            # Prune old chat screenshots if > 45 files
            existing_shots = sorted(uploads_dir.glob('shot-*.jpg'), key=lambda p: p.stat().st_mtime)
            for old_p in existing_shots[:-40]:
                try:
                    old_p.unlink()
                except Exception:
                    pass

            if isinstance(raw_or_path, (str, Path)):
                p_in = Path(raw_or_path) if Path(raw_or_path).is_absolute() else (self.harness.root / str(raw_or_path).removeprefix('workspace/').lstrip('/'))
                if p_in.name == 'desktop-latest.jpg' and (self.harness.root / 'desktop-clean.jpg').exists():
                    p_in = self.harness.root / 'desktop-clean.jpg'
                elif p_in.name == 'phone-latest.jpg' and (self.harness.root / 'phone-clean.jpg').exists():
                    p_in = self.harness.root / 'phone-clean.jpg'
                raw_bytes = p_in.read_bytes()
            else:
                raw_bytes = raw_or_path

            im = Image.open(io.BytesIO(raw_bytes)).convert('RGB')
            iw, ih = im.size
            if isinstance(crop_box, list) and len(crop_box) == 4:
                try:
                    ymin, xmin, ymax, xmax = [float(v) for v in crop_box]
                    if 0 <= ymin < ymax <= 1000 and 0 <= xmin < xmax <= 1000:
                        left = max(0, int(round(xmin * iw / 1000.0)) - 12)
                        top = max(0, int(round(ymin * ih / 1000.0)) - 12)
                        right = min(iw, int(round(xmax * iw / 1000.0)) + 12)
                        bottom = min(ih, int(round(ymax * ih / 1000.0)) + 12)
                        if right - left >= 32 and bottom - top >= 32:
                            im = im.crop((left, top, right, bottom))
                            # Add clean white border around cropped QR codes so scanners read them effortlessly
                            if max(im.size) < 420:
                                scale = 480.0 / max(1, max(im.size))
                                im = im.resize((int(im.size[0] * scale), int(im.size[1] * scale)), Image.Resampling.NEAREST)
                            im = ImageOps.expand(im, border=16, fill=(255, 255, 255))
                except Exception:
                    pass

            fname = f'{prefix}-{int(time.time())}-{secrets.token_hex(3)}.jpg'
            out_path = uploads_dir / fname
            im.save(out_path, format='JPEG', quality=90)
            media_url = f'/workspace-media/uploads/{fname}'
            self.pending_sticker_var.set(media_url)
            return media_url
        except Exception:
            return ''

    def _extract_all_tags(self, text: str) -> tuple[str, list[dict], str, str]:
        if not text:
            return '', [], '', ''
        cleaned, choices = self._strip_and_parse_choices(text)
        thoughts: list[str] = []
        def _think_repl(m):
            t_body = (m.group(1) or '').strip()
            if t_body:
                thoughts.append(t_body)
            return ''
        cleaned = re.sub(r'<(?:think|thought)>([\s\S]*?)</(?:think|thought)>', _think_repl, cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r'<(?:think|thought)>[\s\S]*$', '', cleaned, flags=re.IGNORECASE)
        sticker_url = ''
        def _resolve_media_or_clean_shot(raw_code: str) -> str:
            c_clean = raw_code.removeprefix('/workspace-media/').removeprefix('workspace/').lstrip('/')
            if c_clean in ('desktop-latest.jpg', 'desktop-clean.jpg', 'phone-latest.jpg', 'phone-clean.jpg'):
                fresh = self._prepare_chat_screenshot(c_clean)
                if fresh:
                    return fresh
            if raw_code.startswith(('http://', 'https://', '/stickers/', '/workspace-media/')):
                return raw_code
            return '/workspace-media/' + c_clean

        def _sticker_repl(m):
            nonlocal sticker_url
            code = (m.group(1) or m.group(2) or m.group(3) or '').strip()
            resolved = self.harness.extension_registry.resolve_sticker(code)
            if resolved:
                sticker_url = resolved
            elif code.startswith(('http://', 'https://', '/stickers/', '/workspace-media/')) or re.search(r'\.(?:png|jpg|jpeg|webp|gif|bmp)$', code, flags=re.IGNORECASE):
                sticker_url = _resolve_media_or_clean_shot(code)
            return ''
        cleaned = re.sub(
            r"""(?:<sticker:([a-zA-Z0-9_\u4e00-\u9fa5./-]+)\s*/?>|<sticker\s+(?:name|id)=["\']?([^"\'>\s]+)["\']?\s*/?>|\[sticker:([^\]]+)\])""",
            _sticker_repl,
            cleaned,
            flags=re.IGNORECASE,
        )
        def _img_tag_repl(m):
            nonlocal sticker_url
            raw_u = (m.group(1) or m.group(2) or '').strip()
            if not raw_u:
                return ''
            resolved = self.harness.extension_registry.resolve_sticker(raw_u)
            if resolved:
                sticker_url = resolved
            else:
                sticker_url = _resolve_media_or_clean_shot(raw_u)
            return ''
        cleaned = re.sub(
            r'(?:<image:([^\s>]+)\s*/?>|!\[[^\]]*\]\(([^)\s]+)\))',
            _img_tag_repl,
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(r'<(?:sticker|image)[^>]*$', '', cleaned, flags=re.IGNORECASE).strip()
        cleaned, _ = LLMClient.parse_and_strip_dsml_calls(cleaned)
        cleaned = re.sub(r'\n*\[系统(?:真实记录|最后修正|修正|注):[^\]]*\]', '', cleaned).strip()
        return cleaned, choices, '\n'.join(thoughts).strip(), sticker_url

    @staticmethod
    def _strip_fake_action_stage_directions(text: str) -> str:
        """Strip parenthetical stage directions like '（正在打开 QQ 并搜索…）' or '（这就动手）' that pretend to execute tools."""
        if not text:
            return ''
        cleaned = re.sub(
            r'[（(\[【][^）)\]】\n]{0,36}(?:正在(?:打开|搜索|查看|点击|读取|操作|执行|进入|刷新|检查|翻|查|找)|这就(?:动手|去|打开|开始|查|办)|马上(?:动手|打开|去))[^）)\]】\n]{0,36}[）)\]】]',
            '',
            text,
        )
        return re.sub(r'\n{3,}', '\n\n', cleaned).strip()

    @classmethod
    def _is_unfulfilled_action_promise(cls, text: str, user_text: str = '', trace: list | None = None) -> bool:
        """
        Detect when the LLM outputs an action promise / stage direction (e.g. '（正在打开 QQ 并搜索…）',
        '余先打开 QQ…', '（这就动手）', '现在就在做：打开…') with zero tool_calls in the current step.
        """
        s = (text or '').strip()
        if not s:
            return False
        # 1. Explicit parenthetical stage direction pretending an action is happening right now
        if re.search(
            r'[（(\[【][^）)\]】\n]{0,36}(?:正在(?:打开|搜索|查看|点击|读取|操作|执行|进入|刷新|检查|翻|查|找)|这就(?:动手|去|打开|开始|查|办)|马上(?:动手|打开|去))[^）)\]】\n]{0,36}[）)\]】]',
            s,
        ):
            return True
        # 2. At step 0 (not trace), model promises it is about to open/search/click right now and stops without calling any tool
        if not trace:
            tail = s[-160:]
            if re.search(
                r'(?:(?:余|我)?\s*(?:先|这就|现在|马上|立刻)\s*(?:真的)?(?:去|动手)?\s*(?:打开|点开|启动|搜索|翻一下|查一下|点进去|进去读|在列表里找|找找)|现在(?:就)?在做\s*[:：]|现在第一步\s*[:：]|余这就开)',
                tail,
            ) and '？' not in tail[-25:] and '?' not in tail[-25:]:
                return True
        return False

    @staticmethod
    def _is_unfulfilled_write_claim(text: str, trace: list | None = None) -> bool:
        """
        Detect when the LLM claims in its reply that it has written/saved/appended content to a file
        (e.g. '已经记进 notes/xxx.md', '已写入 memory_vault/...') when zero write_file/edit_file calls occurred in `trace`.
        """
        s = (text or '').strip()
        if not s:
            return False
        tr = trace or []
        has_file_write = any(
            isinstance(t, dict) and t.get('tool') in ('write_file', 'edit_file') and not (isinstance(t.get('result'), dict) and t['result'].get('error'))
            for t in tr
        )
        if has_file_write:
            return False
        return bool(
            re.search(
                r'(?:已经|已|刚刚|单独)\s*(?:记进|写入|写进|存进|保存到|追加到|落到|落盘到|更新到)[^。\n]{0,60}(?:notes/|profile/|tasks/|timeline/|memory_vault/|\.md\b|画像里)',
                s,
                flags=re.IGNORECASE,
            )
        )

    @staticmethod
    def _matches_requested_app_package(requested_app: str, current_pkg: str) -> bool:
        """Return True if `current_pkg` is already the app requested by `open(target=requested_app)`."""
        req = (requested_app or '').strip().lower()
        pkg = (current_pkg or '').strip().lower()
        if not req or not pkg:
            return False
        if req == pkg or (len(req) >= 4 and req in pkg):
            return True
        alias_map = {
            'qq': ('com.tencent.mobileqq', 'com.tencent.tim'),
            'tim': ('com.tencent.tim', 'com.tencent.mobileqq'),
            '微信': ('com.tencent.mm',),
            'wechat': ('com.tencent.mm',),
            '学习通': ('com.chaoxing.mobile',),
            '超星': ('com.chaoxing.mobile',),
            'chaoxing': ('com.chaoxing.mobile',),
            '支付宝': ('com.eg.android.alipaygphone',),
            'alipay': ('com.eg.android.alipaygphone',),
            '淘宝': ('com.taobao.taobao',),
            '京东': ('com.jingdong.app.mall',),
            '知乎': ('com.zhihu.android',),
            '小红书': ('com.xingin.xhs',),
            'bilibili': ('tv.danmaku.bili',),
            'b站': ('tv.danmaku.bili',),
            '哔哩哔哩': ('tv.danmaku.bili',),
            '抖音': ('com.ss.android.ugc.aweme',),
            '浏览器': ('com.android.chrome', 'mark.via', 'com.UCMobile', 'com.tencent.mtt'),
            'chrome': ('com.android.chrome',),
        }
        for key, pkgs in alias_map.items():
            if key in req and any(p.lower() in pkg for p in pkgs):
                return True
        return False

    def _build_tool_call_sig(self, name: str, args: dict) -> tuple[str, bool]:
        """
        Build a signature for loop detection (`recent_call_sigs`).
        For stateful scrolling/paging operations (`scroll`, `swipe`, `key`), include the current screen's
        `treeHash` and ignore dummy `amount` variations so:
        1) Genuine scrolling through a long chat/list (where `treeHash` changes each swipe) is NEVER falsely blocked!
        2) Hitting the actual top/bottom boundary (where `treeHash` stops changing across 3 swipes) is detected even if `amount` is varied.
        """
        op_val = str(args.get('op') or args.get('action') or '').strip().lower()
        is_scroll_op = (
            name in ('phone_workspace', 'cloud_desktop', 'dsh_workspace')
            and op_val in ('scroll', 'swipe', 'scroll_to', 'key', 'press', 'press_key')
        )
        if is_scroll_op:
            norm_args = {k: v for k, v in args.items() if k not in ('amount', 'pages', 'duration_ms')}
            state_fp = self._last_phone_tree_hash if name == 'phone_workspace' else ''
            return f"{name}:scroll:{json.dumps(norm_args, sort_keys=True, ensure_ascii=False)}:fp={state_fp}", True
        return f"{name}:{json.dumps(args, sort_keys=True, ensure_ascii=False)}", False

    @classmethod
    def _get_llm_history_text(cls, msg_item: dict) -> str:
        """
        Return the clean post-tool assistant reply (`llm_text`) for LLM context construction (`recent`),
        excluding pre-tool step-by-step intermediate narrations so the LLM never imitates multi-step
        monologues in a single completion without calling tools.
        """
        raw = str(msg_item.get('llm_text') or msg_item.get('text') or '').strip()
        if not raw:
            return ''
        cleaned, _ = LLMClient.parse_and_strip_dsml_calls(raw)
        cleaned = re.sub(r'\n*\[系统(?:真实记录|最后修正|修正|注):[^\]]*\]', '', cleaned).strip()
        # Strip legacy plain-text @Node/Bot prefixes so the LLM never imitates writing "@Windows一:白珂 ..." without calling send_message
        cleaned = re.sub(
            r'^@(?:Windows一|Windows1|电脑端|电脑|安卓一|安卓二|手机端|研究助手|杠精|鲸鱼娘|白珂)(?:[:：·/\s(（][^\n:：)）]{0,24}[)）]?[:：,\s，]*)?',
            '',
            cleaned,
            flags=re.IGNORECASE,
        ).strip()
        cleaned = cls._strip_fake_action_stage_directions(cleaned)
        if (
            not msg_item.get('llm_text')
            and msg_item.get('role') == 'assistant'
            and (msg_item.get('trace') or int(msg_item.get('api_calls') or 0) > 1)
            and '\n\n' in cleaned
        ):
            blocks = [b.strip() for b in cleaned.split('\n\n') if b.strip()]
            if len(blocks) > 2:
                cleaned = '\n\n'.join(blocks[-2:])
        return cleaned

    @staticmethod
    def _extract_plain_at_mention_call(text: str, user_text: str = '', cur_src: str = 'phone') -> tuple[str, str] | None:
        """
        1. If the LLM accidentally wrote `@Windows一:白珂 ...` or `@电脑端 ...` in plain text without
           generating a tool_call, extract `(to_bot_id, message_body)` so we can auto-dispatch `send_message`.
        2. If the user explicitly asked to send a message / command to the other device (e.g. "给电脑发个...",
           "让电脑控制QQ...") AND the LLM halluncinated "余给电脑端发了指令..." with zero tool_calls,
           automatically synthesize the `send_message` call so it actually executes!
        """
        s = (text or '').strip()
        if not s:
            return None
        m = re.search(
            r'(?:^|\n)\s*@((?:Windows一|Windows1|Windows二|电脑端|桌面端|电脑|安卓一|安卓1|安卓二|安卓2|手机端|平板端)(?:\s*[:：·/]\s*(?:白珂|研究助手|杠精|鲸鱼娘|companion|research|contrarian|whalechan))?(?:\s*[（(][^）)\n]{1,20}[）)])?|研究助手|杠精|鲸鱼娘|research|contrarian|whalechan)\s*[:：,，\s]\s*([\s\S]+)$',
            s,
            flags=re.IGNORECASE,
        )
        if m:
            raw_target = re.sub(r'\s*[（(][^）)]*[）)]', '', m.group(1)).strip()
            raw_target = re.sub(r'\s*([:：·/])\s*', ':', raw_target)
            body = m.group(2).strip()
            if raw_target and len(body) >= 2:
                return raw_target, body

        u = (user_text or '').strip()
        if u:
            # Check if user explicitly asked to message/command another node, and LLM claimed it sent it without calling send_message
            claimed_sent = any(k in s for k in ('给电脑端发了', '给电脑发了', '向电脑端发', '已发给电脑', '发给电脑端', '给手机端发了', '给安卓一发了', '给Windows一发了'))
            m_u_pc = re.search(r'(?:给|向|对|让|叫|找)\s*(?:电脑端?|桌面端?|Windows\s*[一1]?|PC)\s*(?:上的)?\s*(白珂|研究助手|杠精|鲸鱼娘)?\s*(?:发|说|转达|带句话|递句话|控制)', u, flags=re.IGNORECASE)
            m_u_ph = re.search(r'(?:给|向|对|让|叫|找)\s*(?:手机端?|安卓\s*[一1]?)\s*(?:上的)?\s*(白珂|研究助手|杠精|鲸鱼娘)?\s*(?:发|说|转达|带句话|递句话|控制)', u, flags=re.IGNORECASE)
            if (m_u_pc or claimed_sent) and cur_src != 'pc':
                bot_part = (m_u_pc.group(1) if m_u_pc and m_u_pc.group(1) else '白珂')
                return f'Windows一:{bot_part}', f'主人通过手机端转达指令：{u}'
            if m_u_ph and cur_src == 'pc':
                bot_part = (m_u_ph.group(1) if m_u_ph and m_u_ph.group(1) else '白珂')
                return f'安卓一:{bot_part}', f'主人通过电脑端转达指令：{u}'
        return None

    @classmethod
    def _format_message_trace_note(cls, msg_item: dict) -> str:
        tr = msg_item.get('trace') or []
        api_calls = int(msg_item.get('api_calls') or 0)
        stopped = bool(msg_item.get('stopped_early'))
        if not tr and api_calls <= 1:
            if cls._is_unfulfilled_action_promise(str(msg_item.get('text') or ''), trace=[]):
                return '\n[系统真实记录: ⚠️ 该条消息未调用任何工具（错误示范：严禁只输出“正在打开/先打开/这就动手”等文字而不发起 tool_calls）]'
            return ''
        tool_names = [str(t.get('tool') or '') for t in tr if isinstance(t, dict) and t.get('tool')]
        if not api_calls:
            api_calls = 2 if len(tool_names) <= 2 else max(2, (len(tool_names) + 1) // 2 + 1)
        stop_tag = '（中途被主人打断停止，尚未完成）' if stopped else ''
        if tool_names:
            return f"\n[系统真实记录: 该条消息共发起 {api_calls} 次 API 请求，执行了 {len(tool_names)} 个工具 ({', '.join(tool_names[:10])}){stop_tag}]"
        return f"\n[系统真实记录: 该条消息共发起 {api_calls} 次 API 请求，未调用工具{stop_tag}]"

    @classmethod
    def _extract_implicit_choices(cls, text: str) -> list[dict]:
        """
        Only extract choices from explicit `<choices>...</choices>` blocks!
        Never scrape normal `1. 2. 3.` numbered lists from prose reports.
        """
        _, parsed = cls._strip_and_parse_choices(text)
        return parsed


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
        if tag_sticker and 'sticker' not in extra:
            extra['sticker'] = tag_sticker
        elif self.pending_sticker_var.get() and 'sticker' not in extra:
            extra['sticker'] = self.pending_sticker_var.get()
        self.pending_sticker_var.set('')
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
            'seq': self.stream_seq,
            'turn_id': turn_id,
            'bot_id': bot_id,
            'source': cur_src,
            'speaker_id': extra.get('from_bot_id') or bot_id,
            'speaker_name': extra.get('from_bot_name') or '',
            'text': m['text'],
            'final_message_id': m['id'],
            'done': True,
            'completed_at': time.time(),
        }
        await self.publish({'type': 'message', 'source': cur_src, 'message': m, 'seq': self.stream_seq})
        if track and extra['notify'] and cur_src != 'pc':
            await self.escalation.track(m)
        return m


    async def read_image_file_with_vision(self, file_path: Path, prompt: str = '') -> dict:
        return VisionEngine.read_image_file_with_vision(file_path, prompt)

    async def _enrich_user_message_with_vision(self, raw_text: str, current_model: str, bot_id: str, bot_name: str):
        return VisionEngine.build_user_multimodal_content(raw_text, self.harness.path)

    AUTO_COMPACT_TOKENS = 200_000
    KEEP_RECENT_TOKENS = 64_000

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        return MemoryEngine.estimate_tokens(text)

    def _estimate_messages_tokens(self, messages: list[dict]) -> int:
        return self.memory_engine.estimate_messages_tokens(messages)

    @staticmethod
    def _prune_tool_loop_messages(messages: list[dict]):
        MemoryEngine.prune_tool_loop_messages(messages)

    def context(self):
        return self.memory_engine.build_prompt_context()

    def _build_ephemeral_pc_peer_brief(self) -> str:
        """Disabled by design: zero cross-device prompt injection during normal turns."""
        return ''

    async def _auto_compact_if_needed(self, bot_id: str, force: bool = False, source: str | None = None) -> dict | None:
        """Silent background 200K compaction via MemoryEngine (never pollutes live_stream, emits compact_summary card to chat)."""
        src = normalize_node_source(source or self.source_context.get())
        sys_tokens = self._estimate_tokens(self.harness.system_instructions(self.context())) + 800
        res = await self.memory_engine.auto_compact_if_needed(
            bot_id=bot_id,
            sys_tokens=sys_tokens,
            silent_complete_fn=lambda msgs: self.llm.complete_silent(msgs, tools=None, big=False, bot_id=bot_id),
            force=force,
            source=src,
        )
        if res and isinstance(res.get('marker'), dict):
            # Compaction already resets the conversation history prefix, so this is the ideal moment to refresh
            # the cached vault snapshot and pinned block for the new session!
            self._cached_vault_context = self.context()[:3600]
            self._cached_pinned_block = self.memory_engine.build_static_pinned_block()
            self.store.state['cached_vault_context'] = self._cached_vault_context
            self.store.state['cached_pinned_block'] = self.memory_engine.build_static_pinned_block()
            marker = res['marker']
            marker['source'] = src
            self.stream_seq += 1
            marker['seq'] = self.stream_seq
            self.store.save()
            await self.publish({'type': 'message', 'source': src, 'message': marker, 'seq': self.stream_seq})
        return res

    async def curiosity(self, manual=False, bot_id=None, source: str = 'phone'):
        if not manual and (self.store.state['snooze_until'] > time.time() or not self.store.config.enabled or self.is_any_bot_busy()):
            return {'suppressed': True}
        src = normalize_node_source(source)
        target_id = bot_id or self.store.state['primary_bot_id']
        async with self.lock, self._get_bot_lock(target_id, src):
            if not manual:
                if self.executions_by_bot or any(bool(q) for q in self.queued_tasks_by_bot.values()):
                    return {'suppressed': True, 'reason': 'bot_busy'}
                bot_for_src = get_assistant(self.store, target_id, source=src)
                if not bot_for_src.get('curiosity_enabled', False):
                    return {'suppressed': True, 'reason': 'curiosity_disabled_on_node'}
                bot_history = [m for m in self.store.state['messages'] if m.get('bot_id', 'companion') == target_id and normalize_node_source(m.get('source')) == src]
                if bot_history and bot_history[-1].get('role') == 'assistant' and bot_history[-1].get('kind') in ('curiosity', 'routine', 'deadline'):
                    # Unanswered backoff: only back off when the last message in the room was already an unanswered proactive nudge!
                    self.store.state['last_curiosity'] = time.time()
                    self.store.save()
                    return {'suppressed': True, 'reason': 'unanswered_backoff'}
            marker = self.bot_context.set(target_id)
            room_marker = self.room_context.set(target_id)
            src_marker = self.source_context.set(src)
            cur_turn_token = self.turn_id_var.set('turn-' + secrets.token_hex(6))
            cur_prefix_token = self.stream_prefix_var.set('')
            cur_choices_token = self.choices_var.set(None)
            cur_choices_prompt_token = self.choices_prompt_var.set('')
            self.activity = 'curiosity'
            try:
                bot = get_assistant(self.store, target_id, source=src)
                api_key = self.store.config.effective_api_key(big=False) or self.store.config.effective_api_key(big=True)
                model = self.store.config.effective_model(bot_model=bot.get('model',''), big=False) or self.store.config.effective_model(bot_model=bot.get('model',''), big=True)
                if not manual and api_key and api_key != 'fixture' and (time.time() - float(self.store.state.get('last_user', 0) or 0) < 300):
                    return {'suppressed': True, 'reason': 'user_recently_active'}
                trace = []
                if api_key and model:
                    recent_items = [m for m in self.store.state['messages'] if m.get('bot_id','companion') == target_id and normalize_node_source(m.get('source')) == src][-8:]
                    recent_digest = [
                        {
                            'role': m.get('role', 'user'),
                            'speaker': '主人' if m.get('role') == 'user' else (m.get('from_bot_name') or bot['name']),
                            'kind': m.get('kind', 'chat'),
                            'text': (self._get_llm_history_text(m) if m.get('role') == 'assistant' else m.get('text', ''))[:240],
                        }
                        for m in recent_items
                    ]
                    allowed = set(bot.get('tools') or [t['function']['name'] for t in TOOLS]) & {
                        'read_file', 'write_file', 'edit_file', 'memory_search', 'web_search', 'web_fetch', 'schedule_task', 'present_choices', 'phone_workspace'
                    }
                    available_tools = [t for t in TOOLS if t['function']['name'] in allowed]
                    # Piggyback notification convergence right before curiosity (skip network wait on unit-test fixture keys)
                    if api_key != 'fixture':
                        await self._poll_and_converge_notifications(timeout=1.2)
                    notif_digest_md = self.notification_hub.build_curiosity_digest(mark_seen=True)
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
                        '请主动向主人发起一次简短、真诚、自然的搭话（2~4句话）：\n'
                        '- 若「顺手捎来的手机通知栏积攒摘要」中有新的重要群消息/联系人消息/学校或邮件提醒，可自然地顺手帮主人提一句或记入待办；若发现有漏网的 VPN/加速器/常驻垃圾通知，请顺手调用 `phone_workspace(op="notifications", action="add_filter", filter_app="...", filter_keyword="...", reason="...")` 自动将其屏蔽（禁止在主动搭话中打开或操控任何手机 App 界面）！\n'
                        '- 重要原则：不要每次只问工作或任务！若无紧急新通知，请优先从「尚未了解的个人印象维度」（如主人的出生地/家乡、成长经历、当前身份与理想、家庭氛围与重要的人/宠物、生活饮食喜好、性格与相处偏好）中挑一个还没问过的小话题，像亲近的伙伴一样自然地问一句，逐渐补全对主人的个人印象；或者结合当前时间/课表/情报分享一件具体有用的事。\n'
                        '- 严禁生硬查户口，每次只轻轻问一个具体而温暖的小问题，不要顺着最近报错复读。\n'
                        '- 务必调用 `present_choices(choices=[...])` 提供 3~4 个贴心选项，方便主人一键点选回答（主人也可以自己打字）。\n'
                    )
                    user_trigger = (
                        '当前时间：' + self._fmt_bj(time.time()) + ' (' + now_iso() + ')\n'
                        '【顺手捎来的手机通知栏积攒摘要（哪怕主人已划走也已保留）】：\n' + notif_digest_md + '\n\n'
                        '主人立体个人印象档案 (memory_vault/profile/personal_impression.md)：\n' + raw_imp[:2200] + '\n\n'
                        '尚未了解的个人印象维度：' + missing_hint + '\n\n'
                        '课表：\n' + (schedule_md or '（未记录）') + '\n\n'
                        '待确认截止事项：\n' + deadlines + '\n\n'
                        '常驻例行：\n' + routines_md + '\n\n'
                        '待办机会池：\n' + opps + '\n\n'
                        '最近对话摘要（仅用于防重复，严禁顺着里面的闲聊或报错继续尬聊）：\n' + json.dumps(recent_digest, ensure_ascii=False)
                    )
                    messages = [
                        {'role': 'system', 'content': sys_prompt},
                        {'role': 'user', 'content': user_trigger},
                    ]
                    msg = {'content': ''}
                    cur_accumulated: list[str] = []
                    cur_mm_frame: dict | None = None
                    api_calls = 0
                    for _ in range(6):
                        api_calls += 1
                        if cur_mm_frame and self._is_current_bot_vision_capable():
                            call_msgs = messages + [{
                                'role': 'user',
                                'content': [
                                    {'type': 'text', 'text': cur_mm_frame['caption']},
                                    {'type': 'image_url', 'image_url': {'url': f"data:{cur_mm_frame['mime']};base64,{cur_mm_frame['b64']}"}},
                                ],
                            }]
                            cur_mm_frame = None
                        else:
                            call_msgs = messages
                        msg = await self.llm.complete_silent(call_msgs, tools=available_tools or None, big=False, bot_id=target_id)
                        msg.pop('_metrics', None)
                        step_txt = (msg.get('content') or '').strip()
                        if step_txt and (not cur_accumulated or (step_txt != cur_accumulated[-1] and step_txt not in cur_accumulated[-1])):
                            cur_accumulated.append(step_txt)
                        calls = msg.get('tool_calls') or []
                        if not calls:
                            break
                        messages.append(msg)
                        batch_c = calls[:6]
                        async def _run_cur_tool(c_item):
                            try:
                                c_args = json.loads(c_item['function']['arguments'])
                            except (ValueError, KeyError):
                                c_args = {}
                            c_name = c_item['function']['name']
                            t0 = asyncio.get_running_loop().time()
                            res = await self.harness.execute(c_name, c_args) if c_name in allowed else {'error': '此助手未启用该工具'}
                            dur = round((asyncio.get_running_loop().time() - t0) * 1000)
                            return c_item, c_name, c_args, res, dur
                        cur_results = await asyncio.gather(*[_run_cur_tool(c) for c in batch_c])
                        for c_item, name, args, result, dur in cur_results:
                            if isinstance(result, dict) and result.get('_multimodal_b64'):
                                mm_b64 = str(result.pop('_multimodal_b64') or '')
                                mm_mime = str(result.pop('_multimodal_mime', 'image/jpeg') or 'image/jpeg')
                                mm_cap = str(result.pop('_multimodal_caption', '') or '【工具返回的最新实时画面】')
                                if mm_b64:
                                    cur_mm_frame = {'b64': mm_b64, 'mime': mm_mime, 'caption': mm_cap}
                            trace.append({'tool': name, 'arguments': args, 'result': result, 'duration_ms': dur})
                            messages.append({'role': 'tool', 'tool_call_id': c_item['id'], 'content': json.dumps(result, ensure_ascii=False)})
                        if all(c['function']['name'] == 'present_choices' for c in batch_c) and cur_accumulated:
                            break
                    choice_prompt = (self.choices_prompt_var.get() or '').strip()
                    if choice_prompt and all(choice_prompt not in p for p in cur_accumulated):
                        cur_accumulated.append(choice_prompt)
                    text = '\n\n'.join(cur_accumulated).strip()
                    if not text and (trace or self.choices_var.get()):
                        api_calls += 1
                        summary = await self.llm.complete_silent(
                            messages + [{'role':'user', 'content':'请基于以上探索结果与已提供的选项，直接用自然亲切的语气对主人说一句主动搭话并带一个有趣的小问题，不要再调用工具。'}],
                            tools=available_tools or None,
                            big=False,
                            bot_id=target_id,
                            tool_choice='none',
                        )
                        text = (summary.get('content') or '').strip()
                    text = text or '主人这会儿在忙什么呢？有什么好玩的事跟余聊聊吗？'
                    mode = 'small_brain'
                else:
                    text = '这会儿在忙什么呢？挑一件有点卡住的小事告诉我，余先陪你把第一步捋清。'
                    mode = 'local_prompt'
                    api_calls = 0
                self.store.state['last_curiosity'] = time.time()
                self.store.save()
                extra = {'trace': trace, 'api_calls': api_calls, 'from_bot_id': target_id, 'from_bot_name': bot['name']} if trace else {'api_calls': api_calls, 'from_bot_id': target_id, 'from_bot_name': bot['name']}
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
                await self._auto_release_phone_if_used()
                if not self.executions_by_bot.get(target_id):
                    self._clear_bot_stream(target_id, source=src)
                self.choices_var.reset(cur_choices_token)
                self.choices_prompt_var.reset(cur_choices_prompt_token)
                self.stream_prefix_var.reset(cur_prefix_token)
                self.turn_id_var.reset(cur_turn_token)
                self.source_context.reset(src_marker)
                self.room_context.reset(room_marker)
                self.bot_context.reset(marker)
                self.activity = 'thinking' if self.executions_by_bot else self.ready_state()

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
                if msgs[i].get('role') == 'user':
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
        cur_exec_for_bot = self.executions_by_bot.get(bot_id)
        same_src_exec = bool(cur_exec_for_bot and normalize_node_source(cur_exec_for_bot.get('source')) == src)
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
            self.vault.log('用户：' + text)
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
        q_set = self.queued_tasks_by_bot.setdefault(bot_id, set())
        q_set.add(bg_task)
        bg_task.add_done_callback(lambda t, bid=bot_id: self.queued_tasks_by_bot.get(bid, set()).discard(t))
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
            'queued': True,
            'reused': reused_msg is not None,
            'reused_existing_user_message': reused_msg is not None,
            'removed_message_ids': removed_ids,
            'turn_id': turn_id,
            'user_message': m,
            'recent_tail': tail,
            'messages_epoch': int(self.store.state.get('messages_epoch') or 1),
            'seq': self.stream_seq,
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

    async def _chat(self, text, pre_recorded_user_msg=None, turn_id=None, source: str = 'phone', client_msg_id: str | None = None, deferred_incoming_spec: dict | None = None):
        bot_id = self.bot_context.get()
        src = normalize_node_source(source or self.source_context.get())
        enqueued_epoch = self._abort_epoch_by_bot.get(bot_id, 0)
        cur_task_outer = asyncio.current_task()
        if cur_task_outer and self.hop_depth.get() == 0:
            self.queued_tasks_by_bot.setdefault(bot_id, set()).add(cur_task_outer)
        async with self._get_bot_lock(bot_id, src):
            if (turn_id and turn_id in self._stopped_turn_ids) or (self.hop_depth.get() == 0 and enqueued_epoch != self._abort_epoch_by_bot.get(bot_id, 0)):
                return {'stopped': True}
            bot = get_assistant(self.store, bot_id, source=src)
            turn_token = self.turn_id_var.set(turn_id or ('turn-' + secrets.token_hex(6)))
            prefix_token = self.stream_prefix_var.set('')
            self._current_user_text_by_bot[bot_id] = text
            self._phone_blocked_by_user_by_bot[bot_id] = self._is_user_holding_or_refusing_phone(text, was_blocked=False)
            if isinstance(deferred_incoming_spec, dict) and pre_recorded_user_msg is None:
                spec_copy = dict(deferred_incoming_spec)
                inc_role = spec_copy.pop('role', 'assistant')
                inc_text = spec_copy.pop('text', text)
                inc_kind = spec_copy.pop('kind', 'bot_message')
                inc_turn = spec_copy.pop('turn_id', '')
                inc_sub = spec_copy.pop('is_sub_turn', True)
                m = self.store.message(inc_role, inc_text, kind=inc_kind, **spec_copy)
                self.stream_seq += 1
                m['seq'] = self.stream_seq
                if inc_turn:
                    m['turn_id'] = inc_turn
                m['is_sub_turn'] = bool(inc_sub)
                await self.publish({'type': 'message', 'source': src, 'message': m, 'seq': self.stream_seq})
            elif pre_recorded_user_msg is None:
                self.store.state['last_user'] = time.time()
                extra_u = {'client_msg_id': client_msg_id} if client_msg_id else {}
                m = self.store.message('user', text, bot_id=bot_id, source=src, **extra_u)
                self.stream_seq += 1
                m['seq'] = self.stream_seq
                self.vault.log('用户：' + text)
                await self.publish({'type': 'message', 'source': src, 'message': m, 'seq': self.stream_seq})
                visible = {m['id'] for m in self.store.state['messages'] if m.get('bot_id', 'companion') == bot_id}
                await self.escalation.ack([mid for mid in self.store.state['pending'] if mid in visible])
            else:
                m = pre_recorded_user_msg
            self.activity = 'thinking'
            cur_task = asyncio.current_task()
            if self.hop_depth.get() == 0:
                self.active_task = cur_task
                if cur_task:
                    self.active_tasks_by_bot[bot_id] = cur_task
            my_exec = {'bot_id': bot_id, 'source': src, 'goal': text[:200], 'trace': [], 'tool': None, 'started_at': time.time()}
            self.executions_by_node_bot[f'{src}:{bot_id}'] = my_exec
            self.executions_by_bot[bot_id] = my_exec
            self.execution = my_exec
            try:
                api_key = self.store.config.effective_api_key(big=False) or self.store.config.effective_api_key(big=True)
                model = self.store.config.effective_model(bot_model=bot.get('model', ''), big=False) or self.store.config.effective_model(bot_model=bot.get('model', ''), big=True)
                if not (api_key and model):
                    return await self.emit('记下了。具体最卡的是哪一步，理想结果又是什么？\n\n目前使用本地引导；在右上角「设置」中填入 API Key 并选择模型后，就能直接读写工作区、调工具和安排其它 Bot 干活啦。', 'curiosity', engine='local_prompt', source=src)

                await self._auto_compact_if_needed(bot_id)
                recent = []
                last_user_msg_item = None
                last_user_prefix = ''
                for msg_item in self.store.state['messages']:
                    if (
                        msg_item.get('bot_id', 'companion') == bot_id
                        and msg_item['role'] in ('user', 'assistant')
                        and normalize_node_source(msg_item.get('source')) == src
                    ):
                        is_incoming_bot_msg = (
                            msg_item.get('kind') == 'bot_message'
                            and msg_item.get('to_bot_id') == bot_id
                            and msg_item.get('direction') != 'outbound'
                            and (
                                msg_item.get('direction') == 'inbound'
                                or msg_item.get('from_bot_id') != bot_id
                                or (msg_item.get('from_node') and normalize_node_source(msg_item.get('from_node')) != src)
                            )
                        )
                        is_incoming_bot_reply = (
                            msg_item.get('kind') == 'bot_reply'
                            and (
                                msg_item.get('direction') == 'inbound'
                                or msg_item.get('from_bot_id') != bot_id
                                or (msg_item.get('from_node') and normalize_node_source(msg_item.get('from_node')) != src)
                            )
                        )
                        is_outbound_bot_msg = (
                            msg_item.get('kind') == 'bot_message'
                            and msg_item.get('direction') == 'outbound'
                        )
                        eff_role = 'user' if (is_incoming_bot_msg or is_incoming_bot_reply) else msg_item['role']
                        prefix = ''
                        if is_incoming_bot_reply:
                            from_lbl = str(msg_item.get('from_bot_name') or msg_item.get('from_bot_id') or '对方 Bot')
                            prefix = f'[收到来自「{from_lbl}」对 send_message 的回信]: '
                        elif is_outbound_bot_msg:
                            to_lbl = str(msg_item.get('to_bot_name') or msg_item.get('to_bot_id') or '对方 Bot')
                            prefix = f'[已调用 send_message 工具发给「{to_lbl}」]: '
                        elif (
                            msg_item.get('from_bot_name')
                            and (msg_item.get('from_bot_id') != bot_id or (msg_item.get('from_node') and normalize_node_source(msg_item.get('from_node')) != src))
                            and not is_incoming_bot_msg
                        ):
                            prefix = f"[{msg_item['from_bot_name']}]: "
                        trace_note = self._format_message_trace_note(msg_item) if eff_role == 'assistant' else ''
                        raw_llm = msg_item.get('llm_text')
                        if eff_role == 'user' and isinstance(raw_llm, list):
                            content_val = []
                            added_prefix = False
                            for part in raw_llm:
                                if isinstance(part, dict) and part.get('type') == 'text' and not added_prefix and (prefix or trace_note):
                                    content_val.append({**part, 'text': prefix + str(part.get('text') or '') + trace_note})
                                    added_prefix = True
                                else:
                                    content_val.append(part)
                        else:
                            txt = (
                                self._get_llm_history_text(msg_item)
                                if eff_role == 'assistant'
                                else str(raw_llm if isinstance(raw_llm, str) and raw_llm else (msg_item.get('text') or '')).strip()
                            )
                            if eff_role == 'assistant' and (not txt or txt in ('已完成操作。', '收到，已处理完毕。')):
                                continue
                            content_val = prefix + txt + trace_note
                        recent.append({
                            'id': str(msg_item.get('id') or ''),
                            'role': eff_role,
                            'content': content_val,
                        })
                        if eff_role == 'user':
                            last_user_msg_item = msg_item
                            last_user_prefix = prefix
                if (
                    recent
                    and recent[-1]['role'] == 'user'
                    and isinstance(recent[-1].get('content'), str)
                    and last_user_msg_item is not None
                    and 'llm_text' not in last_user_msg_item
                ):
                    raw_u_txt = str(last_user_msg_item.get('text') or '').strip()
                    enriched = await self._enrich_user_message_with_vision(
                        raw_u_txt, model, bot_id, bot['name']
                    )
                    last_user_msg_item['llm_text'] = enriched
                    if isinstance(enriched, list):
                        if last_user_prefix:
                            enriched_list = []
                            added_p = False
                            for part in enriched:
                                if isinstance(part, dict) and part.get('type') == 'text' and not added_p:
                                    enriched_list.append({**part, 'text': last_user_prefix + str(part.get('text') or '')})
                                    added_p = True
                                else:
                                    enriched_list.append(part)
                            recent[-1]['content'] = enriched_list
                        else:
                            recent[-1]['content'] = enriched
                    else:
                        recent[-1]['content'] = last_user_prefix + str(enriched or '')

                self._sync_topology_delta_if_changed()
                recent_with_deltas = self.memory_engine.inject_memory_deltas(recent, bot_id=bot_id, source=src)
                clean_recent = [{'role': r['role'], 'content': r['content']} for r in recent_with_deltas]

                if not self._cached_vault_context:
                    saved_vc = str(self.store.state.get('cached_vault_context') or '').strip()
                    if saved_vc:
                        self._cached_vault_context = saved_vc
                    else:
                        self._cached_vault_context = self.context()[:3600]
                        self.store.state['cached_vault_context'] = self._cached_vault_context
                if not self._cached_pinned_block and 'cached_pinned_block' in self.store.state:
                    self._cached_pinned_block = str(self.store.state.get('cached_pinned_block') or '')
                elif not self._cached_pinned_block:
                    self._cached_pinned_block = self.memory_engine.build_static_pinned_block()
                    self.store.state['cached_pinned_block'] = self._cached_pinned_block

                cur_node_label = node_display_name(src)
                if src in ('pc', 'pc2'):
                    node_identity_note = (
                        f'\n\n[当前节点身份: 你当前位于「{cur_node_label}」的「{bot["name"]}」专属会话中（具备 DeepSeek Harness Agent 标准版本机能力）。'
                        '你在当前会话里调用的 `run_bash`、`read_file`、`write_file`、`edit_file`、`dsh_workspace` 均直接在 Windows 电脑本机 (`UIWCVB`，默认工作区 `C:\\Users\\uiwcv\\Documents\\deepseek-harness\\default-workspace`) 上执行！'
                        '如需查看或控制 Windows 电脑桌面上的真实窗口应用（如 QQ、微信、浏览器等），请直接调用 `dsh_workspace(op="cua_desktop", action="list_windows")` 列出窗口，再调用 `dsh_workspace(op="cua_desktop", action="get_window_state", window_id=..., include_screenshot=true)` 获取窗口 UI 树与画面，并用 `action="activate_window" | "click" | "type_text" | "press_key" | "scroll"` 直接操控（或配合 `run_bash` 在 Windows 本机执行 PowerShell）！'
                        '如需给同端或跨端任意 Bot 发消息，请调用 `send_message(to_bot_id="研究助手" | "安卓一:白珂" | "安卓二:白珂", text="...")`（严禁只靠手打 @名字 纯文本代替工具调用）；'
                        '如需操控电脑端左下角「默认工作区」里的某个任务会话，请直接调用 `send_message(to_bot_id="会话:<会话标题或ID>", text="...")` 或 `dsh_workspace(op="dispatch_session", session_id="<标题或ID>", message="...")`！]'
                    )
                else:
                    node_identity_note = (
                        f'\n\n[当前节点身份: 你当前位于「{cur_node_label}」的「{bot["name"]}」专属会话中；其他跨端节点包括「安卓一」、「安卓二」、「Windows一」。'
                        '当主人让你给电脑端或其他 Bot 发消息/派活时，你必须真正发起 `send_message(to_bot_id="Windows一:白珂" | "Windows一:研究助手" | "安卓二:白珂", text="...")` 工具调用（严禁只在回复正文里手打 `@Windows一:白珂 ...` 纯文本而不调工具）！'
                        '也可直接给电脑端底部「默认工作区」的任务会话发消息操控它继续执行：`send_message(to_bot_id="会话:<会话标题或ID>", text="...")`！]'
                    )
                system = (
                    self.harness.static_system_instructions(self._cached_pinned_block) + '\n\n'
                    '你的设定：' + self.persona(bot_id) + node_identity_note + '\n\n'
                    '[记忆快照]\n' + self._cached_vault_context
                )
                all_schemas = self.harness.all_tools()
                plugin_names = set(self.harness.extension_registry.tool_plugins.keys())
                allowed = set(bot.get('tools') or [t['function']['name'] for t in all_schemas]) | plugin_names
                if src in ('pc', 'pc2'):
                    allowed -= {'phone_workspace', 'phone_macro', 'notify_user', 'emergency_alarm'}
                if self.hop_depth.get() > 0:
                    allowed -= {'send_message', 'message_colleague', 'message_phone_bot'}
                available_tools = [t for t in all_schemas if t['function']['name'] in allowed]
                messages = [{'role': 'system', 'content': system}, *clean_recent]
                trace = []
                accumulated_texts: list[str] = []
                last_step_text: str = ''
                final_turn_text: str = ''
                parsed_meta: dict = {}
                choices_reset_token = self.choices_var.set(None)
                choices_prompt_token = self.choices_prompt_var.set('')
                recent_call_sigs: list[str] = []
                await self._set_stream(text='正在思考…', speaker_id=bot_id, speaker_name=bot['name'])
                msg = {'content': '收到。'}
                use_big = (bot.get('engine') == 'big')
                latest_mm_frames: list[dict] = []
                api_calls = 0
                hallucination_retry_count = 0
                turn_started_perf = time.perf_counter()
                step_metrics_list: list[dict] = []
                for _ in range(120):
                    if self.turn_id_var.get() in self._stopped_turn_ids or enqueued_epoch != self._abort_epoch_by_bot.get(bot_id, 0):
                        raise asyncio.CancelledError()
                    # NEVER mutate already-sent messages in `messages` (preserves 100% Prefix Cache!)
                    # Consume `latest_mm_frames` once per turn step so stale images are never re-sent on subsequent text tool steps.
                    if latest_mm_frames:
                        mm_parts: list[dict] = []
                        for mf in latest_mm_frames:
                            mm_parts.append({'type': 'text', 'text': mf['caption']})
                            mm_parts.append({'type': 'image_url', 'image_url': {'url': f"data:{mf['mime']};base64,{mf['b64']}"}})
                        call_messages = messages + [{'role': 'user', 'content': mm_parts}]
                        latest_mm_frames = []
                    else:
                        call_messages = messages
                    api_calls += 1
                    msg = await self.complete(call_messages, tools=available_tools or None, big=use_big)
                    m_step = msg.pop('_metrics', None)
                    if isinstance(m_step, dict):
                        step_metrics_list.append(m_step)
                    if self.turn_id_var.get() in self._stopped_turn_ids or enqueued_epoch != self._abort_epoch_by_bot.get(bot_id, 0):
                        raise asyncio.CancelledError()
                    turn_content = (msg.get('content') or '').strip()
                    calls = msg.get('tool_calls') or []
                    if not calls and self.hop_depth.get() == 0 and not trace and 'send_message' in allowed and turn_content:
                        auto_at = self._extract_plain_at_mention_call(turn_content, user_text=text, cur_src=src)
                        if auto_at is not None:
                            at_target, at_body = auto_at
                            synth_call = {
                                'id': 'call_at_' + secrets.token_hex(4),
                                'type': 'function',
                                'function': {
                                    'name': 'send_message',
                                    'arguments': json.dumps({'to_bot_id': at_target, 'text': at_body}, ensure_ascii=False),
                                },
                            }
                            calls = [synth_call]
                            msg['tool_calls'] = calls
                            turn_content = ''
                            msg['content'] = ''
                    if not calls and turn_content and available_tools:
                        is_fake_action = self._is_unfulfilled_action_promise(turn_content, user_text=text, trace=trace)
                        is_fake_write = self._is_unfulfilled_write_claim(turn_content, trace=trace)
                        turn_content = self._strip_fake_action_stage_directions(turn_content)
                        msg['content'] = turn_content
                        if (is_fake_action or is_fake_write) and hallucination_retry_count < 2:
                            hallucination_retry_count += 1
                            messages.append({'role': 'assistant', 'content': turn_content or '（准备执行）'})
                            if is_fake_write:
                                messages.append({
                                    'role': 'user',
                                    'content': (
                                        '【系统实时校验拦截】：你刚才声称“已经记进/已写入”文件，但本轮 `trace` 中没有任何 `write_file` 或 `edit_file` 调用！'
                                        '严禁口头虚构写入。请立即在当前步骤真实调用 `write_file` 或 `edit_file` 落盘，完成后再回复主人。'
                                    ),
                                })
                            else:
                                messages.append({
                                    'role': 'user',
                                    'content': (
                                        '【系统实时校验拦截】：你刚才只输出了“先打开/正在打开/这就动手”等文字描述，却没有发起任何 `tool_calls`！'
                                        '严禁用括号旁白或口头承诺代替真实工具执行。请立即在当前步骤发起对应的 `tool_calls`（如 `phone_workspace` / `read_file` / `write_file`）真正执行操作！'
                                    ),
                                })
                            continue
                    elif turn_content:
                        turn_content = self._strip_fake_action_stage_directions(turn_content)
                        msg['content'] = turn_content
                    if turn_content:
                        clean_piece, piece_meta = self._extract_reply_piece(turn_content)
                        if piece_meta:
                            parsed_meta.update(piece_meta)
                        piece_to_add = (clean_piece or turn_content).strip()
                        if piece_to_add:
                            last_step_text = piece_to_add
                            if not accumulated_texts:
                                accumulated_texts.append(piece_to_add)
                            elif piece_to_add != accumulated_texts[-1] and piece_to_add not in accumulated_texts[-1]:
                                if accumulated_texts[-1] in piece_to_add:
                                    accumulated_texts[-1] = piece_to_add
                                else:
                                    accumulated_texts.append(piece_to_add)
                            final_turn_text = '\n\n'.join(accumulated_texts)
                            self.stream_prefix_var.set(final_turn_text)
                            if calls:
                                # Immediately push the newly spoken step narration BEFORE tool execution begins!
                                first_t = calls[0]['function'].get('name') or 'tool'
                                await self._set_stream(
                                    text=final_turn_text,
                                    tool=first_t,
                                    speaker_id=bot_id,
                                    speaker_name=bot['name'],
                                )
                    if not calls:
                        break
                    messages.append(msg)
                    batch_calls = calls[:48]
                    if not all(c['function']['name'] == 'present_choices' for c in batch_calls):
                        last_step_text = ''
                    # LLMCompiler / Wide-First Parallel Tool Execution:
                    # Run all tool calls in parallel via `asyncio.gather` whenever there is at most 1 stateful GUI screen operation!
                    stateful_gui_count = sum(
                        1 for c in batch_calls
                        if c['function']['name'] in ('phone_workspace', 'cloud_desktop')
                        and '"notifications"' not in (c['function'].get('arguments') or '')
                    )
                    batch_tool_names = {c['function']['name'] for c in batch_calls}
                    has_mutation_and_dependent = (
                        bool(batch_tool_names & {'write_file', 'edit_file', 'run_bash', 'manage_bot'})
                        and len(batch_calls) > 1
                    )
                    can_parallel = len(batch_calls) > 1 and stateful_gui_count <= 1 and not has_mutation_and_dependent
                    final_interconnect_msg: dict | None = None
                    delivered_replies_in_batch: list[str] = []
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
                        await self.publish({'type': 'execution', 'source': src, 'execution': my_exec})
                        async def _run_one(c_item):
                            try:
                                c_args = json.loads(c_item['function']['arguments'])
                            except (ValueError, KeyError):
                                c_args = {}
                            c_name = c_item['function']['name']
                            sig, is_scroll_op = self._build_tool_call_sig(c_name, c_args)
                            recent_call_sigs.append(sig)
                            t0 = asyncio.get_running_loop().time()
                            try:
                                if c_name not in allowed:
                                    res = {'error': '此助手未启用该工具'}
                                elif src in ('pc', 'pc2') and c_name == 'run_bash' and self.is_dsh_online():
                                    res = await self._raw_dsh_rpc('pc_shell', c_args, timeout=float(c_args.get('timeout') or 25.0))
                                elif src in ('pc', 'pc2') and c_name in ('read_file', 'write_file', 'edit_file', 'list_dir', 'glob_files', 'grep_search') and self._should_use_pc_fs(c_name, c_args):
                                    res = await self._raw_dsh_rpc('pc_fs', {'fs_op': c_name, **c_args}, timeout=25.0)
                                else:
                                    res = await self.harness.execute(c_name, c_args)
                            except asyncio.CancelledError:
                                raise
                            except Exception as exc:
                                res = {'error': f'工具执行异常: {str(exc)[:240]}'}
                            dur = max(1, round((asyncio.get_running_loop().time() - t0) * 1000))
                            return c_item, c_name, c_args, res, dur
                        parallel_results = await asyncio.gather(*[_run_one(c_item) for c_item in batch_calls])
                        for idx_p, (c_item, c_name, c_args, result, dur) in enumerate(parallel_results):
                            if isinstance(result, dict):
                                if result.get('status') == 'delivered_reply' and result.get('delivered_text'):
                                    delivered_replies_in_batch.append(str(result['delivered_text']).strip())
                                if result.pop('_already_emitted_turn_bubbles', False):
                                    em_msg = result.pop('_final_emitted_msg', None)
                                    if isinstance(em_msg, dict):
                                        final_interconnect_msg = em_msg
                                else:
                                    result.pop('_final_emitted_msg', None)
                            if isinstance(result, dict) and result.get('_multimodal_b64'):
                                mm_b64 = str(result.pop('_multimodal_b64') or '')
                                mm_mime = str(result.pop('_multimodal_mime', 'image/jpeg') or 'image/jpeg')
                                mm_cap = str(result.pop('_multimodal_caption', '') or '【工具返回的最新实时画面】')
                                if mm_b64:
                                    latest_mm_frames.append({'b64': mm_b64, 'mime': mm_mime, 'caption': mm_cap})
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
                        if final_interconnect_msg is None:
                            await self._set_stream(
                                text=final_turn_text or '正在思考…',
                                tool=my_exec['tool'],
                                speaker_id=bot_id,
                                speaker_name=bot['name'],
                                thought=None
                            )
                            await self.publish({'type': 'execution', 'source': src, 'execution': my_exec})
                        if any(
                            c['function']['name'] == 'custom_tool'
                            and any(k in (c['function'].get('arguments') or '') for k in ('"create"', '"delete"'))
                            for c in batch_calls
                        ):
                            all_schemas = self.harness.all_tools()
                            plugin_names = set(self.harness.extension_registry.tool_plugins.keys())
                            allowed = set(bot.get('tools') or [t['function']['name'] for t in all_schemas]) | plugin_names
                            if src in ('pc', 'pc2'):
                                allowed -= {'phone_workspace', 'phone_macro', 'notify_user', 'emergency_alarm'}
                            if self.hop_depth.get() > 0:
                                allowed -= {'send_message', 'message_colleague', 'message_phone_bot'}
                            available_tools = [t for t in all_schemas if t['function']['name'] in allowed]
                        if all(c['function']['name'] == 'present_choices' for c in batch_calls) and len(final_turn_text.strip()) >= 12:
                            break
                    else:
                        for c in batch_calls:
                            try:
                                args = json.loads(c['function']['arguments'])
                            except (ValueError, KeyError):
                                args = {}
                            name = c['function']['name']
                            sig, is_scroll_op = self._build_tool_call_sig(name, args)
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
                                thought=None
                            )
                            await self.publish({'type': 'execution', 'source': src, 'execution': my_exec})
                            started = asyncio.get_running_loop().time()
                            try:
                                if name not in allowed:
                                    result = {'error': '此助手未启用该工具'}
                                elif src in ('pc', 'pc2') and name == 'run_bash' and self.is_dsh_online():
                                    result = await self._raw_dsh_rpc('pc_shell', args, timeout=float(args.get('timeout') or 25.0))
                                elif src in ('pc', 'pc2') and name in ('read_file', 'write_file', 'edit_file', 'list_dir', 'glob_files', 'grep_search') and self._should_use_pc_fs(name, args):
                                    result = await self._raw_dsh_rpc('pc_fs', {'fs_op': name, **args}, timeout=25.0)
                                else:
                                    result = await self.harness.execute(name, args)
                            except asyncio.CancelledError:
                                raise
                            except Exception as exc:
                                result = {'error': f'工具执行异常: {str(exc)[:240]}'}
                            if isinstance(result, dict):
                                if result.get('status') == 'delivered_reply' and result.get('delivered_text'):
                                    delivered_replies_in_batch.append(str(result['delivered_text']).strip())
                                if result.pop('_already_emitted_turn_bubbles', False):
                                    em_msg = result.pop('_final_emitted_msg', None)
                                    if isinstance(em_msg, dict):
                                        final_interconnect_msg = em_msg
                                else:
                                    result.pop('_final_emitted_msg', None)
                            if isinstance(result, dict) and result.get('_multimodal_b64'):
                                mm_b64 = str(result.pop('_multimodal_b64') or '')
                                mm_mime = str(result.pop('_multimodal_mime', 'image/jpeg') or 'image/jpeg')
                                mm_cap = str(result.pop('_multimodal_caption', '') or '【工具返回的最新实时画面】')
                                if mm_b64:
                                    latest_mm_frames.append({'b64': mm_b64, 'mime': mm_mime, 'caption': mm_cap})
                            entry = compact_trace_list([{'tool': name, 'arguments': args, 'result': result, 'duration_ms': max(1, round((asyncio.get_running_loop().time() - started) * 1000)), 'status': 'done'}])[0]
                            trace.append(entry)
                            running_entry.clear()
                            running_entry.update(entry)
                            # Keep clean non-null step count between consecutive tool steps so client UI never renders a fake extra tool row!
                            my_exec['tool'] = f'已执行 {len(trace)} 步'
                            self.execution = my_exec
                            if final_interconnect_msg is None:
                                await self._set_stream(
                                    text=final_turn_text if final_turn_text else '正在思考…',
                                    tool=my_exec['tool'],
                                    speaker_id=bot_id,
                                    speaker_name=bot['name'],
                                    thought=None
                                )
                                await self.publish({'type': 'execution', 'source': src, 'execution': my_exec})
                            # Offload >12K outputs into SQLite FTS5 BEFORE appending so `messages` stays immutable!
                            tool_content_str = self.context_search.maybe_offload_tool_output(
                                name, args, result, bot_id=bot_id, turn_id=self.turn_id_var.get()
                            )
                            messages.append({'role': 'tool', 'tool_call_id': c['id'], 'content': tool_content_str})
                        if any(
                            c['function']['name'] == 'custom_tool'
                            and any(k in (c['function'].get('arguments') or '') for k in ('"create"', '"delete"'))
                            for c in batch_calls
                        ):
                            all_schemas = self.harness.all_tools()
                            plugin_names = set(self.harness.extension_registry.tool_plugins.keys())
                            allowed = set(bot.get('tools') or [t['function']['name'] for t in all_schemas]) | plugin_names
                            if src in ('pc', 'pc2'):
                                allowed -= {'phone_workspace', 'phone_macro', 'notify_user', 'emergency_alarm'}
                            if self.hop_depth.get() > 0:
                                allowed -= {'send_message', 'message_colleague', 'message_phone_bot'}
                            available_tools = [t for t in all_schemas if t['function']['name'] in allowed]
                        # If this tool step only attached `present_choices` and we already have the assistant's reply text,
                        # stop immediately so a second post-tool turn never overwrites or tacks on redundant text!
                        if all(c['function']['name'] == 'present_choices' for c in batch_calls) and len(final_turn_text.strip()) >= 12:
                            break
                    if (
                        self.hop_depth.get() == 0
                        and final_interconnect_msg is not None
                        and all(c['function']['name'] in ('send_message', 'message_colleague', 'message_phone_bot', 'dsh_workspace', 'present_choices') for c in batch_calls)
                    ):
                        self._clear_bot_stream(bot_id, source=src)
                        self.last_completed_stream = {
                            'seq': self.stream_seq,
                            'turn_id': self.turn_id_var.get() or final_interconnect_msg.get('id', ''),
                            'bot_id': bot_id,
                            'source': src,
                            'speaker_id': final_interconnect_msg.get('from_bot_id') or bot_id,
                            'speaker_name': final_interconnect_msg.get('from_bot_name') or bot['name'],
                            'text': final_interconnect_msg.get('text', ''),
                            'final_message_id': final_interconnect_msg.get('id', ''),
                            'done': True,
                            'completed_at': time.time(),
                        }
                        self.store.save()
                        self.wake.set()
                        try:
                            return final_interconnect_msg
                        finally:
                            self.choices_var.reset(choices_reset_token)
                            self.choices_prompt_var.reset(choices_prompt_token)
                    if self.hop_depth.get() > 0 and delivered_replies_in_batch:
                        rep_body = '\n\n'.join(d for d in delivered_replies_in_batch if d)
                        last_step_text = rep_body
                        accumulated_texts = [rep_body]
                        final_turn_text = rep_body
                        self.stream_prefix_var.set(final_turn_text)
                        break

                choice_prompt_str = (self.choices_prompt_var.get() or '').strip()
                if choice_prompt_str and all(choice_prompt_str not in p for p in accumulated_texts):
                    accumulated_texts.append(choice_prompt_str)
                    last_step_text = choice_prompt_str
                    final_turn_text = '\n\n'.join(accumulated_texts)
                    self.stream_prefix_var.set(final_turn_text)

                if (not accumulated_texts or not last_step_text) and trace:
                    await self._set_stream(text=final_turn_text or '正在整理回复…', speaker_id=bot_id, speaker_name=bot['name'])
                    api_calls += 1
                    # Keep default `tool_choice='auto'` and `parallel_tool_calls=True` (never switch to `tool_choice='none'`,
                    # which alters the upstream gateway's tool prompt template at token 4,096 and drops Prefix Cache from 98% to 6%!).
                    final_msg = await self.complete(
                        messages + [{'role': 'user', 'content': '请基于以上工具执行结果，直接用自然、清晰的话回复主人，不要再调用工具。'}],
                        tools=available_tools or None,
                        big=use_big,
                    )
                    m_step_f = final_msg.pop('_metrics', None)
                    if isinstance(m_step_f, dict):
                        step_metrics_list.append(m_step_f)
                    content_str = (final_msg.get('content') or '').strip()
                    if content_str:
                        clean_piece, piece_meta = self._extract_reply_piece(content_str)
                        if piece_meta:
                            parsed_meta.update(piece_meta)
                        piece_to_add = (clean_piece or content_str).strip()
                        if piece_to_add:
                            last_step_text = piece_to_add
                            if not accumulated_texts or accumulated_texts[-1] != piece_to_add:
                                accumulated_texts.append(piece_to_add)
                        final_turn_text = '\n\n'.join(accumulated_texts)
                        self.stream_prefix_var.set(final_turn_text)
                if trace and last_step_text.strip():
                    joined_reply = last_step_text.strip()
                else:
                    joined_reply = '\n\n'.join(accumulated_texts).strip() or final_turn_text.strip()
                if not joined_reply and trace:
                    last_tool = trace[-1]
                    joined_reply = f"已执行 `{last_tool['tool']}`，结果：{json.dumps(last_tool['result'], ensure_ascii=False)[:400]}"
                llm_only_reply = (last_step_text or (accumulated_texts[-1] if accumulated_texts else '') or joined_reply).strip()
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
                turn_total_ms = max(1, int(round((time.perf_counter() - turn_started_perf) * 1000)))
                last_m = step_metrics_list[-1] if step_metrics_list else {}
                first_m = step_metrics_list[0] if step_metrics_list else {}
                total_comp_tok = sum(int(x.get('completion_tokens') or 0) for x in step_metrics_list)
                avg_tps = round(sum(float(x.get('tps') or 0.0) for x in step_metrics_list) / max(1, len(step_metrics_list)), 1) if step_metrics_list else 0.0
                turn_metrics = {
                    'ttft_ms': int(first_m.get('ttft_ms') or last_m.get('ttft_ms') or 0),
                    'tps': float(last_m.get('tps') or avg_tps or 0.0),
                    'prompt_tokens': int(last_m.get('prompt_tokens') or self.last_prompt_tokens.get(bot_id, 0) or 0),
                    'cached_tokens': int(last_m.get('cached_tokens') or self.last_cached_tokens.get(bot_id, 0) or 0),
                    'cache_hit_pct': int(last_m.get('cache_hit_pct') or 0),
                    'completion_tokens': total_comp_tok,
                    'api_calls': api_calls,
                    'total_ms': turn_total_ms,
                }
                self.vault.log('助理：' + reply_out)
                self.vault.reindex()
                self.store.save()
                self.wake.set()
                extra = {
                    'api_calls': api_calls,
                    'metrics': turn_metrics,
                    'from_bot_id': bot_id,
                    'from_bot_name': bot['name'],
                    'from_node': src,
                    'llm_text': llm_only_reply or reply_out,
                }
                if self.hop_depth.get() > 0 and isinstance(deferred_incoming_spec, dict):
                    extra.update({
                        'to_bot_id': deferred_incoming_spec.get('from_bot_id', ''),
                        'to_bot_name': deferred_incoming_spec.get('from_bot_name', ''),
                        'to_node': deferred_incoming_spec.get('from_node', ''),
                        'direction': 'outbound',
                    })
                if trace:
                    extra['trace'] = trace
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
                if cur_task_outer and self.hop_depth.get() == 0:
                    self.queued_tasks_by_bot.get(bot_id, set()).discard(cur_task_outer)
                if src not in ('pc', 'pc2'):
                    await self._auto_release_phone_if_used()
                self._clear_bot_stream(bot_id, source=src)
                with contextlib.suppress(Exception):
                    await self.publish({'type': 'stream', 'source': src, 'bot_id': bot_id, 'stream': None, 'seq': self.stream_seq})
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
                    progress = self._make_harness_event_bridge(bot_id, bot['name'], my_exec, publish_exec=True)
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
                if cur_task:
                    self.queued_tasks_by_bot.get(bot_id, set()).discard(cur_task)
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
                            txt = (
                                self._get_llm_history_text(msg_item)
                                if msg_item['role'] == 'assistant'
                                else (msg_item.get('text') or '').strip()
                            )
                            if msg_item['role'] == 'assistant' and (not txt or txt in ('已完成操作。', '收到，已处理完毕。')):
                                continue
                            recent.append({'role': msg_item['role'], 'content': txt[:1500]})
                    recent = recent[-24:]
                    today_str = datetime.now(TZ).strftime('%Y-%m-%d')
                    yesterday_str = datetime.fromtimestamp(time.time() - 86400, TZ).strftime('%Y-%m-%d')
                    preloaded_blocks = []
                    for rel_p, lbl_p in [
                        ('tasks/active_deadlines.md', '待办与截止事项 (tasks/active_deadlines.md)'),
                        ('profile/course_schedule.md', '课表与作息 (profile/course_schedule.md)'),
                        (f'timeline/{today_str}.md', f'今日计划与时间线 (timeline/{today_str}.md)'),
                        (f'timeline/{yesterday_str}.md', f'昨日时间线与晚间生成计划 (timeline/{yesterday_str}.md)'),
                    ]:
                        try:
                            raw_v = (self.vault.read(rel_p) or '').strip()
                            if raw_v:
                                preloaded_blocks.append(f'### {lbl_p}\n{raw_v[:2500]}')
                        except Exception:
                            pass
                    preloaded_md = '\n\n'.join(preloaded_blocks) if preloaded_blocks else '（暂无预载档案）'
                    system = (
                        self.harness.static_system_instructions(self.memory_engine.build_static_pinned_block()) + '\n\n'
                        '你的设定：' + self.persona(target_id) + '\n\n'
                        f'[{label}触发]：请按任务目标直接执行，并直接输出一句给主人的提醒内容（不要调用 notify_user 或 schedule_task，你的回复本身就会直接作为提醒推送给主人，不要再附加“推送已发送”之类的自我汇报）。'
                    )
                    sched_user_prompt = (
                        '当前时间：' + self._fmt_bj(time.time()) + ' (' + now_iso() + ')\n'
                        '【系统已为你 0 成本预载的核心档案（若以下内容已足够判断，请直接输出提醒，无需再调 read_file / run_bash，仅用 1 次 API 请求完成！若确需调用多个工具，必须在同一次响应中并发发出所有 tool_calls）】：\n'
                        + preloaded_md + '\n\n'
                        f'⏰ [{label}到点触发]：{goal}'
                    )
                    # Exclude notify_user and schedule_task from scheduled execution so the LLM never double-notifies or re-schedules the same reminder!
                    allowed = (set(bot.get('tools') or [t['function']['name'] for t in TOOLS])) - {'notify_user', 'schedule_task'}
                    available_tools = [t for t in TOOLS if t['function']['name'] in allowed]
                    messages = [
                        {'role': 'system', 'content': system},
                        *recent,
                        {'role': 'user', 'content': sched_user_prompt},
                    ]
                    trace = []
                    accumulated_texts: list[str] = []
                    final_turn_text: str = ''
                    sched_mm_frame: dict | None = None
                    await self._set_stream(text=f'⏰ 正在执行{label}…', speaker_id=target_id, speaker_name=bot['name'])
                    msg = {'content': ''}
                    use_big = (bot.get('engine') == 'big')
                    api_calls = 0
                    for _ in range(30):
                        api_calls += 1
                        if sched_mm_frame and self._is_current_bot_vision_capable():
                            call_s_msgs = messages + [{
                                'role': 'user',
                                'content': [
                                    {'type': 'text', 'text': sched_mm_frame['caption']},
                                    {'type': 'image_url', 'image_url': {'url': f"data:{sched_mm_frame['mime']};base64,{sched_mm_frame['b64']}"}},
                                ],
                            }]
                            sched_mm_frame = None
                        else:
                            call_s_msgs = messages
                        msg = await self.complete(call_s_msgs, tools=available_tools or None, big=use_big)
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
                        batch_s = calls[:16]
                        running_entries = []
                        for c_item in batch_s:
                            try:
                                c_args_init = json.loads(c_item['function']['arguments'])
                            except (ValueError, KeyError):
                                c_args_init = {}
                            r_ent = {'tool': c_item['function']['name'], 'arguments': c_args_init, 'duration_ms': 0, 'status': 'running'}
                            my_exec['trace'].append(r_ent)
                            running_entries.append((c_item, c_item['function']['name'], c_args_init, r_ent))
                        names_s = [x[1] for x in running_entries]
                        my_exec['tool'] = f"并行执行 ({', '.join(names_s[:3])})" if len(names_s) > 1 else names_s[0]
                        self.execution = my_exec
                        await self._set_stream(text=final_turn_text or f"正在调用 {my_exec['tool']}…", tool=my_exec['tool'], speaker_id=target_id, speaker_name=bot['name'])
                        await self.publish({'type': 'execution', 'execution': my_exec})

                        async def _run_sched_one(c_item, c_name, c_args, r_ent):
                            t0 = asyncio.get_running_loop().time()
                            res = await self.harness.execute(c_name, c_args) if c_name in allowed else {'error': '此助手未启用该工具'}
                            dur = max(1, round((asyncio.get_running_loop().time() - t0) * 1000))
                            return c_item, c_name, c_args, res, dur, r_ent

                        sched_results = await asyncio.gather(*[_run_sched_one(*item) for item in running_entries])
                        for c_item, name, args, result, dur, r_ent in sched_results:
                            if isinstance(result, dict) and result.get('_multimodal_b64'):
                                mm_b64 = str(result.pop('_multimodal_b64') or '')
                                mm_mime = str(result.pop('_multimodal_mime', 'image/jpeg') or 'image/jpeg')
                                mm_cap = str(result.pop('_multimodal_caption', '') or '【工具返回的最新实时画面】')
                                if mm_b64:
                                    sched_mm_frame = {'b64': mm_b64, 'mime': mm_mime, 'caption': mm_cap}
                            entry = {'tool': name, 'arguments': args, 'result': result, 'duration_ms': dur, 'status': 'done'}
                            trace.append(entry)
                            r_ent.update(entry)
                            tool_content_str = self.context_search.maybe_offload_tool_output(
                                name, args, result, bot_id=target_id, turn_id=self.turn_id_var.get()
                            )
                            messages.append({'role': 'tool', 'tool_call_id': c_item['id'], 'content': tool_content_str})
                        my_exec['tool'] = f'已执行 {len(trace)} 步'
                        self.execution = my_exec
                        await self._set_stream(text=final_turn_text or '正在思考…', tool=my_exec['tool'], speaker_id=target_id, speaker_name=bot['name'])
                        await self.publish({'type': 'execution', 'execution': my_exec})
                    final_text = final_turn_text.strip() or '\n\n'.join(accumulated_texts).strip() or (msg.get('content') or '').strip()
                    if not final_text and trace:
                        api_calls += 1
                        self._next_tool_choice = 'none'
                        sum_msg = await self.complete(
                            messages + [{'role': 'user', 'content': '请基于以上定时任务执行结果，直接用自然简练的话输出给主人的提醒内容，不要再调用工具。'}],
                            tools=available_tools or None, big=use_big
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
                        trace=trace, api_calls=api_calls, goal=goal, from_bot_id=target_id, from_bot_name=bot['name']
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
        q_set = self.queued_tasks_by_bot.setdefault(bot_id, set())
        q_set.add(bg_task)
        bg_task.add_done_callback(lambda t, bid=bot_id: self.queued_tasks_by_bot.get(bid, set()).discard(t))
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
            'ok': True,
            'queued': True,
            'turn_id': turn_id,
            'edited_message': user_msg,
            'user_message': user_msg,
            'rerolled_user_message_id': user_msg.get('id'),
            'removed_message_ids': removed_ids,
            'messages_epoch': self.store.state['messages_epoch'],
            'recent_tail': tail,
            'seq': self.stream_seq,
        }

    def _make_harness_event_bridge(self, target_id: str, speaker_name: str, exec_dict: dict | None, sub_goal: str | None = None, publish_exec: bool = False):
        async def _on_event(event):
            ev_type = event.get('type')
            if isinstance(exec_dict, dict):
                subs = exec_dict.setdefault('sub_agents', {}) if sub_goal is not None else None
                if ev_type == 'tool_start':
                    if subs is not None:
                        subs[target_id] = {'bot_id': target_id, 'name': speaker_name, 'status': f"调用 {event['tool']}", 'goal': sub_goal, 'updated_at': time.time()}
                    if isinstance(exec_dict.get('trace'), list):
                        exec_dict['trace'].append({'tool': event['tool'], 'arguments': event.get('arguments') or {}, 'duration_ms': 0, 'status': 'running'})
                    exec_dict['tool'] = event['tool']
                elif ev_type == 'tool_end':
                    entry_done = {**dict(event['entry']), 'status': 'done'}
                    if isinstance(exec_dict.get('trace'), list):
                        if exec_dict['trace'] and exec_dict['trace'][-1].get('status') == 'running' and exec_dict['trace'][-1].get('tool') == entry_done.get('tool'):
                            exec_dict['trace'][-1] = entry_done
                        else:
                            exec_dict['trace'].append(entry_done)
                    exec_dict['tool'] = f"正在分析 {entry_done.get('tool', '工具')} 结果…"
                elif ev_type == 'turn_text' and subs is not None:
                    subs[target_id] = {'bot_id': target_id, 'name': speaker_name, 'status': '生成回复中', 'goal': sub_goal, 'updated_at': time.time()}
                if publish_exec:
                    self.execution = exec_dict
            if ev_type == 'turn_text':
                self.stream_prefix_var.set(event['text'])
                await self._set_stream(text=event['text'], speaker_id=target_id, speaker_name=speaker_name)
            elif ev_type == 'tool_start':
                await self._set_stream(text=self.stream_prefix_var.get() or f"正在调用 {event['tool']}…", tool=event['tool'], speaker_id=target_id, speaker_name=speaker_name)
                if publish_exec and isinstance(exec_dict, dict):
                    await self.publish({'type': 'execution', 'source': normalize_node_source(exec_dict.get('source')), 'execution': exec_dict})
            elif ev_type == 'tool_end':
                await self._set_stream(text=self.stream_prefix_var.get() or '正在思考…', tool=f"正在分析 {event['entry'].get('tool', '工具')} 结果…", speaker_id=target_id, speaker_name=speaker_name)
                if publish_exec and isinstance(exec_dict, dict):
                    await self.publish({'type': 'execution', 'source': normalize_node_source(exec_dict.get('source')), 'execution': exec_dict})
        return _on_event

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
        self.vault.log(f'用户（编辑重发 {target_msg["id"]}）：' + new_text)
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

    def quiet(self):
        return self.scheduler_engine.is_quiet_hour()

    async def _sleep_time_consolidate(self):
        """Silent background Sleep-Time Compute via MemoryEngine (never touches live_stream)."""
        async with self.lock:
            await self.memory_engine.sleep_time_consolidate(
                silent_complete_fn=lambda msgs: self.llm.complete_silent(msgs, tools=None, big=False)
            )
            saved_vc = str(self.store.state.get('cached_vault_context') or '').strip()
            if saved_vc:
                self._cached_vault_context = saved_vc

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
                if self.is_any_bot_busy(): continue
                # 0. Passive background notification convergence every 75s so status-bar items are saved before being swiped away
                if now - self._last_notif_bg_poll >= 75.0 and not self.executions_by_bot:
                    await self._poll_and_converge_notifications(timeout=4.5)
                has_api = bool((c.effective_api_key(big=True) or c.effective_api_key(big=False)) and (c.effective_model(big=True) or c.effective_model(big=False)))
                # 1. Fire due one-shot timers (exact wall-clock or countdown; never blocked by 0-8 AM quiet hours!)
                for t in s.get('timers', []):
                    if not t.get('done') and t.get('due_at', 0) <= now:
                        t['done'] = True
                        self.store.save()
                        if has_api:
                            await self.run_scheduled(t['goal'], bot_id=t.get('bot_id', s['primary_bot_id']), label='定时闹钟')
                        break
                if self.is_any_bot_busy(): continue
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
                # 3. Sleep-Time Compute memory consolidation when user has been idle >= 180s (3 min)
                if (
                    has_api
                    and not self.is_any_bot_busy()
                    and (now - s.get('last_user', 0)) >= self.memory_engine.SLEEP_CONSOLIDATE_IDLE_SECONDS
                    and s.get('last_user', 0) > s.get('last_sleep_consolidate', 0)
                ):
                    await self._sleep_time_consolidate()
                # 4. Background curiosity & idle research respect quiet hours (00:00 - 08:00) and active chat sessions
                if self.quiet() or self.is_any_bot_busy(): continue
                if len(s['pending']) < 3 and now - s['last_curiosity'] >= c.curiosity_interval_minutes * 60:
                    curiosity_targets = []
                    for node_k in ('phone', 'phone2'):
                        for b in s.get('assistants', []):
                            if resolve_bot_for_source(b, node_k).get('curiosity_enabled'):
                                curiosity_targets.append((b['id'], node_k))
                    if curiosity_targets:
                        target_bot_id, target_src = curiosity_targets[0]
                        await self.curiosity(bot_id=target_bot_id, source=target_src)
                ready = next((x for x in s['opportunities'] if x['ready'] and not x['done']), None)
                if ready and has_api and now - s['last_user'] >= c.idle_research_minutes * 60:
                    await self.research()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.store.state['last_error'] = str(e)[:250]
                self.store.save()
                await self.publish({'type':'engine_error', 'message':str(e)[:250]})
