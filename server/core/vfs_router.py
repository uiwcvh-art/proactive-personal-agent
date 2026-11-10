"""
[Module Responsibility]
虚拟文件系统路由、每节点手机状态隔离与无障碍树帧归一化模块 (`server/core/vfs_router.py`)。
负责：
1. `VirtualPathRouter`: 统一处理 `memory_vault/`、`workspace/` 与 `pc_fs` 跨端路径判定、 canonical 别名收敛与沙箱越界防护。
2. `PhoneNodeSessionState` / `PerNodePhoneRegistry`: 按设备节点 (`phone` vs `phone2`) 隔离手机虚拟屏分辨率、包名、节点树哈希、缓存节点与最近点击坐标，消除双安卓设备并发时的状态互相覆盖。
3. `normalize_single_tree_frame` / `apply_authoritative_tree`: 归一化单帧无障碍树文本与节点计数，消除多字段 (`ui_tree`/`tree`/`summary`) 分裂。

[Public Interface]
- `VirtualPathRouter`
- `PhoneNodeSessionState`, `PerNodePhoneRegistry`
- `score_tree_text(txt_val: str) -> tuple[int, str]`
- `normalize_single_tree_frame(target_d: dict) -> tuple[str, int, str]`
- `apply_authoritative_tree(target_d: dict, auth_d: dict) -> None`

[Dependencies]
- Python 标准库 (`dataclasses`, `pathlib`, `re`)
- `storage.normalize_node_source`
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from storage import normalize_node_source


class VirtualPathRouter:
    """Unified path classification & canonicalization across Workspace, Memory Vault, and PC FS."""

    VAULT_PREFIXES = ('notes/', 'skills/', 'profile/', 'logs/', 'briefs/', 'archive/')
    VAULT_ROOT_FILES = ('INDEX.md', 'SOUL.md', 'USER.md', 'AGENTS.md')
    CANONICAL_ALIASES = {
        'soul.md': 'SOUL.md',
        'workspace/soul.md': 'SOUL.md',
        'profile/soul.md': 'SOUL.md',
        'user.md': 'USER.md',
        'workspace/user.md': 'USER.md',
        'profile/user.md': 'USER.md',
        'agents.md': 'AGENTS.md',
        'workspace/agents.md': 'AGENTS.md',
        'profile/agents.md': 'AGENTS.md',
        'index.md': 'INDEX.md',
    }

    @classmethod
    def normalize_rel_path(cls, rel: str) -> str:
        s = str(rel or '').strip().replace('\\', '/').lstrip('/')
        for prefix in ('memory_vault/', 'vault/', './'):
            if s.startswith(prefix):
                s = s[len(prefix):].lstrip('/')
        alias = cls.CANONICAL_ALIASES.get(s.lower())
        return alias if alias else s

    @classmethod
    def is_vault_rel_path(cls, rel: str) -> bool:
        raw = str(rel or '').strip().replace('\\', '/').lstrip('/')
        if raw.startswith(('memory_vault/', 'vault/')):
            return True
        s = cls.normalize_rel_path(raw)
        if not s:
            return False
        if s in cls.VAULT_ROOT_FILES:
            return True
        return any(s.startswith(p) for p in cls.VAULT_PREFIXES)

    @staticmethod
    def is_pc_explicit_path(args: dict, path_str: str = '') -> bool:
        target = str(
            args.get('target')
            or args.get('device')
            or args.get('env')
            or args.get('node')
            or ''
        ).strip().lower()
        if target in ('pc', 'dsh', 'windows', 'local_pc', 'host', 'desktop_pc'):
            return True
        p = str(path_str or args.get('path') or args.get('cwd') or '').strip()
        if re.match(r'^[A-Za-z]:[\\/]', p) or p.startswith('\\\\') or p.startswith('~\\'):
            return True
        return False


@dataclass
class PhoneNodeSessionState:
    """Per-Android-device (`phone` vs `phone2`) isolated VirtualDisplay & Accessibility state."""

    source: str = 'phone'
    last_tap: tuple[int, int] | None = None
    last_size: tuple[int, int] = (720, 1600)
    last_pkg: str = ''
    last_node_count: int = -1
    last_tree_hash: str = ''
    last_nodes: list[dict[str, Any]] = field(default_factory=list)
    vdisplay_open: bool = False
    used_in_turn: bool = False
    to_foreground_in_turn: bool = False


class PerNodePhoneRegistry:
    """Maintains isolated `PhoneNodeSessionState` instances keyed by normalized node source (`phone`, `phone2`)."""

    def __init__(self) -> None:
        self._by_source: dict[str, PhoneNodeSessionState] = {
            'phone': PhoneNodeSessionState(source='phone'),
            'phone2': PhoneNodeSessionState(source='phone2'),
        }

    def get(self, source: str | None = None) -> PhoneNodeSessionState:
        norm = normalize_node_source(source or 'phone')
        if norm not in ('phone', 'phone2'):
            norm = 'phone'
        st = self._by_source.get(norm)
        if st is None:
            st = PhoneNodeSessionState(source=norm)
            self._by_source[norm] = st
        return st


def score_tree_text(txt_val: str) -> tuple[int, str]:
    s_val = str(txt_val or '').strip()
    if not s_val:
        return (-1, '')
    m_c = re.search(r'\bcount=(\d+)', s_val)
    cnt_hdr = int(m_c.group(1)) if m_c else 0
    ref_lines = len(re.findall(r'(?m)^\s*@[ev]\d+\b', s_val))
    return (max(cnt_hdr, ref_lines) * 1000 + len(s_val), s_val)


def normalize_single_tree_frame(target_d: dict) -> tuple[str, int, str]:
    """Collapse `ui_tree`/`tree`/`summary` and `node_count`/`nodeCount`/`count` into a single authoritative frame."""
    candidates = [score_tree_text(target_d.get(k)) for k in ('ui_tree', 'tree', 'summary') if target_d.get(k)]
    best_txt = max(candidates, key=lambda x: x[0])[1] if candidates else ''
    pkg_now = str(target_d.get('package') or '')
    m_pkg = re.search(r'\bpackage=([^\s\]]+)', best_txt)
    if m_pkg and (not pkg_now or pkg_now == 'unknown'):
        pkg_now = m_pkg.group(1)
    raw_counts = [
        int(target_d[k])
        for k in ('node_count', 'nodeCount', 'count')
        if k in target_d and target_d[k] is not None and str(target_d[k]).isdigit()
    ]
    m_cnt = re.search(r'\bcount=(\d+)', best_txt)
    if m_cnt:
        raw_counts.append(int(m_cnt.group(1)))
    ref_cnt = len(re.findall(r'(?m)^\s*@[ev]\d+\b', best_txt))
    if ref_cnt > 0:
        raw_counts.append(ref_cnt)
    best_cnt = max(raw_counts) if raw_counts else 0
    if best_txt:
        best_txt = re.sub(r'\bcount=\d+', f'count={best_cnt}', best_txt, count=1)
        if pkg_now:
            best_txt = re.sub(r'\bpackage=[^\s\]]+', f'package={pkg_now}', best_txt, count=1)
        target_d['ui_tree'] = best_txt
        target_d['tree'] = best_txt
    target_d.pop('summary', None)
    target_d.pop('nodeCount', None)
    target_d.pop('count', None)
    if pkg_now:
        target_d['package'] = pkg_now
    target_d['node_count'] = best_cnt
    m_h = re.search(r'treeHash=([0-9a-zA-Z_-]+)', best_txt)
    raw_h = m_h.group(1) if m_h else str(target_d.get('treeHash') or target_d.get('tree_hash') or '').strip()
    if ':' in raw_h:
        raw_h = raw_h.split(':', 1)[-1]
    if raw_h:
        target_d['treeHash'] = raw_h
        target_d['tree_hash'] = raw_h
    if pkg_now and raw_h:
        w_sig = int(target_d.get('width') or 720)
        h_sig = int(target_d.get('height') or 1584)
        target_d['layout_signature'] = f"{w_sig}x{h_sig}_{pkg_now}_{raw_h}_n{best_cnt}"
    return pkg_now, best_cnt, (f"{pkg_now}:{raw_h}" if raw_h else '')


def apply_authoritative_tree(target_d: dict, auth_d: dict) -> None:
    old_pkg, old_cnt, old_h = normalize_single_tree_frame(target_d)
    new_pkg, new_cnt, new_h = normalize_single_tree_frame(auth_d)
    if (old_h and new_h and old_h != new_h) or (old_pkg == 'unknown' and new_pkg and new_pkg != 'unknown'):
        target_d['before_action'] = {'package': old_pkg or 'unknown', 'node_count': old_cnt, 'tree_hash': old_h}
    for k_ov in (
        'package', 'node_count', 'ui_tree', 'tree', 'treeHash', 'tree_hash',
        'layout_signature', 'layoutStable', 'settled', 'likely_blank',
        'densityDpi', 'screenWidthDp', 'fontScale', 'settle_ms',
        'width', 'height', 'displayId', 'display', 'nodes',
    ):
        if k_ov in auth_d and auth_d[k_ov] is not None:
            target_d[k_ov] = auth_d[k_ov]
    normalize_single_tree_frame(target_d)
