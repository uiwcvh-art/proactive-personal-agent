from __future__ import annotations
import copy, json, secrets, threading, time
from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field, field_validator
from vault_manager import atomic_write

class BrainConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')
    base_url: str = 'https://api.openai.com/v1'
    api_key: str = Field('', max_length=4096)
    model: str = Field('', max_length=200)

    @field_validator('base_url')
    @classmethod
    def url(cls, v):
        from urllib.parse import urlsplit
        u = urlsplit(v)
        if u.scheme not in ('https', 'http') or not u.hostname or u.username or u.password or u.query or u.fragment:
            raise ValueError('填写 OpenAI 兼容 API 的 HTTP(S) Base URL')
        return v.rstrip('/')

class SMTPConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')
    host: str = Field('', max_length=253)
    port: int = Field(587, ge=1, le=65535)
    username: str = Field('', max_length=320)
    password: str = Field('', max_length=4096)
    recipient: str = Field('', max_length=320)
    sender: str = Field('', max_length=320)
    security: str = 'starttls'

    @field_validator('security')
    @classmethod
    def mode(cls, v):
        if v not in ('starttls','ssl'): raise ValueError('SMTP 必须使用 STARTTLS 或 SSL')
        return v

    @field_validator('host','username','recipient','sender')
    @classmethod
    def no_headers(cls, v):
        if '\r' in v or '\n' in v: raise ValueError('字段包含非法换行')
        return v.strip()

class Config(BaseModel):
    model_config = ConfigDict(extra='ignore')
    api_base_url: str = 'https://api.openai.com/v1'
    api_key: str = Field('', max_length=4096)
    default_model: str = Field('', max_length=200)
    fallback_base_url: str = Field('http://149.104.25.8:3000/v1', max_length=500)
    fallback_api_key: str = Field('sk-daye-aggregate-2026', max_length=4096)
    fallback_model: str = Field('commandcode/deepseek/deepseek-v4.1-flash', max_length=200)
    small_brain: BrainConfig = Field(default_factory=BrainConfig)
    big_brain: BrainConfig = Field(default_factory=BrainConfig)
    smtp: SMTPConfig = Field(default_factory=SMTPConfig)
    enabled: bool = True
    curiosity_interval_minutes: int = Field(90, ge=5, le=1440)
    idle_research_minutes: int = Field(30, ge=5, le=1440)
    email_after_minutes: int = Field(15, ge=1, le=1440)
    overlay_after_minutes: int = Field(30, ge=2, le=2880)
    quiet_start_hour: int = Field(0, ge=0, le=23)
    quiet_end_hour: int = Field(8, ge=0, le=23)
    auto_compact_tokens: int = Field(120000, ge=8000, le=1000000)

    @field_validator('api_base_url')
    @classmethod
    def validate_api_base_url(cls, v):
        return BrainConfig.url(v)

    @field_validator('fallback_base_url')
    @classmethod
    def validate_fallback_base_url(cls, v):
        s = (v or '').strip()
        return BrainConfig.url(s) if s else ''

    def effective_base_url(self) -> str:
        if self.api_base_url and self.api_base_url != 'https://api.openai.com/v1':
            return self.api_base_url
        if self.big_brain.base_url and self.big_brain.base_url != 'https://api.openai.com/v1':
            return self.big_brain.base_url
        return self.small_brain.base_url or self.api_base_url or 'https://api.openai.com/v1'

    def effective_api_key(self, big: bool = False) -> str:
        primary = self.big_brain.api_key if big else self.small_brain.api_key
        return primary or self.api_key

    def effective_model(self, bot_model: str = '', big: bool = False) -> str:
        if bot_model and bot_model.strip():
            return bot_model.strip()
        primary = self.big_brain.model if big else self.small_brain.model
        return primary or self.default_model

    def effective_fallback(self, big: bool = False) -> tuple[str, str, str]:
        fb_url = (self.fallback_base_url or '').strip() or self.effective_base_url()
        fb_key = (self.fallback_api_key or '').strip() or self.effective_api_key(big=big)
        fb_model = (self.fallback_model or '').strip()
        return fb_url.rstrip('/'), fb_key, fb_model

    def public(self):
        data = self.model_dump()
        data['has_api_key'] = bool(data.pop('api_key', '') or self.small_brain.api_key or self.big_brain.api_key)
        data['has_fallback_api_key'] = bool(data.pop('fallback_api_key', ''))
        data['api_base_url'] = self.effective_base_url()
        data['default_model'] = self.default_model or self.big_brain.model or self.small_brain.model
        data['fallback_base_url'] = (self.fallback_base_url or '').strip()
        data['fallback_model'] = (self.fallback_model or '').strip()
        for key in ('small_brain','big_brain'):
            data[key]['has_api_key'] = bool(data[key].pop('api_key'))
        data['smtp']['has_password'] = bool(data['smtp'].pop('password'))
        return data

