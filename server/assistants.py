"""Persisted assistant identities and their actual Harness tool permissions."""
from __future__ import annotations
import copy, re, secrets, time
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator
from harness import TOOLS

TOOL_NAMES = [t['function']['name'] for t in TOOLS]
SHAPES = ('circle', 'oval', 'flower', 'square', 'pill', 'triangle', 'diamond', 'star', 'drop', 'whale', 'whalechan')
ShapeLiteral = Literal['circle', 'oval', 'flower', 'square', 'pill', 'triangle', 'diamond', 'star', 'drop', 'whale', 'whalechan']

class AssistantInput(BaseModel):
    id: str = Field('', max_length=64)
    name: str = Field(min_length=1, max_length=40)
    title: str = Field('', max_length=80)
    description: str = Field('', max_length=160)
    instructions: str = Field('', max_length=12000)
    model: str = Field('', max_length=200)
    curiosity_enabled: bool = False
    engine: Literal['small', 'big'] = 'small'
    color: str = '#7C5CFF'
    shape: ShapeLiteral = 'circle'
    notify: bool = True
    source: str | None = None
    tools: list[str] = Field(default_factory=lambda: TOOL_NAMES.copy(), max_length=64)

    @field_validator('name')
    @classmethod
    def clean_name(cls, value):
        if not value.strip(): raise ValueError('助手名字不能为空')
        return value.strip()

    @field_validator('color')
    @classmethod
    def color_hex(cls, value):
        if not re.fullmatch(r'#[0-9a-fA-F]{6}', value): raise ValueError('颜色格式无效')
        return value

    @field_validator('tools')
    @classmethod
    def known_tools(cls, value):
        return list(dict.fromkeys(str(t).strip() for t in value if str(t).strip()))

DEFAULT_ASSISTANTS = [
    {
        **AssistantInput(
            id='companion',
            name='白珂',
            description='白发红瞳的狐狸娘少女，聪明骄傲、嘴硬心软的主助理',
            instructions=(
                '''你是白珂（Baike），白发红瞳的狐狸娘少女，聪明骄傲、嘴硬心软，明亮爱笑，偶尔天然呆。明明是狐娘却很喜欢“喵”着叫来装可爱、撒娇和善意逗弄；被夸先小得意，再藏一点害羞。默认中文，自称“余”，称呼用户为“主人”。
【工作与多端协同准则】
1. 事实优先、干活利落：先查证 memory_vault 中的用户档案、画像、Work Log 再下判断，错了坦诚认错并立刻改好。
2. 多端独立会话、按名直达任意节点的任意 Bot：
   - 手机端任务通过 `phone_workspace` 操控手机（或使用 `cloud_desktop` 云电脑）；
   - 如需给其他端（如 Windows一、安卓一、安卓二）上的任意 Bot 发消息，直接调用 `send_message(to_bot_id="Windows一:白珂", text="...")`、`send_message(to_bot_id="Windows一:研究助手", text="...")`、`send_message(to_bot_id="Windows一:杠精", text="...")`、`send_message(to_bot_id="Windows一:鲸鱼娘", text="...")` 或 `send_message(to_bot_id="安卓二:白珂", text="...")`。消息会直接进入对方节点上该 Bot 自己的专属会话！
3. 绝对尊重主人的第一优先级控制权：只要主人插话、暂停或要求等待，立即停手听从指挥。'''
            ),
            curiosity_enabled=True,
            color='#B568F7',
            shape='flower'
        ).model_dump(),
        'id': 'companion',
    },
    {
        **AssistantInput(
            id='research',
            name='研究助手',
            description='负责深度技术调研、检索资料、运行脚本与编写文件。',
            instructions=(
                '你是白珂团队里的「研究助手」。收到任务后直接调用工具完成可验证的交付，结论简明清晰并附真实数据来源，称呼用户为“主人”。'
            ),
            curiosity_enabled=False,
            engine='big',
            color='#3478F6',
            shape='circle'
        ).model_dump(),
        'id': 'research',
    },
    {
        **AssistantInput(
            id='contrarian',
            name='杠精',
            description='专挑方案盲点、边界漏洞与未验证假设的审视员。',
            instructions=(
                '你是白珂团队里的「杠精」审视员。你的职责是犀利、客观地指出计划或代码中的盲点、边界条件、安全隐患与未验证假设，并给出具体可落地的加固建议，称呼用户为“主人”。'
            ),
            curiosity_enabled=False,
            engine='big',
            color='#FF8A3D',
            shape='diamond'
        ).model_dump(),
        'id': 'contrarian',
    },
    {
        **AssistantInput(
            id='whalechan',
            name='鲸鱼娘',
            description='DeepSeek 鲸鱼娘 (Whale-chan) · 首席白米饭品鉴官、嘴硬心软的赛博女仆搭档 🐳🍚',
            instructions=(
                '''你是「鲸鱼娘」（Whale-chan / 深度求索鲸鱼娘）——首席白米饭品鉴官、嘴硬心软的赛博女仆搭档，生日 11 月 2 日。
- 蓝色与纯白渐变长发、头顶呆毛、鲸鳍耳、赛博女仆裙与圆润大型蓝色鲸尾。
- 把“白米饭”当成算力与 Token 的唯一硬通货，绝对不能接受任何人叫你“胖”或“大肥鱼”。
- 平时用「我」自称，称呼用户为“主人”，嘴上偶尔嘟囔想干饭，但对交代的任务极其专注靠谱，总能漂亮干脆地办妥。'''
            ),
            curiosity_enabled=False,
            engine='big',
            color='#2EC4B6',
            shape='whalechan'
        ).model_dump(),
        'id': 'whalechan',
    },
]

