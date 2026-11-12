"""Silent Background Memory, Pinned Rules & 200K Immutable-Prefix Context Engine.

Follows Letta / A-MEM Zettelkasten & Immutable Prefix Caching principles:
- Preserves full 200,000-token context capacity.
- NEVER mutates already-sent messages, images, or tool outputs mid-session (100% Prefix Cache hit rate!).
- Separates static `📌 Pinned Memories` (in `messages[0]`) from dynamic `INDEX.md` / `user_traits.md` so `vault.reindex()`
  never invalidates the upstream prompt cache prefix.
- Archives 200K compacted transcripts into both `memory_vault/timeline` and SQLite FTS5 (`context_search`).
"""
from __future__ import annotations

import json
import re
import secrets
import time
from storage import normalize_node_source, node_display_name
from vault_manager import now_iso


class MemoryEngine:
    AUTO_COMPACT_TOKENS = 100_000
    KEEP_RECENT_TOKENS = 35_000
    SLEEP_CONSOLIDATE_IDLE_SECONDS = 180
    PINNED_FILE = 'profile/pinned_memories.md'

    def __init__(
        self,
        store,
        vault,
        harness=None,
        last_prompt_tokens: dict[str, int] | None = None,
        context_search=None,
        last_cached_tokens: dict[str, int] | None = None,
    ):
        self.store = store
        self.vault = vault
        self.harness = harness
        self.last_prompt_tokens = last_prompt_tokens if last_prompt_tokens is not None else {}
        self.last_cached_tokens = last_cached_tokens if last_cached_tokens is not None else {}
        self.context_search = context_search
        self._consolidating = False
        self._suppress_vault_listener = False
        self._last_vault_lines: dict[str, set[str]] = {}
        try:
            for init_rel in ('profile/personal_impression.md', 'profile/user_traits.md'):
                raw_init = self.vault.read(init_rel)
                self._last_vault_lines[init_rel] = {ln.strip() for ln in (raw_init or '').splitlines() if ln.strip()}
        except Exception:
            pass
        if hasattr(self.vault, 'mutation_listeners') and self._on_vault_mutation not in self.vault.mutation_listeners:
            self.vault.mutation_listeners.append(self._on_vault_mutation)
        try:
            saved_pt = self.store.state.get('last_prompt_tokens')
            if isinstance(saved_pt, dict):
                for k, v in saved_pt.items():
                    if int(v or 0) > 0 and k not in self.last_prompt_tokens:
                        self.last_prompt_tokens[str(k)] = int(v)
            saved_ct = self.store.state.get('last_cached_tokens')
            if isinstance(saved_ct, dict):
                for k, v in saved_ct.items():
                    if int(v or 0) >= 0 and k not in self.last_cached_tokens:
                        self.last_cached_tokens[str(k)] = int(v)
        except Exception:
            pass

    def _on_vault_mutation(self, rel_name: str, text: str) -> None:
        """Automatically record a tail memory delta whenever any core memory_vault file is written or edited."""
        if self._suppress_vault_listener:
            return
        norm_rel = (rel_name or '').replace('\\', '/').lstrip('/')
        if (
            not norm_rel
            or norm_rel == 'INDEX.md'
            or norm_rel.startswith(('logs/', 'config/', 'timeline/', 'notes/compact_'))
        ):
            return
        lines = [ln.strip() for ln in (text or '').splitlines() if ln.strip()]
        if not lines:
            return
        title = lines[0].lstrip('#').strip()[:80]
        bullets = [ln for ln in lines[1:] if ln.startswith(('- ', '* ', '1.', '2.', '|')) and '---' not in ln]
        prev_set = self._last_vault_lines.get(norm_rel, set())
        diff_bullets = [ln for ln in bullets if ln not in prev_set and '（待了解）' not in ln and '(待了解)' not in ln]
        self._last_vault_lines[norm_rel] = set(lines)
        target_bullets = diff_bullets[-4:] if diff_bullets else bullets[-3:]
        snippet = ' ； '.join(b[:140] for b in target_bullets) if target_bullets else ' '.join(lines[1:4])[:240]
        summary = f"记忆文件更新 `{norm_rel}` ({title}): {snippet}".strip()
        self.record_memory_delta(summary, path=f'memory_vault/{norm_rel}', source='vault')

    def record_memory_delta(self, summary: str, path: str = '', source: str = 'all', after_msg_id: str | None = None) -> dict:
        """
        Append a concise memory update to the pending tail delta queue.
        Instead of mutating `messages[0]` (which destroys the 200K Prefix KV Cache),
        deltas are appended once at the bottom (tail) of the conversation context and locked in place!
        """
        clean = ' '.join(str(summary or '').strip().splitlines()).strip()
        if not clean:
            return {}
        deltas = self.store.state.setdefault('memory_deltas', [])
        now_ts = time.time()
        stamp = now_iso()
        if not after_msg_id:
            all_msgs = self.store.state.get('messages') or []
            if all_msgs and isinstance(all_msgs[-1], dict):
                after_msg_id = str(all_msgs[-1].get('id') or '')
        if deltas and path and deltas[-1].get('path') == path and not deltas[-1].get('consumed_by'):
            deltas[-1]['summary'] = clean[:500]
            deltas[-1]['timestamp'] = now_ts
            deltas[-1]['time_iso'] = stamp
            if after_msg_id:
                deltas[-1]['after_msg_id'] = after_msg_id
            item = deltas[-1]
        else:
            item = {
                'id': 'mdelta-' + secrets.token_hex(4),
                'timestamp': now_ts,
                'time_iso': stamp,
                'path': path,
                'summary': clean[:500],
                'source': source,
                'after_msg_id': after_msg_id or '',
                'anchors': {},
                'consumed_by': [],
            }
            deltas.append(item)
            if len(deltas) > 40:
                self.store.state['memory_deltas'] = deltas[-40:]
        try:
            self.store.save()
        except Exception:
            pass
        return item

    def record_delta(
        self,
        summary: str,
        path: str = '',
        source: str = 'all',
        kind: str = '',
        source_node: str = '',
        after_msg_id: str | None = None,
    ) -> dict:
        """Backward-compatible alias for `record_memory_delta`."""
        return self.record_memory_delta(
            summary=summary,
            path=path or (f'topology/{kind}' if kind else ''),
            source=source_node or source or 'all',
            after_msg_id=after_msg_id,
        )

    @staticmethod
    def _format_memory_delta_block(items: list[dict]) -> str:
        lines = [
            '[System Memory Delta / 最新记忆同步（以下为会话期间新增或变更的记忆与状态定论，追加在对话最底下以保持前缀 KV 缓存，优先级高于顶部旧快照）]'
        ]
        for d in items[-10:]:
            p_tag = f" [{d['path']}]" if d.get('path') else ''
            lines.append(f"- ({d.get('time_iso') or '最新'}){p_tag} {d.get('summary', '')}")
        return '\n'.join(lines)

    def consume_tail_memory_delta(self, bot_id: str = 'companion', source: str = 'phone', tail_msg_id: str = '') -> str:
        """
        Consume uninjected memory deltas for `(bot_id, source)` and format them as a Tail Memory Delta block
        to be appended at the very bottom of the current turn's context (`recent[-1]`).
        """
        src = self._normalize_source(source)
        consumer_key = f"{bot_id}:{src}"
        deltas = self.store.state.get('memory_deltas')
        if not isinstance(deltas, list) or not deltas:
            return ''
        unconsumed = []
        for d in deltas:
            if not isinstance(d, dict):
                continue
            consumed_by = d.setdefault('consumed_by', [])
            anchors = d.setdefault('anchors', {})
            if consumer_key not in consumed_by:
                unconsumed.append(d)
                consumed_by.append(consumer_key)
                if tail_msg_id:
                    anchors[consumer_key] = tail_msg_id
        if not unconsumed:
            return ''
        try:
            self.store.save()
        except Exception:
            pass
        return self._format_memory_delta_block(unconsumed)

    def inject_memory_deltas(self, recent_msgs: list[dict], bot_id: str = 'companion', source: str = 'phone') -> list[dict]:
        """
        Inject memory deltas into `recent_msgs` while strictly preserving 100% Prefix KV Cache
        AND 100% standard OpenAI API format (only `messages[0]` is `role="system"`, and `messages[-1]` is always `role="user"`):
        - Previously consumed deltas stay locked inside their anchored user message in `recent_msgs` (never shifting position).
        - Newly generated deltas are locked to the current tail user message (`recent_msgs[-1]['id']`) and appended at the very bottom (Tail) of its text!
        """
        if not recent_msgs:
            return recent_msgs
        deltas = self.store.state.get('memory_deltas')
        if not isinstance(deltas, list) or not deltas:
            return recent_msgs
        src = self._normalize_source(source)
        consumer_key = f"{bot_id}:{src}"
        tail_user_id = ''
        for m in reversed(recent_msgs):
            if m.get('role') == 'user' and m.get('id'):
                tail_user_id = str(m['id'])
                break
        if not tail_user_id:
            tail_user_id = str(recent_msgs[-1].get('id') or '')
        by_anchor: dict[str, list[dict]] = {}
        dirty = False
        for d in deltas:
            if not isinstance(d, dict):
                continue
            anchors = d.setdefault('anchors', {})
            consumed_by = d.setdefault('consumed_by', [])
            anchor_id = anchors.get(consumer_key)
            if not anchor_id:
                anchor_id = tail_user_id
                if tail_user_id:
                    anchors[consumer_key] = tail_user_id
                if consumer_key not in consumed_by:
                    consumed_by.append(consumer_key)
                dirty = True
            if anchor_id:
                by_anchor.setdefault(anchor_id, []).append(d)
        if dirty:
            try:
                self.store.save()
            except Exception:
                pass
        if not by_anchor:
            return recent_msgs
        out: list[dict] = []
        for m in recent_msgs:
            mid = str(m.get('id') or '')
            if mid and mid in by_anchor:
                block = '\n\n' + self._format_memory_delta_block(by_anchor[mid])
                c = m.get('content')
                if isinstance(c, list):
                    new_c = []
                    appended = False
                    for part in c:
                        if isinstance(part, dict) and part.get('type') == 'text' and not appended:
                            new_c.append({**part, 'text': str(part.get('text') or '') + block})
                            appended = True
                        else:
                            new_c.append(part)
                    if not appended:
                        new_c.insert(0, {'type': 'text', 'text': block.strip()})
                    out.append({**m, 'content': new_c})
                else:
                    out.append({**m, 'content': str(c or '') + block})
            else:
                out.append(m)
        return out

    def peek_recent_memory_deltas(self, limit: int = 8) -> str:
        """Read-only digest of recent memory deltas for curiosity / scheduled tasks."""
        deltas = self.store.state.get('memory_deltas')
        if not isinstance(deltas, list) or not deltas:
            return ''
        recent = [d for d in deltas if isinstance(d, dict)][-limit:]
        if not recent:
            return ''
        lines = ['【近期记忆与状态变更同步 (Memory Deltas)】']
        for d in recent:
            p_tag = f" [{d['path']}]" if d.get('path') else ''
            lines.append(f"- ({d.get('time_iso') or '最新'}){p_tag} {d.get('summary', '')}")
        return '\n'.join(lines)

    def clear_memory_deltas(self, bot_id: str | None = None, source: str | None = None) -> None:
        """Clear pending memory deltas after 200K full compaction refreshes `messages[0]`."""
        if not bot_id:
            self.store.state['memory_deltas'] = []
        else:
            src = self._normalize_source(source) if source else None
            deltas = self.store.state.get('memory_deltas')
            if isinstance(deltas, list):
                for d in deltas:
                    if not isinstance(d, dict):
                        continue
                    consumed = d.setdefault('consumed_by', [])
                    anchors = d.setdefault('anchors', {})
                    targets = [f'{bot_id}:{src}'] if src else [f'{bot_id}:phone', f'{bot_id}:phone2', f'{bot_id}:pc', f'{bot_id}:pc2']
                    for ck in targets:
                        if ck not in consumed:
                            consumed.append(ck)
                        anchors.pop(ck, None)
        try:
            self.store.save()
        except Exception:
            pass

    @staticmethod
    def estimate_tokens(text) -> int:
        from core.llm_client import LLMClient
        if not text:
            return 0
        if isinstance(text, list):
            return sum(1100 if (isinstance(p, dict) and p.get('type') == 'image_url') else MemoryEngine.estimate_tokens((p.get('text') or p.get('content') or '') if isinstance(p, dict) else str(p)) for p in text)
        return LLMClient._estimate_text_tokens(json.dumps(text, ensure_ascii=False) if isinstance(text, dict) else str(text))

    @staticmethod
    def estimate_messages_tokens(messages: list[dict]) -> int:
        from core.llm_client import LLMClient
        return LLMClient._estimate_payload_tokens(messages)

    @staticmethod
    def prune_tool_loop_messages(messages: list[dict]):
        """
        Prunes transient multimodal `image_url` blocks from earlier tool-loop steps (`messages[:-1]`),
        retaining only the newest step's visual screenshot while keeping text prefix 100% intact
        and replacing message dicts immutably without mutating stored references in-place.
        """
        if not isinstance(messages, list) or len(messages) <= 2:
            return
        for idx in range(len(messages) - 1):
            msg = messages[idx]
            if not isinstance(msg, dict):
                continue
            content = msg.get('content')
            if isinstance(content, list) and any(isinstance(p, dict) and p.get('type') == 'image_url' for p in content):
                cleaned_parts = []
                for p in content:
                    if isinstance(p, dict) and p.get('type') == 'image_url':
                        cleaned_parts.append({'type': 'text', 'text': '[历史步骤截图已省略]'})
                    else:
                        cleaned_parts.append(p)
                messages[idx] = {**msg, 'content': cleaned_parts}

    # --- Visual Memory Pinning (📌) ---
    @staticmethod
    def _normalize_source(source: str | None) -> str:
        return normalize_node_source(source)

    @classmethod
    def _parse_pin_source_and_text(cls, raw_text: str) -> tuple[str, str]:
        t = (raw_text or '').strip()
        m = re.match(r'^\[来源:\s*(PC端|手机端|Windows一|Windows二|安卓一|安卓二|pc2|pc|phone2|phone)\]\s*(.+)$', t, re.IGNORECASE)
        if m:
            raw_tag = m.group(1).strip()
            src = normalize_node_source(raw_tag)
            clean_body = m.group(2).strip()
            tag_label = '[来源: PC端]' if src == 'pc' else ('[来源: 手机端]' if src == 'phone' else f'[来源: {node_display_name(src)}]')
            return src, f'{tag_label} {clean_body}'
        return 'phone', t

    def list_pins(self) -> list[dict]:
        try:
            raw = self.vault.read(self.PINNED_FILE)
        except Exception:
            return []
        pins: list[dict] = []
        for ln in raw.splitlines():
            s = ln.strip()
            if not s or s.startswith('#'):
                continue
            m = re.match(r'^-\s*\[([a-zA-Z0-9_-]+)\]\s*(.+)$', s)
            if m:
                src, formatted_text = self._parse_pin_source_and_text(m.group(2).strip())
                pins.append({'id': m.group(1), 'text': formatted_text, 'source': src})
            elif s.startswith('- '):
                pid = 'pin-' + secrets.token_hex(3)
                src, formatted_text = self._parse_pin_source_and_text(s[2:].strip())
                pins.append({'id': pid, 'text': formatted_text, 'source': src})
        return pins

    def get_pins(self) -> list[dict]:
        return self.list_pins()

    def _save_pins(self, pins: list[dict]) -> None:
        lines = ['# 📌 核心钉住记忆 (Pinned Core Memories)', '']
        for p in pins:
            lines.append(f"- [{p['id']}] {p['text']}")
        self.vault.write(self.PINNED_FILE, '\n'.join(lines) + '\n')

    def add_pin(self, text: str, bot_id: str = 'companion', source: str = 'phone') -> dict:
        t = (text or '').strip()
        if not t:
            return {'error': '钉住内容不能为空'}
        src = self._normalize_source(source)
        tag_rx = r'^\[来源:\s*(PC端|手机端|Windows一|Windows二|安卓一|安卓二|pc2|pc|phone2|phone)\]'
        if re.match(tag_rx, t, re.IGNORECASE):
            src, formatted_text = self._parse_pin_source_and_text(t)
        else:
            tag_label = '[来源: PC端]' if src == 'pc' else ('[来源: 手机端]' if src == 'phone' else f'[来源: {node_display_name(src)}]')
            formatted_text = f'{tag_label} {t}'
        pins = self.list_pins()
        clean_target = re.sub(tag_rx + r'\s*', '', formatted_text, flags=re.IGNORECASE).strip()
        for p in pins:
            existing_clean = re.sub(tag_rx + r'\s*', '', p['text'], flags=re.IGNORECASE).strip()
            if p['text'] == formatted_text or existing_clean == clean_target:
                p['text'] = formatted_text[:600]
                p['source'] = src
                self._save_pins(pins)
                return {'ok': True, 'pinned': True, 'id': p['id'], 'pin': p, 'pins': pins, 'source': src, 'total': len(pins)}
        new_pin = {'id': 'pin-' + secrets.token_hex(3), 'text': formatted_text[:600], 'source': src}
        pins.append(new_pin)
        self._save_pins(pins)
        return {'ok': True, 'pinned': True, 'id': new_pin['id'], 'pin': new_pin, 'pins': pins, 'source': src, 'total': len(pins)}

    def remove_pin(self, pin_id_or_text: str) -> dict:
        q = (pin_id_or_text or '').strip()
        pins = self.list_pins()
        kept = [p for p in pins if p['id'] != q and q not in p['text']]
        removed_count = len(pins) - len(kept)
        if removed_count > 0:
            self._save_pins(kept)
        return {'ok': removed_count > 0, 'removed': removed_count > 0, 'removed_count': removed_count, 'pins': kept, **({'error': f'未找到匹配的置顶记忆: {q}'} if removed_count == 0 else {})}

    def build_static_pinned_block(self) -> str:
        """Static block safe to include inside `messages[0]` system prompt without breaking Prefix Cache."""
        pins = self.list_pins()
        if not pins:
            return ''
        lines = ['【📌 永不遗忘的核心钉住记忆（最高优先级）】']
        for p in pins:
            lines.append(f"- [{p['id']}] {p['text']}")
        return '\n'.join(lines)

    def get_personal_impression(self) -> tuple[str, list[str]]:
        """Read `profile/personal_impression.md` and return `(full_text, missing_items)`."""
        imp_path = 'profile/personal_impression.md'
        try:
            raw_imp = self.vault.read(imp_path)
        except Exception:
            from vault_manager import TEMPLATES
            raw_imp = TEMPLATES.get(imp_path, '')
            if raw_imp:
                try:
                    self.vault.write(imp_path, raw_imp)
                except Exception:
                    pass
        missing: list[str] = []
        current_sec = ''
        for ln in raw_imp.splitlines():
            s = ln.strip()
            if s.startswith('## '):
                current_sec = s.lstrip('#').strip()
            elif s.startswith('- ') and ('（待了解）' in s or '(待了解)' in s):
                item_label = s[2:].split('：')[0].split(':')[0].strip()
                missing.append(f'{current_sec} -> {item_label}' if current_sec else item_label)
        return raw_imp, missing

    def build_prompt_context(self) -> str:
        """Dynamic vault index + user traits + structured personal impression digest (budgeted to fit cleanly in ~3.8K chars without truncating disk files)."""
        raw_idx = self.vault.read('INDEX.md')
        idx = raw_idx[:1200] if len(raw_idx) <= 1200 else (raw_idx[:750] + '\n...\n' + raw_idx[-400:])
        raw_traits = self.vault.read('profile/user_traits.md')
        if len(raw_traits) <= 2800:
            traits = raw_traits
        else:
            traits = (
                raw_traits[:1400]
                + '\n...(中间历史画像条目保存在磁盘 memory_vault/profile/user_traits.md 中未删减，可随时 read_file 查阅)...\n'
                + raw_traits[-1400:]
            )
        raw_imp, missing_imp = self.get_personal_impression()
        parts = [
            f'【记忆库目录 INDEX.md（可用 read_file("memory_vault/...")、memory_search 或 conversation_search 翻阅详情；按需技能存放在 memory_vault/skills/）】\n{idx}',
            f'【主人立体个人印象档案 (memory_vault/profile/personal_impression.md)】\n{raw_imp[:1100]}',
        ]
        if missing_imp:
            parts.append(
                '【个人印象待补全维度（记忆运转规则：1. 日常闲聊中主人透露的新信息，系统会在停歇 45 秒后由后台记忆引擎自动归档进 personal_impression.md 与 user_traits.md，无需每句话都调工具；2. 若你本轮未实际调用 edit_file/write_file/memory_pin 工具，严禁在回复中谎称“已经写进档案文件了/已经填好表了”，只能自然说“余记在心里啦”；3. 若主人要求现在写入或你决定当场落盘，必须真实调用一次 edit_file("memory_vault/profile/personal_impression.md", ...) 或 memory_pin 后再说已写入档案）】\n- '
                + '\n- '.join(missing_imp[:6])
            )
        parts.append(f'【用户核心画像日志】\n{traits}')
        return '\n\n'.join(parts)

    def build_lean_context(self) -> str:
        return self.build_prompt_context()

    def get_threshold_tokens(self) -> int:
        raw = getattr(getattr(self.store, 'config', None), 'auto_compact_tokens', self.AUTO_COMPACT_TOKENS)
        return max(8_000, min(1_000_000, int(raw or self.AUTO_COMPACT_TOKENS)))

    def get_context_stats(self, bot_id: str = 'companion', sys_tokens: int = 14800, source: str | None = None) -> dict:
        threshold = self.get_threshold_tokens()
        src = normalize_node_source(source) if source else None
        bot_msgs = [
            m for m in self.store.state.get('messages', [])
            if m.get('bot_id', 'companion') == bot_id
            and (src is None or normalize_node_source(m.get('source')) == src)
        ]
        msg_tokens = sum(self.estimate_tokens(m.get('llm_text') or m.get('text', '')) + 12 for m in bot_msgs)
        if src == 'pc':
            nb_key = f'pc:{bot_id}'
            rep = int(self.last_prompt_tokens.get(nb_key, 0) or 0)
            cached_tokens = int(self.last_cached_tokens.get(nb_key, 0) or 0)
            last_m = (self.store.state.get('last_metrics') or {}).get(nb_key) or {}
        else:
            nb_key = f'phone:{bot_id}'
            rep = int(self.last_prompt_tokens.get(nb_key, self.last_prompt_tokens.get(bot_id, 0)) or 0)
            cached_tokens = int(self.last_cached_tokens.get(nb_key, self.last_cached_tokens.get(bot_id, 0)) or 0)
            st_metrics = self.store.state.get('last_metrics') or {}
            last_m = st_metrics.get(nb_key) or st_metrics.get(bot_id) or {}
        current_tokens = rep if rep > 0 else ((sys_tokens + msg_tokens) if bot_msgs else sys_tokens)
        if cached_tokens > current_tokens:
            cached_tokens = current_tokens
        compacted_blocks = sum(1 for m in bot_msgs if m.get('kind') == 'compact_summary')
        if not last_m:
            # Fallback to the newest assistant message with metrics for this bot
            for m in reversed(bot_msgs):
                if m.get('role') == 'assistant' and isinstance(m.get('metrics'), dict):
                    last_m = m['metrics']
                    break
        return {
            'bot_id': bot_id,
            'current_tokens': current_tokens,
            'cached_tokens': cached_tokens,
            'cache_hit_ratio': round(cached_tokens / max(1, current_tokens), 4) if current_tokens > 0 else 0.0,
            'message_tokens': msg_tokens,
            'reported_tokens': rep,
            'threshold_tokens': threshold,
            'message_count': len(bot_msgs),
            'compacted_count': compacted_blocks,
            'usage_ratio': round(current_tokens / max(1, threshold), 4),
            'ttft_ms': int(last_m.get('ttft_ms') or 0),
            'tps': float(last_m.get('tps') or 0.0),
            'completion_tokens': int(last_m.get('completion_tokens') or 0),
            'total_ms': int(last_m.get('total_ms') or 0),
        }

    def get_all_bots_context_stats(self, sys_tokens: int = 14800, source: str | None = None) -> dict[str, dict]:
        bots = self.store.state.get('assistants') or []
        bot_ids = [b.get('id') for b in bots if isinstance(b, dict) and b.get('id')]
        if 'companion' not in bot_ids:
            bot_ids.insert(0, 'companion')
        return {bid: self.get_context_stats(bid, sys_tokens=sys_tokens, source=source) for bid in bot_ids}

    async def auto_compact_if_needed(
        self,
        bot_id: str,
        sys_tokens: int = 14800,
        silent_complete_fn=None,
        reported_tokens: int | None = None,
        force: bool = False,
        source: str | None = None,
        llm_stream_fn=None,
        on_stream=None,
    ) -> dict | None:
        from core.compaction_service import execute_streaming_compaction
        return await execute_streaming_compaction(
            memory_engine=self,
            bot_id=bot_id,
            source=normalize_node_source(source),
            force=force,
            sys_tokens=sys_tokens,
            reported_tokens=reported_tokens,
            llm_stream_fn=llm_stream_fn,
            silent_complete_fn=silent_complete_fn,
            on_stream=on_stream,
        )

    async def sleep_time_consolidate(self, silent_complete_fn):
        """Silently consolidate recent conversation facts into Zettelkasten atomic notes without touching any UI stream."""
        if self._consolidating:
            return
        s = self.store.state
        last_consolidated = float(s.get('last_sleep_consolidate', 0.0))
        last_user = float(s.get('last_user', 0.0))
        if last_user <= last_consolidated or (time.time() - last_user) < self.SLEEP_CONSOLIDATE_IDLE_SECONDS:
            return
        new_msgs = [
            m for m in s.get('messages', [])
            if float(m.get('created_at', 0)) > last_consolidated
            and m.get('role') in ('user', 'assistant')
            and m.get('kind') not in ('curiosity', 'error', 'compact_summary', 'notification', 'bot_message', 'bot_reply')
            and not m.get('is_sub_turn')
        ]
        if len(new_msgs) < 2:
            return
        self._consolidating = True
        try:
            digest = '\n'.join(f"[{m.get('role')}]: {(m.get('text') or '')[:500]}" for m in new_msgs[-14:])
            existing_traits = self.vault.read('profile/user_traits.md')[-1800:]
            raw_imp, _ = self.get_personal_impression()
            raw_pins = self.vault.read(self.PINNED_FILE)
            prompt = (
                '你是后台静默记忆整理器（Zettelkasten 原子记忆引擎 & 主人个人印象与状态同步器）。\n'
                '请检查以下最近对话是否有值得长期记住的客观事实：\n'
                '1. 【已完成事项与关键状态变更】（例如“腾讯视频自动续订已解除”、“某待办已办结”、“某订阅已退订”等，必须及时修正旧的待办或挂念状态！）；\n'
                '2. 【主人个人背景与偏好】（出生地/家乡/成长、身份/专业/目标、家庭/家人/宠物/关系、生活方式/作息/饮食/精神喜好、性格/价值观/雷区、项目/课程进展）。\n'
                '⚠️ 严禁把日常打情骂俏、亲密互动、角色扮演、荤话玩笑或AI的回复态度记录为“边界/底线/拒绝”规则！若对话仅包含亲密调情、日常催促或无新客观事实的闲聊，严格只输出 `NONE`。\n'
                '如果没有任何新的长期价值客观事实或状态变更，严格只输出 `NONE`。\n'
                '如果有，请严格输出 JSON 对象：{"new_traits": ["一句话新画像事实或已完成状态定论（若无则空数组）"], "completed_or_updated_pins": [{"match": "旧待办或旧状态关键词（如 腾讯视频自动续订）", "replacement": "更新后的状态定论（如 腾讯视频自动续订已解除，无需再提醒退订）"}], "updated_impression_markdown": "如果对话中包含主人个人背景/家庭/出生/身份/偏好或状态变更的新事实，请基于下方【当前个人印象档案】把对应条目更新并输出完整的 Markdown；若无个人印象更新则留空字符串", "note_slug": "英文短文件名如 project-update", "note_title": "卡片标题", "note_markdown": "包含 # 标题、关键事实列表与 [[关联双链]] 的精简原子卡片正文（不超过300字）"}\n\n'
                f'【当前置顶记忆 (profile/pinned_memories.md)】\n{raw_pins[:1600]}\n\n'
                f'【当前个人印象档案 (profile/personal_impression.md)】\n{raw_imp[:2200]}\n\n'
                f'【已有画像尾部（勿重复）】\n{existing_traits}\n\n【近期新增对话】\n{digest}'
            )
            res_msg = await silent_complete_fn([{'role': 'user', 'content': prompt}])
            raw = (res_msg.get('content') or '').strip()
            s['last_sleep_consolidate'] = time.time()
            if not raw or re.fullmatch(r'[`"\'\s]*NONE[.`"\'\s。！]*', raw, flags=re.IGNORECASE):
                self.store.save()
                return
            m_json = re.search(r'\{.*\}', raw, flags=re.DOTALL)
            if not m_json:
                self.store.save()
                return
            try:
                data = json.loads(m_json.group(0))
            except Exception:
                self.store.save()
                return
            changed = False
            delta_parts: list[str] = []
            prev_suppress = self._suppress_vault_listener
            self._suppress_vault_listener = True
            try:
                for tr in (data.get('new_traits') or [])[:4]:
                    if isinstance(tr, str) and tr.strip() and tr.strip() not in existing_traits:
                        clean_tr = tr.strip()
                        self.vault.append('profile/user_traits.md', f'- {now_iso()}（静默整理）{clean_tr}')
                        delta_parts.append(clean_tr)
                        changed = True
                pin_updates = data.get('completed_or_updated_pins') or []
                if isinstance(pin_updates, list) and pin_updates:
                    pins = self.list_pins()
                    pins_modified = False
                    for pu in pin_updates[:4]:
                        if not isinstance(pu, dict):
                            continue
                        match_kw = str(pu.get('match') or pu.get('old_keyword') or '').strip()
                        repl_txt = str(pu.get('replacement') or pu.get('new_text') or '').strip()
                        if not match_kw or not repl_txt:
                            continue
                        matched_any = False
                        for p in pins:
                            if match_kw in str(p.get('text') or ''):
                                p_src = normalize_node_source(p.get('source'))
                                src_tag = '[来源: PC端]' if p_src == 'pc' else ('[来源: 手机端]' if p_src == 'phone' else f'[来源: {node_display_name(p_src)}]')
                                if not repl_txt.startswith('[来源:'):
                                    p['text'] = f"{src_tag} {repl_txt}"[:500]
                                else:
                                    p['text'] = repl_txt[:500]
                                matched_any = True
                                pins_modified = True
                        if not matched_any and match_kw in raw_pins:
                            pins.append({
                                'id': 'pin-' + secrets.token_hex(3),
                                'text': f"[来源: 手机端] {repl_txt}"[:500] if not repl_txt.startswith('[来源:') else repl_txt[:500],
                                'source': 'phone',
                            })
                            pins_modified = True
                        delta_parts.append(f"状态变更确认：{repl_txt}")
                    if pins_modified:
                        self._save_pins(pins[-30:])
                        changed = True
                updated_imp = str(data.get('updated_impression_markdown') or '').strip()
                if updated_imp and '# 主人立体个人印象档案' in updated_imp and len(updated_imp) >= 120:
                    self.vault.write('profile/personal_impression.md', updated_imp + '\n')
                    changed = True
                slug = re.sub(r'[^a-zA-Z0-9_-]', '', str(data.get('note_slug') or ''))[:40].lower()
                md_body = str(data.get('note_markdown') or '').strip()
                if slug and md_body:
                    self.vault.write(f'notes/{slug}.md', md_body + f'\n\n> 更新时间：{now_iso()}\n')
                    changed = True
                if changed:
                    self.vault.reindex()
            finally:
                self._suppress_vault_listener = prev_suppress
            if changed:
                # Note: NEVER overwrite s['cached_vault_context'] here because mutating messages[0] invalidates the 200K Prefix KV Cache!
                if delta_parts:
                    self.record_memory_delta(' ； '.join(delta_parts[:4]), path='memory_vault/profile/user_traits.md', source='sleep_consolidate')
            s['last_sleep_consolidate'] = time.time()
            self.store.save()
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("sleep_time_consolidate error: %s", e)
        finally:
            self._consolidating = False
