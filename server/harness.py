"""
[Module Responsibility]
受控工作区沙箱与基础工具执行运行时 (`server/harness.py`)。
负责管理虚拟文件系统、Bash 命令执行、多源网页搜索与抓取、代码结构检索及记忆金库工具分发。

[Public Interface]
- `Harness(root, store, vault, extension_registry)`: 核心沙箱执行器
  - `.all_tools() -> list[dict]`: 返回静态与动态扩展工具定义
  - `.path(rel: str) -> Path`: 解析并校验工作区内安全相对路径
  - `.execute(name: str, args: dict) -> dict`: 执行基础沙箱工具
- Re-exports from `core.tool_schemas`: `TOOLS`, `DEFAULT_SYSTEM_MD`, `public_url`, `tool`

[Dependencies]
- `core.tool_schemas`: 工具 Schema 定义与系统提示词契约
- `core.search_engine`: 并发多源搜索引擎
- `transport.extension_registry`: 动态插件与 Skill 注册表
"""
from __future__ import annotations

import asyncio
import fnmatch
import ipaddress
import json
import os
import re
import signal
import socket
import sys
import time
from pathlib import Path
from urllib.parse import quote_plus, urlsplit

import httpx
from bs4 import BeautifulSoup

from core.search_engine import multi_web_search
from storage import compact_trace_list
from transport.extension_registry import ExtensionRegistry
from vault_manager import atomic_write


from core.tool_schemas import BOOL, DEFAULT_SYSTEM_MD, INT, STR, TOOLS, public_url, tool

