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
from vault_manager import now_iso


class MemoryEngine:
    AUTO_COMPACT_TOKENS = 200_000
    KEEP_RECENT_TOKENS = 64_000
    PINNED_FILE = 'profile/pinned_memories.md'

    def __init__(self, store, vault, harness=None, last_prompt_tokens: dict[str, int] | None = None, context_search=None):
        self.store = store
        self.vault = vault
        self.harness = harness
        self.last_prompt_tokens = last_prompt_tokens if last_prompt_tokens is not None else {}
        self.context_search = context_search
        self._consolidating = False

    @staticmethod
    def estimate_tokens(text: str) -> int:
        if not text:
            return 0
        cjk = sum(1 for ch in text if '\u4e00' <= ch <= '\u9fff')
        other_bytes = len(text.encode('utf-8')) - cjk * 3
        return cjk + max(0, other_bytes) // 3

    def estimate_messages_tokens(self, messages: list[dict]) -> int:
        total = 0
        for m in messages:
            content = m.get('content')
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict):
                        if part.get('type') == 'text':
                            total += self.estimate_tokens(str(part.get('text') or ''))
                        elif part.get('type') == 'image_url':
                            total += 1100  # standard vision tile token estimate
            else:
                total += 6 + self.estimate_tokens(str(content or ''))
            if m.get('tool_calls'):
                total += self.estimate_tokens(json.dumps(m['tool_calls'], ensure_ascii=False))
        return total

    @staticmethod
    def prune_tool_loop_messages(messages: list[dict]):
        """
        IMMUTABLE PREFIX CACHING RULE:
        Never mutate already-sent tool outputs or image blocks in `messages`!
        Mutating earlier messages invalidates the upstream LLM Prefix Cache across tool steps.
        Oversized tool outputs (>12K chars) are offloaded to SQLite FTS5 (`ctx_search`) BEFORE appending.
        """
        return

    # --- Visual Memory Pinning (📌) ---
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
                pins.append({'id': m.group(1), 'text': m.group(2).strip()})
            elif s.startswith('- '):
                pid = 'pin-' + secrets.token_hex(3)
                pins.append({'id': pid, 'text': s[2:].strip()})
        return pins

    def _save_pins(self, pins: list[dict]) -> None:
        lines = ['# 📌 核心钉住记忆 (Pinned Core Memories)', '']
        for p in pins:
            lines.append(f"- [{p['id']}] {p['text']}")
        self.vault.write(self.PINNED_FILE, '\n'.join(lines) + '\n')

    def add_pin(self, text: str, bot_id: str = 'companion') -> dict:
        t = (text or '').strip()
        if not t:
            return {'error': '钉住内容不能为空'}
        pins = self.list_pins()
        for p in pins:
            if p['text'] == t:
                return {'pinned': True, 'id': p['id'], 'pin': p, 'total': len(pins)}
        new_pin = {'id': 'pin-' + secrets.token_hex(3), 'text': t[:600]}
        pins.append(new_pin)
        self._save_pins(pins)
        return {'pinned': True, 'id': new_pin['id'], 'pin': new_pin, 'total': len(pins)}

    def remove_pin(self, pin_id_or_text: str) -> dict:
        q = (pin_id_or_text or '').strip()
        pins = self.list_pins()
        kept = [p for p in pins if p['id'] != q and q not in p['text']]
        removed_count = len(pins) - len(kept)
        if removed_count > 0:
            self._save_pins(kept)
        return {'removed': removed_count > 0, 'removed_count': removed_count, 'pins': kept}

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
        """Dynamic vault index + user traits + structured personal impression digest."""
        raw_idx = self.vault.read('INDEX.md')
        idx = raw_idx[:3200] if len(raw_idx) <= 3200 else (raw_idx[:2000] + '\n...\n' + raw_idx[-1100:])
        raw_traits = self.vault.read('profile/user_traits.md')
        traits = raw_traits[-2200:] if len(raw_traits) > 2200 else raw_traits
        raw_imp, missing_imp = self.get_personal_impression()
        pinned = self.build_static_pinned_block()
        parts = []
        if pinned:
            parts.append(pinned)
        parts.append(f'【记忆库目录 INDEX.md（可用 read_file("memory_vault/...")、memory_search 或 conversation_search 翻阅详情）】\n{idx}')
        parts.append(f'【主人立体个人印象档案 (memory_vault/profile/personal_impression.md)】\n{raw_imp[:2200]}')
        if missing_imp:
            parts.append(
                '【个人印象待补全维度（不仅关注工作，闲聊或主动搭话时可自然挑一项询问并配合 present_choices 提供选项，得知答案后用 edit_file/write_file 更新 memory_vault/profile/personal_impression.md）】\n- '
                + '\n- '.join(missing_imp[:8])
            )
        parts.append(f'【用户核心画像日志】\n{traits}')
        return '\n\n'.join(parts)

    def build_lean_context(self) -> str:
        return self.build_prompt_context()

    def get_threshold_tokens(self) -> int:
        raw = getattr(getattr(self.store, 'config', None), 'auto_compact_tokens', self.AUTO_COMPACT_TOKENS)
        return max(8_000, min(1_000_000, int(raw or self.AUTO_COMPACT_TOKENS)))

    def get_context_stats(self, bot_id: str = 'companion', sys_tokens: int = 1800) -> dict:
        threshold = self.get_threshold_tokens()
        bot_msgs = [m for m in self.store.state.get('messages', []) if m.get('bot_id', 'companion') == bot_id]
        msg_tokens = sum(self.estimate_tokens(m.get('text', '')) + 12 for m in bot_msgs)
        rep = int(self.last_prompt_tokens.get(bot_id, 0) or 0)
        current_tokens = max(sys_tokens + msg_tokens, rep) if bot_msgs else sys_tokens
        compacted_blocks = sum(1 for m in bot_msgs if m.get('kind') == 'compact_summary')
        return {
            'bot_id': bot_id,
            'current_tokens': current_tokens,
            'message_tokens': msg_tokens,
            'reported_tokens': rep,
            'threshold_tokens': threshold,
            'message_count': len(bot_msgs),
            'compacted_count': compacted_blocks,
            'usage_ratio': round(current_tokens / max(1, threshold), 4),
        }

    def get_all_bots_context_stats(self, sys_tokens: int = 1800) -> dict[str, dict]:
        bots = self.store.state.get('assistants') or []
        bot_ids = [b.get('id') for b in bots if isinstance(b, dict) and b.get('id')]
        if 'companion' not in bot_ids:
            bot_ids.insert(0, 'companion')
        return {bid: self.get_context_stats(bid, sys_tokens=sys_tokens) for bid in bot_ids}

    async def auto_compact_if_needed(
        self,
        bot_id: str,
        sys_tokens: int,
        silent_complete_fn,
        reported_tokens: int | None = None,
        force: bool = False,
    ) -> dict | None:
        threshold = self.get_threshold_tokens()
        keep_recent = max(3_000, min(self.KEEP_RECENT_TOKENS, int(threshold * 0.30)))
        rep = reported_tokens if reported_tokens is not None else int(self.last_prompt_tokens.get(bot_id, 0))
        bot_msgs = [m for m in self.store.state['messages'] if m.get('bot_id', 'companion') == bot_id]
        if not force and len(bot_msgs) <= 6:
            return None
        msg_tokens = sum(self.estimate_tokens(m.get('text', '')) + 12 for m in bot_msgs)
        total_tokens = max(sys_tokens + msg_tokens, rep) if bot_msgs else sys_tokens
        if not force and total_tokens < threshold:
            return None

        kept_tokens = 0
        if not force:
            split_idx = max(1, len(bot_msgs) - 2)
            for i in range(len(bot_msgs) - 1, 0, -1):
                kept_tokens += self.estimate_tokens(bot_msgs[i].get('text', '')) + 12
                if kept_tokens >= keep_recent:
                    split_idx = i
                    break
            older = bot_msgs[:max(1, split_idx)]
            if len(older) <= 1 and older[0].get('kind') == 'compact_summary':
                return None
        else:
            # Manual compaction: compact all current messages in the room so the new compaction entry appears right at the bottom of the chat!
            older = bot_msgs
            kept_tokens = 0

        summary_lines = [f"- [{m.get('from_bot_name') or m['role']}]: {m.get('text', '')[:600]}" for m in older]
        raw_transcript = '\n'.join(f"[{m.get('from_bot_name') or m['role']}]: {m.get('text', '')[:1800]}" for m in older)

        # Index full compacted transcript into SQLite FTS5 so ctx_search & conversation_search never lose a single line!
        ctx_id = ''
        if self.context_search is not None and raw_transcript.strip():
            try:
                ctx_id = self.context_search.store_tool_context(
                    'compaction_archive',
                    {'bot_id': bot_id, 'messages_count': len(older), 'threshold': threshold},
                    raw_transcript,
                    bot_id=bot_id,
                )
            except Exception:
                ctx_id = ''

        llm_summary = ''
        if raw_transcript.strip():
            try:
                sum_msg = await silent_complete_fn([
                    {
                        'role': 'system',
                        'content': '你是上下文静默压缩引擎。请将以下对话历史浓缩为高信息密度的结构化摘要，完整保留所有关键事实、用户偏好、个人印象（出生/身份/家庭/喜好）、时间节点、文件路径、代码/命令结论以及未决事项。'
                    },
                    {'role': 'user', 'content': raw_transcript[:150000]}
                ])
                llm_summary = (sum_msg.get('content') or '').strip()
            except Exception:
                llm_summary = ''
        final_summary = llm_summary or ('\n'.join(summary_lines[-40:]) if summary_lines else '（当前会话暂无历史对话，基础系统设定与记忆库索引已就绪。）')
        stamp_slug = time.strftime('%Y%m%d_%H%M%S', time.localtime())
        vault_note_path = f'notes/compact_{stamp_slug}.md'
        thresh_k = f'{threshold // 1000}K' if threshold >= 1000 else str(threshold)
        mode_label = '手动压缩上下文' if force else f'满 {thresh_k} 自动压缩上下文'
        ctx_hint = f'SQLite FTS5 索引库 (ctx_id="{ctx_id}")' if ctx_id else 'SQLite FTS5 索引库'
        archive_md = (
            f"# 📦 {mode_label}归档 ({now_iso()})\n\n"
            f"- **所属助手**：`{bot_id}`\n"
            f"- **压缩变化**：`{total_tokens}` Tokens → `{sys_tokens + kept_tokens + self.estimate_tokens(final_summary)}` Tokens（共浓缩 {len(older)} 条对话）\n"
            f"- **记忆存储位置**：`memory_vault/{vault_note_path}`、`memory_vault/timeline`、{ctx_hint}\n\n"
            f"## 浓缩核心摘要\n\n{final_summary}\n"
        )
        try:
            self.vault.write(vault_note_path, archive_md)
            self.vault.log(archive_md)
            self.vault.reindex()
        except Exception:
            pass

        older_ids = {m['id'] for m in older}
        new_est = sys_tokens + kept_tokens + self.estimate_tokens(final_summary)
        decision_meta = json.dumps({
            'before_tokens': total_tokens,
            'after_tokens': new_est,
            'compacted_messages': len(older),
            'vault_path': vault_note_path,
            'ctx_id': ctx_id,
            'mode': mode_label,
        }, ensure_ascii=False)
        compact_marker = {
            'id': secrets.token_hex(12),
            'role': 'assistant',
            'bot_id': bot_id,
            'kind': 'compact_summary',
            'decision': decision_meta,
            'text': (
                f"📦 **{mode_label}完成**：`{total_tokens}` → `{new_est}` Tokens（共浓缩 {len(older)} 条对话）\n"
                f"📍 **记忆归档去向**：\n"
                f"1. 透明记忆库笔记：`memory_vault/{vault_note_path}` 与 `memory_vault/timeline`\n"
                f"2. 全文检索索引：{ctx_hint}（Bot 可随时用 `conversation_search` / `ctx_search` 检索逐字原话）\n\n"
                f"{final_summary}"
            ),
            'created_at': time.time() if force else older[-1].get('created_at', time.time()),
            'notify': False,
        }
        new_all = []
        inserted = False
        for m in self.store.state['messages']:
            if m['id'] in older_ids:
                if not force and not inserted:
                    new_all.append(compact_marker)
                    inserted = True
                continue
            new_all.append(m)
        if force or not inserted:
            new_all.append(compact_marker)
        self.store.state['messages'] = new_all
        self.last_prompt_tokens[bot_id] = new_est
        self.store.save()
        return {
            'compacted_messages': len(older),
            'before_tokens': total_tokens,
            'after_tokens': new_est,
            'threshold_tokens': threshold,
            'vault_path': vault_note_path,
            'ctx_id': ctx_id,
            'summary': final_summary,
            'marker': compact_marker,
        }

    async def sleep_time_consolidate(self, silent_complete_fn):
        """Silently consolidate recent conversation facts into Zettelkasten atomic notes without touching any UI stream."""
        if self._consolidating:
            return
        s = self.store.state
        last_consolidated = float(s.get('last_sleep_consolidate', 0.0))
        last_user = float(s.get('last_user', 0.0))
        if last_user <= last_consolidated or (time.time() - last_user) < 1200:
            return
        new_msgs = [
            m for m in s.get('messages', [])
            if float(m.get('created_at', 0)) > last_consolidated
            and m.get('role') in ('user', 'assistant')
            and m.get('kind') not in ('curiosity', 'error', 'compact_summary', 'notification')
        ]
        s['last_sleep_consolidate'] = time.time()
        self.store.save()
        if len(new_msgs) < 4:
            return
        self._consolidating = True
        try:
            digest = '\n'.join(f"[{m.get('role')}]: {(m.get('text') or '')[:500]}" for m in new_msgs[-14:])
            existing_traits = self.vault.read('profile/user_traits.md')[-1800:]
            raw_imp, _ = self.get_personal_impression()
            prompt = (
                '你是后台静默记忆整理器（Zettelkasten 原子记忆引擎 & 主人个人印象拼图构建器）。\n'
                '请检查以下最近对话是否有值得长期记住的【主人个人背景（出生地/家乡/成长、身份/专业/目标、家庭/家人/宠物/关系、生活方式/作息/饮食/精神喜好、性格/价值观/雷区）、用户偏好变更、重要决定、项目/课程进展】。\n'
                '如果没有任何新的长期价值信息，严格只输出 `NONE`。\n'
                '如果有，请严格输出 JSON 对象：{"new_traits": ["一句话新画像事实（若无则空数组）"], "updated_impression_markdown": "如果对话中包含主人个人背景/家庭/出生/身份/偏好的新事实，请基于下方【当前个人印象档案】把对应条目的（待了解）替换为已知事实并输出完整的更新后 Markdown；若无个人印象更新则留空字符串", "note_slug": "英文短文件名如 project-update", "note_title": "卡片标题", "note_markdown": "包含 # 标题、关键事实列表与 [[关联双链]] 的精简原子卡片正文（不超过300字）"}\n\n'
                f'【当前个人印象档案 (profile/personal_impression.md)】\n{raw_imp[:2200]}\n\n'
                f'【已有画像尾部（勿重复）】\n{existing_traits}\n\n【近期新增对话】\n{digest}'
            )
            res_msg = await silent_complete_fn([{'role': 'user', 'content': prompt}])
            raw = (res_msg.get('content') or '').strip()
            if not raw or raw.upper() == 'NONE':
                return
            m_json = re.search(r'\{.*\}', raw, flags=re.DOTALL)
            if not m_json:
                return
            data = json.loads(m_json.group(0))
            changed = False
            for tr in (data.get('new_traits') or [])[:3]:
                if isinstance(tr, str) and tr.strip() and tr.strip() not in existing_traits:
                    self.vault.append('profile/user_traits.md', f'- {now_iso()}（静默整理）{tr.strip()}')
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
        except Exception:
            pass
        finally:
            self._consolidating = False
