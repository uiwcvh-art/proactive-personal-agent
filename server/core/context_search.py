"""SQLite FTS5 Context & Recall Search Engine (Letta Recall + mksglu/context-mode Architecture).

Provides:
1. `conversation_search`: Cross-room & in-room historical message + tool-trace search (FTS5 BM25 + CJK hybrid).
2. `ctx_search` & `maybe_offload_tool_output`: Automatically indexes bulky tool outputs (>12K chars) and 200K compaction
   archives into SQLite FTS5 chunks so the active 200K context window stays high-signal and 100% prefix-cacheable.
3. `blackboard`: Persistent multi-agent shared key-value blackboard for zero-truncation data handoff between agents.
"""
from __future__ import annotations

import json
import re
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


class ContextSearchEngine:
    OFFLOAD_THRESHOLD_CHARS = 4_800
    CHUNK_SIZE_CHARS = 1_200
    MAX_TRANSIENT_TOOL_CONTEXTS = 50
    TRANSIENT_TOOL_TTL_SECONDS = 48 * 3600
    MAX_ARCHIVE_CONTEXTS = 100
    ARCHIVE_TTL_SECONDS = 90 * 86400
    MAX_MESSAGES_STORE = 1500
    BLACKBOARD_TTL_SECONDS = 14 * 86400

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._indexed_msg_ids: set[str] = set()
        self._fts5_available = False
        self._write_ops_since_prune = 0
        self._last_prune_ts = 0.0
        self._init_db()
        try:
            self.prune_and_optimize(force_vacuum=False)
        except Exception:
            pass

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA journal_mode=WAL;')
        conn.execute('PRAGMA synchronous=NORMAL;')
        return conn

    def _init_db(self) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS messages_store (
                        msg_id TEXT PRIMARY KEY,
                        bot_id TEXT NOT NULL,
                        speaker TEXT NOT NULL,
                        role TEXT NOT NULL,
                        kind TEXT NOT NULL,
                        text TEXT NOT NULL,
                        trace_text TEXT NOT NULL,
                        created_at REAL NOT NULL
                    )
                    """
                )
                conn.execute('CREATE INDEX IF NOT EXISTS idx_messages_bot_time ON messages_store(bot_id, created_at DESC)')
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS tool_contexts (
                        ctx_id TEXT PRIMARY KEY,
                        bot_id TEXT NOT NULL,
                        tool_name TEXT NOT NULL,
                        args_json TEXT NOT NULL,
                        full_text TEXT NOT NULL,
                        total_chars INTEGER NOT NULL,
                        created_at REAL NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS tool_chunks (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        ctx_id TEXT NOT NULL,
                        chunk_idx INTEGER NOT NULL,
                        tool_name TEXT NOT NULL,
                        header TEXT NOT NULL,
                        content TEXT NOT NULL,
                        created_at REAL NOT NULL
                    )
                    """
                )
                conn.execute('CREATE INDEX IF NOT EXISTS idx_chunks_ctx ON tool_chunks(ctx_id, chunk_idx)')
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS blackboard (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL,
                        author_bot TEXT NOT NULL,
                        summary TEXT NOT NULL,
                        updated_at REAL NOT NULL
                    )
                    """
                )
                try:
                    conn.execute(
                        """
                        CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
                            msg_id UNINDEXED,
                            bot_id,
                            speaker,
                            text,
                            trace_text,
                            tokenize='unicode61'
                        )
                        """
                    )
                    conn.execute(
                        """
                        CREATE VIRTUAL TABLE IF NOT EXISTS tool_chunks_fts USING fts5(
                            ctx_id UNINDEXED,
                            chunk_idx UNINDEXED,
                            tool_name,
                            header,
                            content,
                            tokenize='unicode61'
                        )
                        """
                    )
                    self._fts5_available = True
                except sqlite3.OperationalError:
                    self._fts5_available = False

                cur = conn.execute('SELECT msg_id FROM messages_store')
                self._indexed_msg_ids = {str(r['msg_id']) for r in cur.fetchall()}
                conn.commit()
            finally:
                conn.close()

    @staticmethod
    def _summarize_trace(trace: list[dict] | None) -> str:
        if not trace or not isinstance(trace, list):
            return ''
        parts: list[str] = []
        for step in trace[:16]:
            if not isinstance(step, dict):
                continue
            t_name = str(step.get('tool') or '')
            args = step.get('arguments') or {}
            res = step.get('result')
            args_s = json.dumps(args, ensure_ascii=False)[:240]
            res_s = json.dumps(res, ensure_ascii=False)[:600] if res is not None else ''
            parts.append(f"[{t_name}({args_s})] -> {res_s}")
        return '\n'.join(parts)

    def sync_store_messages(self, messages: list[dict]) -> int:
        new_items = [m for m in messages if isinstance(m, dict) and m.get('id') and str(m['id']) not in self._indexed_msg_ids]
        if not new_items:
            return 0
        with self._lock:
            conn = self._connect()
            try:
                for m in new_items:
                    mid = str(m['id'])
                    bot_id = str(m.get('bot_id') or 'companion')
                    speaker = str(m.get('from_bot_name') or ('主人' if m.get('role') == 'user' else bot_id))
                    role = str(m.get('role') or 'assistant')
                    kind = str(m.get('kind') or 'chat')
                    text = str(m.get('text') or '')
                    trace_text = self._summarize_trace(m.get('trace'))
                    created_at = float(m.get('created_at') or time.time())
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO messages_store(msg_id, bot_id, speaker, role, kind, text, trace_text, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (mid, bot_id, speaker, role, kind, text, trace_text, created_at),
                    )
                    if self._fts5_available:
                        conn.execute('DELETE FROM messages_fts WHERE msg_id = ?', (mid,))
                        conn.execute(
                            'INSERT INTO messages_fts(msg_id, bot_id, speaker, text, trace_text) VALUES (?, ?, ?, ?, ?)',
                            (mid, bot_id, speaker, text, trace_text),
                        )
                    self._indexed_msg_ids.add(mid)
                conn.commit()
                return len(new_items)
            finally:
                conn.close()

    def index_message(self, m: dict) -> None:
        if not isinstance(m, dict) or not m.get('id'):
            return
        self.sync_store_messages([m])

    @staticmethod
    def _extract_query_terms(query: str) -> list[str]:
        q = (query or '').strip()
        if not q:
            return []
        terms = [t.strip() for t in re.split(r'[\s,，。；;、|？！?!：:]+', q) if t.strip()]
        # Split mixed CJK / English tokens and extract CJK sliding bigrams/trigrams for high Chinese recall
        extra: list[str] = []
        stop_chars = set('的了是在有和与或把被让给向从到对为着过吗呢吧啊呀哦哈嘛什么怎么哪里哪个多少')
        for t in terms:
            for sub in re.findall(r'[a-zA-Z0-9_.-]{2,}|[\u4e00-\u9fff]{2,8}', t):
                if sub not in terms and sub not in extra:
                    extra.append(sub)
                if '\u4e00' <= sub[0] <= '\u9fff' and len(sub) > 2:
                    cleaned_cjk = ''.join(ch for ch in sub if ch not in stop_chars)
                    if len(cleaned_cjk) >= 2 and cleaned_cjk not in terms and cleaned_cjk not in extra:
                        extra.append(cleaned_cjk)
                    for i in range(len(sub) - 1):
                        bg = sub[i:i + 2]
                        if not any(ch in stop_chars for ch in bg) and bg not in terms and bg not in extra:
                            extra.append(bg)
        return (terms + extra)[:12]

    def conversation_search(
        self,
        query: str,
        bot_id: str = 'all',
        limit: int = 10,
        exclude_msg_ids: set[str] | None = None,
        vault=None,
    ) -> dict:
        """
        Searches historical/archived conversations (pre-200K compacted messages, past summaries,
        memory_vault timeline/notes, and cross-room bot records).
        Automatically excludes messages already in the current bot's active 200K context (`exclude_msg_ids`)
        unless searching other bots' rooms or `compact_summary` items.
        """
        terms = self._extract_query_terms(query)
        limit = max(1, min(30, int(limit or 10)))
        bot_filter = (bot_id or 'all').strip()
        excl = exclude_msg_ids or set()
        scored: dict[str, tuple[float, dict]] = {}

        with self._lock:
            conn = self._connect()
            try:
                if not terms:
                    sql = 'SELECT * FROM messages_store'
                    params: list[Any] = []
                    if bot_filter and bot_filter != 'all':
                        sql += ' WHERE bot_id = ?'
                        params.append(bot_filter)
                    sql += ' ORDER BY created_at DESC LIMIT ?'
                    params.append(limit * 3)
                    rows = [
                        r for r in conn.execute(sql, params).fetchall()
                        if str(r['msg_id']) not in excl or str(r['kind']) == 'compact_summary'
                    ][:limit]
                    return {
                        'query': query,
                        'bot_id': bot_filter,
                        'count': len(rows),
                        'results': [self._format_msg_row(r) for r in rows],
                    }

                # 1. FTS5 search if available
                if self._fts5_available:
                    safe_fts_terms = [re.sub(r'["\'()*^:\-+]', ' ', t).strip() for t in terms if re.sub(r'["\'()*^:\-+]', ' ', t).strip()]
                    if safe_fts_terms:
                        fts_expr = ' OR '.join(f'"{t}"' for t in safe_fts_terms[:6])
                        try:
                            sql = """
                                SELECT m.*, bm25(messages_fts) AS rank_score
                                FROM messages_fts f
                                JOIN messages_store m ON m.msg_id = f.msg_id
                                WHERE messages_fts MATCH ?
                            """
                            params = [fts_expr]
                            if bot_filter and bot_filter != 'all':
                                sql += ' AND m.bot_id = ?'
                                params.append(bot_filter)
                            sql += ' ORDER BY rank_score ASC, m.created_at DESC LIMIT ?'
                            params.append(limit * 4)
                            for r in conn.execute(sql, params).fetchall():
                                mid = str(r['msg_id'])
                                if mid in excl and str(r['kind']) != 'compact_summary':
                                    continue
                                base_score = 10.0 - float(r['rank_score'] or 0.0)
                                if str(r['kind']) == 'compact_summary':
                                    base_score += 5.0
                                scored[mid] = (base_score, dict(r))
                        except sqlite3.Error:
                            pass

                # 2. CJK / substring hybrid scan over archived & historical messages
                like_clauses = []
                like_params: list[Any] = []
                for t in terms[:6]:
                    like_clauses.append('(text LIKE ? OR trace_text LIKE ? OR speaker LIKE ?)')
                    pat = f'%{t}%'
                    like_params.extend([pat, pat, pat])
                where_sql = ' OR '.join(like_clauses)
                sql = f'SELECT * FROM messages_store WHERE ({where_sql})'
                if bot_filter and bot_filter != 'all':
                    sql = f'SELECT * FROM messages_store WHERE bot_id = ? AND ({where_sql})'
                    like_params = [bot_filter] + like_params
                sql += ' ORDER BY created_at DESC LIMIT ?'
                like_params.append(limit * 5)
                for r in conn.execute(sql, like_params).fetchall():
                    mid = str(r['msg_id'])
                    if mid in excl and str(r['kind']) != 'compact_summary':
                        continue
                    row_d = dict(r)
                    hay = (row_d.get('text', '') + '\n' + row_d.get('trace_text', '')).lower()
                    hit_score = sum(4.0 if t.lower() in hay else 0.0 for t in terms)
                    if query.strip().lower() in hay:
                        hit_score += 8.0
                    if str(row_d.get('kind')) == 'compact_summary':
                        hit_score += 5.0
                    prev_score = scored[mid][0] if mid in scored else 0.0
                    scored[mid] = (prev_score + hit_score, row_d)
            finally:
                conn.close()

        ranked = sorted(scored.values(), key=lambda x: (x[0], x[1].get('created_at', 0.0)), reverse=True)[:limit]
        results = [self._format_msg_row(item[1], score=round(item[0], 2)) for item in ranked]

        # 3. Also search archived memory_vault summaries/notes if vault is provided
        vault_hits = []
        if vault is not None and query.strip():
            try:
                v_matches = vault.search(query.strip(), limit=6)
                for vm in (v_matches or []):
                    vault_hits.append(vm)
            except Exception:
                pass

        return {
            'query': query,
            'bot_id': bot_filter,
            'count': len(results),
            'results': results,
            'archived_vault_summaries': vault_hits,
            'note': '已自动过滤当前 200K 活跃窗口内已知对话，重点返回 200K 以前归档总结、早期历史对话与跨 Bot 记录。',
        }

    @staticmethod
    def _format_msg_row(r: dict | sqlite3.Row, score: float | None = None) -> dict:
        d = dict(r)
        ts = float(d.get('created_at') or 0)
        time_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(ts)) if ts > 0 else ''
        text = str(d.get('text') or '')
        trace_t = str(d.get('trace_text') or '')
        out = {
            'msg_id': d.get('msg_id'),
            'bot_id': d.get('bot_id'),
            'speaker': d.get('speaker'),
            'role': d.get('role'),
            'time': time_str,
            'text': text[:1200] + (f'...(共{len(text)}字)' if len(text) > 1200 else ''),
        }
        if trace_t:
            out['tool_trace_excerpt'] = trace_t[:600]
        if score is not None:
            out['score'] = score
        return out

    def store_tool_context(self, tool_name: str, args: dict, full_text: str, bot_id: str = 'companion', custom_ctx_id: str | None = None) -> str:
        ctx_id = custom_ctx_id or ('ctx-' + secrets.token_hex(4))
        now = time.time()
        args_json = json.dumps(args or {}, ensure_ascii=False)[:1000]
        # Store only a short preview in tool_contexts.full_text because full verbatim lines are stored in tool_chunks.
        preview_text = full_text[:1200]
        chunks: list[tuple[int, str, str]] = []
        lines = full_text.splitlines()
        cur_lines: list[str] = []
        cur_len = 0
        start_line = 1
        chunk_idx = 0

        for idx, ln in enumerate(lines, start=1):
            cur_lines.append(ln)
            cur_len += len(ln) + 1
            if cur_len >= self.CHUNK_SIZE_CHARS:
                header = f'{tool_name} lines {start_line}-{idx}'
                chunks.append((chunk_idx, header, '\n'.join(cur_lines)))
                chunk_idx += 1
                cur_lines = []
                cur_len = 0
                start_line = idx + 1
        if cur_lines or not chunks:
            header = f'{tool_name} lines {start_line}-{len(lines)}'
            chunks.append((chunk_idx, header, '\n'.join(cur_lines) if cur_lines else full_text[:self.CHUNK_SIZE_CHARS]))

        with self._lock:
            conn = self._connect()
            try:
                # Deduplicate identical transient tool invocations so repeated read_file / ui_tree calls don't pile up
                if not custom_ctx_id and tool_name != 'compaction_archive':
                    dup_rows = conn.execute(
                        'SELECT ctx_id FROM tool_contexts WHERE bot_id = ? AND tool_name = ? AND args_json = ?',
                        (bot_id, tool_name, args_json),
                    ).fetchall()
                    for dr in dup_rows:
                        old_cid = str(dr['ctx_id'])
                        conn.execute('DELETE FROM tool_contexts WHERE ctx_id = ?', (old_cid,))
                        conn.execute('DELETE FROM tool_chunks WHERE ctx_id = ?', (old_cid,))
                        if self._fts5_available:
                            conn.execute('DELETE FROM tool_chunks_fts WHERE ctx_id = ?', (old_cid,))

                conn.execute(
                    """
                    INSERT OR REPLACE INTO tool_contexts(ctx_id, bot_id, tool_name, args_json, full_text, total_chars, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (ctx_id, bot_id, tool_name, args_json, preview_text, len(full_text), now),
                )
                conn.execute('DELETE FROM tool_chunks WHERE ctx_id = ?', (ctx_id,))
                if self._fts5_available:
                    conn.execute('DELETE FROM tool_chunks_fts WHERE ctx_id = ?', (ctx_id,))
                for c_idx, hdr, body in chunks:
                    conn.execute(
                        'INSERT INTO tool_chunks(ctx_id, chunk_idx, tool_name, header, content, created_at) VALUES (?, ?, ?, ?, ?, ?)',
                        (ctx_id, c_idx, tool_name, hdr, body, now),
                    )
                    if self._fts5_available:
                        conn.execute(
                            'INSERT INTO tool_chunks_fts(ctx_id, chunk_idx, tool_name, header, content) VALUES (?, ?, ?, ?, ?)',
                            (ctx_id, str(c_idx), tool_name, hdr, body),
                        )
                conn.commit()
                self._write_ops_since_prune += 1
            finally:
                conn.close()

        if self._write_ops_since_prune >= 25 or (now - self._last_prune_ts) >= 1800:
            try:
                self.prune_and_optimize(force_vacuum=False)
            except Exception:
                pass
        return ctx_id

    def maybe_offload_tool_output(self, tool_name: str, args: dict, result: Any, bot_id: str = 'companion', turn_id: str = '', **kwargs) -> str:
        """
        Implements mksglu/context-mode offloading:
        If a tool result serialized to JSON/text exceeds OFFLOAD_THRESHOLD_CHARS (12,000 chars),
        stores the full verbatim output into SQLite FTS5 and returns a high-density digest + `ctx_id` as a JSON string.
        Always returns a `str` so OpenAI-compatible `role: "tool"` messages never fail schema validation!
        """
        try:
            serialized = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
        except Exception:
            serialized = str(result)
        if tool_name in ('conversation_search', 'ctx_search') or len(serialized) <= self.OFFLOAD_THRESHOLD_CHARS:
            return serialized

        ctx_id = self.store_tool_context(tool_name, args, serialized, bot_id=bot_id)
        key_lines: list[str] = []
        for ln in serialized.splitlines():
            s_ln = ln.strip()
            if s_ln and re.search(r'(?:error|exception|traceback|failed|warning|http|title|summary|结论|报错|失败|成功)', s_ln, re.IGNORECASE):
                if len(s_ln) <= 300:
                    key_lines.append(s_ln)
                if len(key_lines) >= 15:
                    break
        digest = {
            'ctx_offloaded': True,
            'ctx_id': ctx_id,
            'tool': tool_name,
            'total_chars': len(serialized),
            'head_preview': serialized[:1800],
            'key_highlights': key_lines[:15],
            'tail_preview': serialized[-900:],
            'ctx_search_hint': f'该工具完整输出共 {len(serialized)} 字符，已自动存入本地 FTS5 索引库 (ctx_id="{ctx_id}")。如需查阅中间任意细节，请直接调用 ctx_search(query="关键词", ctx_id="{ctx_id}")。',
        }
        return json.dumps(digest, ensure_ascii=False)

    def ctx_search(self, query: str, ctx_id: str = '', limit: int = 8) -> dict:
        terms = self._extract_query_terms(query)
        limit = max(1, min(20, int(limit or 8)))
        cid = (ctx_id or '').strip()
        scored: dict[tuple[str, int], tuple[float, dict]] = {}

        with self._lock:
            conn = self._connect()
            try:
                if not terms:
                    sql = 'SELECT * FROM tool_chunks'
                    params: list[Any] = []
                    if cid:
                        sql += ' WHERE ctx_id = ?'
                        params.append(cid)
                    sql += ' ORDER BY created_at DESC, chunk_idx ASC LIMIT ?'
                    params.append(limit)
                    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
                    return {'query': query, 'ctx_id': cid, 'count': len(rows), 'chunks': rows}

                if self._fts5_available:
                    safe_fts = [re.sub(r'["\'()*^:\-+]', ' ', t).strip() for t in terms if re.sub(r'["\'()*^:\-+]', ' ', t).strip()]
                    if safe_fts:
                        fts_expr = ' OR '.join(f'"{t}"' for t in safe_fts[:6])
                        try:
                            sql = """
                                SELECT c.*, bm25(tool_chunks_fts) AS rank_score
                                FROM tool_chunks_fts f
                                JOIN tool_chunks c ON c.ctx_id = f.ctx_id AND c.chunk_idx = CAST(f.chunk_idx AS INTEGER)
                                WHERE tool_chunks_fts MATCH ?
                            """
                            params = [fts_expr]
                            if cid:
                                sql += ' AND c.ctx_id = ?'
                                params.append(cid)
                            sql += ' ORDER BY rank_score ASC LIMIT ?'
                            params.append(limit * 2)
                            for r in conn.execute(sql, params).fetchall():
                                key = (str(r['ctx_id']), int(r['chunk_idx']))
                                scored[key] = (10.0 - float(r['rank_score'] or 0.0), dict(r))
                        except sqlite3.Error:
                            pass

                like_clauses = []
                like_params: list[Any] = []
                for t in terms[:6]:
                    like_clauses.append('(content LIKE ? OR header LIKE ?)')
                    pat = f'%{t}%'
                    like_params.extend([pat, pat])
                where_sql = ' OR '.join(like_clauses)
                sql = f'SELECT * FROM tool_chunks WHERE ({where_sql})'
                if cid:
                    sql = f'SELECT * FROM tool_chunks WHERE ctx_id = ? AND ({where_sql})'
                    like_params = [cid] + like_params
                sql += ' ORDER BY created_at DESC, chunk_idx ASC LIMIT ?'
                like_params.append(limit * 3)
                for r in conn.execute(sql, like_params).fetchall():
                    row_d = dict(r)
                    key = (str(row_d['ctx_id']), int(row_d['chunk_idx']))
                    hay = (row_d.get('content', '') + '\n' + row_d.get('header', '')).lower()
                    hit_score = sum(4.0 if t.lower() in hay else 0.0 for t in terms)
                    if query.strip().lower() in hay:
                        hit_score += 8.0
                    prev_score = scored[key][0] if key in scored else 0.0
                    scored[key] = (prev_score + hit_score, row_d)
            finally:
                conn.close()

        ranked = sorted(scored.values(), key=lambda x: x[0], reverse=True)[:limit]
        return {
            'query': query,
            'ctx_id': cid or 'all',
            'count': len(ranked),
            'chunks': [
                {
                    'ctx_id': item[1]['ctx_id'],
                    'chunk_idx': item[1]['chunk_idx'],
                    'tool_name': item[1]['tool_name'],
                    'header': item[1]['header'],
                    'content': item[1]['content'],
                    'score': round(item[0], 2),
                }
                for item in ranked
            ],
        }

    # --- Shared Multi-Agent Blackboard ---
    def blackboard_write(self, key: str, value: str, author_bot: str = 'companion', summary: str = '') -> dict:
        k = (key or '').strip()[:120]
        if not k:
            return {'error': 'key 不能为空'}
        val = str(value or '')
        summ = (summary or val[:200]).strip()[:300]
        now = time.time()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO blackboard(key, value, author_bot, summary, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (k, val, author_bot, summ, now),
                )
                conn.commit()
            finally:
                conn.close()
        return {'ok': True, 'key': k, 'chars': len(val), 'author_bot': author_bot, 'summary': summ}

    def blackboard_read(self, key: str) -> dict:
        k = (key or '').strip()
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute('SELECT * FROM blackboard WHERE key = ?', (k,)).fetchone()
                if not row:
                    return {'error': f'共享黑板中未找到 key="{k}"'}
                return dict(row)
            finally:
                conn.close()

    def blackboard_list(self) -> dict:
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute('SELECT key, author_bot, summary, length(value) AS chars, updated_at FROM blackboard ORDER BY updated_at DESC LIMIT 50').fetchall()
                return {'items': [dict(r) for r in rows]}
            finally:
                conn.close()

    def blackboard_delete(self, key: str) -> dict:
        k = (key or '').strip()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute('DELETE FROM blackboard WHERE key = ?', (k,))
                conn.commit()
                return {'deleted': True, 'key': k}
            finally:
                conn.close()

    def prune_and_optimize(self, force_vacuum: bool = False) -> dict:
        """
        Automatic TTL & capacity pruning for SQLite FTS5 ContextSearchEngine:
        1. Deduplicates identical transient `(bot_id, tool_name, args_json)` tool_contexts and shrinks legacy oversized `full_text` blobs.
        2. Prunes transient tool outputs older than `TRANSIENT_TOOL_TTL_SECONDS` (48h) or beyond `MAX_TRANSIENT_TOOL_CONTEXTS` (50).
        3. Preserves `compaction_archive` records up to `ARCHIVE_TTL_SECONDS` (90d) / `MAX_ARCHIVE_CONTEXTS` (100).
        4. Prunes stale error messages (>3d) and bounds non-summary `messages_store` to `MAX_MESSAGES_STORE` (1500).
        5. Runs FTS5 `optimize`, `PRAGMA optimize`, WAL checkpoint, and controlled `VACUUM`.
        """
        now = time.time()
        self._last_prune_ts = now
        self._write_ops_since_prune = 0
        before_bytes = self.db_path.stat().st_size if self.db_path.exists() else 0
        deleted_contexts = 0
        deleted_messages = 0
        shrunk_blobs = 0
        need_vacuum = bool(force_vacuum)

        with self._lock:
            conn = self._connect()
            try:
                # 1. Shrink legacy full_text column in tool_contexts (chunks already hold verbatim text)
                cur = conn.execute(
                    'UPDATE tool_contexts SET full_text = substr(full_text, 1, 1200) WHERE length(full_text) > 1200'
                )
                shrunk_blobs = int(cur.rowcount or 0)
                if shrunk_blobs > 0:
                    need_vacuum = True

                # 2. Deduplicate transient tool_contexts with identical (bot_id, tool_name, args_json), keeping newest
                cur = conn.execute(
                    """
                    DELETE FROM tool_contexts
                    WHERE tool_name != 'compaction_archive'
                      AND ctx_id NOT IN (
                          SELECT ctx_id FROM (
                              SELECT ctx_id,
                                     ROW_NUMBER() OVER (PARTITION BY bot_id, tool_name, args_json ORDER BY created_at DESC) AS rn
                              FROM tool_contexts
                              WHERE tool_name != 'compaction_archive'
                          ) WHERE rn = 1
                      )
                    """
                )
                deleted_contexts += int(cur.rowcount or 0)

                # 3. Prune expired or excess transient tool_contexts
                transient_cutoff = now - self.TRANSIENT_TOOL_TTL_SECONDS
                cur = conn.execute(
                    """
                    DELETE FROM tool_contexts
                    WHERE tool_name != 'compaction_archive'
                      AND (
                          created_at < ?
                          OR ctx_id NOT IN (
                              SELECT ctx_id FROM tool_contexts
                              WHERE tool_name != 'compaction_archive'
                              ORDER BY created_at DESC
                              LIMIT ?
                          )
                      )
                    """,
                    (transient_cutoff, self.MAX_TRANSIENT_TOOL_CONTEXTS),
                )
                deleted_contexts += int(cur.rowcount or 0)

                # 4. Bound compaction_archive records to 90 days / 100 entries
                archive_cutoff = now - self.ARCHIVE_TTL_SECONDS
                cur = conn.execute(
                    """
                    DELETE FROM tool_contexts
                    WHERE tool_name = 'compaction_archive'
                      AND (
                          created_at < ?
                          OR ctx_id NOT IN (
                              SELECT ctx_id FROM tool_contexts
                              WHERE tool_name = 'compaction_archive'
                              ORDER BY created_at DESC
                              LIMIT ?
                          )
                      )
                    """,
                    (archive_cutoff, self.MAX_ARCHIVE_CONTEXTS),
                )
                deleted_contexts += int(cur.rowcount or 0)

                # 5. Clean orphaned tool_chunks and tool_chunks_fts
                cur_ch = conn.execute(
                    'DELETE FROM tool_chunks WHERE ctx_id NOT IN (SELECT ctx_id FROM tool_contexts)'
                )
                if int(cur_ch.rowcount or 0) > 0:
                    need_vacuum = True
                if self._fts5_available:
                    conn.execute(
                        'DELETE FROM tool_chunks_fts WHERE ctx_id NOT IN (SELECT ctx_id FROM tool_contexts)'
                    )

                # 6. Prune stale error messages (>3d) & bound non-summary messages_store
                err_cutoff = now - 3 * 86400
                cur_m1 = conn.execute(
                    "DELETE FROM messages_store WHERE kind = 'error' AND created_at < ?",
                    (err_cutoff,),
                )
                deleted_messages += int(cur_m1.rowcount or 0)

                cur_m2 = conn.execute(
                    """
                    DELETE FROM messages_store
                    WHERE kind != 'compact_summary'
                      AND msg_id NOT IN (
                          SELECT msg_id FROM messages_store
                          WHERE kind != 'compact_summary'
                          ORDER BY created_at DESC
                          LIMIT ?
                      )
                    """,
                    (self.MAX_MESSAGES_STORE,),
                )
                deleted_messages += int(cur_m2.rowcount or 0)

                if deleted_messages > 0:
                    need_vacuum = True
                    if self._fts5_available:
                        conn.execute(
                            'DELETE FROM messages_fts WHERE msg_id NOT IN (SELECT msg_id FROM messages_store)'
                        )
                    cur_ids = conn.execute('SELECT msg_id FROM messages_store')
                    self._indexed_msg_ids = {str(r['msg_id']) for r in cur_ids.fetchall()}

                # 7. Prune expired blackboard entries (>14d) or excess (>50)
                bb_cutoff = now - self.BLACKBOARD_TTL_SECONDS
                conn.execute(
                    """
                    DELETE FROM blackboard
                    WHERE updated_at < ?
                       OR key NOT IN (
                           SELECT key FROM blackboard ORDER BY updated_at DESC LIMIT 50
                       )
                    """,
                    (bb_cutoff,),
                )

                # 8. Optimize FTS5 indexes
                if self._fts5_available:
                    try:
                        conn.execute("INSERT INTO messages_fts(messages_fts) VALUES('optimize')")
                        conn.execute("INSERT INTO tool_chunks_fts(tool_chunks_fts) VALUES('optimize')")
                    except sqlite3.Error:
                        pass
                try:
                    conn.execute('PRAGMA optimize;')
                except sqlite3.Error:
                    pass
                conn.commit()

                freelist = int((conn.execute('PRAGMA freelist_count;').fetchone() or [0])[0])
                if deleted_contexts > 0 or freelist > 48 or before_bytes > 4_500_000:
                    need_vacuum = True
            finally:
                conn.close()

            if need_vacuum:
                vconn = sqlite3.connect(str(self.db_path), timeout=15.0, isolation_level=None)
                try:
                    vconn.execute('VACUUM;')
                    vconn.execute('PRAGMA wal_checkpoint(TRUNCATE);')
                except sqlite3.Error:
                    pass
                finally:
                    vconn.close()

        after_bytes = self.db_path.stat().st_size if self.db_path.exists() else 0
        return {
            'before_bytes': before_bytes,
            'after_bytes': after_bytes,
            'freed_bytes': max(0, before_bytes - after_bytes),
            'deleted_contexts': deleted_contexts,
            'deleted_messages': deleted_messages,
            'shrunk_blobs': shrunk_blobs,
            'vacuumed': need_vacuum,
        }
