"""Android Virtual Display Workspace Controller (`phone_workspace` & `phone_macro`).

Implements the agreed Vision + Accessibility Dual Passthrough architecture:
1. Every visual action on the Android Virtual Display returns BOTH:
   - The native Accessibility UI tree (`@e1..@eN` with normalized 0..1000 coordinates)
   - The real-time screenshot annotated with a `0..1000` ruler border, `@eN` purple badges, and tap crosshair
2. Passed directly to the active multimodal model via standard `image_url` blocks (never forwarded to a secondary OCR LLM).
3. Zero mechanical same-coordinate tap blockers (`repeated_same_spot` removed).
"""
from __future__ import annotations
import asyncio
import base64
import json
import re
import secrets
import time
from storage import normalize_node_source
from core.vision_engine import annotate_tap_crosshair, draw_vision_ruler_overlay


class PhoneControllerMixin:
    def resolve_phone_command(self, event: dict):
        req_id = str(event.get('request_id') or '')
        fut = self.phone_pending.pop(req_id, None)
        if fut and not fut.done():
            fut.set_result(event.get('result') or {'ok': True})

    async def _raw_phone_rpc(self, op: str, args: dict, timeout: float = 32.0) -> dict:
        bot_id = self.bot_context.get()
        cur_src = normalize_node_source(
            args.get('source')
            if args.get('source') in ('phone', 'phone2', '安卓一', '安卓二')
            else (self.source_context.get() or 'phone')
        )
        if cur_src not in ('phone', 'phone2'):
            cur_src = 'phone'
        req_id = 'ph-' + secrets.token_hex(6)
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self.phone_pending[req_id] = fut
        await self.publish({
            'type': 'phone_workspace_command',
            'request_id': req_id,
            'bot_id': bot_id,
            'source': cur_src,
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

    @staticmethod
    def _annotate_tap_crosshair(img_bytes: bytes, tap_x: int, tap_y: int, width: int, height: int) -> bytes:
        return annotate_tap_crosshair(img_bytes, tap_x, tap_y, width, height)

    @staticmethod
    def _draw_vision_ruler_overlay(img_bytes: bytes, a11y_summary: str = '', screen_w: int = 0, screen_h: int = 0) -> tuple[bytes, int, int]:
        return draw_vision_ruler_overlay(img_bytes, a11y_summary=a11y_summary, screen_w=screen_w, screen_h=screen_h)

    async def _auto_release_phone_if_used(self):
        if not self._vdisplay_currently_open and not self._used_phone_in_turn:
            self._phone_to_foreground_in_turn = False
            return
        was_vdisplay_open = self._vdisplay_currently_open
        self._used_phone_in_turn = False
        self._vdisplay_currently_open = False
        self._phone_to_foreground_in_turn = False
        if not was_vdisplay_open:
            return
        try:
            bot_id = self.bot_context.get()
            cur_src = normalize_node_source(self.source_context.get() or 'phone')
            if cur_src not in ('phone', 'phone2'):
                cur_src = 'phone'
            await self.publish({
                'type': 'phone_workspace_command',
                'request_id': 'ph-auto-' + secrets.token_hex(4),
                'bot_id': bot_id,
                'source': cur_src,
                'op': 'auto_release',
                'args': {'op': 'auto_release', 'delay_ms': 1800, 'to_foreground': False, 'source': cur_src},
                'timestamp': time.time(),
            })
        except Exception:
            pass

    @staticmethod
    def _resolve_phone_grid_or_norm(args: dict, width: int, height: int) -> tuple[int, int] | None:
        """Convert grid (`A1..F10`) or normalized 0..1000 (`nx`, `ny`) into pixel coordinates `(px, py)`."""
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

    @staticmethod
    def _is_user_holding_or_refusing_phone(user_text: str, was_blocked: bool = False) -> bool:
        return False

    async def _poll_and_converge_notifications(self, app_filter: str = '', limit: int = 50, timeout: float = 5.5) -> dict:
        self._last_notif_bg_poll = time.time()
        try:
            raw_res = await self._raw_phone_rpc(
                'notifications',
                {'op': 'notifications', 'app': app_filter, 'limit': max(20, min(80, limit))},
                timeout=timeout,
            )
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
        if (self.source_context.get() or 'phone') == 'pc':
            return {
                'ok': False,
                'error': '你当前位于「Windows一」，不能直接调用安卓端的 phone_workspace 工具！请调用 send_message(to_bot_id="安卓一", text="...") 直接发消息让「安卓一」自己执行并回复你。',
            }
        op = str(args.get('op') or 'status').strip().lower()
        if op in ('promote', 'expand_to_foreground', 'to_foreground', 'fullscreen', 'release', 'close', 'destroy', 'stop_display'):
            op = 'release'
            args = {**args, 'op': 'release', 'destroy': True, 'to_foreground': False}
            self._used_phone_in_turn = False
            self._vdisplay_currently_open = False
            self._phone_to_foreground_in_turn = False

        notif_ops = (
            'notifications', 'get_notifications', 'recent_notifications', 'notifs', 'notification',
            'add_filter', 'remove_filter', 'list_filters', 'clear_inbox',
        )
        non_gui_ops = (
            'status', 'device_info', 'phone_info', 'battery',
            'shell', 'bash', 'cmd', 'exec',
            'pull_file', 'push_file', 'list_files', 'files',
            'apps', 'connect', 'delay', 'wait', 'sleep',
            'release', 'auto_release', *notif_ops,
        )
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
            return await self._raw_phone_rpc('shell', {'op': 'shell', 'command': cmd_str}, timeout=35.0)

        if op in ('device_info', 'phone_info', 'battery'):
            return await self._raw_phone_rpc('device_info', {'op': 'device_info'}, timeout=10.0)

        if op in ('list_files', 'files'):
            dir_p = str(args.get('path') or args.get('target') or args.get('text') or '/sdcard/Download').strip()
            lim_f = max(1, min(100, int(args.get('limit') or 25)))
            return await self._raw_phone_rpc('list_files', {'op': 'list_files', 'path': dir_p, 'limit': lim_f}, timeout=15.0)

        if op == 'pull_file':
            file_p = str(args.get('path') or args.get('target') or args.get('text') or 'latest').strip()
            src_dest = str(args.get('source') or 'workspace').strip().lower()
            if src_dest not in ('workspace', 'computer'):
                src_dest = 'workspace'
            return await self._raw_phone_rpc('pull_file', {'op': 'pull_file', 'path': file_p, 'source': src_dest}, timeout=45.0)

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
                return self.notification_hub.remove_filter(
                    str(args.get('filter_id') or args.get('id') or args.get('filter_keyword') or args.get('keyword') or args.get('target') or args.get('app') or '')
                )
            if action == 'list_filters':
                return {'ok': True, 'action': 'list_filters', 'filters': self.notification_hub.get_filters()}
            if action == 'clear_inbox':
                return self.notification_hub.clear_inbox(str(args.get('app') or args.get('target') or ''))
            app_q = str(args.get('app') or args.get('package') or args.get('target') or args.get('text') or '').strip()
            lim = int(args.get('limit') or 30)
            poll_info = await self._poll_and_converge_notifications(app_filter='', limit=max(40, lim), timeout=8.0)
            inbox_res = self.notification_hub.query_inbox(app_query=app_q, limit=lim, mark_seen=True)
            return {
                'ok': True,
                'op': 'notifications',
                'live_polled': poll_info.get('ok', False),
                **inbox_res,
            }

        if op in ('send_screenshot', 'share_image', 'send_qr', 'send_image'):
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
                return {'ok': False, 'error': '未能获取手机虚拟屏当前画面'}
            crop_box = args.get('crop_box') if isinstance(args.get('crop_box'), list) else None
            media_url = self._prepare_chat_screenshot(raw_clean, crop_box=crop_box, prefix='phone-shot')
            return {
                'ok': bool(media_url),
                'op': 'send_screenshot',
                'chat_image_url': media_url,
                'cropped': bool(crop_box),
                'note': f'已将手机虚拟屏干净无标尺截图直接挂载到本轮聊天回复气泡上（{media_url}），主人打开聊天框即可直接扫码或查看！',
            }

        if op in ('run', 'task', 'inspect'):
            op = 'ui_tree'
            args = {'op': 'ui_tree', 'refresh': True}

        cur_w, cur_h = self._last_phone_size
        if op == 'open':
            comp_cand = str(args.get('component') or '').strip()
            u_cand = str(args.get('uri') or args.get('url') or args.get('target') or args.get('app') or args.get('package') or args.get('text') or '').strip()
            is_bare_domain = (
                bool(u_cand)
                and not comp_cand
                and ' ' not in u_cand
                and (u_cand.startswith('www.') or '/' in u_cand)
                and '.' in u_cand
                and not re.match(r'^(?:com|cn|org|net|io|dev|me|tv|app|gov|edu)\.[a-zA-Z0-9_.]+$', u_cand)
            )
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
            if op == 'scroll' and (args.get('target') or args.get('text') or args.get('label')):
                op = 'scroll_to'
                args = {**args, 'op': 'scroll_to'}
            elif args.get('x1') is None and args.get('start_x') is None and args.get('nx') is None and not args.get('grid'):
                direction = str(args.get('direction') or 'down').strip().lower()
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
            if args.get('ref') or args.get('target') or args.get('x') is not None or args.get('nx') is not None or args.get('grid') or args.get('submit'):
                op = 'tap_and_type'
                args = {**args, 'op': 'tap_and_type'}
            else:
                op = 'text'
                args = {**args, 'op': 'text'}
        elif op == 'tap':
            t_in = str(args.get('target') or args.get('label') or '').strip()
            r_in = str(args.get('ref') or '').strip()
            if not r_in and t_in.startswith(('@e', '@v', 'e', 'v')) and re.fullmatch(r'@?[ev]\d+', t_in, flags=re.IGNORECASE):
                r_in = t_in if t_in.startswith('@') else ('@' + t_in)
                t_in = ''
            if (r_in or t_in or args.get('text') or args.get('view_id') or args.get('id')) and args.get('x') is None and args.get('nx') is None and not args.get('grid'):
                op = 'tap_node'
                args = {
                    **args,
                    'op': 'tap_node',
                    'ref': r_in,
                    'target': t_in,
                    'label': '',
                    'text': str(args.get('text') or t_in).strip(),
                }

        resolved_pt = self._resolve_phone_grid_or_norm(args, cur_w, cur_h)
        if resolved_pt is not None and op in ('tap', 'tap_node', 'click_node', 'tap_and_type', 'long_press'):
            args = {**args, 'x': resolved_pt[0], 'y': resolved_pt[1]}
            if op in ('tap_node', 'click_node') and not args.get('ref') and not args.get('text') and not args.get('target'):
                op = 'tap'
                args['op'] = 'tap'

        if op in ('wait', 'delay', 'sleep'):
            ms = int(args.get('delay_ms') or args.get('ms') or 600)
            await asyncio.sleep(max(0.05, min(5.0, ms / 1000.0)))
            return {'ok': True, 'op': 'delay', 'delay_ms': ms}

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
            batch_results = []
            last_res: dict = {'ok': True}
            for idx_s, step_item in enumerate(steps_list):
                is_last_step = (idx_s + 1 == len(steps_list))
                step_payload = step_item if is_last_step else {**step_item, '_skip_vision': True}
                last_res = await self._tool_phone_workspace(step_payload)
                batch_results.append({'step': idx_s + 1, 'op': step_item.get('op'), 'ok': not bool(isinstance(last_res, dict) and last_res.get('error'))})
                if isinstance(last_res, dict) and last_res.get('error'):
                    break
                if not is_last_step:
                    await asyncio.sleep(0.22)
            if isinstance(last_res, dict):
                last_res['batch_steps_executed'] = batch_results
            return last_res

        ref_arg = str(args.get('ref') or '').strip()
        target_only_arg = str(args.get('target') or args.get('label') or '').strip()
        id_arg = str(args.get('view_id') or args.get('id') or '').strip()

        if op == 'tap_and_type':
            content_txt = str(args.get('text') or '').strip()
            tx = int(args['x']) if args.get('x') is not None else None
            ty = int(args['y']) if args.get('y') is not None else None
            tap_r: dict = {}
            if tx is not None and ty is not None:
                tap_r = await self._raw_phone_rpc('tap', {'op': 'tap', 'x': tx, 'y': ty})
            elif ref_arg or target_only_arg or id_arg:
                tap_r = await self._raw_phone_rpc('tap_node', {'op': 'tap_node', 'ref': ref_arg, 'text': target_only_arg, 'id': id_arg})
                if isinstance(tap_r, dict) and tap_r.get('x') is not None and tap_r.get('y') is not None:
                    tx, ty = int(float(tap_r['x'])), int(float(tap_r['y']))
            if not tap_r or (isinstance(tap_r, dict) and tap_r.get('error')):
                return {
                    'ok': False,
                    'error': '未找到指定的输入框节点，请直接对照实时截图四周的 0..1000 坐标尺传入 nx=..., ny=... 点击输入框并输入文字。',
                }
            await asyncio.sleep(0.35)
            type_r = await self._raw_phone_rpc('text', {'op': 'text', 'text': content_txt, 'clear': bool(args.get('clear', False))}) if (content_txt or args.get('clear')) else {}
            if args.get('submit'):
                await asyncio.sleep(0.22)
                key_r = await self._raw_phone_rpc('key', {'op': 'key', 'key': 'enter'})
                if isinstance(key_r, dict) and key_r.get('ui_tree'):
                    type_r = key_r
            res = {
                **(tap_r if isinstance(tap_r, dict) else {}),
                **(type_r if isinstance(type_r, dict) else {}),
                'ok': True,
                'op': 'tap_and_type',
                'ref': ref_arg,
                'x': tx,
                'y': ty,
                'typed': bool(content_txt),
                'submitted': bool(args.get('submit')),
            }
        elif op == 'long_press':
            pass
        else:
            reused_existing_app = False
            if op == 'launch' and not str(args.get('component') or '').strip() and not args.get('force'):
                req_app = str(args.get('app') or args.get('target') or args.get('package') or args.get('text') or '').strip()
                if req_app and (self._vdisplay_currently_open or self._last_phone_pkg):
                    await self._raw_phone_rpc('create', {'op': 'create'})
                    probe_tree = await self._raw_phone_rpc('ui_tree', {'op': 'ui_tree', 'refresh': True})
                    if isinstance(probe_tree, dict) and not probe_tree.get('error'):
                        probe_pkg = str(probe_tree.get('package') or '')
                        if probe_pkg and self._matches_requested_app_package(req_app, probe_pkg):
                            res = {**probe_tree, 'reused_active_session': True}
                            op = 'ui_tree'
                            reused_existing_app = True
            if not reused_existing_app:
                res = await self._raw_phone_rpc(op, args)
                if (
                    isinstance(res, dict)
                    and op not in ('launch', 'create', 'release', 'auto_release', 'status', 'apps')
                    and any(k in str(res.get('error') or '') for k in ('虚拟屏尚未启动', '先创建独立工作区', '尚未收到工作区画面'))
                ):
                    wake_r = await self._raw_phone_rpc('create', {'op': 'create'})
                    if isinstance(wake_r, dict) and not wake_r.get('error'):
                        self._vdisplay_currently_open = True
                        res = await self._raw_phone_rpc(op, args)

        if not isinstance(res, dict):
            return res
        if open_uri_meta:
            res = {**open_uri_meta, **res}
        disp_info = res.get('display') if isinstance(res.get('display'), dict) else {}
        if op in ('launch', 'create', 'open_uri') or bool(disp_info.get('created') and not disp_info.get('released')):
            if not res.get('error'):
                self._vdisplay_currently_open = True

        w = int(res.get('width') or self._last_phone_size[0] or 1080)
        h = int(res.get('height') or self._last_phone_size[1] or 2400)
        if w > 100 and h > 100:
            self._last_phone_size = (w, h)
        pkg = str(res.get('package') or '')
        if pkg:
            self._last_phone_pkg = pkg
        node_count = res.get('node_count') if 'node_count' in res else res.get('nodeCount')
        if node_count is not None:
            self._last_phone_node_count = int(node_count)

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

        b64_img = ''
        raw_jpg = b''
        if res.get('image'):
            b64_img = str(res['image']).split(',')[-1]
            raw_jpg = base64.b64decode(b64_img)
            res = {k: v for k, v in res.items() if k != 'image'}
            res['saved_screenshot'] = 'phone-latest.jpg'

        existing_summary = str(res.get('ui_tree') or res.get('summary') or '').strip()
        m_hash = re.search(r'treeHash=([0-9a-fA-F-]+)', existing_summary)
        cur_tree_hash = f"{pkg}:{m_hash.group(1)}" if m_hash else ''
        if cur_tree_hash:
            self._last_phone_tree_hash = cur_tree_hash

        is_visual_op = not bool(args.get('_skip_vision')) and op in (
            'launch', 'ui_tree', 'dump', 'tree', 'nodes', 'read_screen', 'capture',
            'tap', 'tap_node', 'click_node', 'tap_and_type', 'swipe', 'text',
            'type_node', 'key', 'open_uri', 'long_press',
        )
        if is_visual_op and not raw_jpg:
            snap = await self._raw_phone_rpc('preview', {'op': 'preview', 'since': -1})
            if snap.get('image'):
                b64_img = str(snap['image']).split(',')[-1]
                w = int(snap.get('width') or w)
                h = int(snap.get('height') or h)
                self._last_phone_size = (w, h)
                raw_jpg = base64.b64decode(b64_img)
                res['saved_screenshot'] = 'phone-latest.jpg'

        if raw_jpg:
            (self.harness.root / 'phone-clean.jpg').write_bytes(raw_jpg)
            if args.get('send_to_chat'):
                crop_b = args.get('crop_box') if isinstance(args.get('crop_box'), list) else None
                res['chat_image_url'] = self._prepare_chat_screenshot(raw_jpg, crop_box=crop_b, prefix='phone-shot')
            if tapped_xy is not None:
                raw_jpg = self._annotate_tap_crosshair(raw_jpg, tapped_xy[0], tapped_xy[1], w, h)
            annotated_jpg, _, _ = self._draw_vision_ruler_overlay(
                raw_jpg, a11y_summary=existing_summary, screen_w=w, screen_h=h
            )
            (self.harness.root / 'phone-latest.jpg').write_bytes(annotated_jpg)
            b64_img = base64.b64encode(annotated_jpg).decode('ascii')

        if is_visual_op and b64_img:
            change_line = f"{res.get('change_summary')}\n" if res.get('change_summary') else ''
            res['_multimodal_b64'] = b64_img
            res['_multimodal_mime'] = 'image/jpeg'
            res['_multimodal_caption'] = (
                f'【手机虚拟屏最新实时画面 (op={op}, 包名={pkg or "unknown"}, 物理尺寸={w}x{h})】\n'
                f'{change_line}'
                '1. 画面四周已绘制 `0..1000` 归一化坐标标尺（顶部 `X:200/500/800`，左右 `Y:100..900`），同时无障碍树中每个 `@eN` 节点也已附带 `norm=(nx,ny)` 0..1000 坐标与紫底角标。\n'
                '2. 请结合「实时截图画面」与「无障碍树文本」直接决策：\n'
                '   - 若无障碍树中有目标控件且紫底 `@eN` 准确对准目标，可直接调 `phone_workspace(op="tap", target="@eN")`；\n'
                '   - 若为微信/小程序等自绘界面（`node_count=0`）或 `@eN` 偏移，请直接看实时截图对照四周 `0..1000` 标尺调用 `phone_workspace(op="tap", nx=0..1000, ny=0..1000)` 点击！'
            )
            res['vision_direct'] = True

        if tapped_xy is not None:
            tx, ty = tapped_xy
            nx_t = int(round(tx * 1000.0 / max(1, w)))
            ny_t = int(round(ty * 1000.0 / max(1, h)))
            res['tap_calibration'] = f'🎯 已在截图绘制红色准星 `TAP({nx_t},{ny_t})`（像素坐标 {tx},{ty}，屏幕尺寸 {w}x{h}）。'
        return res

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