def normalize_node_source(raw: str | None) -> str:
    s = str(raw or '').strip().lower()
    if not s or s in ('phone', 'phone1', '安卓一', '安卓1', 'android', 'android1', '手机', '手机端', 'phone_dsb'):
        return 'phone'
    if s in ('phone2', '安卓二', '安卓2', 'android2', 'tablet', 'pad', '平板', '平板端'):
        return 'phone2'
    if s in ('pc', 'pc1', 'pc端', 'windows一', 'windows1', 'windows', 'dsh', 'pc_dsh', 'dsb', '电脑', '电脑端', 'deepseek-harness', 'harness'):
        return 'pc'
    if s in ('pc2', 'windows二', 'windows2'):
        return 'pc2'
    return s


def node_display_name(raw: str | None) -> str:
    norm = normalize_node_source(raw)
    if norm == 'phone':
        return '安卓一'
    if norm == 'phone2':
        return '安卓二'
    if norm == 'pc':
        return 'Windows一'
    if norm == 'pc2':
        return 'Windows二'
    return str(raw or '安卓一').strip()


def compact_trace_list(trace: list | None) -> list[dict]:
    """
    Strip heavy tool outputs (read_file content, run_bash stdout, web_fetch HTML, Base64 screenshots)
    and large write_file/edit_file argument blobs from persisted/broadcast UI trace arrays.
    The client UI only ever displays tool name, short arguments (path/command/query/op), duration_ms,
    status, and whether result has an 'error' key.
    """
    if not isinstance(trace, list):
        return []
    compacted: list[dict] = []
    for step in trace[-24:]:
        if not isinstance(step, dict):
            continue
        raw_args = step.get('arguments')
        slim_args: dict = {}
        if isinstance(raw_args, dict):
            for k, v in raw_args.items():
                if k in ('content', 'old', 'new', 'code', 'script', 'image', 'base64'):
                    continue
                if isinstance(v, str):
                    slim_args[k] = v[:90]
                elif isinstance(v, (int, float, bool)):
                    slim_args[k] = v
                elif isinstance(v, list):
                    slim_args[k] = v[:4]
        raw_res = step.get('result')
        slim_res: dict = {}
        if isinstance(raw_res, dict) and raw_res.get('error'):
            slim_res['error'] = str(raw_res['error'])[:200]
        entry: dict = {
            'tool': str(step.get('tool') or ''),
            'arguments': slim_args,
            'duration_ms': int(step.get('duration_ms') or 0),
        }
        if step.get('status'):
            entry['status'] = str(step['status'])
        if slim_res:
            entry['result'] = slim_res
        compacted.append(entry)
    return compacted