class Harness:
    def __init__(self, root: Path, store=None, vault=None, extension_registry: ExtensionRegistry | None = None):
        self.root = root.resolve()
        root.mkdir(parents=True, exist_ok=True)
        (self.root / 'skills').mkdir(parents=True, exist_ok=True)
        (self.root / 'plugins').mkdir(parents=True, exist_ok=True)
        sys_md = self.root / 'SYSTEM.md'
        if not sys_md.exists() or sys_md.read_text('utf-8', errors='replace') != DEFAULT_SYSTEM_MD:
            atomic_write(sys_md, DEFAULT_SYSTEM_MD)
        self.store = store
        self.vault = vault
        self.vision_reader = None
        self.active_bot_getter = None
        self.custom_handlers = {}
        self.extension_registry = extension_registry or ExtensionRegistry()
        self.extension_registry.discover_plugins_dir(Path(__file__).parent / 'plugins')
        self.extension_registry.discover_plugins_dir(self.root / 'plugins')
        self._web_cache: dict[str, tuple[float, dict]] = {}
        self._sys_md_cache: tuple[int, str] | None = None

    def all_tools(self) -> list[dict]:
        base_names = {t['function']['name'] for t in TOOLS}
        extra = [s for s in self.extension_registry.tool_schemas() if s['function']['name'] not in base_names]
        return TOOLS + extra

    _VAULT_SUBDIRS = ('profile', 'tasks', 'timeline', 'notes', 'logs', 'playbooks', 'skills', 'conversations')

    def _is_other_bot_private_file(self, candidate: Path) -> bool:
        if self.vault is None or not callable(self.active_bot_getter):
            return False
        cur_bot = str(self.active_bot_getter() or '').strip()
        if not cur_bot or cur_bot == 'all':
            return False
        try:
            rel_v = candidate.resolve().relative_to(self.vault.root.resolve()).as_posix()
        except Exception:
            return False
        if rel_v.startswith('timeline/bots/'):
            parts = rel_v.split('/')
            return len(parts) >= 3 and parts[2] != cur_bot
        if rel_v.startswith('conversations/'):
            fname = rel_v.split('/', 1)[1]
            return '_' in fname and not fname.startswith(f'{cur_bot}_')
        return False

    def _is_vault_rel_path(self, p: str) -> str | None:
        clean = str(p or '').strip().lstrip('/')
        if clean == 'memory_vault' or clean.startswith('memory_vault/'):
            return clean[len('memory_vault'):].lstrip('/')
        if clean == 'INDEX.md' or clean in self._VAULT_SUBDIRS or any(clean.startswith(d + '/') for d in self._VAULT_SUBDIRS):
            return clean
        return None

    def _display_rel_path(self, resolved: Path) -> str:
        try:
            if self.vault is not None and resolved.is_relative_to(self.vault.root.resolve()):
                rel_v = resolved.relative_to(self.vault.root.resolve()).as_posix()
                return 'memory_vault' if rel_v == '.' else f'memory_vault/{rel_v}'
            if resolved.is_relative_to(self.root.resolve()):
                return resolved.relative_to(self.root.resolve()).as_posix()
        except Exception:
            pass
        return resolved.name

    def path(self, p):
        if not p or p == '.':
            return self.root
        if Path(p).is_absolute() or '\\' in p or ':' in p:
            raise ValueError(f'只允许工作区相对路径 (收到: {p})')
        vault_rel = self._is_vault_rel_path(p)
        if vault_rel is not None and self.vault is not None:
            if not vault_rel:
                return self.vault.root
            cand = (self.vault.root / vault_rel).resolve()
            if '..' in Path(vault_rel).parts or not cand.is_relative_to(self.vault.root.resolve()):
                raise ValueError('禁止上级路径或符号链接')
            if cand.is_dir():
                return cand
            return self.vault.path(vault_rel)
        path = self.root / p
        if '..' in path.parts or any(x.is_symlink() for x in [path, *list(path.parents)[:len(path.parts) - len(self.root.parts)]]):
            raise ValueError('禁止上级路径或符号链接')
        if not path.resolve().is_relative_to(self.root):
            raise ValueError('超出工作区')
        resolved = path.resolve()
        if not resolved.exists() and self.vault is not None:
            vault_cand = (self.vault.root / p).resolve()
            if vault_cand.is_relative_to(self.vault.root.resolve()) and vault_cand.exists():
                return vault_cand
        return resolved

    async def fetch(self, url):
        now = time.time()
        cached = self._web_cache.get(url)
        if cached and now - cached[0] < 600:
            return dict(cached[1])
        async with httpx.AsyncClient(timeout=20, trust_env=False) as client:
            orig_url = url
            for _ in range(5):
                await public_url(url)
                async with client.stream('GET', url, headers={'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36'}) as r:
                    if r.is_redirect:
                        url = str(r.url.join(r.headers['location']))
                        continue
                    r.raise_for_status()
                    if not any(x in r.headers.get('content-type', '') for x in ('text/', 'json', 'xml')):
                        raise ValueError('仅接受文本网页')
                    buf = bytearray()
                    async for chunk in r.aiter_bytes():
                        buf.extend(chunk)
                        if len(buf) > 512_000:
                            raise ValueError('网页超过 512 KB')
                    soup = BeautifulSoup(buf.decode('utf-8', errors='replace'), 'html.parser')
                    for node in soup(['script', 'style', 'nav', 'footer']):
                        node.decompose()
                    text = soup.get_text(' ', strip=True)
                    links = [{'title': a.get_text(' ', strip=True)[:180], 'url': a['href']} for a in soup.select('a[href]') if a['href'].startswith('http')][:16]
                    res = {'source': url, 'text': text[:16000], 'links': links}
                    if len(self._web_cache) > 120:
                        self._web_cache.clear()
                    self._web_cache[orig_url] = (now, res)
                    return res
            raise ValueError('重定向过多')

    async def execute(self, name, args):
        try:
            if name in self.custom_handlers:
                return await self.custom_handlers[name](args)
            if name in self.extension_registry.tool_plugins:
                return await self.extension_registry.execute_tool(name, args, {'root': str(self.root)})
            if name == 'read_file':
                raw_path_arg = str(args.get('path') or '').strip()
                p = self.path(raw_path_arg)
                if self._is_other_bot_private_file(p):
                    return {'ok': False, 'path': raw_path_arg, 'error': '该对话日志归属其他 Bot 独立会话，当前 Bot 无权跨会话读取'}
                if p.is_dir():
                    return {
                        'ok': False,
                        'is_directory': True,
                        'path': raw_path_arg,
                        'error': f'路径 "{raw_path_arg}" 是目录而非文件，请改用 list_dir(path="{raw_path_arg}") 查看目录内容。',
                    }
                if not p.exists():
                    raise ValueError(f'文件不存在: {raw_path_arg}')
                if p.suffix.lower() in ('.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp'):
                    if self.vision_reader is not None:
                        return await self.vision_reader(p)
                    from core.vision_engine import VisionEngine
                    return await VisionEngine().read_image_file_with_vision(p, str(args.get('prompt') or ''))
                if p.stat().st_size > 1_000_000:
                    raise ValueError('文件过大')
                raw_text = p.read_text('utf-8', errors='replace')
                lines = raw_text.splitlines()
                if 'start_line' in args or 'end_line' in args:
                    raw_s = int(args.get('start_line') if args.get('start_line') is not None else 1)
                    raw_e = int(args.get('end_line') if args.get('end_line') is not None else (max(1, raw_s) + 200))
                    if raw_s < 1 or raw_e < 1 or raw_s > raw_e:
                        return {
                            'ok': False,
                            'error': f'无效的行号范围 start_line={raw_s}, end_line={raw_e}（要求 1 <= start_line <= end_line，当前文件共 {len(lines)} 行）',
                            'total_lines': len(lines),
                        }
                    if len(lines) > 0 and raw_s > len(lines):
                        return {
                            'ok': False,
                            'error': f'start_line={raw_s} 超出文件总行数（当前文件共 {len(lines)} 行）',
                            'total_lines': len(lines),
                        }
                    s = max(1, raw_s)
                    e = min(len(lines), raw_e)
                    sliced = '\n'.join(f'{idx}: {lines[idx - 1]}' for idx in range(s, e + 1) if 1 <= idx <= len(lines))
                    return {'content': sliced[:160000], 'total_lines': len(lines), 'range': f'{s}-{e}', 'resolved_path': self._display_rel_path(p)}
                # Default smart pagination for large files (>160 lines or >9,000 chars) to prevent context bloat (P0-7 / B-3)
                if len(lines) > 160 or len(raw_text) > 9000:
                    default_end = min(len(lines), 120)
                    sliced = '\n'.join(f'{idx}: {lines[idx - 1]}' for idx in range(1, default_end + 1))
                    return {
                        'content': sliced[:12000],
                        'total_lines': len(lines),
                        'range': f'1-{default_end}',
                        'resolved_path': self._display_rel_path(p),
                        'truncated': True,
                        'hint': f'文件共 {len(lines)} 行，已默认返回前 {default_end} 行以保护上下文容量；如需阅读后续部分请传 start_line={default_end + 1}, end_line={min(len(lines), default_end + 120)} 或用 grep_search 定位行号。',
                    }
                return {'content': raw_text[:160000], 'total_lines': len(lines), 'resolved_path': self._display_rel_path(p)}
            if name == 'delete_file':
                raw_path_arg = str(args.get('path') or '').strip()
                if not raw_path_arg:
                    raise ValueError('path 不能为空')
                purge_flag = bool(args.get('purge', True))
                vault_rel_del = self._is_vault_rel_path(raw_path_arg)
                if self.vault is not None and (vault_rel_del is not None or not (self.root / raw_path_arg).exists()):
                    rel_v = vault_rel_del if vault_rel_del is not None else raw_path_arg
                    if (self.vault.root / rel_v).exists() or vault_rel_del is not None:
                        return self.vault.delete(rel_v, purge=purge_flag)
                p = self.path(raw_path_arg)
                if not p.exists() or p.is_dir():
                    raise ValueError(f'文件不存在或是目录: {raw_path_arg}')
                rel_ws = self._display_rel_path(p)
                p.unlink()
                return {'ok': True, 'deleted': rel_ws, 'resolved_path': rel_ws, 'purged': purge_flag}
            if name == 'move_file':
                raw_src = str(args.get('src') or '').strip()
                raw_dst = str(args.get('dst') or '').strip()
                if not raw_src or not raw_dst:
                    raise ValueError('src 和 dst 不能为空')
                if raw_src.strip('/') == raw_dst.strip('/'):
                    return {'ok': False, 'error': f'源路径与目标路径相同 ({raw_src})，无需移动'}
                if self.vault is not None and (self._is_vault_rel_path(raw_src) is not None or self._is_vault_rel_path(raw_dst) is not None):
                    return self.vault.move(raw_src, raw_dst)
                p_src = self.path(raw_src)
                p_dst = self.path(raw_dst)
                if p_src == p_dst:
                    return {'ok': False, 'error': f'源路径与目标路径相同 ({raw_src})，无需移动'}
                if not p_src.exists() or p_src.is_dir():
                    raise ValueError(f'源文件不存在或是目录: {raw_src}')
                p_dst.parent.mkdir(parents=True, exist_ok=True)
                p_src.replace(p_dst)
                return {
                    'ok': True,
                    'moved_from': self._display_rel_path(p_src),
                    'moved_to': self._display_rel_path(p_dst),
                }
            if name == 'send_image':
                import hashlib as _hashlib
                import secrets as _secrets
                orig_path_arg = str(args.get('path') or '').strip(); raw_path_arg = orig_path_arg.removeprefix('workspace/').lstrip('/')
                if not raw_path_arg:
                    raise ValueError('path 不能为空')
                p = self.path(raw_path_arg)
                if not p.exists() or not p.is_file():
                    raise ValueError(f'图片文件不存在: {orig_path_arg}')
                raw_bytes = p.read_bytes()
                if len(raw_bytes) == 0:
                    return {'ok': False, 'op': 'send_image', 'source_path': orig_path_arg, 'error': f'图片文件为空 (0 字节)，禁止发送空文件: {orig_path_arg}'}
                crop_box = args.get('crop_box') if isinstance(args.get('crop_box'), list) else None
                max_side = max(256, min(2560, int(args.get('max_side') or 1280)))
                uploads_dir = self.root / 'uploads'
                uploads_dir.mkdir(parents=True, exist_ok=True)
                stamp = int(time.time() * 1000) % 100000000
                rand_suffix = _secrets.token_hex(2)
                stem = re.sub(r'[^a-zA-Z0-9_-]', '_', p.stem)[:32] or 'image'
                out_name = f'{stem}-{stamp}-{rand_suffix}.jpg'
                out_path = uploads_dir / out_name
                try:
                    import io as _io
                    from PIL import Image as _Image
                    im = _Image.open(_io.BytesIO(raw_bytes)).convert('RGB')
                    iw, ih = im.size
                    if crop_box and len(crop_box) == 4:
                        ymin, xmin, ymax, xmax = [max(0, min(1000, int(v))) for v in crop_box]
                        if xmax > xmin and ymax > ymin:
                            left = max(0, int(round(xmin * iw / 1000.0)))
                            top = max(0, int(round(ymin * ih / 1000.0)))
                            right = min(iw, int(round(xmax * iw / 1000.0)))
                            bottom = min(ih, int(round(ymax * ih / 1000.0)))
                            im = im.crop((left, top, right, bottom))
                            iw, ih = im.size
                    if max(iw, ih) > max_side:
                        scale = max_side / float(max(iw, ih))
                        im = im.resize((max(1, int(round(iw * scale))), max(1, int(round(ih * scale)))), _Image.Resampling.LANCZOS)
                    buf = _io.BytesIO()
                    im.save(buf, format='JPEG', quality=90)
                    out_bytes = buf.getvalue()
                    out_path.write_bytes(out_bytes)
                except Exception as img_err:
                    if p.suffix.lower() == '.svg' and b'<svg' in raw_bytes[:1024].lower():
                        out_name = f'{stem}-{stamp}-{rand_suffix}.svg'
                        out_path = uploads_dir / out_name
                        out_bytes = raw_bytes
                        out_path.write_bytes(out_bytes)
                    else:
                        return {'ok': False, 'op': 'send_image', 'source_path': orig_path_arg, 'error': f'文件不是合法的可解码图片 ({p.suffix or "无后缀"}: {img_err})，仅支持 PNG/JPG/WEBP/GIF/BMP/SVG 图片: {orig_path_arg}'}
                media_url = f'/workspace-media/uploads/{out_name}'
                out_md5 = _hashlib.md5(out_bytes).hexdigest()
                return {
                    'ok': True,
                    'op': 'send_image',
                    'source_path': raw_path_arg,
                    'chat_image_url': media_url,
                    'markdown_embed': f'![{p.stem}]({media_url})',
                    'output_bytes': len(out_bytes),
                    'output_md5': out_md5,
                }
            if name in ('write_file', 'edit_file'):
                raw_target_p = str(args.get('path') or '').strip()
                vault_rel_w = self._is_vault_rel_path(raw_target_p)
                if vault_rel_w is not None and Path(raw_target_p).suffix.lower() != '.md':
                    return {
                        'ok': False,
                        'path': raw_target_p,
                        'error': f'memory_vault/ 记忆库仅接收 .md (Markdown) 笔记（当前后缀为 {Path(raw_target_p).suffix or "无后缀"}）；请改用 .md 后缀，或将非 Markdown 文件写入工作区根目录。',
                    }
                p = self.path(raw_target_p)
                old_text = p.read_text('utf-8', errors='replace') if p.exists() else ''
                dedup_count = 0
                if name == 'write_file':
                    if args.get('append') and old_text:
                        sep = '' if old_text.endswith('\n') else '\n'
                        text = old_text + sep + args['content']
                    else:
                        text = args['content']
                else:
                    if not p.exists():
                        raise ValueError(f'文件不存在: {raw_target_p}')
                    match_cnt = old_text.count(args['old']) if args.get('old') else 0
                    if not args.get('old') or match_cnt != 1:
                        raise ValueError('旧文本匹配 0 次（必须精确匹配一次）：请先 read_file 核对原文' if match_cnt == 0 else f'旧文本匹配 {match_cnt} 次（必须精确匹配一次）：请提供更长上下文使匹配唯一化')
                    text = old_text.replace(args['old'], args['new'], 1)
                if (name == 'edit_file' or (name == 'write_file' and args.get('append'))) and text:
                    raw_lines = text.splitlines(keepends=True)
                    old_lines_set = set(old_text.splitlines())
                    dedup_lines: list[str] = []
                    in_fence = False
                    for ln in raw_lines:
                        stripped = ln.strip()
                        if stripped.startswith('```'):
                            in_fence = not in_fence
                        if (
                            not in_fence
                            and dedup_lines
                            and len(stripped) >= 12
                            and stripped == dedup_lines[-1].strip()
                            and old_text.count(stripped + '\n') < 2
                        ):
                            dedup_count += 1
                            continue
                        dedup_lines.append(ln)
                    if dedup_count > 0:
                        text = ''.join(dedup_lines)
                if len(text.encode()) > 1_000_000:
                    raise ValueError('文件过大')
                snap_saved = None
                if self.vault is not None and vault_rel_w is not None:
                    snap_saved = self.vault.write(vault_rel_w, text)
                    if vault_rel_w != 'INDEX.md':
                        self.vault.reindex()
                else:
                    atomic_write(p, text)
                if self.store is not None:
                    self.store.sync_from_workspace(force=True)
                rel = self._display_rel_path(p)
                out_res: dict = {'ok': True, 'written': rel, 'resolved_path': rel, 'bytes': len(text.encode())}
                if dedup_count > 0:
                    out_res['deduplicated_adjacent_lines'] = dedup_count
                if snap_saved:
                    out_res['previous_backup'] = snap_saved
                    if len(old_text) > len(text) + 80:
                        out_res['warning'] = f'新内容比原文件少 {len(old_text) - len(text)} 字符；旧版已自动备份至 {snap_saved}，如需找回可直接 read_file("{snap_saved}")'
                return out_res
            if name == 'list_dir':
                rel_arg = str(args.get('path') or '.').strip() or '.'
                raw_depth = int(args.get('depth') if args.get('depth') is not None else 1)
                max_depth = max(1, min(3, raw_depth))
                target = self.path(rel_arg)
                if not target.is_dir():
                    raise ValueError(f'目标不是目录: {rel_arg}')
                entries = []
                hidden_root_names = {'.transfers', '.versions', 'pylibs', '.venv', 'venv', '.local', 'imapwork', '.ms_access_token', 'imap_ms_creds.json', 'settings.json', 'bots.json'}
                if self.vault is not None and target == self.root:
                    hidden_root_names.update(self._VAULT_SUBDIRS)
                    hidden_root_names.add('INDEX.md')

                def _walk_dir(cur_dir: Path, rel_prefix: str, cur_depth: int):
                    if len(entries) >= 250:
                        return
                    try:
                        children = sorted(cur_dir.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
                    except OSError:
                        return
                    for child in children:
                        if len(entries) >= 250:
                            break
                        if child.name in hidden_root_names or child.name.startswith('.transfers') or child.name.startswith('org.chromium.'):
                            continue
                        try:
                            is_d = child.is_dir() and not child.is_symlink()
                            stat = child.stat()
                        except OSError:
                            continue
                        rel_name = f'{rel_prefix}{child.name}' + ('/' if is_d else '')
                        entries.append({
                            'name': rel_name,
                            'type': 'dir' if is_d else 'file',
                            'bytes': 0 if is_d else stat.st_size,
                        })
                        if is_d and cur_depth < max_depth:
                            _walk_dir(child, rel_name, cur_depth + 1)

                _walk_dir(target, '', 1)
                if rel_arg == '.' and self.vault is not None:
                    entries.append({'name': 'memory_vault/', 'type': 'dir', 'bytes': 0})
                    if max_depth > 1:
                        _walk_dir(self.vault.root, 'memory_vault/', 2)
                ld_out: dict = {'path': rel_arg, 'resolved_path': self._display_rel_path(target), 'depth': max_depth, 'entries': entries}
                if raw_depth != max_depth:
                    ld_out['depth_requested'] = raw_depth
                    ld_out['depth_clamped'] = True
                return ld_out
            if name == 'glob_files':
                if 'pattern' in args and str(args.get('pattern') or '').strip() == '':
                    return {'ok': False, 'pattern': '', 'matches': [], 'error': 'pattern 不能为空（如需匹配全部文件请传 "**/*"）'}
                pattern = str(args.get('pattern') or '**/*').strip()
                if pattern.count('[') != pattern.count(']') or '[[' in pattern:
                    return {'ok': False, 'pattern': pattern, 'matches': [], 'error': f'invalid glob pattern（方括号未闭合或语法错误）: {pattern}'}
                skip_dirs = {'.history', '.cache', '.config', '.python_packages', '.transfers', '.versions', '__pycache__', 'node_modules', '.git', 'pylibs', '.venv', 'venv', '.local', 'imapwork'}
                if self.vault is not None:
                    skip_dirs.update(self._VAULT_SUBDIRS)
                skip_names = {'.ms_access_token', 'imap_ms_creds.json', 'settings.json', 'bots.json', 'INDEX.md'}
                matches = []
                for f in sorted(self.root.rglob('*')):
                    if f.is_file() and not f.is_symlink():
                        rel_parts = f.relative_to(self.root).parts
                        if f.name in skip_names or any(part in skip_dirs or part.startswith('org.chromium.') for part in rel_parts):
                            continue
                        rel = f.relative_to(self.root).as_posix()
                        if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(f.name, pattern):
                            matches.append(rel)
                if self.vault is not None:
                    for f in sorted(self.vault.root.rglob('*.md')):
                        if f.is_file() and not f.is_symlink():
                            rel_parts = f.relative_to(self.vault.root).parts
                            if '.history' in rel_parts or self._is_other_bot_private_file(f):
                                continue
                            rel = 'memory_vault/' + f.relative_to(self.vault.root).as_posix()
                            if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(f.name, pattern):
                                matches.append(rel)
                return {'pattern': pattern, 'matches': matches[:200]}
            if name == 'grep_search':
                query = str(args.get('query') or '').strip()
                if not query:
                    raise ValueError('query 不能为空')
                if bool(args.get('literal', False)):
                    rx = re.compile(re.escape(query), re.IGNORECASE)
                else:
                    try:
                        rx = re.compile(query, re.IGNORECASE)
                    except re.error as re_err:
                        return {'ok': False, 'regex_valid': False, 'query': query, 'path': str(args.get('path') or '.'), 'hits': [], 'error': f'invalid regex ({re_err}): 正则语法非法，请修正括号/转义或传 literal=true'}
                skip_dirs = {'.history', '.cache', '.config', '.python_packages', '.transfers', '.versions', '__pycache__', 'node_modules', '.git', 'pylibs', '.venv', 'venv', '.local', 'imapwork'}
                skip_names = {'.ms_access_token', 'imap_ms_creds.json', 'settings.json', 'bots.json'}
                skip_exts = {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp', '.dmp', '.pyc', '.so', '.whl', '.zip', '.tar', '.gz', '.pdf', '.sqlite', '.db', '.ico', '.woff', '.woff2', '.ttf', '.mp3', '.mp4', '.wav', '.ogg', '.apk', '.exe', '.dll', '.bin'}
                raw_path_filter = str(
                    args.get('path') or args.get('file_path') or args.get('target') or args.get('dir') or ''
                ).strip().rstrip('/')
                search_targets: list[tuple[Path, Path, str]] = []
                if raw_path_filter and raw_path_filter != '.':
                    vault_sub_rel = self._is_vault_rel_path(raw_path_filter)
                    if self.vault is not None and vault_sub_rel is not None:
                        target_p = (self.vault.root / vault_sub_rel).resolve() if vault_sub_rel else self.vault.root
                        if not target_p.is_relative_to(self.vault.root.resolve()) or not target_p.exists():
                            return {'ok': False, 'query': query, 'path': raw_path_filter, 'effective_path': raw_path_filter, 'hits': [], 'error': f'搜索路径不存在: {raw_path_filter}'}
                        search_targets.append((target_p, self.vault.root, 'memory_vault/'))
                    else:
                        clean_ws = raw_path_filter.removeprefix('workspace/').lstrip('/')
                        ws_cand = (self.root / clean_ws).resolve()
                        vault_cand = (self.vault.root / clean_ws).resolve() if self.vault is not None else None
                        if ws_cand.is_relative_to(self.root.resolve()) and ws_cand.exists():
                            search_targets.append((ws_cand, self.root, ''))
                        elif vault_cand is not None and vault_cand.is_relative_to(self.vault.root.resolve()) and vault_cand.exists():
                            search_targets.append((vault_cand, self.vault.root, 'memory_vault/'))
                        else:
                            return {'ok': False, 'query': query, 'path': raw_path_filter, 'effective_path': raw_path_filter, 'hits': [], 'error': f'搜索路径不存在: {raw_path_filter}'}
                else:
                    if self.vault is not None:
                        search_targets.append((self.vault.root, self.vault.root, 'memory_vault/'))
                        skip_dirs = skip_dirs | set(self._VAULT_SUBDIRS)
                        skip_names = skip_names | {'INDEX.md'}
                    search_targets.append((self.root, self.root, ''))

                hits = []
                files_scanned = 0
                for target_p, base_dir, prefix in search_targets:
                    file_iter = [target_p] if target_p.is_file() else sorted(target_p.rglob('*'))
                    for f in file_iter:
                        if not f.is_file() or f.is_symlink():
                            continue
                        if f.suffix.lower() in skip_exts or f.name in skip_names or self._is_other_bot_private_file(f):
                            continue
                        rel_parts = f.relative_to(base_dir).parts if f != base_dir else ()
                        if any(part in skip_dirs or part.startswith('org.chromium.') for part in rel_parts):
                            continue
                        if not raw_path_filter and f.name.startswith('.'):
                            continue
                        try:
                            st_size = f.stat().st_size
                            if st_size > 512_000 or st_size == 0:
                                continue
                            with f.open('rb') as bf:
                                head_chunk = bf.read(4096)
                            if b'\x00' in head_chunk:
                                continue
                            files_scanned += 1
                            rel = prefix + f.relative_to(base_dir).as_posix()
                            for idx, line in enumerate(f.read_text('utf-8', errors='ignore').splitlines(), 1):
                                if rx.search(line):
                                    hits.append({'file': rel, 'line': idx, 'text': line[:220]})
                                    if len(hits) >= 80:
                                        return {
                                            'ok': True,
                                            'query': query,
                                            'path': raw_path_filter or '.',
                                            'effective_path': raw_path_filter or '.',
                                            'files_scanned': files_scanned,
                                            'hits': hits,
                                            'truncated': True,
                                        }
                        except Exception:
                            continue
                return {
                    'ok': True,
                    'query': query,
                    'path': raw_path_filter or '.',
                    'effective_path': raw_path_filter or '.',
                    'files_scanned': files_scanned,
                    'hits': hits,
                }
            if name == 'memory_search':
                query = str(args.get('query') or '').strip()
                if not query or self.vault is None:
                    return {'hits': []}
                raw_lim = args.get('limit')
                lim = max(1, min(25, int(raw_lim) if raw_lim is not None else 8))
                rx = re.compile(re.escape(query), re.IGNORECASE)
                hits = []
                for f in self.vault.files():
                    content = self.vault.read(f['path'])
                    if rx.search(content) or rx.search(f['path']):
                        hits.append({'path': 'memory_vault/' + f['path'], 'snippet': content[:360]})
                        if len(hits) >= lim:
                            break
                return {'query': query, 'hits': hits[:lim]}
            if name == 'run_bash':
                if sys.platform != 'linux':
                    return {'error': 'Bash 隔离执行仅在 Linux 部署节点可用'}
                timeout_sec = max(1, min(90, int(args.get('timeout') or 30)))
                env = os.environ.copy()
                py_user_base = self.root / '.python_packages'
                py_user_base.mkdir(parents=True, exist_ok=True)
                env['TZ'] = 'Asia/Shanghai'
                env['PYTHONUSERBASE'] = str(py_user_base)
                env['PIP_USER'] = '1'
                env['PATH'] = f"{py_user_base / 'bin'}:{env.get('PATH', '/usr/bin:/bin')}"
                if self.store is not None:
                    env['API_BASE_URL'] = self.store.config.effective_base_url()
                    env['API_KEY'] = self.store.config.effective_api_key(big=True) or self.store.config.effective_api_key(big=False)
                    env['DEFAULT_MODEL'] = self.store.config.effective_model(big=True) or self.store.config.effective_model(big=False)
                proc = await asyncio.create_subprocess_exec(
                    sys.executable,
                    str(Path(__file__).with_name('sandbox.py')),
                    str(self.root),
                    args['command'],
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    start_new_session=True,
                    env=env,
                )

                def kill_group():
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass

                output = bytearray()

                async def collect():
                    while True:
                        chunk = await proc.stdout.read(4096)
                        if not chunk:
                            break
                        output.extend(chunk[:32000 - len(output)])
                        if len(output) >= 32000:
                            kill_group()
                            break
                    await proc.wait()
                    return bytes(output)

                try:
                    out = await asyncio.wait_for(collect(), timeout_sec)
                except asyncio.TimeoutError:
                    kill_group()
                    await proc.wait()
                    if self.store is not None:
                        self.store.sync_from_workspace(force=True)
                    return {
                        'error': f'命令执行超时 ({timeout_sec}s)，已自动终止。若需长时后台运行请使用 bg_task 工具。',
                        'timed_out': True,
                        'exit_code': -9,
                        'output': bytes(output).decode('utf-8', errors='replace'),
                    }
                except asyncio.CancelledError:
                    kill_group()
                    await proc.wait()
                    raise
                finally:
                    kill_group()
                if self.store is not None:
                    self.store.sync_from_workspace(force=True)
                out_str_b = out.decode('utf-8', errors='replace')
                res_bash: dict[str, Any] = {'exit_code': proc.returncode, 'cwd': str(self.root), 'output': out_str_b}
                cmd_raw_s = str(args.get('command') or '')
                if (proc.returncode != 0 or 'No such file or directory' in out_str_b) and any(
                    vp in cmd_raw_s or vp in out_str_b for vp in ('memory_vault', 'notes/', 'profile/', 'timeline/', 'tasks/')
                ):
                    res_bash['hint'] = (
                        '提示：memory_vault 位于 Landlock 隔离的独立安全目录，run_bash 工作目录为其同级 workspace；'
                        '读写或检索 memory_vault 请直接使用 read_file / write_file / edit_file / grep_search 工具，切勿依据 bash 报错误判笔记不存在。'
                    )
                return res_bash
            if name == 'web_fetch':
                u_arg = str(args.get('url') or '').strip()
                if not u_arg:
                    return {'ok': False, 'error': 'url 不能为空'}
                try:
                    return await self.fetch(u_arg)
                except httpx.HTTPStatusError as he:
                    return {'ok': False, 'url': u_arg, 'error_code': f'HTTP_{he.response.status_code}', 'error': f'抓取网页失败 (HTTP {he.response.status_code}): {u_arg}'}
                except httpx.RequestError as re_err:
                    return {'ok': False, 'url': u_arg, 'error_code': type(re_err).__name__, 'error': f'网络连接失败 ({type(re_err).__name__}: 无法访问 {u_arg}，请检查域名或改用 web_search / cloud_desktop)'}
            if name == 'web_search':
                q = str(args.get('query') or '').strip()
                if not q:
                    return {'error': '搜索词不能为空'}
                return await multi_web_search(q, max_results=10)
            raise ValueError('未知工具')
        except asyncio.CancelledError:
            raise
        except Exception as e:
            return {'error': f'{type(e).__name__}: {str(e)[:300]}'}

    def static_system_instructions(self, pinned_block: str = '') -> str:
        """100% static system instructions (no dynamic INDEX.md or timestamp) to lock upstream Prefix Cache."""
        sys_md = self.root / 'SYSTEM.md'
        if sys_md.exists():
            mtime = sys_md.stat().st_mtime_ns
            if self._sys_md_cache and self._sys_md_cache[0] == mtime:
                arch_doc = self._sys_md_cache[1]
            else:
                arch_doc = sys_md.read_text('utf-8', errors='replace')[:12000]
                self._sys_md_cache = (mtime, arch_doc)
        else:
            arch_doc = DEFAULT_SYSTEM_MD
        sticker_hint = self.extension_registry.sticker_prompt_hint()
        img_hint = '【发送图片与表情贴纸规范】：需要向主人展示工作区生成的成品图表/卡片或网络图片时，请调用 `send_image(path="...")`（支持同一轮多次调用按序发送多张图片），或在回复中写 `![图片](文件名或URL)`（严禁将带标尺的调试截图 `desktop-latest.jpg`、`phone-latest.jpg`、`shot-*.jpg` 直接发给主人）。'
        if sticker_hint:
            img_hint += '\n' + sticker_hint
        return (arch_doc + '\n\n' + img_hint + ('\n\n' + pinned_block if pinned_block else '')).strip()

    def system_instructions(self, context: str) -> str:
        return (self.static_system_instructions() + ('\n\n' + context if context else '')).strip()

    async def run(self, goal, complete, context, allowed_tools=None, on_event=None):
        all_schemas = self.all_tools()
        allowed = set(t['function']['name'] for t in all_schemas) if allowed_tools is None else set(allowed_tools)
        available = [t for t in all_schemas if t['function']['name'] in allowed]
        messages = [{'role': 'system', 'content': self.system_instructions(context)}, {'role': 'user', 'content': goal}]
        trace = []
        accumulated: list[str] = []
        msg = {'content': ''}
        latest_mm_frame: dict | None = None
        for _ in range(40):
            call_messages = messages
            if latest_mm_frame is not None:
                call_messages = messages + [{
                    'role': 'user',
                    'content': [
                        {'type': 'text', 'text': latest_mm_frame['caption']},
                        {'type': 'image_url', 'image_url': {'url': f"data:{latest_mm_frame['mime']};base64,{latest_mm_frame['b64']}"}},
                    ],
                }]
                latest_mm_frame = None
            msg = await complete(call_messages, tools=available or None)
            turn_txt = (msg.get('content') or '').strip()
            if turn_txt and (not accumulated or accumulated[-1] != turn_txt):
                accumulated.append(turn_txt)
                if on_event:
                    await on_event({'type': 'turn_text', 'text': '\n\n'.join(accumulated)})
            messages.append(msg)
            calls = msg.get('tool_calls') or []
            if not calls:
                break
            batch_calls = calls[:24]
            gui_cnt = sum(1 for c in batch_calls if c.get('function', {}).get('name') in ('phone_workspace', 'cloud_desktop'))
            if len(batch_calls) > 1 and gui_cnt <= 1:
                parsed_batch = []
                for c in batch_calls:
                    try:
                        c_args = json.loads(c['function']['arguments'])
                    except (ValueError, KeyError):
                        c_args = {}
                    c_name = c['function']['name']
                    parsed_batch.append((c, c_name, c_args))
                    if on_event:
                        await on_event({'type': 'tool_start', 'tool': c_name, 'arguments': c_args})

                async def _exec_one(c_item, c_name, c_args):
                    t0 = asyncio.get_running_loop().time()
                    res = await self.execute(c_name, c_args) if c_name in allowed else {'error': '此助手未启用该工具'}
                    dur = round((asyncio.get_running_loop().time() - t0) * 1000)
                    return c_item, c_name, c_args, res, dur

                results_p = await asyncio.gather(*[_exec_one(c_i, c_n, c_a) for c_i, c_n, c_a in parsed_batch])
                silent_hit = None
                for c_item, name, args, result, dur in results_p:
                    if name in ('stay_silent', 'do_not_disturb') or (isinstance(result, dict) and result.get('_stay_silent')):
                        silent_hit = result if isinstance(result, dict) else {'reason': 'stay_silent'}
                    if isinstance(result, dict) and result.get('_multimodal_b64'):
                        mm_b64 = str(result.pop('_multimodal_b64'))
                        mm_mime = str(result.pop('_multimodal_mime', 'image/jpeg'))
                        mm_cap = str(result.pop('_multimodal_caption', f'[{name} 实时画面（含 0..1000 标尺）]'))
                        latest_mm_frame = {'b64': mm_b64, 'mime': mm_mime, 'caption': mm_cap}
                    entry = compact_trace_list([{
                        'tool': name,
                        'arguments': args,
                        'result': result,
                        'duration_ms': dur,
                        'status': 'done',
                    }])[0]
                    trace.append(entry)
                    if on_event:
                        await on_event({'type': 'tool_end', 'entry': entry})
                    out_str = json.dumps(result, ensure_ascii=False)
                    if len(out_str) > 16000:
                        out_str = out_str[:4000] + f'\n...[工具单步大输出已截断，原长 {len(out_str)} 字符]...\n' + out_str[-1500:]
                    messages.append({'role': 'tool', 'tool_call_id': c_item['id'], 'content': out_str})
                if silent_hit is not None:
                    return {'text': 'SILENT_OK', 'trace': trace, 'silent': True, 'reason': str(silent_hit.get('reason') or '')}
            else:
                silent_hit = None
                for c in batch_calls:
                    try:
                        args = json.loads(c['function']['arguments'])
                    except (ValueError, KeyError):
                        args = {}
                    name = c['function']['name']
                    if on_event:
                        await on_event({'type': 'tool_start', 'tool': name, 'arguments': args})
                    started = asyncio.get_running_loop().time()
                    result = await self.execute(name, args) if name in allowed else {'error': '此助手未启用该工具'}
                    if name in ('stay_silent', 'do_not_disturb') or (isinstance(result, dict) and result.get('_stay_silent')):
                        silent_hit = result if isinstance(result, dict) else {'reason': 'stay_silent'}
                    if isinstance(result, dict) and result.get('_multimodal_b64'):
                        mm_b64 = str(result.pop('_multimodal_b64'))
                        mm_mime = str(result.pop('_multimodal_mime', 'image/jpeg'))
                        mm_cap = str(result.pop('_multimodal_caption', f'[{name} 实时画面（含 0..1000 标尺）]'))
                        latest_mm_frame = {'b64': mm_b64, 'mime': mm_mime, 'caption': mm_cap}
                    entry = compact_trace_list([{
                        'tool': name,
                        'arguments': args,
                        'result': result,
                        'duration_ms': round((asyncio.get_running_loop().time() - started) * 1000),
                        'status': 'done',
                    }])[0]
                    trace.append(entry)
                    if on_event:
                        await on_event({'type': 'tool_end', 'entry': entry})
                    out_str = json.dumps(result, ensure_ascii=False)
                    if len(out_str) > 16000:
                        out_str = out_str[:4000] + f'\n...[工具单步大输出已截断，原长 {len(out_str)} 字符]...\n' + out_str[-1500:]
                    messages.append({'role': 'tool', 'tool_call_id': c['id'], 'content': out_str})
                if silent_hit is not None:
                    return {'text': 'SILENT_OK', 'trace': trace, 'silent': True, 'reason': str(silent_hit.get('reason') or '')}
        final_text = '\n\n'.join(accumulated).strip() if accumulated else (msg.get('content') or '').strip()
        if not final_text and trace:
            summary_msg = await complete(
                messages + [{'role': 'user', 'content': '请基于以上工具执行结果，直接用自然清晰的语言给出最终答复，不要再调用工具。'}],
                tools=None,
            )
            sum_txt = (summary_msg.get('content') or '').strip()
            if sum_txt and (not accumulated or accumulated[-1] != sum_txt):
                accumulated.append(sum_txt)
            final_text = '\n\n'.join(accumulated).strip()
        return {'text': final_text or '本轮没有生成可验收成果。', 'trace': trace}