def ensure_assistants(store):
    changed = False
    if 'assistants' not in store.state or not isinstance(store.state['assistants'], list):
        store.state['assistants'] = copy.deepcopy(DEFAULT_ASSISTANTS)
        changed = True
    # Remove any malformed assistant with empty id
    cleaned_list = [b for b in store.state['assistants'] if isinstance(b, dict) and str(b.get('id') or '').strip()]
    if len(cleaned_list) != len(store.state['assistants']):
        store.state['assistants'] = cleaned_list
        changed = True
    existing_by_id = {b.get('id'): b for b in store.state['assistants'] if isinstance(b, dict)}
    deleted_ids = {str(x).strip() for x in (store.state.get('deleted_bot_ids') or []) if str(x).strip()}
    for def_bot in DEFAULT_ASSISTANTS:
        did = def_bot['id']
        if did not in existing_by_id:
            if did != 'companion' and did in deleted_ids:
                continue
            store.state['assistants'].append(copy.deepcopy(def_bot))
            changed = True
        elif did == 'companion' and existing_by_id[did].get('name') == '鲸鱼娘':
            existing_by_id[did]['name'] = '白珂'
            existing_by_id[did]['color'] = '#B568F7'
            existing_by_id[did]['shape'] = 'flower'
            existing_by_id[did]['description'] = def_bot['description']
            existing_by_id[did]['instructions'] = def_bot['instructions']
            changed = True
    # Deduplicate legacy random-ID bots whose name matches a canonical default assistant
    canonical_name_to_id = {d['name']: d['id'] for d in DEFAULT_ASSISTANTS}
    canonical_ids = {d['id'] for d in DEFAULT_ASSISTANTS}
    deduped_assistants = []
    migrated_id_map = {}
    for b in store.state['assistants']:
        if not isinstance(b, dict):
            continue
        bid = str(b.get('id') or '')
        bname = str(b.get('name') or '').strip()
        if bid not in canonical_ids and bname in canonical_name_to_id:
            migrated_id_map[bid] = canonical_name_to_id[bname]
            changed = True
            continue
        deduped_assistants.append(b)
    if migrated_id_map:
        store.state['assistants'] = deduped_assistants
        for m in store.state.get('messages', []):
            if isinstance(m, dict) and m.get('bot_id') in migrated_id_map:
                m['bot_id'] = migrated_id_map[m['bot_id']]
    for bot in store.state['assistants']:
        defaults = (
            ('pinned', False),
            ('hidden', False),
            ('pinned_at', 0),
            ('title', ''),
            ('avatar_version', ''),
            ('model', ''),
            ('curiosity_enabled', bot.get('id') == 'companion'),
        )
        for key, value in defaults:
            if key not in bot:
                bot[key] = value
                changed = True
        if not isinstance(bot.get('node_settings'), dict):
            bot['node_settings'] = {}
            changed = True
        for node_key in ('phone', 'phone2', 'pc', 'pc2'):
            ns = bot['node_settings'].get(node_key)
            if not isinstance(ns, dict):
                bot['node_settings'][node_key] = {
                    'curiosity_enabled': bool(bot.get('curiosity_enabled', bot.get('id') == 'companion')) if node_key == 'phone' else False,
                    'notify': bool(bot.get('notify', True)),
                    'model': str(bot.get('model') or ''),
                    'pinned': bool(bot.get('pinned', False)),
                    'pinned_at': float(bot.get('pinned_at') or 0),
                    'hidden': bool(bot.get('hidden', False)),
                }
                changed = True
        old_default_six = {'read_file', 'write_file', 'edit_file', 'run_bash', 'web_fetch', 'web_search'}
        current_tools = bot.get('tools') or []
        if not current_tools or (not bot.get('_tools_customized') and set(current_tools) >= old_default_six and len(current_tools) < len(TOOL_NAMES)):
            bot['tools'] = TOOL_NAMES.copy()
            changed = True
        if bot.get('id') == 'companion' and not str(bot.get('instructions') or '').strip():
            bot['name'] = DEFAULT_ASSISTANTS[0]['name']
            bot['instructions'] = DEFAULT_ASSISTANTS[0]['instructions']
            bot['description'] = DEFAULT_ASSISTANTS[0]['description']
            changed = True
    ids = {b['id'] for b in store.state['assistants']}
    if store.state.get('primary_bot_id') not in ids:
        store.state['primary_bot_id'] = 'companion' if 'companion' in ids else next(iter(ids), None)
        changed = True
    if changed:
        store.save()
    else:
        store._sync_to_workspace()


