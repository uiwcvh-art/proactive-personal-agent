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
    model_config = ConfigDict(extra='forbid')
    api_base_url: str = 'https://api.openai.com/v1'
    api_key: str = Field('', max_length=4096)
    default_model: str = Field('', max_length=200)
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

    @field_validator('api_base_url')
    @classmethod
    def validate_api_base_url(cls, v):
        return BrainConfig.url(v)

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

    def public(self):
        data = self.model_dump()
        data['has_api_key'] = bool(data.pop('api_key', '') or self.small_brain.api_key or self.big_brain.api_key)
        data['api_base_url'] = self.effective_base_url()
        data['default_model'] = self.default_model or self.big_brain.model or self.small_brain.model
        for key in ('small_brain','big_brain'):
            data[key]['has_api_key'] = bool(data[key].pop('api_key'))
        data['smtp']['has_password'] = bool(data['smtp'].pop('password'))
        return data

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
        self._sync_to_workspace()

    def _sync_to_workspace(self):
        """Write settings.json, bots.json, and routines.json into workspace so any Bot can inspect/edit its own setup."""
        with self.lock:
            settings_payload = {
                'api_base_url': self.config.effective_base_url(),
                'api_key': self.config.effective_api_key(),
                'default_model': self.config.effective_model(),
                'enabled': self.config.enabled,
                'curiosity_interval_minutes': self.config.curiosity_interval_minutes,
                'idle_research_minutes': self.config.idle_research_minutes,
                'quiet_start_hour': self.config.quiet_start_hour,
                'quiet_end_hour': self.config.quiet_end_hour,
            }
            atomic_write(self.settings_ws_path, json.dumps(settings_payload, ensure_ascii=False, indent=2))
            self._settings_mtime = self.settings_ws_path.stat().st_mtime_ns if self.settings_ws_path.exists() else 0
            if 'assistants' in self.state:
                atomic_write(self.bots_ws_path, json.dumps(self.state['assistants'], ensure_ascii=False, indent=2))
                self._bots_mtime = self.bots_ws_path.stat().st_mtime_ns if self.bots_ws_path.exists() else 0
            if 'routines' in self.state:
                atomic_write(self.routines_ws_path, json.dumps(self.state['routines'], ensure_ascii=False, indent=2))
                self._routines_mtime = self.routines_ws_path.stat().st_mtime_ns if self.routines_ws_path.exists() else 0

    def sync_from_workspace(self) -> bool:
        """If a Bot edited workspace/settings.json or workspace/bots.json via tools/bash, apply changes."""
        changed = False
        with self.lock:
            try:
                if self.settings_ws_path.exists():
                    mtime = self.settings_ws_path.stat().st_mtime_ns
                    if mtime != getattr(self, '_settings_mtime', 0):
                        raw = json.loads(self.settings_ws_path.read_text('utf-8'))
                        if isinstance(raw, dict):
                            update = {}
                            for k in ('api_base_url', 'api_key', 'default_model', 'enabled',
                                      'curiosity_interval_minutes', 'idle_research_minutes',
                                      'quiet_start_hour', 'quiet_end_hour'):
                                if k in raw:
                                    update[k] = raw[k]
                            if update:
                                self.update_config(update)
                                changed = True
            except Exception:
                pass
            try:
                if self.bots_ws_path.exists():
                    mtime = self.bots_ws_path.stat().st_mtime_ns
                    if mtime != getattr(self, '_bots_mtime', 0):
                        raw = json.loads(self.bots_ws_path.read_text('utf-8'))
                        if isinstance(raw, list) and raw:
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
                                    valid_bots.append(item)
                            if valid_bots:
                                self.state['assistants'] = valid_bots
                                self.save()
                                changed = True
            except Exception:
                pass
            try:
                if self.routines_ws_path.exists():
                    mtime = self.routines_ws_path.stat().st_mtime_ns
                    if mtime != getattr(self, '_routines_mtime', 0):
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
                            self.state['routines'] = valid_routines
                            self.save()
                            changed = True
            except Exception:
                pass
        return changed

    def save(self):
        with self.lock:
            atomic_write(self.state_path, json.dumps(self.state, ensure_ascii=False, indent=2))
            self._sync_to_workspace()

    def update_config(self, update: dict):
        with self.lock:
            data = self.config.model_dump()
            # Synchronize top-level unified API config with small_brain & big_brain
            unified_url = update.get('api_base_url')
            unified_key = update.get('api_key')
            unified_model = update.get('default_model')
            for k, v in update.items():
                if isinstance(v, dict) and isinstance(data.get(k), dict):
                    data[k].update(v)
                else:
                    data[k] = v
            if unified_url is not None:
                data['small_brain']['base_url'] = unified_url
                data['big_brain']['base_url'] = unified_url
            if unified_key is not None and unified_key != '':
                data['small_brain']['api_key'] = unified_key
                data['big_brain']['api_key'] = unified_key
            if unified_model is not None and unified_model != '':
                data['small_brain']['model'] = unified_model
                data['big_brain']['model'] = unified_model

            config = Config.model_validate(data)
            atomic_write(self.config_path, config.model_dump_json(indent=2))
            self.config = config
            self._sync_to_workspace()
            return config.public()

    def message(self, role, text, kind='chat', **extra):
        m = {'id': secrets.token_hex(12), 'role':role, 'text':text, 'kind':kind, 'created_at':time.time(), **extra}
        with self.lock:
            self.state['messages'].append(m)
            self.state['messages'] = self.state['messages'][-600:]
            self.save()
        return m
