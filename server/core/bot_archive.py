"""
[Module Responsibility]
Bot 完整档案一键导出与导入模块 (`server/core/bot_archive.py`)。
支持将单个 Bot 的人设指令、节点配置 (`home_node`/`node_settings`)、完整对话历史（含原生 `turn_steps`）、
专属定时例行任务、黑板关联条目与 `memory_vault/` 专属会话笔记打包为 `.dsb-bot.json`，
并在恢复出厂设置、跨端迁移或克隆分身时一键无损导入回归。
"""
from __future__ import annotations

import copy
import re
import secrets
import time
from typing import Any

from assistants import TOOL_NAMES, ensure_assistants, get_assistant, resolve_bot_for_source
from storage import normalize_node_source
from vault_manager import now_iso

ARCHIVE_SCHEMA = 'dsb-bot-archive/v1'


def export_bot_bundle(
    store: Any,
    vault: Any,
    bot_id: str,
    source: str | None = None,
    include_messages: bool = True,
    message_limit: int = 800,
) -> dict[str, Any]:
    """Export a single Bot's complete persona, settings, history, routines, and vault notes as a portable dict."""
    ensure_assistants(store)
    bot = copy.deepcopy(get_assistant(store, bot_id))
    canonical_id = str(bot['id'])
    norm_src = normalize_node_source(source) if source else str(bot.get('home_node') or 'phone')

    all_msgs = store.state.get('messages') or []
    bot_msgs: list[dict] = []
    if include_messages:
        cap = max(1, min(5000, int(message_limit or 800)))
        matched = [
            copy.deepcopy(m)
            for m in all_msgs
            if isinstance(m, dict) and m.get('bot_id', 'companion') == canonical_id
        ]
        bot_msgs = matched[-cap:]

    routines = [
        copy.deepcopy(r)
        for r in (store.state.get('routines') or [])
        if isinstance(r, dict) and r.get('bot_id') == canonical_id
    ]
    timers = [
        copy.deepcopy(t)
        for t in (store.state.get('timers') or [])
        if isinstance(t, dict) and t.get('bot_id') == canonical_id and not t.get('done')
    ]
    bb_posts = [
        copy.deepcopy(p)
        for p in (store.state.get('blackboard_posts') or [])
        if isinstance(p, dict)
        and (p.get('author_bot_id') == canonical_id or p.get('claimed_by') == canonical_id)
    ][-50:]

    vault_notes: dict[str, str] = {}
    if vault is not None:
        try:
            for rel in vault.files():
                norm_rel = str(rel).replace('\\', '/')
                if (
                    norm_rel.startswith(f'timeline/bots/{canonical_id}/')
                    or norm_rel.startswith(f'conversations/{canonical_id}_')
                ):
                    vault_notes[norm_rel] = vault.read(norm_rel)[:60000]
        except Exception:
            pass

    safe_slug = re.sub(r'[^a-zA-Z0-9_\-\u4e00-\u9fa5]', '_', str(bot.get('name') or canonical_id)).strip('_') or canonical_id
    fname = f'{safe_slug}-{canonical_id}.dsb-bot.json'
    return {
        'ok': True,
        'format': 'dsb-bot-archive',
        'version': '1.0',
        'schema': ARCHIVE_SCHEMA,
        'exported_at': now_iso(),
        'exported_ts': time.time(),
        'filename': fname,
        'suggested_filename': fname,
        'source_node': norm_src,
        'bot': bot,
        'messages': bot_msgs,
        'routines': routines,
        'timers': timers,
        'blackboard_posts': bb_posts,
        'vault_notes': vault_notes,
        'stats': {
            'messages_count': len(bot_msgs),
            'routines_count': len(routines),
            'vault_notes_count': len(vault_notes),
        },
    }


