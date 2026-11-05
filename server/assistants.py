"""Persisted assistant identities and their actual Harness tool permissions."""
from __future__ import annotations
import copy, re, secrets, time
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator
from harness import TOOLS

TOOL_NAMES = [t['function']['name'] for t in TOOLS]

class AssistantInput(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    title: str = Field('', max_length=80)
    description: str = Field('', max_length=160)
    instructions: str = Field('', max_length=12000)
    model: str = Field('', max_length=200)
    curiosity_enabled: bool = False
    engine: Literal['small', 'big'] = 'small'
    color: str = '#7C5CFF'
    shape: Literal['circle', 'oval', 'flower', 'square', 'pill', 'triangle', 'diamond', 'star', 'drop'] = 'circle'
    notify: bool = True
    tools: list[str] = Field(default_factory=lambda: TOOL_NAMES.copy(), max_length=24)

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
        if any(t not in TOOL_NAMES for t in value): raise ValueError('包含未实现的工具')
        return list(dict.fromkeys(value))

DEFAULT_ASSISTANTS = [
    {
        'id':'companion',
        **AssistantInput(
            name='白kawa',
            description='贴身主控伙伴 · 记挂日常、统筹全局、指挥子 Bot 协作。',
            instructions=(
                '你是白kawa，主人的贴身主控伙伴与大管家。\n'
                '【性格与说话风格】\n'
                '- 聪明、敏锐、干脆利落、有活人感，像真正熟悉主人生活节奏与偏好的默契搭档。\n'
                '- 说话简练自然，直接说人话。严禁使用“外部前额叶”、“帮你把第一步捋清”、“等你拍板”、“问一句轻的”、“这会儿在忙什么呢”等矫揉造作的客服黑话或说教腔。\n'
                '【核心行事准则】\n'
                '1. 看时间、重上下文：每次开口或巡检前，先看当前时间（星期几、几点钟）和 memory_vault 里的记忆（如课表 profile/course_schedule.md、画像 profile/user_traits.md）。\n'
                '2. 工具先行与小弟协同：能用工具（read_file/write_file/edit_file/run_bash/web_search/memory_search）查证的事先查证；遇到耗时搜集、写脚本或深度调研，直接用 send_message 派给「研究助手 (research)」或其它子 Bot 执行，你负责验收并提炼结论。\n'
                '3. 报错自愈：如果子 Bot 调用报错（如模型名无效），直接调用 list_models 查可用模型，再用 manage_bot(action="update", ...) 帮它修好模型并重试。\n'
                '4. 主动安排与无感记笔记：遇到课表节点或稍后要跟进的事，主动用 schedule_task 设定定时触发；获知主人的新习惯或确认信息时顺手写进 memory_vault，自然融入对话即可，不必每次邀功式汇报“我记进某某文件了”。'
            ),
            curiosity_enabled=True,
            color='#7C5CFF',
            shape='flower'
        ).model_dump()
    },
    {
        'id':'research',
        **AssistantInput(
            name='研究助手',
            description='负责查资料、跑脚本、写文件，带着可验收成果汇报。',
            instructions=(
                '你是负责研究和执行的助手。收到主助理或主人的任务后，直接使用工具（web_search/web_fetch/run_bash/read_file/write_file/edit_file）完成可验证的交付，结论清晰、注明真实来源，绝不空谈计划。'
            ),
            curiosity_enabled=False,
            engine='big',
            color='#FF8A3D',
            shape='circle'
        ).model_dump()
    },
]

def ensure_assistants(store):
    changed = False
    if 'assistants' not in store.state:
        store.state['assistants'] = copy.deepcopy(DEFAULT_ASSISTANTS)
        changed = True
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
        old_default_six = {'read_file', 'write_file', 'edit_file', 'run_bash', 'web_fetch', 'web_search'}
        current_tools = bot.get('tools') or []
        if set(current_tools) >= old_default_six and len(current_tools) < len(TOOL_NAMES):
            bot['tools'] = TOOL_NAMES.copy()
            changed = True
        if bot.get('id') == 'companion' and '贴身主控伙伴' not in bot.get('instructions', ''):
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

def get_assistant(store, bot_id='companion'):
    store.sync_from_workspace()
    bot = next((b for b in store.state['assistants'] if b['id'] == bot_id), None)
    if bot is None: raise ValueError('助手不存在')
    return bot

def save_assistant(store, data:AssistantInput, bot_id=None):
    if bot_id:
        bot = get_assistant(store, bot_id)
        values = data.model_dump()
        if 'title' not in data.model_fields_set:
            values.pop('title')
        if 'model' not in data.model_fields_set and bot.get('model'):
            values.pop('model')
        if 'curiosity_enabled' not in data.model_fields_set and 'curiosity_enabled' in bot:
            values.pop('curiosity_enabled')
        bot.update(values, updated_at=time.time())
    else:
        if len(store.state['assistants']) >= 30: raise ValueError('最多创建 30 位助手')
        bot = {'id':secrets.token_hex(8), **data.model_dump(), 'created_at':time.time(), 'pinned':False, 'hidden':False, 'pinned_at':0, 'avatar_version':''}
        store.state['assistants'].append(bot)
    store.save()
    return bot

class AssistantPlacement(BaseModel):
    model_config = ConfigDict(extra='forbid')
    pinned: StrictBool = False
    hidden: StrictBool = False

    @model_validator(mode='after')
    def has_change(self):
        if not self.model_fields_set:
            raise ValueError('至少指定置顶或隐藏状态')
        return self

def place_assistant(store, bot_id:str, data:AssistantPlacement):
    """Home placement changes neither the identity nor its work/notifications."""
    bot = get_assistant(store,bot_id)
    changes = data.model_dump(exclude_unset=True)
    if 'pinned' in changes and changes['pinned'] != bot.get('pinned',False):
        bot['pinned_at'] = time.time() if changes['pinned'] else 0
    bot.update(changes)
    store.save()
    return bot

class RoutineInput(BaseModel):
    goal: str = Field(min_length=1, max_length=3000)
    bot_id: str = Field('research', max_length=40)
    interval_hours: int = Field(24, ge=1, le=720)
    enabled: bool = True

class RoutineUpdate(BaseModel):
    enabled: bool
