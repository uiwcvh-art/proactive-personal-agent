"""
[Module Responsibility]
主动关怀、空闲研究、定时巡检与睡眠记忆整合模块 (`server/core/brain_proactive_mixin.py`)。
负责处理主动好奇心触发 (`curiosity`)、空闲深度研究 (`research`, `_research`, `confirm`)、
定时提醒与周期例行任务执行 (`add_deadline`, `run_scheduled`)、
睡眠期记忆去重与归档 (`_sleep_time_consolidate`, `quiet`) 及后台主调度协程 (`scheduler`)。

[Public Interface]
- `BrainProactiveMixin`: 为 `Brain` 提供主动性与后台调度的 Mixin

[Dependencies]
- `assistants.get_assistant`, `assistants.resolve_bot_for_source`
- `core.xml_tool_parser.is_silent_proactive_output`
- `harness.TOOLS`, `storage.normalize_node_source`, `vault_manager.TZ`, `vault_manager.now_iso`
"""
from __future__ import annotations
import asyncio, contextlib, json, secrets, time
from datetime import datetime
from assistants import get_assistant, resolve_bot_for_source
from core.xml_tool_parser import is_silent_proactive_output
from harness import TOOLS
from storage import normalize_node_source
from vault_manager import TZ, now_iso


class BrainProactiveMixin:
    def _is_current_bot_vision_capable(self) -> bool:
        return True

    async def _tool_stay_silent(self, args: dict) -> dict:
        reason = str((args or {}).get('reason') or (args or {}).get('summary') or (args or {}).get('text') or '当前无重要新情况，保持沉默不打扰主人').strip()[:300]
        try:
            snooze_min = max(0, min(1440, int((args or {}).get('snooze_minutes') or 0)))
        except (ValueError, TypeError):
            snooze_min = 0
        now_ts = time.time()
        if self.activity == 'curiosity':
            cur_min = max(1, int(getattr(self.store.config, 'curiosity_interval_minutes', 30) or 30))
            delay_min = snooze_min if snooze_min > 0 else cur_min
            self.store.state['last_curiosity'] = now_ts
            self.store.state['curiosity_next_run'] = now_ts + delay_min * 60
        self.store.save()
        if self.vault is not None:
            try:
                self.vault.append_work_log(f'[静默不打扰] {reason}' + (f' (延后 {snooze_min} 分钟)' if snooze_min > 0 else ''))
            except Exception:
                pass
        return {
            'ok': True,
            'status': 'silent',
            '_stay_silent': True,
            'reason': reason,
            'snooze_minutes': snooze_min,
        }

    def _finish_curiosity_cycle(self, status: str, bot_id: str, detail: str = '', snooze_min: int | None = None) -> None:
        self.scheduler_engine.record_curiosity_cycle(status, bot_id, detail=detail, snooze_min=snooze_min)

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
                    self._finish_curiosity_cycle('unanswered_backoff', target_id, '上条主动消息尚未收到主人回复，自动顺延下个周期')
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
                    last_u = float(self.store.state.get('last_user', 0) or 0)
                    self.store.state['curiosity_next_run'] = max(float(self.store.state.get('curiosity_next_run') or 0), last_u + 300.0)
                    self.store.save()
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
                    allowed = (set(bot.get('tools') or [t['function']['name'] for t in TOOLS]) & {
                        'read_file', 'write_file', 'edit_file', 'memory_search', 'web_search', 'web_fetch', 'schedule_task', 'present_choices', 'phone_workspace', 'stay_silent'
                    }) | {'stay_silent'}
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
                        '【不打扰主人优先原则 · 没事就调 `stay_silent` 沉默】：\n'
                        '- 请先快速审视下方的「手机通知栏积攒摘要」、「课表」、「待确认截止事项」与「最近对话摘要」。\n'
                        '- 如果发现有漏网的 VPN/加速器/常驻垃圾通知，请先调用 `phone_workspace(op="notifications", action="add_filter", filter_app="...", filter_keyword="...", reason="...")` 自动将其拉黑屏蔽（禁止在主动搭话中打开或操控任何手机 App 界面）。\n'
                        + (
                            '- 【核心判断】：如果当前没有任何重要新通知、没有临近的紧急截止事项或课程提醒、或者主人刚刚聊完天/正在休息/专注，没有真正有价值的事情需要打扰主人，请**立即调用 `stay_silent(reason="当前无重要事项，保持安静不打扰主人", snooze_minutes=0)`** 静默结束本轮（`snooze_minutes=0` 表示按正常配置间隔等待下次巡检），绝不要为了凑话而强行发消息打扰主人！\n'
                            if not manual
                            else ''
                        )
                        + '- 只有在确实有重要通知提醒、有贴心实用的情报分享，或适合自然关心一句时，才向主人发起简短、真诚、自然的搭话（2~4句话），并调用 `present_choices(choices=[...])` 提供 3~4 个贴心选项。\n'
                        '- 严禁生硬查户口，不要顺着最近报错复读，绝不允许只输出一个句号 `.`。\n'
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
                        step_txt = self._clean_and_filter_dot_text(msg.get('content') or '')
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
                        silent_res = None
                        for c_item, name, args, result, dur in cur_results:
                            if name in ('stay_silent', 'do_not_disturb') or (isinstance(result, dict) and result.get('_stay_silent')):
                                silent_res = result if isinstance(result, dict) else {'reason': 'stay_silent'}
                            if isinstance(result, dict) and result.get('_multimodal_b64'):
                                mm_b64 = str(result.pop('_multimodal_b64') or '')
                                mm_mime = str(result.pop('_multimodal_mime', 'image/jpeg') or 'image/jpeg')
                                mm_cap = str(result.pop('_multimodal_caption', '') or '【工具返回的最新实时画面】')
                                if mm_b64:
                                    cur_mm_frame = {'b64': mm_b64, 'mime': mm_mime, 'caption': mm_cap}
                            trace.append({'tool': name, 'arguments': args, 'result': result, 'duration_ms': dur})
                            messages.append({'role': 'tool', 'tool_call_id': c_item['id'], 'content': json.dumps(result, ensure_ascii=False)})
                        if silent_res is not None:
                            self._finish_curiosity_cycle('silent', target_id, str(silent_res.get('reason') or ''), snooze_min=int(silent_res.get('snooze_minutes') or 0) or None)
                            return {'status': 'silent', 'ok': True, 'silent': True, 'bot_id': target_id, 'reason': str(silent_res.get('reason') or '')}
                        if all(c['function']['name'] == 'present_choices' for c in batch_c) and cur_accumulated:
                            break
                    choice_prompt = self._clean_and_filter_dot_text(self.choices_prompt_var.get() or '')
                    if choice_prompt and all(choice_prompt not in p for p in cur_accumulated):
                        cur_accumulated.append(choice_prompt)
                    text = self._clean_and_filter_dot_text('\n\n'.join(cur_accumulated))
                    if text and is_silent_proactive_output(text):
                        self._finish_curiosity_cycle('silent', target_id, text[:120])
                        return {'status': 'silent', 'ok': True, 'silent': True, 'bot_id': target_id}
                    if not text and (trace or self.choices_var.get()):
                        api_calls += 1
                        summary = await self.llm.complete_silent(
                            messages + [{'role':'user', 'content':'请基于以上探索结果与已提供的选项，直接用自然亲切的语气对主人说一句主动搭话并带一个有趣的小问题（若无需打扰请直接输出 SILENT_OK），不要再调用工具。'}],
                            tools=available_tools or None,
                            big=False,
                            bot_id=target_id,
                            tool_choice='none',
                        )
                        text = self._clean_and_filter_dot_text(summary.get('content') or '')
                        if text and is_silent_proactive_output(text):
                            self._finish_curiosity_cycle('silent', target_id, text[:120])
                            return {'status': 'silent', 'ok': True, 'silent': True, 'bot_id': target_id}
                    text = text or '主人这会儿在忙什么呢？有什么好玩的事跟余聊聊吗？'
                    mode = 'small_brain'
                else:
                    text = '这会儿在忙什么呢？挑一件有点卡住的小事告诉我，余先陪你把第一步捋清。'
                    mode = 'local_prompt'
                    api_calls = 0
                cur_min = max(1, int(getattr(self.store.config, 'curiosity_interval_minutes', 30) or 30))
                self._finish_curiosity_cycle('emitted', target_id, text[:120], snooze_min=cur_min)
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
                fin_cur_turn = self.turn_id_var.get() or ''
                if not self.executions_by_bot.get(target_id):
                    self._clear_bot_stream(target_id, source=src)
                    with contextlib.suppress(Exception):
                        await self.publish({'type': 'stream', 'source': src, 'bot_id': target_id, 'turn_id': fin_cur_turn, 'stream': None, 'seq': self.stream_seq})
                        await self.publish({'type': 'execution', 'source': src, 'bot_id': target_id, 'execution': None, 'seq': self.stream_seq})
                self.choices_var.reset(cur_choices_token)
                self.choices_prompt_var.reset(cur_choices_prompt_token)
                self.stream_prefix_var.reset(cur_prefix_token)
                self.turn_id_var.reset(cur_turn_token)
                self.source_context.reset(src_marker)
                self.room_context.reset(room_marker)
                self.bot_context.reset(marker)
                self.activity = 'thinking' if self.executions_by_bot else self.ready_state()

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
                res_txt = str(result.get('text') or '').strip()
                if result.get('silent') or is_silent_proactive_output(res_txt):
                    self.vault.append_work_log(f'[静默任务] {(goal or "任务池")[:80]} · {res_txt[:160]}')
                    return {
                        'status': 'silent',
                        'ok': True,
                        'silent': True,
                        'bot_id': bot_id,
                        'goal': goal or '整理任务池快照',
                        'summary': res_txt or 'SILENT_OK',
                        'trace': result.get('trace', []),
                    }
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
                fin_res_turn = self.turn_id_var.get() or ''
                cur_res_src = normalize_node_source(self.source_context.get())
                self._clear_bot_stream(bot_id)
                with contextlib.suppress(Exception):
                    await self.publish({'type': 'stream', 'source': cur_res_src, 'bot_id': bot_id, 'turn_id': fin_res_turn, 'stream': None, 'seq': self.stream_seq})
                    await self.publish({'type': 'execution', 'source': cur_res_src, 'bot_id': bot_id, 'execution': None, 'seq': self.stream_seq})
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
                    is_silent_routine = any(
                        kw in goal
                        for kw in ('全程静默', '静默巡检', '静默整理', '不打扰主人', '无新消费或已记过则静默', '不要主动发消息打扰', '不推提醒')
                    )
                    system = (
                        self.harness.static_system_instructions(self.memory_engine.build_static_pinned_block()) + '\n\n'
                        '你的设定：' + self.persona(target_id) + '\n\n'
                        + (
                            f'[{label}触发（后台静默任务）]：请按任务目标静默执行工具操作。若无必须立刻打扰主人的重大异常，执行完毕后请直接调用 `stay_silent(reason="...")` 工具或输出 `SILENT_OK` 及简短工作记录，系统会自动将其写入后台日志且绝不会向主人发送聊天气泡或系统通知。'
                            if is_silent_routine
                            else (
                                f'[{label}触发]：\n'
                                '1. **不打扰优先**：若经核查任务规则发现当前时段无需提醒/不满足触发条件（例如：该时段无课、主人在休息免打扰、账单无新消费、或任务规则指出无需打扰），请**立即调用 `stay_silent(reason="...")` 工具**（或直接输出 `SILENT_OK`），系统将静默收尾，绝不会向主人发气泡或通知！\n'
                                '2. **多步任务必须闭环办完**：若任务需要操作手机/云电脑/查票/脚本，请一步步调用工具直至最终目标 100% 完成，再输出最终汇报内容，绝不可在中间步骤（如“余点一下某按钮”）尚未完成时提前结束！\n'
                                '3. 确实需要提醒或汇报主人时，请直接输出给主人的正文内容（不要调用 notify_user 或 schedule_task，你的最终回复本身就会直接作为提醒推送给主人）。'
                            )
                        )
                    )
                    sched_user_prompt = (
                        '当前时间：' + self._fmt_bj(time.time()) + ' (' + now_iso() + ')\n'
                        '【系统已为你 0 成本预载的核心档案（若以下内容已足够判断：需要提醒则直接输出提醒，无需打扰则直接调用 `stay_silent(reason="...")`，无需再调 read_file / run_bash！若确需调用多个工具，请在同一次响应中并发发出所有 tool_calls）】：\n'
                        + preloaded_md + '\n\n'
                        f'⏰ [{label}到点触发]：{goal}'
                    )
                    # Exclude notify_user and schedule_task from scheduled execution so the LLM never double-notifies or re-schedules the same reminder!
                    allowed = ((set(bot.get('tools') or [t['function']['name'] for t in TOOLS])) - {'notify_user', 'schedule_task'}) | {'stay_silent'}
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
                    sched_retry_count = 0
                    await self._set_stream(text=f'⏰ 正在执行{label}…', speaker_id=target_id, speaker_name=bot['name'])
                    msg = {'content': ''}
                    use_big = (bot.get('engine') == 'big')
                    api_calls = 0
                    for _ in range(60):
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
                        turn_txt = self._clean_and_filter_dot_text(msg.get('content') or '')
                        calls = msg.get('tool_calls') or []
                        if not calls:
                            thought_s = str(msg.get('thought') or msg.get('reasoning_content') or '').strip()
                            if (
                                available_tools
                                and sched_retry_count < 3
                                and (
                                    (not turn_txt and thought_s and self._is_unfulfilled_action_promise('', user_text=goal, trace=trace, thought=thought_s))
                                    or (turn_txt and self._is_unfulfilled_action_promise(turn_txt, user_text=goal, trace=trace, thought=thought_s))
                                )
                            ):
                                sched_retry_count += 1
                                messages.append({'role': 'assistant', 'content': turn_txt or '（准备继续执行）'})
                                messages.append({
                                    'role': 'user',
                                    'content': (
                                        '[系统执行引擎纠偏]：你刚才表示要继续执行下一步操作，但本轮响应中未附带任何 `tool_calls` 工具调用，任务尚未办完！'
                                        '请立即在同一响应中发起所需的 `tool_calls` 继续推进任务直至彻底完成；若经核查无需提醒主人，请调用 `stay_silent(reason="...")`。'
                                    ),
                                })
                                continue
                            if turn_txt:
                                final_turn_text = turn_txt
                                if not accumulated_texts or accumulated_texts[-1] != turn_txt:
                                    accumulated_texts.append(turn_txt)
                                self.stream_prefix_var.set(final_turn_text)
                            break
                        # Intermediate narration accompanying tool calls is NOT the final summary
                        final_turn_text = ''
                        if turn_txt:
                            self.stream_prefix_var.set(turn_txt)
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
                        await self._set_stream(text=turn_txt or f"正在调用 {my_exec['tool']}…", tool=my_exec['tool'], speaker_id=target_id, speaker_name=bot['name'])
                        await self.publish({'type': 'execution', 'execution': my_exec})

                        async def _run_sched_one(c_item, c_name, c_args, r_ent):
                            t0 = asyncio.get_running_loop().time()
                            res = await self.harness.execute(c_name, c_args) if c_name in allowed else {'error': '此助手未启用该工具'}
                            dur = max(1, round((asyncio.get_running_loop().time() - t0) * 1000))
                            return c_item, c_name, c_args, res, dur, r_ent

                        sched_results = await asyncio.gather(*[_run_sched_one(*item) for item in running_entries])
                        silent_sched_res = None
                        for c_item, name, args, result, dur, r_ent in sched_results:
                            if name in ('stay_silent', 'do_not_disturb') or (isinstance(result, dict) and result.get('_stay_silent')):
                                silent_sched_res = result if isinstance(result, dict) else {'reason': 'stay_silent'}
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
                        if silent_sched_res is not None:
                            return {
                                'status': 'silent',
                                'ok': True,
                                'silent': True,
                                'bot_id': target_id,
                                'goal': goal,
                                'summary': str(silent_sched_res.get('reason') or 'SILENT_OK'),
                                'trace': trace,
                                'api_calls': api_calls,
                            }
                        my_exec['tool'] = f'已执行 {len(trace)} 步'
                        self.execution = my_exec
                        await self._set_stream(text=turn_txt or '正在思考…', tool=my_exec['tool'], speaker_id=target_id, speaker_name=bot['name'])
                        await self.publish({'type': 'execution', 'execution': my_exec})
                    raw_sched_text = self._clean_and_filter_dot_text(final_turn_text) or self._clean_and_filter_dot_text('\n\n'.join(accumulated_texts)) or self._clean_and_filter_dot_text(msg.get('content') or '')
                    if not raw_sched_text and trace:
                        api_calls += 1
                        self._next_tool_choice = 'none'
                        sum_msg = await self.complete(
                            messages + [{'role': 'user', 'content': '请基于以上定时任务完整执行结果，直接用自然简练的话输出给主人的最终汇报/提醒内容（若无需打扰主人则直接输出 SILENT_OK），不要再调用工具。'}],
                            tools=available_tools or None, big=use_big
                        )
                        sum_txt = self._clean_and_filter_dot_text(sum_msg.get('content') or '')
                        if sum_txt:
                            raw_sched_text = sum_txt
                    short_goal = goal.split('：')[0].split(':')[0][:60].strip()
                    if is_silent_routine or (raw_sched_text and is_silent_proactive_output(raw_sched_text)):
                        clean_silent = raw_sched_text.replace('SILENT_OK', '').strip()
                        self.vault.append_work_log(f'[静默巡检] {short_goal} · {clean_silent[:180]}')
                        return {
                            'status': 'silent',
                            'ok': True,
                            'silent': True,
                            'bot_id': target_id,
                            'goal': goal,
                            'summary': clean_silent or 'SILENT_OK',
                            'trace': trace,
                            'api_calls': api_calls,
                        }
                    final_text = raw_sched_text or f'⏰ {goal}'
                    self.store.state['curiosity_next_run'] = max(float(self.store.state.get('curiosity_next_run') or 0), time.time() + 180.0)
                    self.store.save()
                    self.vault.log(f'完成{label}：{short_goal}', bot_id=target_id)
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
                    fin_sched_turn = self.turn_id_var.get() or ''
                    cur_sched_src = normalize_node_source(self.source_context.get())
                    self._clear_bot_stream(target_id, source=cur_sched_src)
                    with contextlib.suppress(Exception):
                        await self.publish({'type': 'stream', 'source': cur_sched_src, 'bot_id': target_id, 'turn_id': fin_sched_turn, 'stream': None, 'seq': self.stream_seq})
                        await self.publish({'type': 'execution', 'source': cur_sched_src, 'bot_id': target_id, 'execution': None, 'seq': self.stream_seq})
                    try:
                        self.stream_prefix_var.reset(sched_prefix_token)
                        self.turn_id_var.reset(sched_turn_token)
                    except Exception:
                        pass
        finally:
            self.room_context.reset(room_marker)
            self.bot_context.reset(marker)

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
                if hasattr(self, 'blackboard_engine'):
                    await self.blackboard_engine.tick()
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
                has_api = bool((c.effective_api_key(big=True) or c.effective_api_key(big=False)) and (c.effective_model(big=True) or c.effective_model(big=False)))
                from core.turn_ledger import TurnLedgerManager
                # 1. Fire due one-shot timers per-bot (queued behind active turns of THAT bot; never blocked by other nodes/bots!)
                if not self.lock.locked():
                    for t in s.get('timers', []):
                        if t.get('done') or float(t.get('due_at', 0)) > now:
                            continue
                        t_bot = t.get('bot_id', s['primary_bot_id'])
                        if self.is_node_bot_busy(t_bot, 'phone'):
                            continue
                        t['done'] = True
                        t['fired_at'] = time.time()
                        self.store.save()
                        if has_api:
                            t0_tm = time.perf_counter()
                            try:
                                tm_res = await self.run_scheduled(t['goal'], bot_id=t_bot, label='定时闹钟')
                                TurnLedgerManager.append_run_log(
                                    self.store,
                                    {
                                        'kind': 'timer', 'task_id': t.get('id', ''), 'bot_id': t_bot, 'label': '定时闹钟',
                                        'goal': str(t.get('goal') or '')[:120], 'status': 'ok',
                                        'steps': len((tm_res or {}).get('trace') or []) if isinstance(tm_res, dict) else 0,
                                        'duration_ms': int(round((time.perf_counter() - t0_tm) * 1000)), 'ts': now_iso(),
                                    },
                                )
                            except Exception as tm_err:
                                t['last_error'] = str(tm_err)[:240]
                                TurnLedgerManager.append_run_log(
                                    self.store,
                                    {'kind': 'timer', 'task_id': t.get('id', ''), 'bot_id': t_bot, 'status': 'error', 'error': str(tm_err)[:200], 'ts': now_iso()},
                                )
                                await self.emit(f'⏰ [定时闹钟执行异常告警] 任务「{str(t.get("goal") or "")[:60]}」触发报错：{str(tm_err)[:180]}', 'error', track=False, bot_id=t_bot, source='phone')
                            self.store.save()
                        self.wake.set()
                        break
                # 2. Fire due routines per-bot with 4h missed-run catch-up, quiet-hour deferral, exponential skip backoff & circuit breaker!
                if not self.lock.locked():
                    now = time.time()
                    for r in s['routines']:
                        if not r.get('enabled', True):
                            continue
                        if not r.get('next_run'):
                            r['next_run'] = self._compute_next_routine_run(r, now)
                            r['next_run_bj'] = self._fmt_bj(r['next_run'])
                            self.store.save()
                        cb_until = float(r.get('circuit_broken_until') or 0)
                        if cb_until > now:
                            continue
                        is_clock_routine = bool(r.get('daily_time'))
                        r_bot = r.get('bot_id', 'companion')
                        if not is_clock_routine and not r.get('allow_quiet') and not r.get('quiet_ok') and self.quiet():
                            deferred_ts, _ = self.scheduler_engine.defer_out_of_quiet_hours(now)
                            if float(r.get('next_run') or 0) <= now:
                                r['next_run'] = deferred_ts
                                r['next_run_bj'] = self._fmt_bj(deferred_ts)
                                r['deferred_reason'] = 'quiet_hours'
                                r['last_status'] = 'quiet_deferred'
                                TurnLedgerManager.append_run_log(
                                    self.store,
                                    {'kind': 'routine', 'task_id': r.get('id', ''), 'bot_id': r_bot, 'goal': str(r.get('goal') or '')[:120], 'status': 'quiet_deferred', 'next_run_bj': r['next_run_bj'], 'ts': now_iso()},
                                )
                                self.store.save()
                            continue
                        due_ts = float(r['next_run'])
                        if now < due_ts:
                            continue
                        overdue_sec = max(0.0, now - due_ts)
                        is_wakeup_goal = any(k in str(r.get('goal') or '') for k in ('叫醒', '起床'))
                        max_catchup_sec = 1500.0 if is_wakeup_goal else 14400.0
                        if overdue_sec <= max_catchup_sec and self.is_node_bot_busy(r_bot, 'phone'):
                            continue
                        r.pop('deferred_reason', None)
                        orig_due_bj = self._fmt_bj(due_ts)
                        if overdue_sec > max_catchup_sec:
                            skips = int(r.get('consecutive_skips') or 0) + 1
                            r['consecutive_skips'] = skips
                            backoff_s = min(3600.0, 30.0 * (2 ** min(6, skips - 1)))
                            cand_next = max(self._compute_next_routine_run(r, now + 2), now + backoff_s)
                            if skips >= 5:
                                r['circuit_broken_until'] = now + 1800.0
                            r['next_run'] = cand_next
                            r['next_run_bj'] = self._fmt_bj(cand_next)
                            r['last_status'] = 'expired_skip'
                            r['last_late_seconds'] = int(overdue_sec)
                            TurnLedgerManager.append_run_log(
                                self.store,
                                {'kind': 'routine', 'task_id': r.get('id', ''), 'bot_id': r_bot, 'goal': str(r.get('goal') or '')[:120], 'status': 'expired_skip', 'late_seconds': int(overdue_sec), 'next_run_bj': r['next_run_bj'], 'ts': now_iso()},
                            )
                            if skips <= 2 or skips % 5 == 0:
                                self.vault.append_work_log(f"[定时例行过期顺延 x{skips}] {r.get('id')} 原定 {orig_due_bj}（已超窗 {int(overdue_sec // 60)} 分钟），已推进 next_run -> {r['next_run_bj']}")
                            self.store.save()
                            continue
                        is_late = overdue_sec >= 120.0
                        r['last_run'] = now
                        r['consecutive_skips'] = 0
                        r.pop('circuit_broken_until', None)
                        r['next_run'] = self._compute_next_routine_run(r, now + 2)
                        r['next_run_bj'] = self._fmt_bj(r['next_run'])
                        r['last_late_seconds'] = int(overdue_sec)
                        self.store.save()
                        if has_api:
                            r_label = f"定点例行 ({r['daily_time']})" if is_clock_routine else f"例行巡检 ({r.get('interval_hours', 24)}h)"
                            eff_goal = (
                                f"[⏰ 延迟补跑说明：本任务原定 {orig_due_bj} 触发，因前序任务占用延迟 {int(overdue_sec // 60)} 分钟补跑]\n{r['goal']}"
                                if is_late
                                else r['goal']
                            )
                            t0_rt = time.perf_counter()
                            try:
                                rt_res = await self.run_scheduled(eff_goal, bot_id=r_bot, label=r_label + ('·补跑' if is_late else ''))
                                rt_silent = bool(isinstance(rt_res, dict) and rt_res.get('silent'))
                                r['last_status'] = 'silent' if rt_silent else ('late_ok' if is_late else 'ok')
                                r['last_error'] = ''
                                TurnLedgerManager.append_run_log(
                                    self.store,
                                    {
                                        'kind': 'routine', 'task_id': r.get('id', ''), 'bot_id': r_bot, 'label': r_label,
                                        'goal': str(r.get('goal') or '')[:120], 'status': r['last_status'],
                                        'late_seconds': int(overdue_sec),
                                        'steps': len((rt_res or {}).get('trace') or []) if isinstance(rt_res, dict) else 0,
                                        'duration_ms': int(round((time.perf_counter() - t0_rt) * 1000)), 'ts': now_iso(),
                                    },
                                )
                            except Exception as rt_err:
                                r['last_status'] = 'error'
                                r['last_error'] = str(rt_err)[:240]
                                self.vault.append_work_log(f"[定时例行异常] {r.get('id')} ({r_label}): {str(rt_err)[:200]}")
                                TurnLedgerManager.append_run_log(
                                    self.store,
                                    {
                                        'kind': 'routine', 'task_id': r.get('id', ''), 'bot_id': r_bot, 'label': r_label,
                                        'goal': str(r.get('goal') or '')[:120], 'status': 'error', 'error': str(rt_err)[:200], 'ts': now_iso(),
                                    },
                                )
                                await self.emit(
                                    f'⏰ [定时例行执行异常告警] 「{r_label}」（{str(r.get("goal") or "")[:50]}…）执行报错：{str(rt_err)[:180]}（已自动顺延下次触发至 {r["next_run_bj"]}）',
                                    'error', track=False, bot_id=r_bot, source='phone',
                                )
                            self.store.save()
                        self.wake.set()
                        break
                # 2.5 Passive background notification convergence every 75s when phone is online and idle
                if not self.is_any_bot_busy() and self.is_phone_online() and now - self._last_notif_bg_poll >= 75.0 and not self.executions_by_bot:
                    await self._poll_and_converge_notifications(timeout=4.5)
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
                cur_interval_s = max(1, int(getattr(c, 'curiosity_interval_minutes', 30) or 30)) * 60
                cur_due = float(s.get('curiosity_next_run') or 0) or (float(s.get('last_curiosity') or 0) + cur_interval_s)
                last_u_ts = float(s.get('last_user') or 0)
                if len(s['pending']) < 3 and now >= cur_due:
                    if last_u_ts > 0 and (now - last_u_ts) < 300:
                        s['curiosity_next_run'] = max(cur_due, last_u_ts + 300.0)
                        self.store.save()
                    else:
                        curiosity_targets = []
                        for node_k in ('phone', 'phone2'):
                            for b in s.get('assistants', []):
                                if resolve_bot_for_source(b, node_k).get('curiosity_enabled'):
                                    curiosity_targets.append((b['id'], node_k))
                        if curiosity_targets:
                            target_bot_id, target_src = curiosity_targets[0]
                            cur_res = await self.curiosity(bot_id=target_bot_id, source=target_src)
                            if isinstance(cur_res, dict) and cur_res.get('suppressed'):
                                sup_reason = str(cur_res.get('reason') or '')
                                delay_s = 120.0 if sup_reason == 'bot_busy' else float(cur_interval_s)
                                s['curiosity_next_run'] = max(float(s.get('curiosity_next_run') or 0), now + delay_s)
                                self.store.save()
                        else:
                            s['curiosity_next_run'] = now + cur_interval_s
                            self.store.save()
                ready = next((x for x in s['opportunities'] if x['ready'] and not x['done']), None)
                if ready and has_api and now - s['last_user'] >= c.idle_research_minutes * 60:
                    await self.research()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.store.state['last_error'] = str(e)[:250]
                self.store.save()
                await self.publish({'type':'engine_error', 'message':str(e)[:250]})

    async def _tool_system_status(self, args: dict) -> dict:
        import shutil
        req_op = str(args.get('op') or args.get('action') or 'status').strip().lower()
        if req_op not in ('status', 'doctor', 'context', 'all', 'check', ''):
            return {
                'ok': False,
                'error': f'未知的 system_status 操作: {req_op}（支持 status / doctor / context / all）',
            }
        bot_id = self.bot_context.get() or 'companion'
        cur_src = normalize_node_source(self.source_context.get())
        last_pt = int(self.last_prompt_tokens.get(bot_id, 0) or 0)
        last_ct = int(self.last_cached_tokens.get(bot_id, 0) or 0)
        hit_pct = int(round((last_ct * 100.0) / max(1, last_pt))) if last_pt > 0 else 0
        limit_tok = self.memory_engine.get_threshold_tokens()
        rem_tok = max(0, limit_tok - last_pt)
        cur_trace_steps = len((self.execution or {}).get('trace') or [])
        vault_files = self.vault.files() if self.vault is not None else []
        idx_lines = 0
        if self.vault is not None and (self.vault.root / 'INDEX.md').exists():
            idx_lines = sum(1 for ln in (self.vault.root / 'INDEX.md').read_text('utf-8', errors='ignore').splitlines() if ln.strip().startswith('- [['))
        tess_ok = bool(shutil.which('tesseract'))
        return {
            'ok': True,
            'op': req_op or 'status',
            'node': cur_src,
            'bot_id': bot_id,
            'context_budget': {
                'last_prompt_tokens': last_pt,
                'last_cached_tokens': last_ct,
                'cache_hit_pct': hit_pct,
                'auto_compact_limit_tokens': limit_tok,
                'remaining_tokens_before_compact': rem_tok,
                'usage_pct': round((last_pt * 100.0) / max(1, limit_tok), 1),
                'current_turn_tool_steps': cur_trace_steps,
                'soft_step_budget_per_turn': 15,
                'vault_markdown_files_count': len(vault_files),
                'vault_index_entries_count': idx_lines,
            },
            'channels_doctor': {
                'vision_read_file': {
                    'ok': True,
                    'active_mode': 'multimodal_vision_direct',
                    'multimodal_passthrough': True,
                    'pil_metadata': True,
                    'ocr_fallback_used': False,
                    'local_tesseract_binary_installed': tess_ok,
                },
                'send_image': {'ok': True, 'route': '/workspace-media/uploads/'},
                'memory_vault': {
                    'ok': self.vault is not None,
                    'allowed_tools': ['read_file', 'write_file', 'edit_file', 'delete_file', 'move_file', 'list_dir', 'grep_search', 'glob_files', 'memory_search'],
                    'bash_access': 'isolated_by_landlock_by_design (use file tools instead of run_bash for memory_vault)',
                    'allowed_extensions': ['.md'],
                },
                'phone_online': bool(self.is_phone_online()),
                'pc_online': bool(self.is_dsh_online()) if hasattr(self, 'is_dsh_online') else False,
            },
            **self.scheduler_engine.build_system_status_extras(cur_src, bot_id, last_pt, last_ct, hit_pct),
        }