def import_bot_bundle(
    store: Any,
    vault: Any,
    bundle: dict[str, Any],
    mode: str = 'merge',
    source: str | None = None,
    new_id: str | None = None,
    adapt_home_node: bool = False,
) -> dict[str, Any]:
    """
    Import a `.dsb-bot.json` bundle into `store` and `vault`.
    - `mode="merge"` (default): upsert Bot persona & settings, merge non-duplicate messages by `id`, restore routines & vault files.
    - `mode="replace"`: overwrite target Bot's config and replace all messages belonging to that `bot_id`.
    - `mode="clone"`: create a brand-new Bot ID (`new_id` or generated) and import history into the new room.
    """
    if not isinstance(bundle, dict):
        raise ValueError('无效的 Bot 档案格式（需要 JSON 对象）')
    raw_bot = bundle.get('bot') if isinstance(bundle.get('bot'), dict) else None
    if raw_bot is None and bundle.get('name') and ('instructions' in bundle or 'id' in bundle):
        raw_bot = bundle
    if not isinstance(raw_bot, dict) or not str(raw_bot.get('name') or '').strip():
        raise ValueError('档案缺少有效的 bot.name 字段')

    ensure_assistants(store)
    eff_mode = str(mode or 'merge').strip().lower()
    if eff_mode not in ('merge', 'replace', 'clone'):
        eff_mode = 'merge'

    norm_src = normalize_node_source(source) if source else None
    orig_id = re.sub(r'[^a-zA-Z0-9_-]', '', str(raw_bot.get('id') or '')).lower() or ('bot-' + secrets.token_hex(4))
    existing_ids = {str(b.get('id') or '') for b in store.state.get('assistants', []) if isinstance(b, dict)}

    if eff_mode == 'clone':
        cand_id = re.sub(r'[^a-zA-Z0-9_-]', '', str(new_id or '')).lower()
        target_id = cand_id if (cand_id and cand_id not in existing_ids) else f'{orig_id[:20]}-{secrets.token_hex(3)}'
    else:
        target_id = re.sub(r'[^a-zA-Z0-9_-]', '', str(new_id or orig_id)).lower() or orig_id

    bot_copy = copy.deepcopy(raw_bot)
    bot_copy['id'] = target_id
    bot_copy['name'] = str(bot_copy.get('name') or target_id).strip()[:40]
    if eff_mode == 'clone' and any(b.get('name') == bot_copy['name'] and b.get('id') != target_id for b in store.state['assistants']):
        bot_copy['name'] = f"{bot_copy['name']}·副本"[:40]
    if adapt_home_node and norm_src:
        bot_copy['home_node'] = 'pc' if norm_src in ('pc', 'pc2') else 'phone'
    elif not bot_copy.get('home_node'):
        bot_copy['home_node'] = 'pc' if (norm_src in ('pc', 'pc2')) else 'phone'

    raw_tools = bot_copy.get('tools')
    if not isinstance(raw_tools, list) or not raw_tools:
        bot_copy['tools'] = TOOL_NAMES.copy()
    else:
        cleaned_tools = [str(t).strip() for t in raw_tools if str(t).strip() and str(t).strip() != 'send_message']
        for must_t in ('blackboard', 'peek_bots', 'cloud_desktop'):
            if must_t in TOOL_NAMES and must_t not in cleaned_tools:
                cleaned_tools.append(must_t)
        bot_copy['tools'] = cleaned_tools

    if not isinstance(bot_copy.get('node_settings'), dict):
        bot_copy['node_settings'] = {}
    for nk in ('phone', 'phone2', 'pc', 'pc2'):
        bot_copy['node_settings'].setdefault(nk, {
            'curiosity_enabled': bool(bot_copy.get('curiosity_enabled', False)) if nk == 'phone' else False,
            'notify': bool(bot_copy.get('notify', True)),
            'model': str(bot_copy.get('model') or ''),
            'pinned': bool(bot_copy.get('pinned', False)),
            'pinned_at': float(bot_copy.get('pinned_at') or 0),
            'hidden': False,
        })
    bot_copy['updated_at'] = time.time()

    # Upsert assistant entry & clear from deleted_bot_ids if previously deleted
    deleted_ids = store.state.get('deleted_bot_ids')
    if isinstance(deleted_ids, list) and target_id in deleted_ids:
        store.state['deleted_bot_ids'] = [x for x in deleted_ids if x != target_id]

    idx_existing = next((i for i, b in enumerate(store.state['assistants']) if b.get('id') == target_id), -1)
    if idx_existing >= 0:
        store.state['assistants'][idx_existing] = {**store.state['assistants'][idx_existing], **bot_copy}
    else:
        store.state['assistants'].append(bot_copy)

    # Restore messages
    incoming_msgs = bundle.get('messages') if isinstance(bundle.get('messages'), list) else []
    cur_msgs = list(store.state.get('messages') or [])
    if eff_mode == 'replace':
        cur_msgs = [m for m in cur_msgs if m.get('bot_id', 'companion') != target_id]
    existing_msg_ids = {str(m.get('id') or '') for m in cur_msgs if isinstance(m, dict) and m.get('id')}
    imported_msg_count = 0
    for rm in incoming_msgs:
        if not isinstance(rm, dict) or rm.get('role') not in ('user', 'assistant'):
            continue
        mc = copy.deepcopy(rm)
        mc['bot_id'] = target_id
        if mc.get('role') == 'assistant' and mc.get('from_bot_id') == orig_id:
            mc['from_bot_id'] = target_id
            mc['from_bot_name'] = bot_copy['name']
        if adapt_home_node and norm_src:
            mc['source'] = norm_src
        mid = str(mc.get('id') or '').strip()
        if eff_mode == 'clone' or not mid:
            mid = secrets.token_hex(12)
            mc['id'] = mid
        if mid in existing_msg_ids:
            continue
        existing_msg_ids.add(mid)
        cur_msgs.append(mc)
        imported_msg_count += 1

    if imported_msg_count > 0:
        cur_msgs.sort(key=lambda x: float(x.get('created_at') or x.get('created') or 0.0))
        store.state['messages'] = cur_msgs

    # Restore routines
    incoming_routines = bundle.get('routines') if isinstance(bundle.get('routines'), list) else []
    cur_routines = list(store.state.get('routines') or [])
    if eff_mode == 'replace':
        cur_routines = [r for r in cur_routines if r.get('bot_id') != target_id]
    existing_r_ids = {str(r.get('id') or '') for r in cur_routines if isinstance(r, dict)}
    restored_routines = 0
    for rr in incoming_routines:
        if not isinstance(rr, dict) or not str(rr.get('goal') or '').strip():
            continue
        rc = copy.deepcopy(rr)
        rc['bot_id'] = target_id
        rid = str(rc.get('id') or '')
        if eff_mode == 'clone' or not rid:
            rid = secrets.token_hex(8)
            rc['id'] = rid
        if rid not in existing_r_ids:
            existing_r_ids.add(rid)
            cur_routines.append(rc)
            restored_routines += 1
    store.state['routines'] = cur_routines

    # Restore vault notes
    restored_notes = 0
    raw_notes = bundle.get('vault_notes') if isinstance(bundle.get('vault_notes'), dict) else {}
    if vault is not None and raw_notes:
        for rel_p, content in raw_notes.items():
            if not isinstance(rel_p, str) or not isinstance(content, str):
                continue
            target_rel = rel_p
            if orig_id != target_id:
                target_rel = target_rel.replace(f'timeline/bots/{orig_id}/', f'timeline/bots/{target_id}/')
                target_rel = target_rel.replace(f'conversations/{orig_id}_', f'conversations/{target_id}_')
            if target_rel.endswith('.md') and '..' not in target_rel:
                try:
                    vault.write(target_rel, content)
                    restored_notes += 1
                except Exception:
                    pass
        if restored_notes > 0:
            try:
                vault.reindex()
            except Exception:
                pass

    store.state['messages_epoch'] = int(store.state.get('messages_epoch') or 1) + 1
    store.save()
    eff_view_src = norm_src or str(bot_copy.get('home_node') or 'phone')
    return {
        'ok': True,
        'mode': eff_mode,
        'bot_id': target_id,
        'bot': resolve_bot_for_source(bot_copy, eff_view_src),
        'imported_messages': imported_msg_count,
        'restored_routines': restored_routines,
        'restored_vault_notes': restored_notes,
        'messages_epoch': store.state['messages_epoch'],
    }
