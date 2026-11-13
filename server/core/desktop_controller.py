"""Cloud & Local PC Desktop Controller (`cloud_desktop` & `dsh_workspace`).

Handles:
1. Cloud Desktop (`cloud_desktop`) GUI/Browser/Terminal operations with direct 0..1000 ruler + `@bN` DOM badge multimodal passthrough.
2. Windows PC DeepSeek Harness (`dsh_workspace`) sessions, CUA native window control, local shell/filesystem RPC, and multi-device peer state.
"""
from __future__ import annotations
import asyncio
import base64
import os
import re
import secrets
import time
from pathlib import Path
from assistants import get_assistant
from storage import node_display_name, normalize_node_source
from core.vision_engine import annotate_tap_crosshair, draw_vision_ruler_overlay


class DesktopControllerMixin:
    async def _broadcast_desktop_frame(self, res_or_frame: dict, op: str):
        if not isinstance(res_or_frame, dict):
            return
        b64 = res_or_frame.get('image')
        if b64:
            try:
                raw_clean = base64.b64decode(str(b64).split(',')[-1])
                (self.harness.root / 'desktop-clean.jpg').write_bytes(raw_clean)
                (self.harness.root / 'desktop-latest.jpg').write_bytes(raw_clean)
            except Exception:
                pass
        room_id = self.room_context.get() or self.bot_context.get()
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

    async def _finalize_desktop_result(self, res: dict, op: str, tap_xy: tuple[int, int] | None = None) -> dict:
        if not isinstance(res, dict):
            return res
        b64_raw = str(res.get('image') or '').strip()
        if not b64_raw:
            return res
        await self._broadcast_desktop_frame(res, op)
        w = int(res.get('width') or 1280)
        h = int(res.get('height') or 800)
        clean_res = {k: v for k, v in res.items() if k != 'image'}
        clean_res['saved_screenshot'] = 'desktop-latest.jpg'
        try:
            raw_bytes = base64.b64decode(b64_raw.split(',')[-1])
            if tap_xy is not None:
                raw_bytes = annotate_tap_crosshair(raw_bytes, tap_xy[0], tap_xy[1], w, h)
            dom_elems = (
                clean_res.get('dom_elements')
                or (clean_res.get('dom_snapshot') or {}).get('elements')
                or []
            )
            dom_summary_lines = []
            for el in dom_elems[:25]:
                if isinstance(el, dict) and el.get('ref'):
                    ex = int(el.get('x') or 0)
                    ey = int(el.get('y') or 0)
                    enx = int(el.get('nx') if el.get('nx') is not None else round(ex * 1000 / max(1, w)))
                    eny = int(el.get('ny') if el.get('ny') is not None else round(ey * 1000 / max(1, h)))
                    el['nx'] = enx
                    el['ny'] = eny
                    elbl = str(el.get('label') or '')[:50]
                    etag = str(el.get('tag') or 'el')
                    if ex > 0 and ey > 0:
                        dom_summary_lines.append(f"{el['ref']} [{etag}] \"{elbl}\" center=({ex},{ey}) norm=({enx},{eny})")
            annotated_bytes, _, _ = draw_vision_ruler_overlay(
                raw_bytes,
                a11y_summary='\n'.join(dom_summary_lines),
                screen_w=w,
                screen_h=h,
            )
            (self.harness.root / 'desktop-latest.jpg').write_bytes(annotated_bytes)
            clean_res['_multimodal_b64'] = base64.b64encode(annotated_bytes).decode('ascii')
            clean_res['_multimodal_mime'] = 'image/jpeg'
            clean_res['_multimodal_caption'] = (
                f'【云电脑最新画面 (op={op}, 分辨率 {w}x{h})】\n'
                '画面四周已标注 `0..1000` 归一化坐标标尺（顶部 `X:200/500/800`，两侧 `Y:100..900`），网页交互元素已标注 `@b1..@bN` 紫底角标。\n'
                '请直接看图操作：点击任意位置调 `cloud_desktop(op="tap", nx=0..1000, ny=0..1000)` 或 `cloud_desktop(op="tap", ref="@b1")`。'
            )
        except Exception:
            pass
        return clean_res

    async def _stage_pc_file_to_cloud_workspace(self, raw_p: str) -> Path | None:
        if not raw_p or not self.is_dsh_online():
            return None
        pc_res = await self._raw_dsh_rpc('pc_fs', {'fs_op': 'read_file', 'path': raw_p, 'base64': True}, timeout=25.0)
        if not isinstance(pc_res, dict) or not pc_res.get('ok'):
            return None
        b64 = str(pc_res.get('_multimodal_b64') or pc_res.get('data_b64') or '').strip()
        if b64:
            raw_bytes = base64.b64decode(b64.split(',')[-1])
        elif isinstance(pc_res.get('content'), str):
            raw_bytes = pc_res['content'].encode('utf-8')
        else:
            return None
        safe_name = re.sub(r'[^a-zA-Z0-9._\u4e00-\u9fa5-]', '_', Path(raw_p.replace('\\', '/')).name) or 'pc-file.bin'
        staged = self.harness.root / 'uploads' / safe_name
        staged.parent.mkdir(parents=True, exist_ok=True)
        staged.write_bytes(raw_bytes)
        return staged

    async def _tool_cloud_desktop(self, args: dict):
        if self.computer is None:
            return {'error': '云桌面组件未挂载'}
        op = str(args.get('op') or args.get('action') or 'status').strip().lower()
        if op == 'open_url':
            op = 'navigate'
        elif op == 'browser_snapshot':
            op = 'screenshot'
        if op == 'open':
            target_val = str(args.get('url') or args.get('uri') or args.get('target') or args.get('app') or args.get('text') or '').strip()
            if target_val.startswith(('http://', 'https://')) or ('.' in target_val and ' ' not in target_val and not target_val.startswith('/')):
                url_to_open = target_val if target_val.startswith(('http://', 'https://')) else ('https://' + target_val)
                args = {**args, 'op': 'navigate', 'url': url_to_open}
                op = 'navigate'
            else:
                args = {**args, 'op': 'launch_app', 'app': target_val or 'lite_browser'}
                op = 'launch_app'
        elif op == 'tap':
            op = 'click'
        elif op == 'press':
            op = 'key'
        elif op == 'close':
            op = 'close_window'
        elif op == 'inspect':
            op = 'ui_tree'
        elif op in ('delay', 'sleep'):
            op = 'wait'

        try:
            if op in ('files', 'list_files'):
                return await self.computer.client.call('GET', '/files')
            if op == 'pull_file':
                rel_p = str(args.get('path') or args.get('target') or args.get('text') or '').strip()
                if not rel_p:
                    files_res = await self.computer.client.call('GET', '/files')
                    items = (files_res.get('files') or []) if isinstance(files_res, dict) else []
                    if not items:
                        return {'ok': False, 'error': '云电脑下载/工作区目录暂无文件'}
                    rel_p = str(items[0].get('path') or '')
                if not rel_p.startswith(('downloads/', 'workspace/')):
                    rel_p = 'downloads/' + rel_p.lstrip('/')
                dest_dir = self.harness.root / 'uploads'
                dest_dir.mkdir(parents=True, exist_ok=True)
                dest_file = dest_dir / Path(rel_p).name
                receipt = await self.computer.client.get_file(rel_p, dest_file)
                return {
                    'ok': True,
                    'op': 'pull_file',
                    'source_path': rel_p,
                    'workspace_path': f'uploads/{dest_file.name}',
                    'bytes': receipt.get('bytes', 0),
                }
            if op == 'push_file':
                orig_p = str(args.get('path') or args.get('target') or args.get('text') or '').strip()
                rel_p = orig_p[len('workspace/'):] if orig_p.startswith('workspace/') else orig_p
                src_file = (self.harness.root / rel_p).resolve() if not re.match(r'^[A-Za-z]:[/\\]', orig_p) else Path('__nonexistent__')
                if not src_file.is_file() or not src_file.is_relative_to(self.harness.root.resolve()):
                    staged = await self._stage_pc_file_to_cloud_workspace(orig_p)
                    if staged is None or not staged.is_file():
                        return {'ok': False, 'error': f'工作区文件不存在: {orig_p}'}
                    src_file = staged
                    rel_p = f'uploads/{staged.name}'
                receipt = await self.computer.client.upload_file(src_file)
                return {
                    'ok': True,
                    'op': 'push_file',
                    'workspace_path': rel_p,
                    'desktop_path': receipt.get('path', f'workspace/{src_file.name}'),
                    'bytes': receipt.get('bytes', src_file.stat().st_size),
                }
            if op in ('send_screenshot', 'share_image', 'send_qr', 'send_image'):
                frame = await self.computer.client.call('GET', '/frame')
                await self._broadcast_desktop_frame(frame, 'send_screenshot')
                b64_f = str(frame.get('image') or '').strip()
                if not b64_f:
                    return {'ok': False, 'error': '未能获取云电脑当前截图'}
                raw_clean = base64.b64decode(b64_f.split(',')[-1])
                crop_box = args.get('crop_box') if isinstance(args.get('crop_box'), list) else None
                media_url = self._prepare_chat_screenshot(raw_clean, crop_box=crop_box, prefix='desktop-shot')
                return {
                    'ok': bool(media_url),
                    'op': 'send_screenshot',
                    'chat_image_url': media_url,
                    'cropped': bool(crop_box),
                    'note': f'已将云电脑干净无标尺截图直接挂载到本轮聊天回复气泡上（{media_url}），主人打开聊天框即可直接扫码或查看！',
                }
            if op == 'status':
                st = await self.computer.client.status(force=True)
                try:
                    fr = await self.computer.client.call('GET', '/frame')
                    await self._broadcast_desktop_frame(fr, 'status')
                except Exception:
                    pass
                return st
            if op in ('screenshot', 'capture', 'frame', 'ui_tree', 'read_screen', 'nodes'):
                frame = await self.computer.client.call('GET', '/frame')
                st = await self.computer.client.status(force=True)
                out = {
                    **frame,
                    'ok': True,
                    'captured': True,
                    'width': frame.get('width', 1280),
                    'height': frame.get('height', 800),
                    'title': frame.get('title') or st.get('title', ''),
                    'url': st.get('url', ''),
                    'windows': st.get('windows', []),
                    'window_count': st.get('window_count', 0),
                    'dom_elements': st.get('dom_elements', []),
                    'page_text': st.get('page_text', ''),
                    'saved_to': 'desktop-latest.jpg',
                }
                return await self._finalize_desktop_result(out, 'inspect')
            if op in ('bash', 'exec', 'shell', 'terminal') or (op == 'run' and (args.get('command') or args.get('goal'))):
                cmd = str(args.get('command') or '').strip()
                goal = str(args.get('goal') or args.get('text') or args.get('url') or '').strip()
                if not cmd and goal:
                    m_url = re.search(r'https?://[^\s"\'<>]+', goal)
                    if m_url and not any(k in goal for k in (';', '&&', '|', 'python', 'pip', 'gcc', 'ls ', 'cat ', 'echo ')):
                        res = await self.computer.client.call('POST', '/navigate', body={'url': m_url.group(0), 'include_frame': True})
                        return await self._finalize_desktop_result(res, 'navigate')
                    cmd = goal
                if not cmd:
                    return {'error': 'cloud_desktop bash 需要提供 command'}
                timeout_s = max(5, min(90, int(args.get('timeout') or 25)))
                res = await self.computer.client.call('POST', '/input', body={'kind': 'bash', 'command': cmd, 'timeout': timeout_s, 'include_frame': True})
                return await self._finalize_desktop_result(res, 'bash')
            if op == 'navigate':
                res = await self.computer.client.call('POST', '/navigate', body={'url': str(args.get('url') or args.get('target') or args.get('text') or ''), 'include_frame': True})
                return await self._finalize_desktop_result(res, 'navigate')
            if op in ('search', 'web_search'):
                q = str(args.get('query') or args.get('text') or args.get('goal') or args.get('url') or '').strip()
                engine = str(args.get('engine') or args.get('target') or 'auto').strip()
                res = await self.computer.client.call('POST', '/search', body={'query': q, 'engine': engine, 'include_frame': True})
                return await self._finalize_desktop_result(res, 'search')
            if op in ('launch_app', 'app', 'open_app'):
                app_name = str(args.get('app') or args.get('target') or args.get('text') or 'lite_browser').strip()
                res = await self.computer.client.call('POST', '/input', body={'kind': 'launch_app', 'app': app_name, 'text': str(args.get('url') or args.get('text') or ''), 'include_frame': True})
                return await self._finalize_desktop_result(res, 'launch_app')
            if op == 'batch':
                steps = args.get('steps') if isinstance(args.get('steps'), list) else []
                res = await self.computer.client.call('POST', '/input', body={'kind': 'batch', 'steps': steps[:20], 'include_frame': True})
                return await self._finalize_desktop_result(res, 'batch')

            ref_in = str(args.get('ref') or '').strip()
            target_in = str(args.get('target') or '').strip()
            if not ref_in and target_in.startswith(('@b', '@v', '@e')):
                ref_in = target_in
                target_in = ''
            if op == 'click' and ref_in.startswith('@b'):
                op = 'click_ref'
                args = {**args, 'ref': ref_in}
            elif op == 'type' and ref_in.startswith('@b'):
                op = 'type_ref'
                args = {**args, 'ref': ref_in}
            if op in ('click_text', 'tap_node', 'click_node') or (
                op == 'click'
                and args.get('x') is None
                and args.get('nx') is None
                and not args.get('grid')
                and (args.get('text') or target_in or ref_in)
            ):
                txt_q = str(target_in or args.get('text') or args.get('goal') or ref_in).strip()
                res = await self.computer.client.call('POST', '/input', body={'kind': 'find_text', 'text': txt_q, 'click': True, 'include_frame': True})
                return await self._finalize_desktop_result(res, 'click')
            if op in ('click', 'double_click', 'right_click', 'type', 'key', 'hotkey', 'scroll', 'move', 'hover', 'drag', 'wait', 'find_text', 'focus_window', 'close_window', 'tile_windows', 'clipboard', 'click_ref', 'type_ref'):
                tap_xy: tuple[int, int] | None = None
                if op == 'type' and (args.get('x') is not None or args.get('nx') is not None or args.get('grid') or target_in):
                    click_step = await self._tool_cloud_desktop({
                        'op': 'click',
                        'x': args.get('x'),
                        'y': args.get('y'),
                        'nx': args.get('nx'),
                        'ny': args.get('ny'),
                        'grid': args.get('grid'),
                        'target': target_in,
                    })
                    if isinstance(click_step, dict) and click_step.get('x') is not None and click_step.get('y') is not None:
                        tap_xy = (int(click_step['x']), int(click_step['y']))
                    await asyncio.sleep(0.18)
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
                        nx=int(args.get('nx') if args.get('nx') is not None else -1),
                        ny=int(args.get('ny') if args.get('ny') is not None else -1),
                        nx2=int(args.get('nx2') if args.get('nx2') is not None else -1),
                        ny2=int(args.get('ny2') if args.get('ny2') is not None else -1),
                        grid=str(args.get('grid') or ''),
                        grid2=str(args.get('grid2') or ''),
                    )
                elif op == 'wait':
                    wait_amt = int(args.get('amount') or (max(1, int(args.get('duration_ms') or 1000) // 1000)) or 1)
                    payload.update(amount=max(1, min(10, wait_amt)))
                elif op == 'find_text':
                    payload.update(text=str(args.get('text') or args.get('query') or args.get('target') or ''))
                elif op in ('type', 'clipboard'):
                    payload.update(text=str(args.get('text') or ''))
                elif op in ('key', 'hotkey'):
                    payload.update(key=str(args.get('key') or args.get('target') or args.get('text') or 'Return'))
                elif op == 'scroll':
                    payload.update(
                        x=int(args.get('x') if args.get('x') is not None else 640),
                        y=int(args.get('y') if args.get('y') is not None else 400),
                        nx=int(args.get('nx') if args.get('nx') is not None else -1),
                        ny=int(args.get('ny') if args.get('ny') is not None else -1),
                        grid=str(args.get('grid') or ''),
                        direction=str(args.get('direction') or args.get('text') or 'down').strip().lower(),
                        amount=max(1, min(20, int(args.get('amount') or 4))),
                    )
                elif op in ('focus_window', 'close_window'):
                    payload.update(target=str(args.get('target') or args.get('app') or args.get('text') or ''))
                elif op in ('click_ref', 'type_ref'):
                    payload.update(ref=str(args.get('ref') or args.get('target') or ''), text=str(args.get('text') or ''))
                res = await self.computer.client.call('POST', '/input', body=payload)
                if op in ('type', 'type_ref') and args.get('submit'):
                    await asyncio.sleep(0.15)
                    res = await self.computer.client.call('POST', '/input', body={'kind': 'key', 'key': 'Return', 'include_frame': True})
                if isinstance(res, dict) and res.get('x') is not None and res.get('y') is not None and op in ('click', 'double_click', 'right_click'):
                    try:
                        tap_xy = (int(res['x']), int(res['y']))
                    except (ValueError, TypeError):
                        pass
                return await self._finalize_desktop_result(res, op, tap_xy=tap_xy)
            return {'error': f'未知 cloud_desktop op: {op}'}
        except Exception as e:
            return {'error': str(e)[:260]}

    def is_dsh_online(self, ws_clients_count: int = 0) -> bool:
        if ws_clients_count > 0:
            return True
        if not self.dsh_bridge_info.get('online'):
            return False
        last_seen = float(self.dsh_bridge_info.get('last_seen') or 0.0)
        return (time.time() - last_seen) < 90.0

    def extract_dialogs_by_source(self, source: str = 'phone', limit: int = 3) -> tuple[list[dict], str]:
        target_src = normalize_node_source(source)
        cap = max(1, min(100, int(limit or 3)))
        all_msgs = self.store.state.get('messages') or []
        bot_names = {b['id']: b['name'] for b in self.store.state.get('assistants', [])}
        dialogs: list[dict] = []
        cur_u = ''
        cur_t = ''
        cur_b = '主助理'
        scan_window = max(60, cap * 4)
        filtered_msgs = [m for m in all_msgs if normalize_node_source(m.get('source')) == target_src][-scan_window:]
        for m in filtered_msgs:
            role = m.get('role')
            txt = str(m.get('text') or '').strip()
            if not txt:
                continue
            ts = float(m.get('created_at') or m.get('time') or 0)
            t_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(ts)) if ts > 0 else ''
            b_id = str(m.get('bot_id') or 'companion')
            b_name = str(m.get('from_bot_name') or bot_names.get(b_id) or '主助理')
            if role == 'user':
                if cur_u:
                    dialogs.append({
                        'time': cur_t,
                        'bot_name': cur_b,
                        'source': target_src,
                        'user': cur_u[:240],
                        'assistant': '(处理中...)',
                    })
                cur_u = txt
                cur_t = t_str
                cur_b = bot_names.get(b_id, '主助理')
            elif role == 'assistant':
                dialogs.append({
                    'time': t_str or cur_t,
                    'bot_name': b_name,
                    'source': target_src,
                    'user': cur_u[:240] if cur_u else '(主动通知)',
                    'assistant': txt[:320],
                })
                cur_u = ''
        if cur_u:
            dialogs.append({
                'time': cur_t,
                'bot_name': cur_b,
                'source': target_src,
                'user': cur_u[:240],
                'assistant': '(处理中...)',
            })
        sliced = dialogs[-cap:]
        last_time = sliced[-1]['time'] if sliced else ''
        return sliced, last_time

    def update_dsh_peer(self, payload: dict | None = None) -> str:
        now_t = time.time()
        self.dsh_bridge_info['online'] = True
        self.dsh_bridge_info['last_seen'] = now_t
        if not isinstance(payload, dict):
            return str(self.dsh_bridge_info.get('active_device_id') or 'pc_dsh')

        st_in = payload.get('status') if isinstance(payload.get('status'), dict) else {}
        if not st_in and any(k in payload for k in ('dsh_running', 'sessions', 'workspaces', 'last_3_dialogs')):
            st_in = {
                k: v for k, v in payload.items()
                if k in ('dsh_running', 'sessions', 'workspaces', 'active_session_id', 'version', 'last_3_dialogs', 'last_active_time', 'sessions_count', 'running_count')
            }

        raw_dev_id = str(
            payload.get('device_id')
            or payload.get('peer_id')
            or payload.get('id')
            or st_in.get('device_id')
            or 'pc_dsh'
        ).strip() or 'pc_dsh'

        dev_name = str(
            payload.get('device_name')
            or payload.get('name')
            or st_in.get('device_name')
            or (f'{raw_dev_id} (电脑端 DSH)' if raw_dev_id != 'pc_dsh' else 'PC DeepSeek Harness (DSB)')
        ).strip()

        peers = self.dsh_bridge_info.setdefault('peers', {})
        existing = peers.get(raw_dev_id) if isinstance(peers.get(raw_dev_id), dict) else {}
        prev_status = existing.get('status') if isinstance(existing.get('status'), dict) else {}
        merged_status = {**prev_status, **st_in} if st_in else dict(prev_status)

        raw_sessions = merged_status.get('sessions')
        if isinstance(raw_sessions, list):
            tagged_sessions = []
            for s in raw_sessions:
                if isinstance(s, dict):
                    tagged_sessions.append({
                        **s,
                        'device_id': str(s.get('device_id') or raw_dev_id),
                        'device_name': str(s.get('device_name') or dev_name),
                        'device_type': 'pc',
                        'source': 'pc',
                    })
            merged_status['sessions'] = tagged_sessions
        else:
            tagged_sessions = list(existing.get('sessions') or [])

        if st_in:
            self.dsh_bridge_info['status'] = dict(merged_status)

        peer_entry = {
            **existing,
            'id': raw_dev_id,
            'peer_id': raw_dev_id,
            'device_id': raw_dev_id,
            'name': dev_name,
            'device_name': dev_name,
            'kind': 'pc',
            'device_type': 'pc',
            'source': 'pc',
            'online': True,
            'last_seen': now_t,
            'sessions': tagged_sessions,
            'sessions_count': int(merged_status.get('sessions_count') or len(tagged_sessions)),
            'running_count': int(merged_status.get('running_count') or sum(1 for s in tagged_sessions if s.get('status') == 'running')),
            'status': merged_status,
        }
        peers[raw_dev_id] = peer_entry
        if raw_dev_id != 'pc_dsh' or not self.dsh_bridge_info.get('active_device_id'):
            self.dsh_bridge_info['active_device_id'] = raw_dev_id
        return raw_dev_id

    def resolve_target_dsh_peer(self, device_id: str | None = None) -> tuple[str, dict | None]:
        peers_map = self.dsh_bridge_info.get('peers') or {}
        req = str(device_id or '').strip()
        if req and req.lower() not in ('auto', 'default', 'active', 'latest', 'pc', 'pc_dsh'):
            if req in peers_map and isinstance(peers_map[req], dict):
                return req, peers_map[req]
            low = req.lower()
            prefixed = low if low.startswith('pc-') else f'pc-{low}'
            for k, v in peers_map.items():
                if not isinstance(v, dict):
                    continue
                kl = str(k).lower()
                nm = str(v.get('device_name') or v.get('name') or '').lower()
                if kl == low or kl == prefixed or low in nm:
                    return str(k), v
            return req, None

        active_id = str(self.dsh_bridge_info.get('active_device_id') or '').strip()
        if active_id and active_id in peers_map and isinstance(peers_map[active_id], dict):
            return active_id, peers_map[active_id]

        specific_peers = [
            (k, v) for k, v in peers_map.items()
            if k not in ('pc_dsh', 'phone_dsb') and isinstance(v, dict)
        ]
        if specific_peers:
            specific_peers.sort(key=lambda item: float(item[1].get('last_seen') or 0.0), reverse=True)
            return specific_peers[0][0], specific_peers[0][1]

        if 'pc_dsh' in peers_map and isinstance(peers_map['pc_dsh'], dict):
            return 'pc_dsh', peers_map['pc_dsh']
        return 'pc_dsh', None

    def get_dsh_peers(self, ws_clients_count: int = 0, phone_clients_count: int = 0) -> list[dict]:
        dsh_on = self.is_dsh_online(ws_clients_count)
        last_seen = float(self.dsh_bridge_info.get('last_seen') or 0.0)
        peers_map = dict(self.dsh_bridge_info.get('peers') or {})
        pc_peer = {
            'id': 'pc_dsh',
            'peer_id': 'pc_dsh',
            'device_id': 'pc_dsh',
            'name': 'PC DeepSeek Harness (DSB)',
            'device_name': 'PC DeepSeek Harness (DSB)',
            'kind': 'pc',
            'device_type': 'pc',
            'source': 'pc',
            'online': dsh_on,
            'ws_connected': ws_clients_count > 0,
            'last_seen': last_seen,
            'status': self.dsh_bridge_info.get('status') or {},
        }
        if 'pc_dsh' in peers_map and isinstance(peers_map['pc_dsh'], dict):
            pc_peer = {**peers_map['pc_dsh'], **pc_peer}
        out = [
            pc_peer,
            {
                'id': 'phone_dsb',
                'peer_id': 'phone_dsb',
                'device_id': 'phone_dsb',
                'name': 'Phone DSB App',
                'device_name': 'Phone DSB App',
                'kind': 'phone',
                'device_type': 'phone',
                'source': 'phone',
                'online': phone_clients_count > 0,
                'clients': phone_clients_count,
            },
        ]
        now_t = time.time()
        for k, v in peers_map.items():
            if k not in ('pc_dsh', 'phone_dsb') and isinstance(v, dict):
                p_seen = float(v.get('last_seen') or 0.0)
                p_online = bool(v.get('online', True) and (now_t - p_seen) < 90.0)
                out.append({
                    **v,
                    'id': k,
                    'peer_id': k,
                    'device_id': k,
                    'kind': 'pc',
                    'device_type': 'pc',
                    'source': 'pc',
                    'online': p_online,
                })
        return out

    def resolve_dsh_command(self, event: dict):
        req_id = str(event.get('request_id') or '')
        self.update_dsh_peer(event)
        if req_id and isinstance(self.dsh_bridge_info.get('inbox'), list):
            self.dsh_bridge_info['inbox'] = [
                item for item in self.dsh_bridge_info['inbox']
                if str(item.get('request_id') or '') != req_id
            ]
        fut = self.dsh_pending.pop(req_id, None)
        if fut and not fut.done():
            fut.set_result(event.get('result') if isinstance(event.get('result'), dict) else {'ok': True, 'data': event.get('result')})

    async def _raw_dsh_rpc(self, op: str, args: dict, timeout: float = 30.0) -> dict:
        req_device_id = str(args.get('device_id') or '').strip()
        target_id, target_peer = self.resolve_target_dsh_peer(req_device_id)
        if req_device_id and req_device_id.lower() not in ('auto', 'default', 'active', 'latest', 'pc', 'pc_dsh') and target_peer is None:
            avail = [k for k in (self.dsh_bridge_info.get('peers') or {}).keys() if k != 'phone_dsb']
            return {
                'ok': False,
                'pc_online': False,
                'device_id': req_device_id,
                'available_devices': avail,
                'op': op,
                'error': f'未找到指定电脑设备: {req_device_id}',
            }

        peer_status = (target_peer.get('status') if isinstance(target_peer, dict) and isinstance(target_peer.get('status'), dict) else None) or self.dsh_bridge_info.get('status') or {}
        if self.dsh_dispatch and not self.is_dsh_online() and op not in ('status', 'list_sessions'):
            for _ in range(4):
                await asyncio.sleep(0.5)
                if self.is_dsh_online():
                    break
        if self.dsh_dispatch and self.is_dsh_online():
            if op == 'status' and not self.dsh_bridge_info.get('ws_connected') and peer_status:
                return {'ok': True, 'pc_online': True, 'device_id': target_id, **peer_status}
            if op == 'list_sessions' and not self.dsh_bridge_info.get('ws_connected') and isinstance(peer_status.get('sessions'), list):
                lim = max(1, min(200, int(args.get('limit') or 25)))
                sess_list = list(peer_status.get('sessions') or [])
                return {
                    'ok': True,
                    'pc_online': True,
                    'device_id': target_id,
                    'device_name': (target_peer or {}).get('device_name', target_id),
                    'total_sessions': len(sess_list),
                    'sessions': sess_list[:lim],
                }
            req_id = 'dsh-' + secrets.token_hex(6)
            loop = asyncio.get_running_loop()
            fut = loop.create_future()
            self.dsh_pending[req_id] = fut
            cmd_pkt = {
                'type': 'dsh_workspace_command',
                'request_id': req_id,
                'device_id': target_id,
                'peer_id': target_id,
                'bot_id': self.bot_context.get(),
                'op': op,
                'args': {**args, 'device_id': target_id},
                'timestamp': time.time(),
            }
            inbox = self.dsh_bridge_info.setdefault('inbox', [])
            inbox.append(cmd_pkt)
            if len(inbox) > 50:
                del inbox[:-50]
            try:
                await self.dsh_dispatch(cmd_pkt)
                res = await asyncio.wait_for(fut, timeout=timeout)
                return res if isinstance(res, dict) else {'ok': True}
            except asyncio.TimeoutError:
                self.dsh_pending.pop(req_id, None)
                if op == 'status' and self.is_dsh_online():
                    return {'ok': True, 'pc_online': True, 'device_id': target_id, **peer_status}
                if op == 'list_sessions' and isinstance(peer_status.get('sessions'), list):
                    lim = max(1, min(200, int(args.get('limit') or 25)))
                    sess_list = list(peer_status.get('sessions') or [])
                    return {
                        'ok': True,
                        'pc_online': True,
                        'device_id': target_id,
                        'total_sessions': len(sess_list),
                        'sessions': sess_list[:lim],
                    }
                return {'ok': False, 'timed_out': True, 'device_id': target_id, 'op': op, 'error': f'电脑端 DeepSeek Harness ({target_id}) 在 {int(timeout)}s 内未返回响应'}
            except Exception as e:
                self.dsh_pending.pop(req_id, None)
                return {'ok': False, 'device_id': target_id, 'op': op, 'error': f'发送指令至电脑端失败: {str(e)[:200]}'}

        plugin_dir = Path(__file__).resolve().parent.parent.parent / 'dsh-plugin'
        if not (plugin_dir / 'dsh_core.py').is_file() and Path(r'D:\dsh-baike-plugin\dsh_core.py').is_file():
            plugin_dir = Path(r'D:\dsh-baike-plugin')
        if (plugin_dir / 'dsh_core.py').is_file() and (
            (Path.home() / '.dsh').is_dir() or Path(r'D:\DeepSeek-Harness\data').is_dir() or os.getenv('DSH_DESKTOP_HOME')
        ):
            import sys as _sys
            if str(plugin_dir) not in _sys.path:
                _sys.path.insert(0, str(plugin_dir))
            from dsh_core import DSHController
            ctrl = DSHController()
            if op == 'status':
                return {'ok': True, 'pc_online': True, 'local_direct': True, **ctrl.status()}
            if op == 'list_sessions':
                return await asyncio.to_thread(
                    ctrl.list_sessions,
                    int(args.get('limit') or 25),
                    args.get('workspace'),
                    bool(args.get('include_blank', False)),
                    args.get('query'),
                )
            if op == 'inspect_session':
                return await asyncio.to_thread(
                    ctrl.inspect_session,
                    str(args.get('session_id') or 'latest'),
                    int(args.get('max_messages') or 12),
                    int(args.get('max_tools') or 15),
                    bool(args.get('include_reasoning', False)),
                )
            if op == 'wake_gui':
                return await asyncio.to_thread(ctrl.wake_gui)
            if op in ('send_message', 'new_session'):
                return await ctrl.control_session(
                    op=op,
                    message=str(args.get('message') or args.get('text') or ''),
                    session_id=args.get('session_id'),
                    cwd=args.get('cwd'),
                    timeout_sec=float(args.get('timeout_sec') or timeout),
                    wake_gui=bool(args.get('wake_gui', False)),
                )
        return {
            'ok': False,
            'pc_online': False,
            'device_id': target_id,
            'op': op,
            'error': '电脑端 DeepSeek Claw Sentinel 当前未在线连接（请确认电脑已开机并启动 DeepSeek-Claw-Sentinel）',
        }

    async def _tool_dsh_workspace(self, args: dict) -> dict:
        op = str(args.get('op') or args.get('action') or 'status').strip().lower()
        if op == 'open_url':
            op = 'navigate'
        elif op == 'browser_snapshot':
            op = 'screenshot'
        if op in ('list', 'sessions'):
            op = 'list_sessions'
        elif op in ('inspect', 'read', 'context', 'tools'):
            op = 'inspect_session'
        elif op in ('send', 'steer', 'chat', 'reply', 'ask', 'send_message'):
            op = 'send_message'
        elif op in ('new', 'create', 'spawn', 'start'):
            op = 'new_session'
        elif op in ('wake', 'open_gui', 'launch_gui'):
            op = 'wake_gui'
        elif op in ('peek', 'last_3', 'peek_dialogs', 'dialogs'):
            op = 'status'
        elif op in ('shell', 'exec', 'run_bash', 'powershell', 'cmd', 'pc_shell'):
            op = 'pc_shell'
        elif op in ('desktop', 'cua', 'cua_desktop', 'screen', 'screenshot'):
            op = 'cua_desktop'

        if op == 'send_message':
            if self.hop_depth.get() > 0:
                return {'error': '当前处于被跨端/同事协作唤醒的子轮次中，请勿再次嵌套调用 send_message，请直接用自然语言正文输出你的处理结果与回复（系统会自动将你的正文送回发件方）。'}
            target_bot_arg = str(args.get('to_bot_id') or args.get('bot_id') or args.get('target') or '').strip()
            msg_text = str(args.get('message') or args.get('text') or '').strip()
            cur_src = normalize_node_source(self.source_context.get())
            default_node = '安卓一' if cur_src == 'pc' else 'Windows一'
            to_spec = f'{default_node}:{target_bot_arg}' if target_bot_arg and ':' not in target_bot_arg else (target_bot_arg or default_node)
            return await self._tool_send_message({'to_bot_id': to_spec, 'text': msg_text})

        # Enforce symmetric cross-device isolation (P0-6): phone nodes cannot directly execute PC tools
        cur_node_src = normalize_node_source(self.source_context.get() or 'phone')
        if cur_node_src in ('phone', 'phone2') and op not in ('status', 'send_message'):
            return {
                'ok': False,
                'error': '你当前位于「安卓一」，不能直接调用电脑端的 dsh_workspace 工具操控电脑本机！请调用 blackboard(action="post", content="@Windows一 ...", mentions=["Windows一"]) 发布任务让「Windows一」接手执行。',
            }

        if op == 'cua_desktop':
            nested = args.get('args') if isinstance(args.get('args'), dict) else {}
            cua_action = str(args.get('action') or args.get('tool') or nested.get('action') or nested.get('tool') or 'inspect').strip()
            if cua_action in ('send_screenshot', 'share_image', 'send_qr', 'send_image'):
                clean_p = self.harness.root / 'cua-clean.jpg'
                if not clean_p.exists():
                    snap_res = await self._tool_dsh_workspace({'op': 'cua_desktop', 'action': 'inspect'})
                    if not clean_p.exists():
                        return {'ok': False, 'error': '未能获取电脑真机窗口当前截图'}
                raw_clean = clean_p.read_bytes()
                crop_box = (args.get('crop_box') or nested.get('crop_box')) if isinstance(args.get('crop_box') or nested.get('crop_box'), list) else None
                media_url = self._prepare_chat_screenshot(raw_clean, crop_box=crop_box, prefix='pc-cua-shot')
                return {
                    'ok': bool(media_url),
                    'op': 'cua_desktop',
                    'action': 'send_screenshot',
                    'chat_image_url': media_url,
                    'cropped': bool(crop_box),
                    'note': f'已将电脑真机干净无标尺截图直接挂载到本轮聊天回复气泡上（{media_url}）！',
                }
            try:
                cur_bot_name = get_assistant(self.store, self.bot_context.get()).get('name') or '主助理'
            except Exception:
                cur_bot_name = '主助理'
            merged_cua_args = {**nested, **{k: v for k, v in args.items() if k != 'args'}, 'action': cua_action, 'tool': cua_action, 'bot_name': cur_bot_name}
            res = await self._raw_dsh_rpc('cua_desktop', merged_cua_args, timeout=float(args.get('timeout_sec') or 35.0))
            if isinstance(res, dict):
                res.pop('raw', None)
                imgs = res.pop('images', None)
                if isinstance(imgs, list) and imgs:
                    first_img = imgs[0] if isinstance(imgs[0], dict) else {}
                    b64_data = str(first_img.get('data') or '').strip()
                    if b64_data:
                        try:
                            raw_img = base64.b64decode(b64_data.split(',')[-1])
                            (self.harness.root / 'cua-clean.jpg').write_bytes(raw_img)
                            annotated_bytes, iw, ih = draw_vision_ruler_overlay(
                                raw_img,
                                a11y_summary='',
                                screen_w=0,
                                screen_h=0,
                            )
                            (self.harness.root / 'cua-latest.jpg').write_bytes(annotated_bytes)
                            res['saved_screenshot'] = 'cua-latest.jpg'
                            res['_multimodal_b64'] = base64.b64encode(annotated_bytes).decode('ascii')
                            res['_multimodal_mime'] = 'image/jpeg'
                            res['_multimodal_caption'] = (
                                f'【Windows 电脑真机窗口实时画面 (action={cua_action}, 尺寸={iw}x{ih})】\n'
                                '1. 画面上、下、左、右四边均已绘制与手机端完全一致的 `0..1000` 归一化坐标标尺（顶部/底部 `X:100..900`，左侧/右侧 `Y:100..900`）。\n'
                                '2. 操作逻辑与手机端 `phone_workspace` 完全一致：\n'
                                '   - 若 UIA 树中有目标元素编号，直接调 `dsh_workspace(op="cua_desktop", action="tap", element_index=N)`（或 `ref="@eN"`）；\n'
                                '   - 若自绘界面无节点或需点击画面任意位置，直接对照四周 `0..1000` 标尺调用 `dsh_workspace(op="cua_desktop", action="tap", nx=0..1000, ny=0..1000)`！'
                            )
                        except Exception:
                            res['_multimodal_b64'] = b64_data
                            res['_multimodal_mime'] = str(first_img.get('mimeType') or 'image/png')
                            res['_multimodal_caption'] = f'【Windows 电脑真机窗口实时画面 (action={cua_action})】'
            return res

        limit = max(1, min(100, int(args.get('limit') or 3)))
        req_source = str(args.get('source') or '').strip().lower()
        req_device_id = str(args.get('device_id') or '').strip()
        phone_online = bool(self.phone_online_checker()) if callable(self.phone_online_checker) else False

        if op == 'status':
            phone_dialogs, phone_last_time = self.extract_dialogs_by_source('phone', limit=limit)
            bot_names = [b.get('name', b.get('id', '')) for b in self.store.state.get('assistants', [])]
            if req_source in ('phone', 'phone2', '安卓一', '安卓二') or req_device_id.lower() in ('phone', 'phone2', 'phone_dsb', '安卓一', '安卓二'):
                norm_ph = normalize_node_source(req_source or req_device_id)
                ph_label = node_display_name(norm_ph)
                ph_dialogs, ph_last_time = self.extract_dialogs_by_source(norm_ph, limit=limit)
                lines = [
                    f"{ph_label}在线状态: {'🟢 在线' if phone_online else '⚪ 后台待命'} (最后活跃: {ph_last_time or '未知'}, 常驻Bot: {', '.join(bot_names)})",
                    f"{ph_label}最近 {len(ph_dialogs)} 条对话 (limit={limit}):",
                ]
                for idx, d in enumerate(ph_dialogs, 1):
                    lines.append(f"{idx}. [{d.get('time', '')}] ({d.get('bot_name', '主助理')}) 主人: {d.get('user', '')} -> AI: {d.get('assistant', '')}")
                return {
                    'ok': True,
                    'source': norm_ph,
                    'limit': limit,
                    'phone_online': phone_online,
                    'online_bots': bot_names,
                    'last_active_time': ph_last_time,
                    'dialogs': ph_dialogs,
                    'phone_dialogs': ph_dialogs,
                    'last_3_dialogs': ph_dialogs,
                    'summary': '\n'.join(lines),
                }

            res = await self._raw_dsh_rpc('status', {**args, 'limit': limit}, timeout=10.0)
            if req_device_id and not res.get('ok') and '未找到指定电脑设备' in str(res.get('error') or ''):
                return res
            target_id, target_peer = self.resolve_target_dsh_peer(req_device_id)
            pc_on = bool(self.dsh_bridge_info.get('online') or res.get('ok'))
            pc_chat_dialogs, pc_chat_time = self.extract_dialogs_by_source('pc', limit=limit)
            dsh_dialogs = list(res.get('last_3_dialogs') or [])
            peer_sessions = ((target_peer or {}).get('status') or {}).get('sessions') if isinstance(target_peer, dict) else None
            sessions = list(peer_sessions) if isinstance(peer_sessions, list) else self._get_dsh_sessions_list()
            combined_dialogs = (pc_chat_dialogs + dsh_dialogs)[-limit:]
            last_time = str(res.get('last_active_time') or pc_chat_time or '')
            lines = [
                f"手机端 (安卓一) 在线状态: {'🟢 在线' if phone_online else '⚪ 后台待命'} (最后活跃: {phone_last_time or '未知'}, 常驻Bot: {', '.join(bot_names)})",
                f"电脑端 (Windows一 / {target_id}) 在线状态: {'🟢 在线' if pc_on else '⚪ 离线'} (最后活跃: {last_time or '未知'}, 常驻Bot: {', '.join(bot_names)})",
                f"手机端最近 {len(phone_dialogs)} 条对话:",
            ]
            if phone_dialogs:
                for idx, d in enumerate(phone_dialogs, 1):
                    lines.append(f"  {idx}. [{d.get('time', '')}] ({d.get('bot_name', '主助理')}) 主人: {str(d.get('user', ''))[:120]} -> AI: {str(d.get('assistant', ''))[:160]}")
            else:
                lines.append("  (暂无最近对话)")
            lines.append(f"电脑端最近 {len(combined_dialogs)} 条对话:")
            if combined_dialogs:
                for idx, d in enumerate(combined_dialogs, 1):
                    lines.append(f"  {idx}. [{d.get('time', '')}] ({d.get('session_title') or d.get('bot_name') or 'PC'}) 用户: {str(d.get('user', ''))[:120]} -> AI: {str(d.get('assistant', ''))[:160]}")
            else:
                lines.append("  (暂无最近对话)")
            lines.append("提示: 如需跨端协同或提醒其他 Bot 接手，请调用 blackboard(action='post', content='...', mentions=['Windows一']) 或 peek_bots(target='all')。")
            compact_sessions = [
                {'title': s.get('title'), 'short_id': s.get('short_id'), 'status': s.get('status', 'idle')}
                for s in sessions[:10]
            ]
            return {
                'ok': True,
                'device_id': target_id,
                'source': normalize_node_source(req_source) if req_source else 'pc',
                'limit': limit,
                'sessions': sessions,
                'dialogs': combined_dialogs,
                'phone_online': phone_online,
                'phone_last_active_time': phone_last_time,
                'phone_dialogs': phone_dialogs,
                'pc_online': pc_on,
                'pc_last_active_time': last_time,
                'pc_dialogs': combined_dialogs,
                'online_bots': bot_names,
                'workspace_sessions': compact_sessions,
                'summary': '\n'.join(lines),
            }

        timeout = float(args.get('timeout_sec') or (180.0 if op == 'new_session' else 25.0))
        res = await self._raw_dsh_rpc(op, args, timeout=timeout)
        if isinstance(res, dict):
            wline = str(res.get('work_log_line') or '').strip()
            if wline:
                self.vault.append_work_log(wline)
        return res