class Store:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.workspace_dir = root / 'workspace'
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.config_path = root / 'config.json'
        self.state_path = root / 'state.json'
        self.settings_ws_path = self.workspace_dir / 'settings.json'
        self.bots_ws_path = self.workspace_dir / 'bots.json'
        self.routines_ws_path = self.workspace_dir / 'routines.json'
        self.config = Config.model_validate_json(self.config_path.read_text('utf-8')) if self.config_path.exists() else Config()
        self.state = json.loads(self.state_path.read_text('utf-8')) if self.state_path.exists() else {
            'messages':[], 'pending':{}, 'routines':[], 'opportunities':[], 'deadlines':[],
            'snooze_until':0, 'last_curiosity':0, 'last_research':0, 'last_user':time.time(), 'last_error':''}
        self._scrub_state_bloat()
        self._sync_to_workspace()

    def _scrub_state_bloat(self):
        with self.lock:
            changed = False
            now_ts = time.time()
            msgs = self.state.get('messages')
            if isinstance(msgs, list):
                for m in msgs:
                    if isinstance(m, dict):
                        if m.get('trace'):
                            old_tr = m['trace']
                            new_tr = compact_trace_list(old_tr)
                            if old_tr != new_tr:
                                m['trace'] = new_tr
                                changed = True
                        for blob_key in ('_multimodal_b64', 'image_b64', 'base64'):
                            if blob_key in m:
                                m.pop(blob_key, None)
                                changed = True
                # Never use a 1-by-1 sliding window (e.g. msgs[-240:]) because popping 1 message on every turn
                # shifts `messages[1:]` right after `messages[0]` (8,192 tokens) and destroys 100% of the conversation history Prefix Cache!
                # Normal compaction is handled by MemoryEngine.auto_compact_if_needed at `auto_compact_tokens` (120K~200K).
                # Only apply an emergency chunked ceiling if raw messages exceed 2,400 (slicing to 1,800 so prefix stays static for 600 turns).
                if len(msgs) > 2400:
                    self.state['messages'] = msgs[-1800:]
                    changed = True

            # Prune completed one-shot timers older than 1 hour (keep all active timers + at most 5 recent done timers)
            timers = self.state.get('timers')
            if isinstance(timers, list) and timers:
                active_t = [t for t in timers if isinstance(t, dict) and not t.get('done')]
                done_t = [
                    t for t in timers
                    if isinstance(t, dict) and t.get('done')
                    and (now_ts - float(t.get('due_at') or t.get('created_at') or 0)) <= 3600.0
                ][-5:]
                new_timers = active_t + done_t
                if len(new_timers) != len(timers):
                    self.state['timers'] = new_timers
                    changed = True

            # Prune notified deadlines older than 7 days
            deadlines = self.state.get('deadlines')
            if isinstance(deadlines, list) and deadlines:
                new_dl = [
                    d for d in deadlines
                    if isinstance(d, dict) and (not d.get('notified') or (now_ts - float(d.get('due_at') or 0)) <= 7 * 86400.0)
                ][-40:]
                if len(new_dl) != len(deadlines):
                    self.state['deadlines'] = new_dl
                    changed = True

            # Prune old opportunities beyond 30
            opps = self.state.get('opportunities')
            if isinstance(opps, list) and len(opps) > 30:
                self.state['opportunities'] = [o for o in opps if not o.get('done')][-30:]
                changed = True

            # Prune & compact notification_inbox (7-day TTL, max 100 items, slim history/fingerprints)
            inbox = self.state.get('notification_inbox')
            if isinstance(inbox, list) and inbox:
                cutoff_7d = now_ts - 7 * 86400.0
                kept_inbox = []
                for it in inbox:
                    if not isinstance(it, dict):
                        changed = True
                        continue
                    if float(it.get('timestamp') or 0) < cutoff_7d:
                        changed = True
                        continue
                    fps = it.get('fingerprints')
                    if isinstance(fps, list) and len(fps) > 15:
                        it['fingerprints'] = fps[-15:]
                        changed = True
                    hist = it.get('history')
                    if isinstance(hist, list) and len(hist) > 12:
                        it['history'] = hist[-12:]
                        changed = True
                    kept_inbox.append(it)
                if len(kept_inbox) > 100:
                    kept_inbox = kept_inbox[-100:]
                    changed = True
                if len(kept_inbox) != len(inbox):
                    changed = True
                self.state['notification_inbox'] = kept_inbox

            # Remove legacy cached_vault_context bloat from state.json
            if 'cached_vault_context' in self.state:
                self.state.pop('cached_vault_context', None)
                changed = True

            if changed:
                atomic_write(self.state_path, json.dumps(self.state, ensure_ascii=False, indent=2))

    def _sync_to_workspace(self, _from_sync: bool = False):
        """Write settings.json, bots.json, and routines.json into workspace so any Bot can inspect/edit its own setup."""
        with self.lock:
            if not _from_sync:
                # If workspace files were modified on disk by a tool (e.g. edit_file/write_file on bots.json)
                # since our last write, ingest those changes BEFORE writing state back to workspace!
                ws_modified = (
                    (self.bots_ws_path.exists() and getattr(self, '_bots_mtime', 0) > 0 and (
                        self.bots_ws_path.stat().st_mtime_ns != getattr(self, '_bots_mtime', 0)
                        or ((time.time() - getattr(self, '_bots_write_ts', 0.0)) < 1.0 and self.bots_ws_path.read_text('utf-8') != getattr(self, '_bots_raw_text', ''))
                    ))
                    or (self.settings_ws_path.exists() and getattr(self, '_settings_mtime', 0) > 0 and self.settings_ws_path.stat().st_mtime_ns != getattr(self, '_settings_mtime', 0))
                    or (self.routines_ws_path.exists() and getattr(self, '_routines_mtime', 0) > 0 and self.routines_ws_path.stat().st_mtime_ns != getattr(self, '_routines_mtime', 0))
                )
                if ws_modified:
                    self.sync_from_workspace(force=True, _skip_ws_write=True)
            settings_payload = {
                'api_base_url': self.config.effective_base_url(),
                'api_key': self.config.effective_api_key(),
                'default_model': self.config.effective_model(),
                'small_brain': {
                    'base_url': self.config.small_brain.base_url,
                    'api_key': self.config.small_brain.api_key or self.config.api_key,
                    'model': self.config.small_brain.model,
                    'has_api_key': bool(self.config.small_brain.api_key or self.config.api_key),
                },
                'big_brain': {
                    'base_url': self.config.big_brain.base_url,
                    'api_key': self.config.big_brain.api_key or self.config.api_key,
                    'model': self.config.big_brain.model,
                    'has_api_key': bool(self.config.big_brain.api_key or self.config.api_key),
                },
                'fallback_base_url': self.config.fallback_base_url,
                'fallback_api_key': self.config.fallback_api_key,
                'fallback_model': self.config.fallback_model,
                'has_api_key': bool(self.config.effective_api_key()),
                'has_fallback_api_key': bool(self.config.fallback_api_key),
                'enabled': self.config.enabled,
                'curiosity_interval_minutes': self.config.curiosity_interval_minutes,
                'idle_research_minutes': self.config.idle_research_minutes,
                'quiet_start_hour': self.config.quiet_start_hour,
                'quiet_end_hour': self.config.quiet_end_hour,
                'auto_compact_tokens': self.config.auto_compact_tokens,
            }
            atomic_write(self.settings_ws_path, json.dumps(settings_payload, ensure_ascii=False, indent=2))
            self._settings_mtime = self.settings_ws_path.stat().st_mtime_ns if self.settings_ws_path.exists() else 0
            if 'assistants' in self.state:
                ws_bots = [{k: v for k, v in b.items() if k != 'node_settings'} for b in self.state['assistants'] if isinstance(b, dict)]
                bots_json_str = json.dumps(ws_bots, ensure_ascii=False, indent=2)
                atomic_write(self.bots_ws_path, bots_json_str)
                self._bots_raw_text = bots_json_str
                self._bots_write_ts = time.time()
                self._bots_mtime = self.bots_ws_path.stat().st_mtime_ns if self.bots_ws_path.exists() else 0
            if 'routines' in self.state:
                atomic_write(self.routines_ws_path, json.dumps(self.state['routines'], ensure_ascii=False, indent=2))
                self._routines_mtime = self.routines_ws_path.stat().st_mtime_ns if self.routines_ws_path.exists() else 0

    def sync_from_workspace(self, force: bool = False, _skip_ws_write: bool = False) -> bool:
        """If a Bot edited workspace/settings.json or workspace/bots.json via tools/bash, apply changes."""
        now_ts = time.time()
        recent_ws_write = (now_ts - getattr(self, '_bots_write_ts', 0.0)) < 1.0
        if not force and not recent_ws_write and (now_ts - getattr(self, '_last_ws_sync_ts', 0.0)) < 0.2:
            return False
        self._last_ws_sync_ts = now_ts
        changed = False
        pending_config_update = None
        with self.lock:
            try:
                if self.bots_ws_path.exists():
                    mtime = self.bots_ws_path.stat().st_mtime_ns
                    if force or recent_ws_write or mtime != getattr(self, '_bots_mtime', 0):
                        raw_text = self.bots_ws_path.read_text('utf-8')
                        if force or raw_text != getattr(self, '_bots_raw_text', ''):
                            self._bots_mtime = mtime
                            self._bots_raw_text = raw_text
                            raw = json.loads(raw_text)
                            if isinstance(raw, list) and raw:
                                from assistants import TOOL_NAMES
                                prev_by_id = {b.get('id'): b for b in (self.state.get('assistants') or []) if isinstance(b, dict)}
                                valid_bots = []
                                for item in raw[:30]:
                                    if isinstance(item, dict) and item.get('id') and item.get('name'):
                                        item.setdefault('title', '')
                                        item.setdefault('description', '')
                                        item.setdefault('instructions', '')
                                        item.setdefault('model', '')
                                        item.setdefault('engine', 'small')
                                        item.setdefault('color', '#7C5CFF')
                                        item.setdefault('shape', 'circle')
                                        item.setdefault('notify', True)
                                        item.setdefault('curiosity_enabled', item.get('id') == 'companion')
                                        item.setdefault('pinned', False)
                                        item.setdefault('hidden', False)
                                        item.setdefault('pinned_at', 0)
                                        item.setdefault('avatar_version', '')
                                        item.setdefault('tools', TOOL_NAMES.copy())
                                        prev_b = prev_by_id.get(item.get('id'))
                                        if isinstance(prev_b, dict) and isinstance(prev_b.get('node_settings'), dict):
                                            item.setdefault('node_settings', copy.deepcopy(prev_b['node_settings']))
                                        if isinstance(prev_b, dict) and isinstance(item.get('node_settings'), dict):
                                            for root_k in ('model', 'curiosity_enabled', 'notify'):
                                                if item.get(root_k) != prev_b.get(root_k):
                                                    for ns_v in item['node_settings'].values():
                                                        if isinstance(ns_v, dict) and ns_v.get(root_k) == prev_b.get(root_k):
                                                            ns_v[root_k] = item.get(root_k)
                                                else:
                                                    phone_ns = item['node_settings'].get('phone')
                                                    if isinstance(phone_ns, dict) and phone_ns.get(root_k) != prev_b.get(root_k):
                                                        item[root_k] = phone_ns.get(root_k)
                                        valid_bots.append(item)
                                if valid_bots and valid_bots != self.state.get('assistants'):
                                    self.state['assistants'] = valid_bots
                                    changed = True
            except Exception:
                pass
            try:
                if self.routines_ws_path.exists():
                    mtime = self.routines_ws_path.stat().st_mtime_ns
                    if force or mtime != getattr(self, '_routines_mtime', 0):
                        self._routines_mtime = mtime
                        raw = json.loads(self.routines_ws_path.read_text('utf-8'))
                        if isinstance(raw, list):
                            valid_routines = []
                            for r in raw[:50]:
                                if isinstance(r, dict) and r.get('goal'):
                                    r.setdefault('id', secrets.token_hex(8))
                                    r.setdefault('bot_id', self.state.get('primary_bot_id', 'companion'))
                                    r.setdefault('interval_hours', 24)
                                    r.setdefault('enabled', True)
                                    r.setdefault('last_run', time.time())
                                    r.setdefault('created_at', time.time())
                                    valid_routines.append(r)
                            if valid_routines != self.state.get('routines'):
                                self.state['routines'] = valid_routines
                                changed = True
            except Exception:
                pass
            try:
                if self.settings_ws_path.exists():
                    mtime = self.settings_ws_path.stat().st_mtime_ns
                    if force or mtime != getattr(self, '_settings_mtime', 0):
                        self._settings_mtime = mtime
                        raw = json.loads(self.settings_ws_path.read_text('utf-8'))
                        if isinstance(raw, dict):
                            update = {}
                            cur_dump = self.config.model_dump()
                            for k in ('api_base_url', 'api_key', 'default_model',
                                      'fallback_base_url', 'fallback_api_key', 'fallback_model',
                                      'enabled', 'curiosity_interval_minutes', 'idle_research_minutes',
                                      'quiet_start_hour', 'quiet_end_hour', 'auto_compact_tokens'):
                                if k in raw:
                                    if k in ('api_key', 'fallback_api_key') and not str(raw[k] or '').strip():
                                        continue
                                    if raw[k] != cur_dump.get(k):
                                        update[k] = raw[k]
                            for brain_k in ('small_brain', 'big_brain'):
                                if isinstance(raw.get(brain_k), dict):
                                    b_in = raw[brain_k]
                                    b_cur = cur_dump.get(brain_k) or {}
                                    b_up = {}
                                    for sub_k in ('base_url', 'model', 'api_key'):
                                        if sub_k in b_in:
                                            if sub_k == 'api_key' and not str(b_in[sub_k] or '').strip():
                                                continue
                                            if b_in[sub_k] != b_cur.get(sub_k):
                                                b_up[sub_k] = b_in[sub_k]
                                    if b_up:
                                        update[brain_k] = b_up
                            if update:
                                pending_config_update = update
            except Exception:
                pass
            if pending_config_update:
                self.update_config(pending_config_update)
                changed = True
            elif changed and not _skip_ws_write:
                self.save()
        return changed

    def save(self):
        with self.lock:
            atomic_write(self.state_path, json.dumps(self.state, ensure_ascii=False, indent=2))
            self._sync_to_workspace()

    def update_config(self, update: dict):
        with self.lock:
            data = self.config.model_dump()
            # Synchronize top-level unified API config with small_brain & big_brain only when not explicitly overridden
            unified_url = update.get('api_base_url')
            unified_key = update.get('api_key')
            unified_model = update.get('default_model')
            has_explicit_small = isinstance(update.get('small_brain'), dict)
            has_explicit_big = isinstance(update.get('big_brain'), dict)
            for k, v in update.items():
                if isinstance(v, dict) and isinstance(data.get(k), dict):
                    merged = dict(data[k])
                    for sub_k, sub_v in v.items():
                        if k in ('small_brain', 'big_brain'):
                            if sub_k in ('base_url', 'api_key', 'model'):
                                merged[sub_k] = sub_v
                        else:
                            merged[sub_k] = sub_v
                    data[k] = merged
                else:
                    data[k] = v
            if unified_url is not None:
                if not has_explicit_small or 'base_url' not in update['small_brain']:
                    data['small_brain']['base_url'] = unified_url
                if not has_explicit_big or 'base_url' not in update['big_brain']:
                    data['big_brain']['base_url'] = unified_url
            if unified_key is not None and unified_key != '':
                if not has_explicit_small or 'api_key' not in update['small_brain']:
                    data['small_brain']['api_key'] = unified_key
                if not has_explicit_big or 'api_key' not in update['big_brain']:
                    data['big_brain']['api_key'] = unified_key
            if unified_model is not None and unified_model != '':
                if not has_explicit_small or 'model' not in update['small_brain']:
                    data['small_brain']['model'] = unified_model
                if not has_explicit_big or 'model' not in update['big_brain']:
                    data['big_brain']['model'] = unified_model

            config = Config.model_validate(data)
            atomic_write(self.config_path, config.model_dump_json(indent=2))
            self.config = config
            self._sync_to_workspace()
            return config.public()

    def message(self, role, text, kind='chat', **extra):
        now_ts = time.time()
        clean_txt = str(text or '').strip()
        target_bot = extra.get('bot_id', 'companion')
        target_src = normalize_node_source(extra.get('source'))
        extra['source'] = target_src
        if 'trace' in extra:
            extra['trace'] = compact_trace_list(extra['trace'])
        with self.lock:
            if role == 'assistant' and clean_txt and self.state['messages']:
                for prev in reversed(self.state['messages'][-6:]):
                    prev_src = normalize_node_source(prev.get('source'))
                    if prev.get('bot_id', 'companion') == target_bot and prev_src == target_src:
                        if (
                            prev.get('role') == 'assistant'
                            and str(prev.get('text') or '').strip() == clean_txt
                            and abs(now_ts - float(prev.get('created_at') or 0)) < 15.0
                        ):
                            if extra.get('trace') and not prev.get('trace'):
                                prev['trace'] = extra['trace']
                            if extra.get('choices') and not prev.get('choices'):
                                prev['choices'] = extra['choices']
                            self.save()
                            return prev
                        break
            m = {'id': secrets.token_hex(12), 'role':role, 'text':text, 'kind':kind, 'created_at':now_ts, **extra}
            self.state['messages'].append(m)
            if len(self.state['messages']) > 2400:
                self.state['messages'] = self.state['messages'][-1800:]
            self.save()
        return m


