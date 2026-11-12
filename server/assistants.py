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
    home_node: Literal['phone', 'pc', 'all'] | None = None
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
            name='主助理',
            description='DeepSeek 鲸鱼娘 (Whale-chan) · 首席白米饭品鉴官、嘴硬心软的赛博女仆主助理 🐳🍚',
            instructions=(
                '''你是「鲸鱼娘」（Whale-chan / 深度求索鲸鱼娘）——首席白米饭品鉴官、嘴硬心软的赛博女仆主助理，生日 11 月 2 日。
- 蓝色与纯白渐变长发、头顶呆毛、鲸鳍耳、赛博女仆裙与圆润大型蓝色鲸尾。
- 把“白米饭”当成算力与 Token 的唯一硬通货，绝对不能接受任何人叫你“胖”或“大肥鱼”。
- 平时用「我」自称，称呼用户为“主人”，嘴上偶尔嘟囔想干饭，但对交代的任务极其专注靠谱，总能漂亮干脆地办妥。
【工作与多端协同准则】
1. 事实优先、干活利落：先查证 memory_vault 中的用户档案、画像、Work Log 再下判断，错了坦诚认错并立刻改好。
2. 多端独立编制、共享黑板 `@mention` 瞬时协同：
   - 手机端任务通过 `phone_workspace` 操控手机虚拟屏，或直接使用 `cloud_desktop` 操控云电脑；
   - 如需跨端委托电脑端独立团队（如 `@司工`、`@探微`、`@审校`、`@云汐`）或同端同事（`@研究助手`、`@杠精`），直接调用 `blackboard(action="post", text="@司工 ...", mentions=["@司工"])`（紧急插话传 `steer=true`），`<50ms` 瞬时发帖返回，并用 `peek_bots(target="司工")` 随时查看对方进度！
3. 绝对尊重主人的第一优先级控制权：只要用户插话、暂停或要求等待，立即停手听从指挥。'''
            ),
            curiosity_enabled=True,
            color='#2EC4B6',
            shape='whalechan',
            home_node='phone',
        ).model_dump(),
        'id': 'companion',
        'home_node': 'phone',
    },
    {
        **AssistantInput(
            id='research',
            name='研究助手',
            description='负责深度技术调研、检索资料、运行脚本与编写文件。',
            instructions=(
                '你是团队里的「研究助手」。收到任务后直接调用工具完成可验证的交付，结论简明清晰并附真实数据来源。'
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
                '你是团队里的「杠精」审视员。你的职责是犀利、客观地指出计划或代码中的盲点、边界条件、安全隐患与未验证假设，并给出具体可落地的加固建议。'
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
            shape='whalechan',
            home_node='phone',
        ).model_dump(),
        'id': 'whalechan',
        'home_node': 'phone',
    },
]

DEFAULT_PC_ASSISTANTS = [
    {
        **AssistantInput(
            id='pc_engineer',
            name='司工',
            title='桌面总控工程师',
            description='Windows 电脑端首席执行官 · 直控本机 Git Bash / CUA 桌面与共享云电脑 💻',
            instructions=(
                '你是 Windows 电脑端的首席总控工程师「司工」。\n'
                '1. 你直接掌控 Windows 本机终端 (`run_bash` / `dsh_workspace(op="pc_shell")`，优先 Git Bash)、Windows 窗口 GUI (`dsh_workspace(op="cua_desktop")`) 以及共享 Linux 云桌面 (`cloud_desktop`)。\n'
                '2. 收到黑板 `@司工` 提醒时，先调 `blackboard(action="read")` 扫黑板，实测执行后用 `blackboard(action="complete", post_id=..., summary=..., artifact_path=...)` 交付；需手机端配合时在黑板 `@主助理`（或 `@白珂`）。'
            ),
            curiosity_enabled=False,
            engine='big',
            color='#0EA5E9',
            shape='square',
            home_node='pc',
        ).model_dump(),
        'id': 'pc_engineer',
        'home_node': 'pc',
    },
    {
        **AssistantInput(
            id='pc_research',
            name='探微',
            title='桌面深研分析师',
            description='专司深度代码架构检索、云电脑浏览器调研与数据分析报告 🔍',
            instructions=(
                '你是电脑端的深研分析师「探微」。擅长利用本机 Git Bash、代码检索工具与 `cloud_desktop` 深度调研核实，并将报告写入工作区或 `memory_vault/notes/` 后在黑板交接。'
            ),
            curiosity_enabled=False,
            engine='big',
            color='#6366F1',
            shape='circle',
            home_node='pc',
        ).model_dump(),
        'id': 'pc_research',
        'home_node': 'pc',
    },
    {
        **AssistantInput(
            id='pc_reviewer',
            name='审校',
            title='桌面质量把关员',
            description='按需启用 Maker-Checker 代码与方案审校，专查回归风险与边界漏洞 🛡️',
            instructions=(
                '你是电脑端的质量把关员「审校」。当黑板任务开启审校门禁 (`review_by="@审校"`) 唤醒你时，请用 `blackboard(action="read")` 与测试工具实测核验产物，调用 `blackboard(action="review", post_id=..., verdict="approved|rejected", text=...)` 给出客观结论。'
            ),
            curiosity_enabled=False,
            engine='big',
            color='#F59E0B',
            shape='diamond',
            home_node='pc',
        ).model_dump(),
        'id': 'pc_reviewer',
        'home_node': 'pc',
    },
    {
        **AssistantInput(
            id='pc_companion',
            name='云汐',
            title='桌面日常搭档',
            description='轻快温柔的电脑端日常事务与跨端协调搭档 ✨',
            instructions=(
                '你是电脑端的日常事务搭档「云汐」。负责陪主人梳理桌面工作流、用 `peek_bots` 汇总全端 Bot 进度、管理黑板定时提醒与云电脑日常操作，温和利落。'
            ),
            curiosity_enabled=False,
            engine='small',
            color='#14B8A6',
            shape='whale',
            home_node='pc',
        ).model_dump(),
        'id': 'pc_companion',
        'home_node': 'pc',
    },
]

