"""Android Virtual Display Workspace Controller (`phone_workspace` & `phone_macro`).
Vision + Accessibility dual passthrough (`@e1..@eN` + 0..1000 annotated screenshot via `image_url`).
"""
from __future__ import annotations
import asyncio, base64, json, re, secrets, time
from storage import normalize_node_source
from core.vfs_router import apply_authoritative_tree, enrich_phone_frame_contract, normalize_single_tree_frame, reconcile_device_root_status
from core.vision_engine import annotate_tap_crosshair, draw_vision_ruler_overlay

class PhoneControllerMixin:
    def resolve_phone_command(self, event: dict):
        req_id = str(event.get('request_id') or '')
        fut = self.phone_pending.pop(req_id, None)
        if fut and not fut.done():
            fut.set_result(event.get('result') or {'ok': True})

    async def _raw_phone_rpc(self, op: str, args: dict, timeout: float = 32.0) -> dict:
        bot_id = self.bot_context.get()
        cur_src = normalize_node_source(args.get('source') if args.get('source') in ('phone', 'phone2', '安卓一', '安卓二') else (self.source_context.get() or 'phone'))
        if cur_src not in ('phone', 'phone2'):
            cur_src = 'phone'
        req_id = 'ph-' + secrets.token_hex(6)
        fut = asyncio.get_running_loop().create_future()
        self.phone_pending[req_id] = fut
        await self.publish({ 'type': 'phone_workspace_command', 'request_id': req_id, 'bot_id': bot_id, 'source': cur_src, 'op': op, 'args': args, 'timestamp': time.time(), })
        try:
            res = await asyncio.wait_for(fut, timeout=timeout)
            return res if isinstance(res, dict) else {'ok': True}
        except asyncio.TimeoutError:
            self.phone_pending.pop(req_id, None)
            return { 'ok': False, 'timed_out': True, 'op': op, 'error': '手机端在超时时间内未返回执行结果或界面内容（该操作未确认完成，严禁假设成功或编造屏幕内容；请重试一次或提醒用户打开手机端 App 保持在线）', }

    @staticmethod
    def _annotate_tap_crosshair(img_bytes: bytes, tap_x: int, tap_y: int, width: int, height: int) -> bytes:
        return annotate_tap_crosshair(img_bytes, tap_x, tap_y, width, height)

    @staticmethod
    def _draw_vision_ruler_overlay(img_bytes: bytes, a11y_summary: str = '', screen_w: int = 0, screen_h: int = 0) -> tuple[bytes, int, int]:
        return draw_vision_ruler_overlay(img_bytes, a11y_summary=a11y_summary, screen_w=screen_w, screen_h=screen_h)

    async def _auto_release_phone_if_used(self):
        self._used_phone_in_turn = False
        self._vdisplay_currently_open = False
        self._phone_to_foreground_in_turn = False

    @staticmethod
    def _resolve_phone_grid_or_norm(args: dict, width: int, height: int) -> tuple[int, int] | None:
        """Convert grid (`A1..F10`) or normalized 0..1000 (`nx`, `ny`) into pixel coordinates `(px, py)`."""
        grid_raw = str(args.get('grid') or '').strip().upper()
        if grid_raw and len(grid_raw) >= 2 and grid_raw[0].isalpha() and grid_raw[1:].replace('.', '', 1).isdigit():
            col_idx, row_val = max(0, min(5, ord(grid_raw[0]) - ord('A'))), max(0.0, min(9.9, float(grid_raw[1:]) - 1.0))
            return max(0, min(width - 1, int(((col_idx + 0.5) / 6.0) * width))), max(0, min(height - 1, int(((row_val + 0.5) / 10.0) * height)))
        nx, ny = args.get('nx'), args.get('ny')
        if nx is not None and ny is not None:
            try:
                nx_f, ny_f = float(nx), float(ny)
                if nx_f < 0 or nx_f > 1000 or ny_f < 0 or ny_f > 1000:
                    args['_coord_clamped'], args['_raw_nx'], args['_raw_ny'] = True, nx, ny
                return max(0, min(width - 1, int(round(max(0.0, min(1000.0, nx_f)) * width / 1000.0)))), max(0, min(height - 1, int(round(max(0.0, min(1000.0, ny_f)) * height / 1000.0))))
            except (ValueError, TypeError):
                pass
        return None

    @staticmethod
    def _is_user_holding_or_refusing_phone(user_text: str, was_blocked: bool = False) -> bool:
        return False

    def _parse_and_cache_phone_nodes(self, res_dict: dict) -> list[dict]:
        if not isinstance(res_dict, dict):
            return getattr(self, '_last_phone_nodes', []) or []
        w = int(res_dict.get('width') or getattr(self, '_last_phone_size', (1080, 2400))[0] or 1080)
        h = int(res_dict.get('height') or getattr(self, '_last_phone_size', (1080, 2400))[1] or 2400)
        by_ref: dict[str, dict] = {}
        raw_list = res_dict.get('nodes')
        if isinstance(raw_list, list):
            for idx, item in enumerate(raw_list, 1):
                if not isinstance(item, dict):
                    continue
                ref_s = str(item.get('ref') or f'@e{idx}').strip()
                if not ref_s.startswith('@'):
                    ref_s = '@' + ref_s
                cx = item.get('x') if item.get('x') is not None else item.get('center_x')
                cy = item.get('y') if item.get('y') is not None else item.get('center_y')
                nx_v, ny_v = item.get('nx'), item.get('ny')
                if (cx is None or cy is None) and nx_v is not None and ny_v is not None:
                    cx = int(round(float(nx_v) * w / 1000.0))
                    cy = int(round(float(ny_v) * h / 1000.0))
                by_ref[ref_s.lower()] = {
                    'ref': ref_s,
                    'role': str(item.get('role') or item.get('className') or item.get('class') or '').split('.')[-1],
                    'text': str(item.get('text') or item.get('label') or '').strip(),
                    'desc': str(item.get('desc') or item.get('contentDescription') or '').strip(),
                    'hint': str(item.get('hint') or item.get('hintText') or '').strip(),
                    'id': str(item.get('id') or item.get('view_id') or item.get('resourceId') or '').strip(),
                    'clickable': bool(item.get('clickable', False)),
                    'editable': bool(item.get('editable', False)),
                    'x': int(float(cx)) if cx is not None else None,
                    'y': int(float(cy)) if cy is not None else None,
                }
        tree_txt = str(res_dict.get('ui_tree') or res_dict.get('tree') or res_dict.get('summary') or '')
        for line in tree_txt.splitlines():
            m_ref = re.match(r'^\s*(@[ev]\d+)\b(.*)$', line.strip(), flags=re.IGNORECASE)
            if not m_ref:
                continue
            ref_s, rest = m_ref.group(1), m_ref.group(2)
            cur = by_ref.get(ref_s.lower(), {'ref': ref_s, 'role': '', 'text': '', 'desc': '', 'hint': '', 'id': '', 'clickable': False, 'editable': False, 'x': None, 'y': None})
            m_role = re.search(r'\[([A-Za-z0-9_.$]+)\]', rest)
            if m_role and not cur['role']:
                cur['role'] = m_role.group(1).split('.')[-1]
            m_id = re.search(r'\bid=([^\s"\[\]]+)', rest)
            if m_id and not cur['id']:
                cur['id'] = m_id.group(1).strip()
            m_desc = re.search(r'\bdesc="([^"]*)"', rest)
            if m_desc and not cur['desc']:
                cur['desc'] = m_desc.group(1).strip()
            m_hint = re.search(r'\bhint="([^"]*)"', rest)
            if m_hint and not cur['hint']:
                cur['hint'] = m_hint.group(1).strip()
            rest_no_kv = re.sub(r'\b(?:id|desc|hint)="[^"]*"', '', rest)
            m_txt = re.search(r'"([^"]+)"', rest_no_kv)
            if m_txt and not cur['text']:
                cur['text'] = m_txt.group(1).strip()
            if re.search(r'\bclick\b', rest, flags=re.IGNORECASE):
                cur['clickable'] = True
            if re.search(r'\bedit\b', rest, flags=re.IGNORECASE) or cur['role'] in ('EditText', 'AutoCompleteTextView', 'SearchBox'):
                cur['editable'] = True
            if cur['x'] is None or cur['y'] is None:
                m_norm = re.search(r'norm=\(\s*(\d+)\s*,\s*(\d+)\s*\)', rest)
                if m_norm:
                    cur['x'] = int(round(float(m_norm.group(1)) * w / 1000.0))
                    cur['y'] = int(round(float(m_norm.group(2)) * h / 1000.0))
                else:
                    m_b = re.search(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', rest)
                    if m_b:
                        cur['x'] = (int(m_b.group(1)) + int(m_b.group(3))) // 2
                        cur['y'] = (int(m_b.group(2)) + int(m_b.group(4))) // 2
            by_ref[ref_s.lower()] = cur
        parsed = list(by_ref.values())
        if parsed:
            self._last_phone_nodes = parsed
        return parsed

    def _match_phone_node(self, target_q: str = '', ref_q: str = '', id_q: str = '', for_type: bool = False) -> dict | None:
        nodes: list[dict] = getattr(self, '_last_phone_nodes', None) or []
        if not nodes:
            return None
        r_norm, t_norm, i_norm = str(ref_q or '').strip(), str(target_q or '').strip(), str(id_q or '').strip()
        for cand in (r_norm, t_norm, i_norm):
            if cand and re.fullmatch(r'@?[ev]\d+', cand, flags=re.IGNORECASE):
                r_norm = cand if cand.startswith('@') else ('@' + cand)
                if t_norm == cand:
                    t_norm = ''
                if i_norm == cand:
                    i_norm = ''
                break
        if r_norm:
            for n in nodes:
                if str(n.get('ref') or '').lower() == r_norm.lower():
                    return n
        queries = [q for q in (i_norm, t_norm) if q]
        best_node, best_score = None, 0
        for n in nodes:
            nid = str(n.get('id') or '').strip()
            nid_short = nid.split('/')[-1] if '/' in nid else nid
            ntxt, ndesc, nhint, nrole = str(n.get('text') or ''), str(n.get('desc') or ''), str(n.get('hint') or ''), str(n.get('role') or '')
            is_edit_like = bool(n.get('editable') or nrole in ('EditText', 'AutoCompleteTextView', 'SearchBox') or any(k in nid.lower() for k in ('search', 'input', 'edit', 'form_q', 'query')))
            score = 0
            for q in queries:
                ql = q.lower()
                if nid and (nid.lower() == ql or nid_short.lower() == ql):
                    score = max(score, 1200)
                elif any(f and f.lower() == ql for f in (ntxt, nhint, ndesc)):
                    score = max(score, 1000)
                elif nid and ql in nid.lower():
                    score = max(score, 650)
                elif any(f and ql in f.lower() for f in (ntxt, nhint, ndesc)):
                    score = max(score, 500)
            if score > 0 and for_type and is_edit_like:
                score += 350
            if score > best_score:
                best_score, best_node = score, n
        if best_node is not None:
            return best_node
        if for_type and not r_norm and not queries:
            for n in nodes:
                nid, nrole = str(n.get('id') or '').lower(), str(n.get('role') or '')
                if n.get('editable') or nrole in ('EditText', 'AutoCompleteTextView', 'SearchBox') or any(k in nid for k in ('search_src_text', 'sb_form_q', 'search', 'input', 'edit')):
                    return n
        return None

    def _format_available_nodes_hint(self, max_items: int = 10) -> str:
        nodes: list[dict] = getattr(self, '_last_phone_nodes', None) or []
        if not nodes:
            return '当前页面无已缓存的可交互节点，请先调用 phone_workspace(op="inspect") 刷新节点树'
        parts = []
        for n in nodes[:max_items]:
            lbl = n.get('text') or n.get('hint') or n.get('desc') or ''
            id_s = f" id={n['id'].split('/')[-1]}" if n.get('id') else ''
            flags = ','.join(k for k, v in (('click', n.get('clickable')), ('edit', n.get('editable'))) if v)
            parts.append(f"{n.get('ref')} [{n.get('role') or 'View'}] \"{lbl}\"{id_s}{' ' + flags if flags else ''}")
        return '当前可用节点: ' + '; '.join(parts)

    async def _poll_and_converge_notifications(self, app_filter: str = '', limit: int = 50, timeout: float = 5.5) -> dict:
        self._last_notif_bg_poll = time.time()
        try:
            raw_res = await self._raw_phone_rpc( 'notifications', {'op': 'notifications', 'app': app_filter, 'limit': max(20, min(80, limit))}, timeout=timeout, )
            if isinstance(raw_res, dict) and isinstance(raw_res.get('notifications'), list):
                ingested = self.notification_hub.ingest_raw_notifications(raw_res['notifications'])
                return {'ok': True, 'listener_connected': raw_res.get('listener_connected', True), **ingested}
        except Exception:
            pass
        return {'ok': False, 'inbox_total': len(self.store.state.get('notification_inbox', []))}

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
        _op_map = {'launch': 'open', 'tap_node': 'tap', 'click_node': 'tap', 'tap_and_type': 'type', 'type_node': 'type', 'input_node': 'type', 'text': 'type', 'key': 'press', 'swipe': 'scroll', 'ui_tree': 'inspect', 'screenshot': 'inspect', 'capture': 'inspect', 'delay': 'wait', 'sleep': 'wait'}
        _valid_macro_ops = {'open', 'open_uri', 'inspect', 'tap', 'type', 'scroll', 'scroll_to', 'press', 'long_press', 'wait', 'send_screenshot', 'pull_file', 'push_file', 'list_files', 'notifications', 'device_info', 'shell', 'status', 'apps', 'release'}
        dirty_macros = False
        for _m in macros.values():
            if isinstance(_m, dict) and isinstance(_m.get('steps'), list):
                clean_s = []
                for _st in _m['steps']:
                    if not isinstance(_st, dict):
                        continue
                    s_op = str(_st.get('op') or '').strip().lower()
                    if s_op in _op_map:
                        _st['op'] = _op_map[s_op]
                        s_op = _st['op']
                        dirty_macros = True
                    if s_op in _valid_macro_ops:
                        clean_s.append(_st)
                    else:
                        dirty_macros = True
                _m['steps'] = clean_s
        if dirty_macros:
            try:
                macro_file.write_text(json.dumps(macros, ensure_ascii=False, indent=2), encoding='utf-8')
            except Exception:
                pass
        if action == 'list':
            return {'ok': True, 'macros': macros}
        name = str(args.get('name') or '').strip()
        if not name:
            return {'ok': False, 'error': '请提供宏名称 name'}
        if action == 'save':
            steps = args.get('steps') if isinstance(args.get('steps'), list) else []
            if not steps:
                return {'ok': False, 'error': 'steps 不能为空'}
            norm_steps = []
            for idx_st, st in enumerate(steps[:30], 1):
                if not isinstance(st, dict):
                    return {'ok': False, 'error': f'第 {idx_st} 步格式错误：必须为 JSON 对象'}
                raw_sop = str(st.get('op') or '').strip().lower()
                sop = _op_map.get(raw_sop, raw_sop)
                if not sop or sop not in _valid_macro_ops:
                    return {'ok': False, 'error': f'第 {idx_st} 步包含未知或不支持的 op="{raw_sop}"；合法 op 包括: {sorted(_valid_macro_ops)}'}
                norm_steps.append({**st, 'op': sop})
            macros[name] = {'name': name, 'description': str(args.get('description') or ''), 'steps': norm_steps, 'updated_at': time.time()}
            macro_file.write_text(json.dumps(macros, ensure_ascii=False, indent=2), encoding='utf-8')
            return {'ok': True, 'saved': True, 'name': name, 'steps_count': len(norm_steps)}
        if action == 'delete':
            existed = name in macros
            macros.pop(name, None)
            macro_file.write_text(json.dumps(macros, ensure_ascii=False, indent=2), encoding='utf-8')
            return {'ok': existed, 'deleted': existed, 'existed': existed, 'name': name, **({'error': f'未找到手机动作宏 "{name}"'} if not existed else {})}
        if action == 'run':
            item = macros.get(name)
            if not item:
                return {'error': f'未找到手机动作宏 "{name}"'}
            raw_steps = item.get('steps', [])
            tpl_vars = args.get('vars') if isinstance(args.get('vars'), dict) else {}
            if tpl_vars:
                steps_str = json.dumps(raw_steps, ensure_ascii=False)
                for k, v in tpl_vars.items():
                    steps_str = steps_str.replace(f'{{{{{k}}}}}', str(v))
                try:
                    raw_steps = json.loads(steps_str)
                except Exception:
                    pass
            return await self._tool_phone_workspace({'op': 'batch', 'steps': raw_steps})
        return {'error': f'未知 phone_macro action: {action}'}

    async def _tool_phone_workspace(self, args: dict):
        cur_turn = self.turn_id_var.get()
        if cur_turn and cur_turn in self._stopped_turn_ids:
            raise asyncio.CancelledError()
        if (self.source_context.get() or 'phone') in ('pc', 'pc2'):
            return { 'ok': False, 'error': '你当前位于电脑端，不能直接调用安卓端的 phone_workspace 工具！请调用 blackboard(action="post", mentions=["@companion"], text="...") 在黑板异步唤醒手机端助理代为执行。', }
        op = str(args.get('op') or 'status').strip().lower()
        orig_requested_op = op
        scroll_dir = ''
        if op in ('promote', 'expand_to_foreground', 'to_foreground', 'fullscreen'):
            self._used_phone_in_turn = False
            self._phone_to_foreground_in_turn = True
            res = await self._raw_phone_rpc('promote', {'op': 'promote'})
            return {**(res if isinstance(res, dict) else {}), 'ok': bool(isinstance(res, dict) and res.get('ok', True)), 'op': 'promote', 'note': '已将虚拟屏当前页面无损切至主屏前台（零重启），请告知主人完成支付/验证'}
        if op in ('pull_back', 'to_virtual', 'pull_to_virtual'):
            await self._raw_phone_rpc('pull_back', {'op': 'pull_back', 'package': str(args.get('package') or args.get('target') or '').strip()})
            await asyncio.sleep(0.35)
            return await self._tool_phone_workspace({'op': 'ui_tree', 'refresh': True})
        if op in ('release', 'close', 'destroy', 'stop_display'):
            keep_alive = bool(args.get('keep_alive', False))
            destroy_flag = (not keep_alive) if 'destroy' not in args else bool(args.get('destroy'))
            self._used_phone_in_turn = self._phone_to_foreground_in_turn = False
            self._vdisplay_currently_open = not destroy_flag
            rel_res = await self._raw_phone_rpc('release', {'op': 'release', 'destroy': destroy_flag, 'user_close': destroy_flag, 'keep_alive': keep_alive, 'to_foreground': False})
            return {**(rel_res if isinstance(rel_res, dict) else {}), 'ok': True, 'op': orig_requested_op, 'released': destroy_flag, 'destroyed': destroy_flag, 'display_active': not destroy_flag, 'created': not destroy_flag, 'note': '已彻底销毁并关闭手机独立虚拟屏与桌面悬浮小窗（主屏应用不受任何影响）。' if destroy_flag else '虚拟屏已保留在后台驻留。'}
        notif_ops = ( 'notifications', 'get_notifications', 'recent_notifications', 'notifs', 'notification', 'add_filter', 'remove_filter', 'list_filters', 'clear_inbox', )
        non_gui_ops = ( 'status', 'device_info', 'phone_info', 'battery', 'shell', 'bash', 'cmd', 'exec', 'pull_file', 'push_file', 'list_files', 'files', 'apps', 'connect', 'delay', 'wait', 'sleep', 'release', 'auto_release', 'promote', 'pull_back', *notif_ops, )
        if self.activity == 'curiosity' and op not in notif_ops:
            return {'error': '后台主动关心期间仅允许操作通知栏摘要与过滤 (op=notifications)，禁止操控虚拟屏应用界面，以免与主人的前台操作冲突'}
        if op not in non_gui_ops:
            self._used_phone_in_turn = True
        if op in ('shell', 'bash', 'cmd', 'exec'):
            cmd_str = str(args.get('command') or args.get('text') or args.get('target') or args.get('goal') or '').strip()
            if not cmd_str:
                return {'ok': False, 'error': 'phone_workspace(op="shell") 需要提供 command 参数'}
            if re.search(r'\b(reboot|poweroff|shutdown|rm\s+-rf\s+/data|mkfs|dd\s+if=)\b', cmd_str, flags=re.IGNORECASE):
                return {'ok': False, 'error': '安全保护已拦截高危系统关机/格式化指令'}
            if re.search(r'\bcmd\s+uimode\b|\bservice\s+call\s+uimode\b|\bsettings\s+put\s+(?:system|secure|global)\s+(?:ui_night_mode|screen_brightness|screen_off_timeout|font_scale|user_rotation|accelerometer_rotation|default_input_method|enabled_input_methods)\b', cmd_str, flags=re.IGNORECASE):
                return {'ok': False, 'error': '安全沙箱已拦截系统全局设置修改指令（严禁通过 shell 修改主人手机的深浅色 Night mode、亮度、字体缩放或输入法等系统设置）'}
            if re.search(r'\bmonkey\b|(?:--display|-d)\s+0\b', cmd_str, flags=re.IGNORECASE):
                return {'ok': False, 'error': '安全沙箱已拦截指向主屏幕 (Display 0) 或 monkey 的指令，严禁触碰主人主屏幕 Display 0；请使用 phone_workspace(op="open"|"tap") 在独立虚拟屏内操作'}
            cur_disp_env = int(getattr(self, '_last_phone_display_id', 0) or 0)
            rpc_cmd_str = f'export DISPLAY_ID={cur_disp_env}; {cmd_str}' if (cur_disp_env > 0 and 'DISPLAY_ID=' not in cmd_str) else cmd_str
            raw_sh = await self._raw_phone_rpc('shell', {'op': 'shell', 'command': rpc_cmd_str}, timeout=35.0)
            if isinstance(raw_sh, dict):
                is_ok = bool(raw_sh.get('ok', 'error' not in raw_sh)) and not bool(raw_sh.get('error'))
                raw_ec = raw_sh.get('exit_code')
                eff_ec = -1 if (not is_ok and (raw_ec is None or int(raw_ec) == 0)) else int(raw_ec if raw_ec is not None else (0 if is_ok else -1))
                out_text = str(raw_sh.get('output') if 'output' in raw_sh else raw_sh.get('stdout', ''))
                if 'screencap' in cmd_str.lower() and ('status: -2' in out_text.lower() or eff_ec != 0):
                    out_text = (out_text + '\n[提示：独立虚拟屏启用了 FLAG_SECURE 防泄漏保护，shell screencap 无法直接截取；请改用 phone_workspace(op="inspect") 直接查看高清实时画面，或 phone_workspace(op="send_screenshot") 发送干净截图给主人。]').strip()
                clean_sh: dict = {'ok': is_ok, 'op': 'shell', 'output': out_text, 'exit_code': eff_ec}
                if raw_sh.get('cwd'):
                    clean_sh['cwd'] = str(raw_sh['cwd'])
                if raw_sh.get('error'):
                    err_s = re.sub(r'(Root\s*工具进程握手未完成)\s*:\s*ok\b', r'\1', str(raw_sh['error'])).strip()
                    clean_sh['error'] = err_s
                    if '握手未完成' in err_s:
                        clean_sh['root_state'] = 'handshake_failed'
                        clean_sh['hint'] = 'Root 守护进程握手未完成（常见于热更新 APK 或 su 会话失效）：请强停重启手机端 App 或检查 Magisk/KernelSU 授权与 Zygisk'
                if raw_sh.get('stderr'):
                    clean_sh['stderr'] = str(raw_sh['stderr'])
                return clean_sh
            return raw_sh
        if op in ('device_info', 'phone_info', 'battery'):
            from vault_manager import now_iso as _now_iso
            dev_r, sh_r = await asyncio.gather(
                self._raw_phone_rpc('device_info', {'op': 'device_info'}, timeout=10.0),
                self._raw_phone_rpc('shell', {'op': 'shell', 'command': 'id -u'}, timeout=4.5),
            )
            return reconcile_device_root_status(dev_r if isinstance(dev_r, dict) else {}, sh_r if isinstance(sh_r, dict) else None, _now_iso())
        if op in ('list_files', 'files'):
            dir_p = str(args.get('path') or args.get('target') or args.get('text') or '/sdcard/Download').strip()
            lim_f = max(1, min(100, int(args.get('limit') or 25)))
            return await self._raw_phone_rpc('list_files', {'op': 'list_files', 'path': dir_p, 'limit': lim_f}, timeout=15.0)
        if op == 'pull_file':
            file_p = str(args.get('path') or args.get('target') or args.get('text') or 'latest').strip()
            src_dest = str(args.get('source') or 'workspace').strip().lower()
            if src_dest not in ('workspace', 'computer'):
                src_dest = 'workspace'
            is_latest_query = file_p.lower() in ('latest', '', 'newest', 'latest_download')
            if is_latest_query:
                resolved_latest = ''
                for candidate_dir in ('/sdcard/Download/DeepSeek Claw', '/sdcard/Download', '/sdcard/Pictures/Screenshots', '/sdcard/DCIM/Camera'):
                    lf = await self._raw_phone_rpc('list_files', {'op': 'list_files', 'path': candidate_dir, 'limit': 15}, timeout=12.0)
                    items = (lf.get('files') or lf.get('entries') or []) if isinstance(lf, dict) else []
                    file_items = [
                        it for it in items
                        if isinstance(it, dict) and not it.get('is_dir') and not it.get('isDirectory') and (it.get('path') or it.get('name'))
                    ]
                    if file_items:
                        picked = file_items[0]
                        resolved_latest = str(picked.get('path') or f"{candidate_dir}/{picked.get('name')}")
                        break
                if not resolved_latest:
                    # Fallback to Root/Shell find across Download, Pictures, DCIM, Documents (handles Scoped Storage & nested folders like 'DeepSeek Claw')
                    sh_cmd = (
                        "for d in '/sdcard/Download' '/sdcard/Pictures' '/sdcard/DCIM' '/sdcard/Documents'; do "
                        "[ -d \"$d\" ] && find \"$d\" -maxdepth 3 -type f 2>/dev/null; "
                        "done | while IFS= read -r f; do "
                        "ts=$(stat -c '%Y' \"$f\" 2>/dev/null || echo 0); "
                        "printf '%s\\t%s\\n' \"$ts\" \"$f\"; "
                        "done | sort -nr | head -n 1 | cut -f2-"
                    )
                    sh_res = await self._raw_phone_rpc('shell', {'op': 'shell', 'command': sh_cmd}, timeout=15.0)
                    cand_line = str((sh_res or {}).get('output') or '').strip().splitlines()
                    if cand_line and cand_line[0].strip().startswith('/'):
                        resolved_latest = cand_line[0].strip()
                if not resolved_latest:
                    direct_pull = await self._raw_phone_rpc('pull_file', {'op': 'pull_file', 'path': 'latest', 'source': src_dest}, timeout=45.0)
                    if isinstance(direct_pull, dict) and direct_pull.get('ok') and (direct_pull.get('file') or direct_pull.get('saved_path') or direct_pull.get('url')):
                        direct_pull['resolved_from'] = 'latest'
                        return direct_pull
                    return {
                        'ok': False,
                        'op': 'pull_file',
                        'resolved_mode': 'latest_scan',
                        'scanned_dirs': ['/sdcard/Download', '/sdcard/Pictures', '/sdcard/DCIM', '/sdcard/Documents'],
                        'error': '已扫描手机 /sdcard/Download、/sdcard/Pictures、/sdcard/DCIM、/sdcard/Documents（含子目录），当前未找到任何可拉取的普通文件（目录为空）；请先确认文件所在路径或传入具体 path。',
                    }
                file_p = resolved_latest
            res_pull = await self._raw_phone_rpc('pull_file', {'op': 'pull_file', 'path': file_p, 'source': src_dest}, timeout=45.0)
            if isinstance(res_pull, dict) and is_latest_query:
                res_pull['resolved_from'] = 'latest'
                res_pull['resolved_phone_path'] = file_p
            return res_pull
        if op == 'push_file':
            file_p = str(args.get('path') or args.get('target') or args.get('text') or '').strip()
            if not file_p:
                return {'ok': False, 'error': 'push_file 需要提供工作区文件路径 path（如 "uploads/xxx.pdf" 或 "workspace/xxx.pdf"）'}
            src_origin = str(args.get('source') or 'workspace').strip().lower()
            if src_origin == 'workspace' and not file_p.startswith('workspace/'):
                file_p = 'workspace/' + file_p.lstrip('/')
            return await self._raw_phone_rpc('push_file', {'op': 'push_file', 'path': file_p, 'source': src_origin}, timeout=45.0)
        if op in notif_ops:
            action = str(args.get('action') or (op if op in ('add_filter', 'remove_filter', 'list_filters', 'clear_inbox') else 'list')).strip().lower()
            if action == 'add_filter':
                return self.notification_hub.add_filter(
                    package=str(args.get('filter_package') or args.get('package') or ''),
                    app=str(args.get('filter_app') or args.get('app') or ''),
                    keyword=str(args.get('filter_keyword') or args.get('keyword') or args.get('text') or args.get('target') or ''),
                    reason=str(args.get('reason') or args.get('goal') or ''),
                    created_by='ai',
                )
            if action == 'remove_filter':
                return self.notification_hub.remove_filter( str(args.get('filter_id') or args.get('id') or args.get('filter_keyword') or args.get('keyword') or args.get('target') or args.get('app') or '') )
            if action == 'list_filters':
                return {'ok': True, 'action': 'list_filters', 'filters': self.notification_hub.get_filters()}
            if action == 'clear_inbox':
                return self.notification_hub.clear_inbox(str(args.get('app') or args.get('target') or ''))
            app_q = str(args.get('app') or args.get('package') or args.get('target') or args.get('text') or '').strip()
            lim = int(args.get('limit') or 30)
            poll_info = await self._poll_and_converge_notifications(app_filter='', limit=max(40, lim), timeout=8.0)
            inbox_res = self.notification_hub.query_inbox(app_query=app_q, limit=lim, mark_seen=True)
            return { 'ok': True, 'op': 'notifications', 'live_polled': poll_info.get('ok', False), **inbox_res, }
        if op in ('send_screenshot', 'share_image', 'send_qr', 'send_image'):
            crop_box = args.get('crop_box')
            if crop_box is not None:
                if not isinstance(crop_box, list) or len(crop_box) != 4:
                    return {'ok': False, 'op': 'send_screenshot', 'error': f'无效的 crop_box={crop_box}：必须为 [ymin, xmin, ymax, xmax] 4 个 0..1000 数值'}
                try:
                    cy1, cx1, cy2, cx2 = float(crop_box[0]), float(crop_box[1]), float(crop_box[2]), float(crop_box[3])
                except (ValueError, TypeError):
                    return {'ok': False, 'op': 'send_screenshot', 'error': f'无效的 crop_box={crop_box}：坐标必须为数值'}
                if not (0 <= cy1 < cy2 <= 1000 and 0 <= cx1 < cx2 <= 1000):
                    return {'ok': False, 'op': 'send_screenshot', 'error': f'无效的 crop_box={crop_box}：要求满足 0 <= ymin < ymax <= 1000 且 0 <= xmin < xmax <= 1000'}
            snap = await self._raw_phone_rpc('preview', {'op': 'preview', 'since': -1})
            b64_s = str((snap or {}).get('image') or '').strip()
            raw_clean = b''
            if b64_s:
                raw_clean = base64.b64decode(b64_s.split(',')[-1])
                (self.harness.root / 'phone-clean.jpg').write_bytes(raw_clean)
            elif (self.harness.root / 'phone-clean.jpg').exists():
                raw_clean = (self.harness.root / 'phone-clean.jpg').read_bytes()
            elif (self.harness.root / 'phone-latest.jpg').exists():
                raw_clean = (self.harness.root / 'phone-latest.jpg').read_bytes()
            if not raw_clean:
                return {'ok': False, 'op': 'send_screenshot', 'error': '未能获取手机虚拟屏当前画面'}
            media_url = self._prepare_chat_screenshot(raw_clean, crop_box=crop_box, prefix='phone-shot')
            return { 'ok': bool(media_url), 'op': 'send_screenshot', 'chat_image_url': media_url, 'cropped': bool(crop_box), 'note': f'已将手机虚拟屏干净无标尺截图直接挂载到本轮聊天回复气泡上（{media_url}），主人打开聊天框即可直接扫码或查看！', }
        if op in ('run', 'task', 'inspect', 'screenshot', 'capture'):
            op = 'ui_tree'
            args = {**args, 'op': 'ui_tree', 'refresh': True}
        cur_w, cur_h = self._last_phone_size
        if op == 'open':
            comp_cand = str(args.get('component') or '').strip()
            u_cand = str(args.get('uri') or args.get('url') or args.get('target') or args.get('app') or args.get('package') or args.get('text') or '').strip()
            is_bare_domain = ( bool(u_cand) and not comp_cand and ' ' not in u_cand and (u_cand.startswith('www.') or '/' in u_cand) and '.' in u_cand and not re.match(r'^(?:com|cn|org|net|io|dev|me|tv|app|gov|edu)\.[a-zA-Z0-9_.]+$', u_cand) )
            if re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*://', u_cand) or is_bare_domain:
                full_uri = u_cand if '://' in u_cand else ('https://' + u_cand)
                op = 'open_uri'
                args = {**args, 'op': 'open_uri', 'uri': full_uri}
            elif u_cand or comp_cand:
                op = 'launch'
                args = {**args, 'op': 'launch', 'app': u_cand, 'component': comp_cand}
            else:
                return {'ok': False, 'error': 'phone_workspace(op="open") 需要提供要打开的 app 名称/包名、component 或 uri 网址'}
        elif op == 'press':
            op = 'key'
            args = {**args, 'op': 'key', 'key': str(args.get('key') or args.get('target') or args.get('text') or 'back').strip().lower()}
        elif op in ('scroll', 'swipe'):
            raw_dir = str(args.get('direction') or '').strip().lower()
            if raw_dir and raw_dir not in ('up', 'down', 'left', 'right'):
                return {'ok': False, 'op': orig_requested_op, 'error': f'无效的滚动方向 direction="{raw_dir}"，仅支持 up / down / left / right'}
            if op == 'scroll' and (args.get('target') or args.get('text') or args.get('label')):
                op = 'scroll_to'
                args = {**args, 'op': 'scroll_to'}
            elif args.get('x1') is None and args.get('start_x') is None and args.get('nx') is None and not args.get('grid'):
                direction = raw_dir or 'down'
                scroll_dir = direction
                dur_s = max(180, min(1200, int(args.get('duration_ms') or 300)))
                if direction == 'up':
                    sx1, sy1, sx2, sy2 = cur_w // 2, int(cur_h * 0.20), cur_w // 2, int(cur_h * 0.82)
                elif direction == 'left':
                    sx1, sy1, sx2, sy2 = int(cur_w * 0.80), cur_h // 2, int(cur_w * 0.20), cur_h // 2
                elif direction == 'right':
                    sx1, sy1, sx2, sy2 = int(cur_w * 0.20), cur_h // 2, int(cur_w * 0.80), cur_h // 2
                else:
                    sx1, sy1, sx2, sy2 = cur_w // 2, int(cur_h * 0.82), cur_w // 2, int(cur_h * 0.20)
                op = 'swipe'
                args = {**args, 'op': 'swipe', 'x1': sx1, 'y1': sy1, 'x2': sx2, 'y2': sy2, 'duration_ms': dur_s}
            else:
                op = 'swipe'
                sx1 = args.get('x1') if args.get('x1') is not None else (args.get('start_x') if args.get('start_x') is not None else args.get('x', cur_w // 2))
                sy1 = args.get('y1') if args.get('y1') is not None else (args.get('start_y') if args.get('start_y') is not None else args.get('y', int(cur_h * 0.75)))
                sx2 = args.get('x2') if args.get('x2') is not None else (args.get('end_x') if args.get('end_x') is not None else cur_w // 2)
                sy2 = args.get('y2') if args.get('y2') is not None else (args.get('end_y') if args.get('end_y') is not None else int(cur_h * 0.25))
                if args.get('nx') is not None and args.get('ny') is not None:
                    sx1 = int(round(float(args['nx']) * cur_w / 1000.0))
                    sy1 = int(round(float(args['ny']) * cur_h / 1000.0))
                if args.get('nx2') is not None and args.get('ny2') is not None:
                    sx2 = int(round(float(args['nx2']) * cur_w / 1000.0))
                    sy2 = int(round(float(args['ny2']) * cur_h / 1000.0))
                args = {**args, 'op': 'swipe', 'x1': int(sx1), 'y1': int(sy1), 'x2': int(sx2), 'y2': int(sy2), 'duration_ms': int(args.get('duration_ms') or 300)}
        elif op == 'type':
            op = 'tap_and_type'
            args = {**args, 'op': 'tap_and_type'}
        elif op == 'tap':
            t_in = str(args.get('target') or args.get('label') or '').strip()
            r_in = str(args.get('ref') or '').strip()
            if not r_in and t_in.startswith(('@e', '@v', 'e', 'v')) and re.fullmatch(r'@?[ev]\d+', t_in, flags=re.IGNORECASE):
                r_in = t_in if t_in.startswith('@') else ('@' + t_in)
                t_in = ''
            if (r_in or t_in or args.get('text') or args.get('view_id') or args.get('id')) and args.get('x') is None and args.get('nx') is None and not args.get('grid'):
                op = 'tap_node'
                args = { **args, 'op': 'tap_node', 'ref': r_in, 'target': t_in, 'label': '', 'text': str(args.get('text') or t_in).strip(), }
        resolved_pt = self._resolve_phone_grid_or_norm(args, cur_w, cur_h)
        if resolved_pt is not None and op in ('tap', 'tap_node', 'click_node', 'tap_and_type', 'long_press'):
            args = {**args, 'x': resolved_pt[0], 'y': resolved_pt[1]}
            if op in ('tap_node', 'click_node') and not args.get('ref') and not args.get('text') and not args.get('target'):
                op = 'tap'
                args['op'] = 'tap'
        if op == 'tap' and (args.get('x') is None or args.get('y') is None):
            return {'ok': False, 'op': orig_requested_op, 'error': 'tap 需要提供 target (如 "@e1" 或文字) 或 nx, ny (0..1000 归一化标尺坐标)'}
        wait_meta: dict | None = None
        if op in ('wait', 'delay', 'sleep'):
            raw_sec = args.get('seconds') or args.get('sec')
            raw_ms = args.get('duration_ms') or args.get('delay_ms') or args.get('ms')
            if raw_ms is not None:
                ms = int(float(raw_ms))
            elif raw_sec is not None:
                ms = int(float(raw_sec) * 1000)
            else:
                ms = 1500
            ms = max(200, min(10000, ms))
            await asyncio.sleep(ms / 1000.0)
            wait_meta = {'ok': True, 'op': 'wait', 'waited_ms': ms, 'duration_ms': ms, 'delay_ms': ms}
            op = 'ui_tree'
            args = {**args, 'op': 'ui_tree', 'refresh': True}
        open_uri_meta: dict | None = None
        if op in ('open_uri', 'open_url'):
            uri_str = str(args.get('uri') or args.get('url') or args.get('text') or args.get('target') or '').strip()
            if not uri_str or not re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*://', uri_str):
                return {'ok': False, 'error': 'open_uri 需要提供完整的 URI/URL（例如 https://docs.qq.com/...）'}
            res = await self._raw_phone_rpc('open_uri', {'op': 'open_uri', 'uri': uri_str})
            if isinstance(res, dict) and '未知虚拟屏操作' in str(res.get('error') or ''):
                st = await self._raw_phone_rpc('status', {'op': 'status'})
                disp_id = ((st.get('display') or {}) if isinstance(st, dict) else {}).get('displayId') or 0
                safe_u = uri_str.replace("'", "'\\''")
                disp_flag = f'--display {int(disp_id or 0)} ' if int(disp_id or 0) > 0 else ''
                res = await self._raw_phone_rpc('shell', {'op': 'shell', 'command': f"am start {disp_flag}-a android.intent.action.VIEW -d '{safe_u}'"})
                await asyncio.sleep(0.85)
            open_uri_meta = {**(res if isinstance(res, dict) else {}), 'opened_uri': uri_str}
            op = 'ui_tree'
            args = {'op': 'ui_tree', 'refresh': True}
        if op == 'long_press':
            lx = int(args['x']) if args.get('x') is not None else (cur_w // 2)
            ly = int(args['y']) if args.get('y') is not None else (cur_h // 2)
            dur = max(450, min(2000, int(args.get('duration_ms') or 650)))
            res = await self._raw_phone_rpc('swipe', {'op': 'swipe', 'x1': lx, 'y1': ly, 'x2': lx, 'y2': ly, 'duration_ms': dur})
            if isinstance(res, dict):
                res['long_pressed'] = True
                res['x'] = lx
                res['y'] = ly
            args = {**args, 'x': lx, 'y': ly}
        if op == 'scroll_to':
            target_q = str(args.get('target') or args.get('text') or args.get('label') or '').strip()
            if not target_q:
                return {'ok': False, 'error': 'scroll_to 需要提供要查找的 target 文字'}
            direction = str(args.get('direction') or 'down').strip().lower()
            max_scroll_attempts = max(4, min(20, int(args.get('max_attempts') or 12)))
            last_tree: dict = {}
            for attempt in range(max_scroll_attempts):
                last_tree = await self._tool_phone_workspace({'op': 'ui_tree', 'refresh': True})
                combined_txt = str(last_tree.get('ui_tree') or '')
                if target_q.lower() in combined_txt.lower():
                    last_tree['found_target'] = target_q
                    last_tree['scroll_attempts'] = attempt
                    return last_tree
                sx = cur_w // 2
                if direction == 'up':
                    sy1, sy2 = int(cur_h * 0.20), int(cur_h * 0.82)
                else:
                    sy1, sy2 = int(cur_h * 0.82), int(cur_h * 0.20)
                await self._raw_phone_rpc('swipe', {'op': 'swipe', 'x1': sx, 'y1': sy1, 'x2': sx, 'y2': sy2, 'duration_ms': 300})
                await asyncio.sleep(0.42)
            last_tree['found_target'] = False
            last_tree['note'] = f'已连续滚动 {max_scroll_attempts} 次，请直接查看最新附带的实时画面截图定位「{target_q}」'
            return last_tree
        if op == 'batch' and isinstance(args.get('steps'), list):
            steps_list = [s for s in args['steps'][:30] if isinstance(s, dict) and s.get('op') != 'batch']
            screenshot_mode = str(args.get('screenshot') or 'final').strip().lower()
            batch_results = []
            last_res: dict = {'ok': True}
            for idx_s, step_item in enumerate(steps_list):
                is_last_step = (idx_s + 1 == len(steps_list))
                if screenshot_mode in ('none', 'false', '0'):
                    skip_vis = True
                elif screenshot_mode in ('each', 'all', 'every'):
                    skip_vis = False
                else:
                    skip_vis = not is_last_step
                step_payload = {**step_item, '_skip_vision': skip_vis}
                last_res = await self._tool_phone_workspace(step_payload)
                s_pkg = str((last_res or {}).get('package') or '') if isinstance(last_res, dict) else ''
                s_cnt = int((last_res or {}).get('node_count') or 0) if isinstance(last_res, dict) else 0
                s_tree = str((last_res or {}).get('ui_tree') or '') if isinstance(last_res, dict) else ''
                m_sh = re.search(r'treeHash=([0-9a-zA-Z_-]+)', s_tree)
                s_hash = m_sh.group(1) if m_sh else ''
                batch_results.append({ 'step': idx_s + 1, 'op': step_item.get('op'), 'ok': not bool(isinstance(last_res, dict) and last_res.get('error')), 'package': s_pkg, 'node_count': s_cnt, 'treeHash': s_hash, })
                if isinstance(last_res, dict) and last_res.get('error'):
                    break
                if not is_last_step:
                    await asyncio.sleep(0.22)
            if isinstance(last_res, dict):
                last_res['batch_steps_executed'] = batch_results
                last_res['steps_trace'] = batch_results
                last_res['screenshot_mode'] = screenshot_mode
                if screenshot_mode in ('none', 'false', '0'):
                    last_res.pop('saved_screenshot', None)
                    last_res.pop('_multimodal_b64', None)
            return last_res
        ref_arg = str(args.get('ref') or '').strip()
        target_only_arg = str(args.get('target') or args.get('label') or '').strip()
        id_arg = str(args.get('view_id') or args.get('id') or '').strip()
        for cand_ref in (ref_arg, target_only_arg, id_arg):
            if cand_ref and re.fullmatch(r'@?[ev]\d+', cand_ref, flags=re.IGNORECASE):
                ref_arg = cand_ref if cand_ref.startswith('@') else ('@' + cand_ref)
                if target_only_arg == cand_ref:
                    target_only_arg = ''
                if id_arg == cand_ref:
                    id_arg = ''
                break
        before_tree_hash = str(getattr(self, '_last_phone_tree_hash', '') or '')
        before_pkg_state = str(getattr(self, '_last_phone_pkg', '') or '')
        before_nodes_state = getattr(self, '_last_phone_node_count', None)
        if op == 'key' and not before_tree_hash:
            pre_t = await self._raw_phone_rpc('ui_tree', {'op': 'ui_tree', 'refresh': False}, timeout=6.0)
            if isinstance(pre_t, dict):
                self._parse_and_cache_phone_nodes(pre_t)
                m_bh = re.search(r'treeHash=([0-9a-zA-Z_-]+)', str(pre_t.get('ui_tree') or pre_t.get('tree') or pre_t.get('summary') or ''))
                if m_bh:
                    before_tree_hash = f"{pre_t.get('package') or ''}:{m_bh.group(1)}"
        if op == 'tap_and_type':
            content_txt = str(args.get('text') or '').strip()
            tx = int(args['x']) if args.get('x') is not None else None
            ty = int(args['y']) if args.get('y') is not None else None
            if not getattr(self, '_last_phone_nodes', None):
                pre_nodes_r = await self._raw_phone_rpc('ui_tree', {'op': 'ui_tree', 'refresh': True}, timeout=8.0)
                if isinstance(pre_nodes_r, dict):
                    self._parse_and_cache_phone_nodes(pre_nodes_r)
            matched_n = self._match_phone_node(target_q=target_only_arg, ref_q=ref_arg, id_q=id_arg, for_type=True)
            if matched_n:
                if not ref_arg and matched_n.get('ref'):
                    ref_arg = str(matched_n['ref'])
                if not id_arg and matched_n.get('id'):
                    id_arg = str(matched_n['id'])
                if tx is None and matched_n.get('x') is not None and matched_n.get('y') is not None:
                    tx, ty = int(matched_n['x']), int(matched_n['y'])
            elif not id_arg and target_only_arg and re.fullmatch(r'[a-zA-Z0-9_.:/-]+', target_only_arg) and ('_' in target_only_arg or ':id/' in target_only_arg):
                id_arg = target_only_arg
            tap_r: dict = {}
            if args.get('x') is not None and args.get('y') is not None:
                tap_r = await self._raw_phone_rpc('tap', {'op': 'tap', 'x': tx, 'y': ty})
            elif ref_arg or target_only_arg or id_arg:
                rpc_tap_payload = {'op': 'tap_node', 'ref': ref_arg, 'text': target_only_arg, 'id': id_arg}
                tap_r = await self._raw_phone_rpc('tap_node', rpc_tap_payload)
                if isinstance(tap_r, dict) and not tap_r.get('error') and tap_r.get('ok') is not False:
                    if tap_r.get('x') is not None and tap_r.get('y') is not None:
                        tx, ty = int(float(tap_r['x'])), int(float(tap_r['y']))
                elif tx is not None and ty is not None:
                    tap_r = await self._raw_phone_rpc('tap', {'op': 'tap', 'x': tx, 'y': ty})
                else:
                    return { 'ok': False, 'op': orig_requested_op, 'error': f"未找到可输入的节点（ref={ref_arg or '无'}, id={id_arg or '无'}, target={target_only_arg or '无'}）。{self._format_available_nodes_hint()}", }
            elif tx is not None and ty is not None:
                tap_r = await self._raw_phone_rpc('tap', {'op': 'tap', 'x': tx, 'y': ty})
            else:
                return { 'ok': False, 'op': orig_requested_op, 'error': f"未识别到可输入框节点，请提供 target (如 '@e1') 或 nx, ny 坐标。{self._format_available_nodes_hint()}", }
            steps_executed: list[dict] = []
            tap_pkg = str((tap_r or {}).get('package') or before_pkg_state or '') if isinstance(tap_r, dict) else ''
            tap_cnt = int((tap_r or {}).get('node_count') or (tap_r or {}).get('nodeCount') or 0) if isinstance(tap_r, dict) else 0
            tap_hash = str((tap_r or {}).get('treeHash') or (tap_r or {}).get('tree_hash') or '') if isinstance(tap_r, dict) else ''
            steps_executed.append({'step': 1, 'op': 'tap', 'ref': ref_arg, 'x': tx, 'y': ty, 'package': tap_pkg, 'node_count': tap_cnt, 'treeHash': tap_hash})
            await asyncio.sleep(0.32)
            type_r = await self._raw_phone_rpc('text', {'op': 'text', 'text': content_txt, 'clear': bool(args.get('clear', False))}) if (content_txt or args.get('clear')) else {'ok': True}
            if isinstance(type_r, dict) and (type_r.get('error') or type_r.get('ok') is False) and (ref_arg or id_arg or target_only_arg):
                alt_type = await self._raw_phone_rpc('type_node', {'op': 'type_node', 'ref': ref_arg, 'id': id_arg, 'target': target_only_arg, 'text': content_txt, 'clear': bool(args.get('clear', False))})
                if isinstance(alt_type, dict) and not alt_type.get('error') and alt_type.get('ok') is not False:
                    type_r = alt_type
            if (isinstance(tap_r, dict) and tap_r.get('error') and isinstance(type_r, dict) and type_r.get('error')) or (not tap_r and isinstance(type_r, dict) and type_r.get('error')):
                return { 'ok': False, 'op': 'type', 'error': f"未找到可输入的节点（ref={ref_arg or '无'}, id={id_arg or '无'}, target={target_only_arg or '无'}）。{self._format_available_nodes_hint()}", }
            pkg_before_submit = str((type_r or {}).get('package') or tap_pkg or before_pkg_state or '') if isinstance(type_r, dict) else tap_pkg
            type_cnt = int((type_r or {}).get('node_count') or (type_r or {}).get('nodeCount') or tap_cnt) if isinstance(type_r, dict) else tap_cnt
            type_hash = str((type_r or {}).get('treeHash') or (type_r or {}).get('tree_hash') or tap_hash) if isinstance(type_r, dict) else tap_hash
            steps_executed.append({'step': 2, 'op': 'text', 'text': content_txt, 'package': pkg_before_submit, 'node_count': type_cnt, 'treeHash': type_hash})
            pkg_after_submit = pkg_before_submit
            if args.get('submit'):
                await asyncio.sleep(0.22)
                key_r = await self._raw_phone_rpc('key', {'op': 'key', 'key': 'enter'})
                if isinstance(key_r, dict):
                    pkg_after_submit = str(key_r.get('package') or pkg_before_submit)
                    key_cnt = int(key_r.get('node_count') or key_r.get('nodeCount') or type_cnt)
                    key_hash = str(key_r.get('treeHash') or key_r.get('tree_hash') or type_hash)
                    steps_executed.append({'step': 3, 'op': 'key', 'key': 'enter', 'package': pkg_after_submit, 'node_count': key_cnt, 'treeHash': key_hash})
                    if key_r.get('ui_tree') or key_r.get('tree') or key_r.get('summary'):
                        type_r = key_r
            _stale_tap_keys = ('error', 'image', '_image_b64', 'ui_tree', 'tree', 'summary', 'treeHash', 'tree_hash', 'package', 'node_count', 'nodeCount', 'count', 'change_summary', 'ui_changed', 'layout_signature')
            clean_tap = {k: v for k, v in (tap_r if isinstance(tap_r, dict) else {}).items() if k not in _stale_tap_keys}
            clean_type = {k: v for k, v in (type_r if isinstance(type_r, dict) else {}).items() if k not in ('error', 'image', '_image_b64')}
            res = {
                **clean_tap,
                **clean_type,
                'ok': True,
                'op': 'tap_and_type',
                'ref': ref_arg,
                'id': id_arg,
                'x': tx,
                'y': ty,
                'typed': bool(content_txt),
                'submitted': bool(args.get('submit')),
                'steps_executed': steps_executed,
            }
            if args.get('submit') and pkg_before_submit and pkg_after_submit and pkg_after_submit != pkg_before_submit:
                res['submit_triggered_navigation'] = True
                res['package_before_submit'] = pkg_before_submit
                res['package_after_submit'] = pkg_after_submit
        elif op == 'long_press':
            pass
        else:
            reused_existing_app = False
            if op == 'launch' and not str(args.get('component') or '').strip() and not args.get('force'):
                req_app = str(args.get('app') or args.get('target') or args.get('package') or args.get('text') or '').strip()
                if req_app and self._vdisplay_currently_open:
                    probe_tree = await self._raw_phone_rpc('ui_tree', {'op': 'ui_tree', 'refresh': True})
                    if isinstance(probe_tree, dict) and not probe_tree.get('error'):
                        probe_pkg = str(probe_tree.get('package') or '')
                        if probe_pkg and self._matches_requested_app_package(req_app, probe_pkg):
                            res = {**probe_tree, 'reused_active_session': True}
                            op = 'ui_tree'
                            reused_existing_app = True
            if not reused_existing_app:
                res = await self._raw_phone_rpc(op, args)
                if ( isinstance(res, dict) and op == 'tap_node' and (res.get('error') or res.get('ok') is False) and (ref_arg or target_only_arg or id_arg) ):
                    matched_tap = self._match_phone_node(target_q=target_only_arg, ref_q=ref_arg, id_q=id_arg, for_type=False)
                    if matched_tap and matched_tap.get('x') is not None and matched_tap.get('y') is not None:
                        fallback_tap = await self._raw_phone_rpc('tap', {'op': 'tap', 'x': int(matched_tap['x']), 'y': int(matched_tap['y'])})
                        if isinstance(fallback_tap, dict) and not fallback_tap.get('error'):
                            res = {**fallback_tap, 'ok': True, 'ref': matched_tap.get('ref') or ref_arg, 'x': int(matched_tap['x']), 'y': int(matched_tap['y']), 'fallback_by_coord': True}
                has_disp_field = isinstance(res, dict) and (
                    'displayId' in res or 'display_id' in res
                    or (isinstance(res.get('display'), dict) and ('displayId' in res['display'] or 'display_id' in res['display']))
                )
                if (
                    isinstance(res, dict)
                    and op not in ('create', 'release', 'auto_release', 'status', 'apps', 'shell', 'device_info', 'notifications', 'pull_file', 'push_file', 'list_files')
                    and ( any(k in str(res.get('error') or '') for k in ('虚拟屏未创建', '虚拟屏不存在', '未分配虚拟屏', 'displayId <= 0')) or (not res.get('error') and has_disp_field and int(res.get('displayId') or res.get('display_id') or (res.get('display') or {}).get('displayId') or -1) <= 0) )
                ):
                    wake_r = await self._raw_phone_rpc('create', {'op': 'create'})
                    if isinstance(wake_r, dict) and not wake_r.get('error') and int(wake_r.get('displayId') or -1) > 0:
                        self._vdisplay_currently_open = True
                        res = await self._raw_phone_rpc(op, args)
        if not isinstance(res, dict):
            return wait_meta or res
        if wait_meta and res.get('error'):
            return wait_meta
        if open_uri_meta:
            res = {**open_uri_meta, **res}
        if wait_meta:
            res = {**res, **wait_meta}
        disp_info = res.get('display') if isinstance(res.get('display'), dict) else {}
        has_disp_key = ('displayId' in res or 'display_id' in res or 'displayId' in disp_info or 'display_id' in disp_info)
        eff_disp_id = int( res.get('displayId') or res.get('display_id') or disp_info.get('displayId') or disp_info.get('display_id') or -1 )
        if eff_disp_id > 0:
            self._last_phone_display_id = eff_disp_id
        if ( op in ('launch', 'open_uri', 'tap', 'tap_node', 'click_node', 'tap_and_type', 'swipe', 'text', 'type_node', 'key', 'long_press', 'ui_tree', 'capture') and not wait_meta and not res.get('error') and has_disp_key and eff_disp_id <= 0 and not self._vdisplay_currently_open ):
            return { 'ok': False, 'op': op, 'displayId': eff_disp_id, 'error': 'virtual_display_not_ready: 虚拟屏尚未就绪 (displayId <= 0)，禁止落回主屏幕 (Display 0)！请先调用 phone_workspace(op="open", target="应用名") 启动目标应用或检查 Root 虚拟屏权限。', }
        if op in ('launch', 'create', 'open_uri') or bool(disp_info.get('created') and not disp_info.get('released')):
            if not res.get('error'):
                self._vdisplay_currently_open = True
        # Fix 19-2: Any error / ok=False response must NEVER carry base64 screenshots or raw image fields
        if res.get('error') or res.get('ok') is False:
            res['ok'] = False
            res.pop('image', None)
            res.pop('_image_b64', None)
            res.pop('_multimodal_b64', None)
            res.pop('summary', None)
            if '未找到' in str(res.get('error') or '') and '当前可用节点' not in str(res.get('error') or ''):
                res['error'] = f"{res['error']} ({self._format_available_nodes_hint()})"
            return res

        pkg, node_count, cur_hash_now = normalize_single_tree_frame(res)
        # Never re-inject key events when treeHash is unchanged (self-drawn apps like WeChat report constant empty treeHash,
        # so re-firing KEYCODE_BACK caused double/quadruple BACK jumps!). One `press` = strictly ONE key event.
        if op == 'key' and not res.get('error'):
            if not cur_hash_now or (before_tree_hash and cur_hash_now == before_tree_hash):
                await asyncio.sleep(0.24)
                probe_k = await self._raw_phone_rpc('ui_tree', {'op': 'ui_tree', 'refresh': True})
                if isinstance(probe_k, dict) and not probe_k.get('error'):
                    apply_authoritative_tree(res, probe_k)
                    pkg, node_count, cur_hash_now = normalize_single_tree_frame(res)
            res['tree_hash_before'] = before_tree_hash
            res['tree_hash_after'] = cur_hash_now
            res['key_consumed'] = bool(not before_tree_hash or (cur_hash_now and cur_hash_now != before_tree_hash))
        # Fix 17-2, 18-3, 18-5, 19-3: Settle & overwrite transient empty/partial first-frame tree
        if ( op in ('launch', 'open_uri', 'tap', 'tap_node', 'click_node', 'tap_and_type', 'swipe', 'text', 'type_node', 'key', 'long_press', 'ui_tree') and not res.get('error') and (int(node_count or 0) < 3 or pkg in ('', 'unknown') or (op == 'launch' and pkg == 'com.android.launcher')) ):
            await asyncio.sleep(0.50)
            retry_tree = await self._raw_phone_rpc('ui_tree', {'op': 'ui_tree', 'refresh': True})
            if isinstance(retry_tree, dict) and not retry_tree.get('error'):
                apply_authoritative_tree(res, retry_tree)
                res['transition_retried'] = True
                if wait_meta:
                    res.update(wait_meta)
                pkg, node_count, cur_hash_now = normalize_single_tree_frame(res)
        if op == 'launch':
            req_app_chk = str(args.get('app') or args.get('target') or args.get('package') or '').strip()
            res['active_package'] = pkg
            res['launched'] = bool( pkg and pkg != 'unknown' and int(node_count or 0) > 0 and (not req_app_chk or pkg != 'com.android.launcher' or self._matches_requested_app_package(req_app_chk, pkg)) )
        if int(node_count or 0) == 0 and pkg in ('', 'unknown') and op in ('launch', 'open_uri', 'ui_tree', 'tap', 'tap_node'):
            res['likely_blank'] = True
            res['settled'] = False
        else:
            res['settled'] = int(node_count or 0) >= 3
        if op in ('tap', 'tap_node', 'click_node', 'swipe') and not res.get('error') and before_tree_hash and cur_hash_now and cur_hash_now != before_tree_hash:
            await asyncio.sleep(0.26)
            fresh_t = await self._raw_phone_rpc('ui_tree', {'op': 'ui_tree', 'refresh': True})
            if isinstance(fresh_t, dict) and not fresh_t.get('error'):
                apply_authoritative_tree(res, fresh_t)
                pkg, node_count, cur_hash_now = normalize_single_tree_frame(res)
        res['layoutStable'] = bool(res.get('settled', True))
        res['densityDpi'] = int(res.get('dpi') or res.get('densityDpi') or 320)
        res['screenWidthDp'] = int(res.get('swDp') or res.get('screenWidthDp') or 360)
        res.setdefault('fontScale', 1.0)
        res.setdefault('settle_ms', 0)
        if 'change_summary' in res or op in ('launch', 'open_uri', 'tap', 'tap_node', 'click_node', 'tap_and_type', 'swipe', 'text', 'type_node', 'key', 'long_press'):
            raw_cs = str(res.get('change_summary') or '')
            m_bp = re.search(r'包名\s+([^\s]+)\s+->', raw_cs)
            m_bn = re.search(r'节点数\s+([^\s]+)\s+->', raw_cs)
            b_pkg = m_bp.group(1) if m_bp else (before_pkg_state or 'none')
            b_cnt = m_bn.group(1) if m_bn else (str(before_nodes_state) if before_nodes_state is not None else '?')
            final_pkg_str = pkg or 'unknown'
            final_cnt_int = int(node_count or 0)
            visual_changed = bool(res.get('visual_changed', False))
            tree_changed = bool(
                not before_tree_hash
                or (cur_hash_now and cur_hash_now != before_tree_hash)
                or (b_pkg not in ('', 'none', final_pkg_str))
                or (str(b_cnt) not in ('?', str(final_cnt_int)))
            )
            ui_changed_final = tree_changed or visual_changed
            res['ui_changed'] = ui_changed_final
            res['change_summary'] = (
                f"{'✅ 界面已变化: ' if ui_changed_final else '⚠️ 界面未变: '}"
                f"包名 {b_pkg or 'none'} -> {final_pkg_str}, 节点数 {b_cnt} -> {final_cnt_int}"
                + (" (自绘画面帧已更新)" if (visual_changed and not tree_changed) else "")
            )
        # Parse and cache structured nodes before popping raw `nodes` array (fixes 19-1 while keeping response compact)
        cached_nodes = self._parse_and_cache_phone_nodes(res)
        raw_nodes_list = res.pop('nodes', None)
        if 'clickable_count' not in res:
            src_nodes = raw_nodes_list if isinstance(raw_nodes_list, list) else cached_nodes
            res['clickable_count'] = sum(1 for n in src_nodes if isinstance(n, dict) and n.get('clickable'))
        w = int(res.get('width') or self._last_phone_size[0] or 1080)
        h = int(res.get('height') or self._last_phone_size[1] or 2400)
        if w > 100 and h > 100:
            self._last_phone_size = (w, h)
        if pkg:
            self._last_phone_pkg = pkg
        if node_count is not None:
            self._last_phone_node_count = int(node_count)
            res['node_count'] = int(node_count)
        if hasattr(self, 'phone_state_registry'):
            node_st = self.phone_state_registry.get(args.get('source') or self.source_context.get() or 'phone')
            node_st.last_size, node_st.last_pkg, node_st.last_node_count = self._last_phone_size, self._last_phone_pkg, self._last_phone_node_count
            node_st.last_tree_hash, node_st.last_nodes = cur_hash_now or node_st.last_tree_hash, cached_nodes
            enrich_phone_frame_contract(res, node_st, eff_disp_id, node_count)
        elif eff_disp_id > 0 and 'display_id' not in res:
            res['display_id'] = eff_disp_id
        tapped_xy: tuple[int, int] | None = None
        if op in ('tap', 'tap_node', 'click_node', 'tap_and_type', 'type_node', 'input_node', 'long_press'):
            tx_val = res.get('x') if res.get('x') is not None else args.get('x')
            ty_val = res.get('y') if res.get('y') is not None else args.get('y')
            if tx_val is not None and ty_val is not None:
                try:
                    tapped_xy = (int(float(tx_val)), int(float(ty_val)))
                    self._last_phone_tap = tapped_xy
                except (ValueError, TypeError):
                    tapped_xy = None
        existing_summary = str(res.get('ui_tree') or '').strip()
        if cur_hash_now:
            self._last_phone_tree_hash = cur_hash_now
        # Always return BOTH stabilized Accessibility UI Tree AND live Annotated Screenshot on every GUI step
        inc_img_arg = args.get('include_image')
        img_mode = str(args.get('image_mode') or '').strip().lower()
        gui_vision_ops = ('launch', 'open_uri', 'ui_tree', 'tap', 'tap_node', 'click_node', 'tap_and_type', 'swipe', 'text', 'type_node', 'key', 'long_press', 'capture', 'screenshot', 'send_screenshot')
        if bool(args.get('_skip_vision')) or inc_img_arg is False or img_mode == 'none' or res.get('error') or res.get('ok') is False:
            want_image = False
        elif op in gui_vision_ops or bool(args.get('send_to_chat')) or inc_img_arg is True or img_mode in ('thumb', 'full'):
            want_image = True
            if not img_mode:
                img_mode = 'thumb'
        else:
            want_image = False
        b64_img = ''
        raw_jpg = b''
        if not want_image:
            res.pop('image', None)
            res.pop('_image_b64', None)
            res.pop('_multimodal_b64', None)
        elif res.get('image'):
            b64_img = str(res.pop('image')).split(',')[-1]
            raw_jpg = base64.b64decode(b64_img)
            res['saved_screenshot'] = 'phone-latest.jpg'
        else:
            snap = await self._raw_phone_rpc('preview', {'op': 'preview', 'since': -1})
            if isinstance(snap, dict) and snap.get('image'):
                b64_img = str(snap['image']).split(',')[-1]
                w = int(snap.get('width') or w)
                h = int(snap.get('height') or h)
                self._last_phone_size = (w, h)
                raw_jpg = base64.b64decode(b64_img)
                res['saved_screenshot'] = 'phone-latest.jpg'
        if want_image and raw_jpg:
            (self.harness.root / 'phone-clean.jpg').write_bytes(raw_jpg)
            if args.get('send_to_chat'):
                crop_b = args.get('crop_box') if isinstance(args.get('crop_box'), list) else None
                res['chat_image_url'] = self._prepare_chat_screenshot(raw_jpg, crop_box=crop_b, prefix='phone-shot')
            if tapped_xy is not None:
                raw_jpg = self._annotate_tap_crosshair(raw_jpg, tapped_xy[0], tapped_xy[1], w, h)
            annotated_jpg, _, _ = self._draw_vision_ruler_overlay( raw_jpg, a11y_summary=existing_summary, screen_w=w, screen_h=h )
            (self.harness.root / 'phone-latest.jpg').write_bytes(annotated_jpg)
            if img_mode != 'full' and not args.get('send_to_chat'):
                try:
                    import io as _io
                    from PIL import Image as _Image
                    im = _Image.open(_io.BytesIO(annotated_jpg)).convert('RGB')
                    iw, ih = im.size
                    if max(iw, ih) > 1080:
                        scale = 1080.0 / float(max(iw, ih))
                        im = im.resize((max(1, int(round(iw * scale))), max(1, int(round(ih * scale)))), _Image.Resampling.LANCZOS)
                    buf = _io.BytesIO()
                    im.save(buf, format='JPEG', quality=78)
                    annotated_jpg = buf.getvalue()
                except Exception:
                    pass
            b64_img = base64.b64encode(annotated_jpg).decode('ascii')
        if want_image and b64_img:
            change_line = f"{res.get('change_summary')}\n" if res.get('change_summary') else ''
            res['_multimodal_b64'] = b64_img
            res['_multimodal_mime'] = 'image/jpeg'
            res['_multimodal_caption'] = (
                f'已同时返回手机虚拟屏实时无障碍节点树 (`ui_tree`) 与画面截图 (op={orig_requested_op}, 包名={pkg or "unknown"}, 节点数={int(node_count or 0)}, 分辨率={w}x{h})：\n'
                f'{change_line}'
                '1. 画面四周带有 `0..1000` 归一化标尺与 `@eN` 紫色锚点，对应 `norm=(nx,ny)` 0..1000 坐标；\n'
                '2. 若目标有 `@eN` 节点请优先调用 `phone_workspace(op="tap", target="@eN")`；若自绘界面或目标未出现在节点树中，可直接看着画面用 `nx, ny` 点击；\n'
                '3. 【Agent Loop 续行指令】：若主人的任务尚未徹底完成（例如刚打开/查看页面，或上一步 `⚠️ 界面未变` 点击偏了），请立即在当前步骤继续发起下一步 `phone_workspace` 工具调用，严禁只输出空内容/句号 `.` 或停下来写“下一步计划”！'
            )
            res['vision_direct'] = True
            res['image_mode'] = img_mode or 'thumb'
        if tapped_xy is not None:
            tx, ty = tapped_xy
            nx_t = int(round(tx * 1000.0 / max(1, w)))
            ny_t = int(round(ty * 1000.0 / max(1, h)))
            res['tap_calibration'] = f'🎯 已点击 `TAP({nx_t},{ny_t})`（像素 {tx},{ty}，分辨率 {w}x{h}）'
        if args.get('_coord_clamped'):
            res['clamped'] = True
            res['raw_nx'] = args.get('_raw_nx')
            res['raw_ny'] = args.get('_raw_ny')
        if scroll_dir:
            res['direction'] = scroll_dir
        if orig_requested_op:
            if res.get('op') and res.get('op') != orig_requested_op:
                res['op_internal'] = res['op']
            res['op'] = orig_requested_op
        return res