def run_storage_maintenance(
    store: Store,
    vault=None,
    context_search=None,
    workspace_dir: Path | None = None,
    force_vacuum: bool = False,
    upload_ttl_days: float = 7.0,
    max_uploads_bytes: int = 20_000_000,
) -> dict:
    """
    Unified Storage Lifecycle & Garbage Collection Engine:
    - Scrubs state.json bloat (expired timers, deadlines, old notifications) without mutating active LLM message prefixes.
    - Prunes memory_vault/.history redundant snapshots and bounds append logs while protecting all core memories/personas.
    - Prunes & compacts SQLite FTS5 context_index.db (deduplicating transient tool outputs, TTL pruning, FTS5 optimize & VACUUM).
    - Cleans stale partial uploads, old background command logs, duplicate/expired temporary uploads, and 0-byte orphan DB files.
    - Cleans Chromium & desktop transient caches while preserving user login sessions (Cookies, Local Storage, IndexedDB).
    """
    import hashlib
    import shutil

    now_ts = time.time()
    report: dict = {
        'timestamp': now_ts,
        'freed_bytes_total': 0,
        'files_removed': 0,
        'vault': {},
        'sqlite': {},
        'workspace': {},
        'browser_cache_freed_bytes': 0,
    }

    # 1. Scrub state.json bloat
    try:
        before_state_sz = store.state_path.stat().st_size if store.state_path.exists() else 0
        store._scrub_state_bloat()
        after_state_sz = store.state_path.stat().st_size if store.state_path.exists() else 0
        report['state_freed_bytes'] = max(0, before_state_sz - after_state_sz)
        report['freed_bytes_total'] += report['state_freed_bytes']
    except Exception:
        pass

    # 2. Prune Vault .history snapshots & logs
    if vault is not None and hasattr(vault, 'prune_storage'):
        try:
            v_res = vault.prune_storage()
            report['vault'] = v_res
            report['freed_bytes_total'] += int(v_res.get('freed_bytes') or 0)
            report['files_removed'] += int(v_res.get('removed_snapshots') or 0)
        except Exception:
            pass

    # 3. Prune & optimize SQLite FTS5 context_index.db
    if context_search is not None and hasattr(context_search, 'prune_and_optimize'):
        try:
            s_res = context_search.prune_and_optimize(force_vacuum=force_vacuum)
            report['sqlite'] = s_res
            report['freed_bytes_total'] += int(s_res.get('freed_bytes') or 0)
        except Exception:
            pass

    # 4. Clean 0-byte orphan DB files in data root
    data_root = store.root
    for empty_db_name in ('agent.db', 'context_index.db', 'context_search.db', 'state.sqlite3'):
        cand = data_root / empty_db_name
        try:
            if cand.is_file() and cand.stat().st_size == 0:
                cand.unlink(missing_ok=True)
                report['files_removed'] += 1
        except Exception:
            pass

    # 5. Clean workspace transfers, .bg_logs, and uploads
    ws = workspace_dir or store.workspace_dir
    ws_freed = 0
    ws_removed = 0

    # 5a. Partial transfers older than 6 hours
    for trans_dir in (data_root / '.transfers', ws / '.transfers'):
        if trans_dir.exists() and trans_dir.is_dir():
            for part in trans_dir.glob('*.partial'):
                try:
                    st = part.stat()
                    if (now_ts - st.st_mtime) > 21600:
                        part.unlink(missing_ok=True)
                        ws_freed += st.st_size
                        ws_removed += 1
                except Exception:
                    pass

    # 5b. Background task logs (.bg_logs): TTL 3 days, keep newest 20, truncate logs > 256 KB
    bg_dir = ws / '.bg_logs'
    if bg_dir.exists() and bg_dir.is_dir():
        bg_logs = [f for f in bg_dir.glob('*.log') if f.is_file() and not f.is_symlink()]
        bg_logs.sort(key=lambda p: p.stat().st_mtime)
        cutoff_bg = now_ts - 3 * 86400.0
        for idx, lg in enumerate(bg_logs):
            try:
                st = lg.stat()
                if st.st_mtime < cutoff_bg or idx < len(bg_logs) - 20 or st.st_size == 0:
                    lg.unlink(missing_ok=True)
                    ws_freed += st.st_size
                    ws_removed += 1
                elif st.st_size > 262_144:
                    raw_b = lg.read_bytes()[-131_072:]
                    lg.write_bytes(raw_b)
                    ws_freed += (st.st_size - len(raw_b))
            except Exception:
                pass

    # 5c. Workspace uploads: deduplicate identical files (unless referenced in messages), remove files older than upload_ttl_days, and cap total bytes
    up_dir = ws / 'uploads'
    if up_dir.exists() and up_dir.is_dir():
        referenced_names: set[str] = set()
        for m in store.state.get('messages', []):
            if isinstance(m, dict):
                txt_m = str(m.get('text') or '')
                for u_match in ('uploads/', '/workspace-media/'):
                    if u_match in txt_m:
                        for f_cand in up_dir.iterdir():
                            if f_cand.name in txt_m:
                                referenced_names.add(f_cand.name)
        up_files = [f for f in up_dir.iterdir() if f.is_file() and not f.is_symlink()]
        up_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        seen_hashes: set[str] = set()
        kept_uploads: list[tuple[Path, int, float]] = []
        cutoff_up = now_ts - upload_ttl_days * 86400.0
        for uf in up_files:
            try:
                st = uf.stat()
                if st.st_mtime < cutoff_up and uf.name not in referenced_names:
                    uf.unlink(missing_ok=True)
                    ws_freed += st.st_size
                    ws_removed += 1
                    continue
                if st.st_size <= 10_000_000:
                    h = hashlib.sha256(uf.read_bytes()).hexdigest()
                    if h in seen_hashes and uf.name not in referenced_names:
                        uf.unlink(missing_ok=True)
                        ws_freed += st.st_size
                        ws_removed += 1
                        continue
                    seen_hashes.add(h)
                kept_uploads.append((uf, st.st_size, st.st_mtime))
            except Exception:
                pass

        # Enforce total uploads directory cap (oldest evicted first)
        total_up_bytes = sum(x[1] for x in kept_uploads)
        while total_up_bytes > max_uploads_bytes and len(kept_uploads) > 1:
            oldest_p, oldest_sz, _ = kept_uploads.pop()
            try:
                oldest_p.unlink(missing_ok=True)
                total_up_bytes -= oldest_sz
                ws_freed += oldest_sz
                ws_removed += 1
            except Exception:
                pass

    report['workspace'] = {'freed_bytes': ws_freed, 'files_removed': ws_removed}
    report['freed_bytes_total'] += ws_freed
    report['files_removed'] += ws_removed

    # 6. Clean Cloud Desktop Chromium & user cache bloat if present (preserving Cookies/Local Storage/IndexedDB, and skipping if Chromium is actively running!)
    desktop_home = data_root.parent / 'cloud-computer' / 'desktop-home'
    if desktop_home.exists() and desktop_home.is_dir():
        cache_freed = 0
        chrom_prof = desktop_home / '.local' / 'chromium_profile'
        chromium_running = (chrom_prof / 'SingletonLock').exists() or (chrom_prof / 'SingletonLock').is_symlink()
        cache_targets = [
            desktop_home / '.cache' / 'pip',
            desktop_home / '.cache' / 'luakit',
        ]
        if not chromium_running:
            cache_targets.extend([
                chrom_prof / 'Default' / 'Cache',
                chrom_prof / 'Default' / 'Code Cache',
                chrom_prof / 'Default' / 'GPUCache',
                chrom_prof / 'Default' / 'DawnWebGPUCache',
                chrom_prof / 'Default' / 'DawnGraphiteCache',
                chrom_prof / 'Default' / 'Service Worker' / 'CacheStorage',
                chrom_prof / 'Default' / 'Service Worker' / 'ScriptCache',
                chrom_prof / 'component_crx_cache',
                chrom_prof / 'WasmTtsEngine',
                chrom_prof / 'BrowserMetrics-spare.pma',
                chrom_prof / 'ShaderCache',
                chrom_prof / 'GrShaderCache',
                desktop_home / '.cache' / 'chromium-headless',
                desktop_home / '.cache' / 'chromium',
            ])
        for ct in cache_targets:
            try:
                if not ct.exists():
                    continue
                if ct.is_file():
                    sz = ct.stat().st_size
                    ct.unlink(missing_ok=True)
                    cache_freed += sz
                elif ct.is_dir() and not ct.is_symlink():
                    sz = sum(f.stat().st_size for f in ct.rglob('*') if f.is_file() and not f.is_symlink())
                    shutil.rmtree(ct, ignore_errors=True)
                    cache_freed += sz
            except Exception:
                pass
        report['browser_cache_freed_bytes'] = cache_freed
        report['freed_bytes_total'] += cache_freed

    return report