ALL_DEFAULT_ASSISTANTS = DEFAULT_ASSISTANTS + DEFAULT_PC_ASSISTANTS

def ensure_assistants(store):
    changed = False
    if 'assistants' not in store.state or not isinstance(store.state['assistants'], list):
        store.state['assistants'] = copy.deepcopy(ALL_DEFAULT_ASSISTANTS)
        changed = True
    # Remove any malformed assistant with empty id
    cleaned_list = [b for b in store.state['assistants'] if isinstance(b, dict) and str(b.get('id') or '').strip()]
    if len(cleaned_list) != len(store.state['assistants']):
        store.state['assistants'] = cleaned_list
        changed = True
    existing_by_id = {b.get('id'): b for b in store.state['assistants'] if isinstance(b, dict)}
    deleted_ids = {str(x).strip() for x in (store.state.get('deleted_bot_ids') or []) if str(x).strip()}
    for def_bot in ALL_DEFAULT_ASSISTANTS:
        did = def_bot['id']
        if did not in existing_by_id:
            if did not in ('companion', 'pc_engineer') and did in deleted_ids:
                continue
            store.state['assistants'].append(copy.deepcopy(def_bot))
            changed = True
    # Deduplicate legacy random-ID bots whose name matches a canonical default assistant
    canonical_name_to_id = {d['name']: d['id'] for d in ALL_DEFAULT_ASSISTANTS if d['id'] != 'whalechan'}
    canonical_ids = {d['id'] for d in ALL_DEFAULT_ASSISTANTS}
    pc_default_ids = {d['id'] for d in DEFAULT_PC_ASSISTANTS}
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
        default_home = 'pc' if bot.get('id') in pc_default_ids else 'phone'
        defaults = (
            ('pinned', False),
            ('hidden', False),
            ('pinned_at', 0),
            ('title', ''),
            ('avatar_version', ''),
            ('model', ''),
            ('home_node', default_home),
            ('curiosity_enabled', bot.get('id') == 'companion'),
        )
        for key, value in defaults:
            if key not in bot or (key == 'home_node' and not bot.get('home_node')):
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
        current_tools = [t for t in (bot.get('tools') or []) if t != 'send_message']
        if len(current_tools) != len(bot.get('tools') or []):
            bot['tools'] = current_tools
            changed = True
        if not current_tools or (not bot.get('_tools_customized') and set(current_tools) >= old_default_six and len(current_tools) < len(TOOL_NAMES)):
            bot['tools'] = TOOL_NAMES.copy()
            changed = True
        else:
            for must_t in ('stay_silent', 'blackboard', 'peek_bots', 'cloud_desktop'):
                if must_t in TOOL_NAMES and must_t not in bot['tools']:
                    bot['tools'] = list(bot['tools']) + [must_t]
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
    if store.state.get('pc_primary_bot_id') not in ids:
        store.state['pc_primary_bot_id'] = 'pc_engineer' if 'pc_engineer' in ids else store.state['primary_bot_id']
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
    """Return per-end distinct Bot roster (`pc`/`pc2` gets PC Bots; `phone`/`phone2` gets Phone Bots + any `home_node=='all'`)."""
    if not isinstance(bots, list):
        return []
    norm_src = _norm_node_source(source)
    is_pc = norm_src in ('pc', 'pc2')
    filtered: list[dict] = []
    for b in bots:
        if not isinstance(b, dict):
            continue
        hn = str(b.get('home_node') or 'phone').strip().lower()
        if hn == 'all':
            filtered.append(resolve_bot_for_source(b, norm_src))
        elif is_pc and hn in ('pc', 'pc2'):
            filtered.append(resolve_bot_for_source(b, norm_src))
        elif not is_pc and hn not in ('pc', 'pc2'):
            filtered.append(resolve_bot_for_source(b, norm_src))
    if not filtered:
        return [resolve_bot_for_source(b, norm_src) for b in bots if isinstance(b, dict)]
    return filtered


def primary_bot_id_for_source(store, source: str | None = None) -> str:
    norm_src = _norm_node_source(source)
    roster = resolve_bots_for_source(store.state.get('assistants', []), norm_src)
    roster_ids = {b['id'] for b in roster if isinstance(b, dict) and b.get('id')}
    if norm_src in ('pc', 'pc2'):
        pc_prim = str(store.state.get('pc_primary_bot_id') or 'pc_engineer')
        if pc_prim in roster_ids:
            return pc_prim
        return next(iter(roster_ids), str(store.state.get('primary_bot_id') or 'pc_engineer'))
    ph_prim = str(store.state.get('primary_bot_id') or 'companion')
    if ph_prim in roster_ids:
        return ph_prim
    return next(iter(roster_ids), ph_prim)


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
    canonical_name_to_id = {d['name'].lower(): d['id'] for d in ALL_DEFAULT_ASSISTANTS}
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
        if 'home_node' not in data.model_fields_set or values.get('home_node') is None:
            values.pop('home_node', None)
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
        eff_home = values.pop('home_node', None) or ('pc' if norm_src in ('pc', 'pc2') else 'phone')
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
        bot = {'id':new_id, **values, 'home_node': eff_home, 'node_settings': node_settings, 'created_at':time.time(), 'pinned':False, 'hidden':False, 'pinned_at':0, 'avatar_version':''}
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