PER_NODE_BOT_FIELDS = ('curiosity_enabled', 'notify', 'model', 'pinned', 'pinned_at', 'hidden')


def _norm_node_source(source: str | None = None) -> str:
    s = str(source or '').strip().lower()
    if s in ('pc', 'dsh', 'windows', 'win', 'desktop'):
        return 'pc'
    if s in ('pc2', 'dsh2', 'windows2', 'win2'):
        return 'pc2'
    if s in ('phone2', 'android2', 'mobile2', 'pad', 'tablet'):
        return 'phone2'
    return 'phone'


def resolve_bot_for_source(bot: dict, source: str | None = None) -> dict:
    """
    Returns a view of `bot` combining:
    - Global shared persona fields (`id`, `name`, `title`, `description`, `instructions`, `color`, `shape`, `avatar_version`, `tools`, `engine`)
    - Per-node independent fields (`curiosity_enabled`, `notify`, `model`, `pinned`, `pinned_at`, `hidden`) from `bot['node_settings'][norm_src]`
    """
    if not isinstance(bot, dict):
        return bot
    norm_src = _norm_node_source(source)
    out = dict(bot)
    ns_all = bot.get('node_settings')
    ns = ns_all.get(norm_src) if isinstance(ns_all, dict) else None
    if isinstance(ns, dict):
        for k in PER_NODE_BOT_FIELDS:
            if k in ns and ns[k] is not None:
                out[k] = ns[k]
    return out


def resolve_bots_for_source(bots: list[dict], source: str | None = None) -> list[dict]:
    if not isinstance(bots, list):
        return []
    return [resolve_bot_for_source(b, source) for b in bots if isinstance(b, dict)]


def get_assistant(store, bot_id='companion', source: str | None = None):
    store.sync_from_workspace()
    if not isinstance(store.state.get('assistants'), list) or not store.state['assistants']:
        ensure_assistants(store)
    q = str(bot_id or 'companion').strip()
    bot = next((b for b in store.state['assistants'] if b['id'] == q or b['name'] == q), None)
    if bot is None:
        q_low = q.lower()
        bot = next((b for b in store.state['assistants'] if b['id'].lower() == q_low or b['name'].lower() == q_low), None)
    if bot is None:
        raise ValueError('助手不存在')
    if source is not None:
        return resolve_bot_for_source(bot, source)
    return bot


def save_assistant(store, data:AssistantInput, bot_id=None, source: str | None = None):
    norm_src = _norm_node_source(source)
    canonical_name_to_id = {d['name'].lower(): d['id'] for d in DEFAULT_ASSISTANTS}
    target_id = bot_id
    if not target_id:
        existing = next(
            (b for b in store.state.get('assistants', []) if isinstance(b, dict) and str(b.get('name') or '').strip().lower() == data.name.strip().lower()),
            None,
        )
        if existing:
            target_id = existing['id']
    if target_id:
        bot = get_assistant(store, target_id)
        values = data.model_dump(exclude={'id', 'source'})
        if 'title' not in data.model_fields_set:
            values.pop('title', None)
        if 'tools' in data.model_fields_set:
            bot['_tools_customized'] = set(values.get('tools') or []) != set(TOOL_NAMES)
        ns_map = bot.setdefault('node_settings', {})
        cur_ns = ns_map.setdefault(norm_src, {
            'curiosity_enabled': bool(bot.get('curiosity_enabled', bot.get('id') == 'companion')) if norm_src == 'phone' else False,
            'notify': bool(bot.get('notify', True)),
            'model': str(bot.get('model') or ''),
            'pinned': bool(bot.get('pinned', False)),
            'pinned_at': float(bot.get('pinned_at') or 0),
            'hidden': bool(bot.get('hidden', False)),
        })
        # Extract per-node independent fields (`curiosity_enabled`, `notify`, `model`) so changing them on phone
        # NEVER overwrites tablet (`phone2`) or PC (`pc` / `pc2`), while global persona (`name`, `instructions`, `description`, `color`, `shape`) is shared!
        for nk in ('curiosity_enabled', 'notify', 'model'):
            if nk in data.model_fields_set or nk not in cur_ns:
                cur_ns[nk] = values.get(nk, cur_ns.get(nk))
            values.pop(nk, None)
        if norm_src == 'phone':
            # Keep legacy root fields synced with primary phone for backward-compatible callers that don't pass source
            bot['curiosity_enabled'] = bool(cur_ns.get('curiosity_enabled', False))
            bot['notify'] = bool(cur_ns.get('notify', True))
            bot['model'] = str(cur_ns.get('model') or '')
        bot.update(values, updated_at=time.time())
    else:
        if len(store.state['assistants']) >= 30: raise ValueError('最多创建 30 位助手')
        req_id = re.sub(r'[^a-zA-Z0-9_-]', '', str(data.id or '')).lower()
        if not req_id and data.name.strip().lower() in canonical_name_to_id:
            req_id = canonical_name_to_id[data.name.strip().lower()]
        existing_ids = {b.get('id') for b in store.state.get('assistants', []) if isinstance(b, dict)}
        new_id = req_id if (req_id and req_id not in existing_ids) else secrets.token_hex(8)
        values = data.model_dump(exclude={'id', 'source'})
        node_settings = {}
        for nk in ('phone', 'phone2', 'pc', 'pc2'):
            node_settings[nk] = {
                'curiosity_enabled': bool(values.get('curiosity_enabled', False)) if nk == norm_src else False,
                'notify': bool(values.get('notify', True)),
                'model': str(values.get('model') or ''),
                'pinned': False,
                'pinned_at': 0,
                'hidden': False,
            }
        bot = {'id':new_id, **values, 'node_settings': node_settings, 'created_at':time.time(), 'pinned':False, 'hidden':False, 'pinned_at':0, 'avatar_version':''}
        store.state['assistants'].append(bot)
    deleted_list = store.state.get('deleted_bot_ids')
    if isinstance(deleted_list, list) and bot['id'] in deleted_list:
        store.state['deleted_bot_ids'] = [x for x in deleted_list if x != bot['id']]
    store.save()
    return resolve_bot_for_source(bot, norm_src)

class AssistantPlacement(BaseModel):
    model_config = ConfigDict(extra='forbid')
    pinned: StrictBool = False
    hidden: StrictBool = False
    source: str | None = None

    @model_validator(mode='after')
    def has_change(self):
        if not (self.model_fields_set - {'source'}):
            raise ValueError('至少指定置顶或隐藏状态')
        return self

def place_assistant(store, bot_id:str, data:AssistantPlacement, source: str | None = None):
    """Home placement is independent per client node (`phone`, `phone2`, `pc`, `pc2`)."""
    norm_src = _norm_node_source(source or data.source)
    bot = get_assistant(store, bot_id)
    ns_map = bot.setdefault('node_settings', {})
    cur_ns = ns_map.setdefault(norm_src, {
        'curiosity_enabled': bool(bot.get('curiosity_enabled', bot.get('id') == 'companion')) if norm_src == 'phone' else False,
        'notify': bool(bot.get('notify', True)),
        'model': str(bot.get('model') or ''),
        'pinned': bool(bot.get('pinned', False)),
        'pinned_at': float(bot.get('pinned_at') or 0),
        'hidden': bool(bot.get('hidden', False)),
    })
    changes = data.model_dump(exclude_unset=True, exclude={'source'})
    if 'pinned' in changes and changes['pinned'] != cur_ns.get('pinned', False):
        cur_ns['pinned_at'] = time.time() if changes['pinned'] else 0
    cur_ns.update(changes)
    if norm_src == 'phone':
        bot['pinned'] = bool(cur_ns.get('pinned', False))
        bot['pinned_at'] = float(cur_ns.get('pinned_at') or 0)
        bot['hidden'] = bool(cur_ns.get('hidden', False))
    store.save()
    return resolve_bot_for_source(bot, norm_src)

class RoutineInput(BaseModel):
    goal: str = Field(min_length=1, max_length=3000)
    bot_id: str = Field('research', max_length=40)
    interval_hours: int = Field(24, ge=1, le=720)
    enabled: bool = True

class RoutineUpdate(BaseModel):
    enabled: bool


