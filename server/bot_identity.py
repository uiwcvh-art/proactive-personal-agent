"""Synced personal Bot identity and the actual proactive point of contact."""
from __future__ import annotations
import base64, binascii, hashlib, io, time
import httpx
from typing import Literal
from pathlib import Path
from fastapi import Depends, HTTPException
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from assistants import AssistantInput, ShapeLiteral, get_assistant
from vault_manager import atomic_write

GROK_BOT_AVATAR_PROMPT_TEMPLATE = (
    'minimalist 2D Bot-style avatar, 1:1 solid charcoal-black background (#0F1014), '
    'extreme close-up head composition occupying 85%-90% of frame peeking in from lower left '
    'tilted clockwise by 20 degrees, rounded creamy-white mouthless face with faint pink oval '
    'cheek blush, two vertically oriented solid-colored 3:1 capsule eyes, clean flat vector '
    'cel-shaded illustration, no text, no watermark. Subject details: '
)


def build_grok_bot_prompt(prompt: str, style: str = 'grok_bot') -> str:
    clean_p = prompt.strip()
    if style and style.strip().lower() in ('raw', 'none', 'custom'):
        return clean_p
    low = clean_p.lower()
    if 'minimalist 2d bot-style avatar' in low or 'peeking in from lower left' in low:
        return clean_p
    if style.strip().lower() == 'grok_bot' or len(clean_p) < 160 or any(k in low for k in ('grok', 'bot', 'whale', '鲸', '兔生兔', '头像')):
        return GROK_BOT_AVATAR_PROMPT_TEMPLATE + clean_p
    return clean_p


class IdentityInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(default='', max_length=40)
    title: str = Field(default='', max_length=80)
    color: str = '#7C5CFF'
    shape: Literal['circle', 'oval', 'flower', 'square', 'pill', 'triangle', 'diamond', 'star', 'drop', 'whale', 'whalechan'] = 'circle'
    image: str = Field(default='', max_length=2_000_000)

    @field_validator('name')
    @classmethod
    def name_value(cls, value):
        return AssistantInput.clean_name(value)

    @field_validator('color')
    @classmethod
    def color_value(cls, value):
        return AssistantInput.color_hex(value)

    @field_validator('title')
    @classmethod
    def title_value(cls, value):
        return value.strip()

    @model_validator(mode='after')
    def has_change(self):
        if not self.model_fields_set:
            raise ValueError('至少修改一项身份信息')
        return self


class PrimaryInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    bot_id: str = Field(min_length=1, max_length=40)


class AvatarGenerationInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    prompt: str = Field(min_length=1, max_length=2000)
    model: str = Field(min_length=1, max_length=200)
    style: str = Field(default='grok_bot', max_length=40)

    @field_validator('prompt', 'model')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('描述和图像模型不能为空')
        return value.strip()


def resolve_preset_avatar_data_uri(preset_name: str) -> str | None:
    key = preset_name.strip().lower().removeprefix('preset:')
    mapping = {
        'whale': 'whale_grok.png',
        'big_whale': 'whale_grok.png',
        'whale_grok': 'whale_grok.png',
        'whalechan': 'whalechan_grok.png',
        'whale_chan': 'whalechan_grok.png',
        'whalechan_grok': 'whalechan_grok.png',
    }
    fname = mapping.get(key)
    if not fname:
        return None
    p = Path(__file__).resolve().parent / 'static' / 'avatars' / fname
    if not p.is_file():
        return None
    return 'data:image/png;base64,' + base64.b64encode(p.read_bytes()).decode('ascii')


def clean_avatar(value: str) -> tuple[str, str]:
    """Only a small decoded raster reaches clients; EXIF/text metadata is discarded."""
    if value.startswith('preset:'):
        resolved = resolve_preset_avatar_data_uri(value)
        if resolved:
            value = resolved
    if not value.startswith('data:image/png;base64,'):
        raise ValueError('请使用裁剪后的 PNG 头像')
    try:
        raw = base64.b64decode(value.split(',', 1)[1], validate=True)
        with Image.open(io.BytesIO(raw), formats=['PNG']) as image:
            if not (1 <= image.width <= 1024 and 1 <= image.height <= 1024):
                raise ValueError('头像边长不能超过 1024 像素')
            image.load()
            clean = Image.new('RGBA', image.size)
            clean.paste(image.convert('RGBA'))
            out = io.BytesIO()
            clean.save(out, format='PNG')
            result = out.getvalue()
        return hashlib.sha256(result).hexdigest(), 'data:image/png;base64,' + base64.b64encode(result).decode('ascii')
    except (binascii.Error, UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise ValueError('图片无法读取，请重新选择') from error


def install_identity_routes(app, auth, store, brain, publish):
    avatar_dir = store.root / 'avatars'

    @app.post('/api/bots/{bot_id}/avatar/generate', dependencies=[Depends(auth)])
    async def generate_avatar(bot_id: str, data: AvatarGenerationInput):
        try:
            get_assistant(store, bot_id)
        except ValueError as error:
            raise HTTPException(404, str(error))
        api_key = store.effective_api_key(big=True) if hasattr(store, 'effective_api_key') else (store.config.big_brain.api_key or store.config.small_brain.api_key)
        base_url = store.effective_base_url(big=True) if hasattr(store, 'effective_base_url') else (store.config.big_brain.base_url or store.config.small_brain.base_url)
        if not api_key or not base_url:
            raise HTTPException(409, '先在执行大脑中保存图像服务的 API Key 和 Base URL')
        final_prompt = build_grok_bot_prompt(data.prompt, data.style)
        try:
            async with httpx.AsyncClient(timeout=150, trust_env=False) as client:
                response = await client.post(base_url.rstrip('/') + '/images/generations',
                    headers={'Authorization':'Bearer ' + api_key},
                    json={'model':data.model, 'prompt':final_prompt, 'n':1, 'size':'1024x1024'})
            if response.status_code >= 400:
                raise HTTPException(502, f'图像服务返回 HTTP {response.status_code}；请检查图像模型和服务是否支持 Images API')
            encoded = response.json().get('data', [{}])[0].get('b64_json', '')
            if not encoded or len(encoded) > 35_000_000:
                raise ValueError('图像服务没有返回有效的 base64 图片；请选择支持图片数据返回的模型')
            raw = base64.b64decode(encoded, validate=True)
            with Image.open(io.BytesIO(raw), formats=['PNG','JPEG','WEBP']) as image:
                if not (1 <= image.width <= 4096 and 1 <= image.height <= 4096):
                    raise ValueError('生成图片尺寸超出范围')
                image.load()
                image.thumbnail((1024,1024))
                clean = Image.new('RGBA', image.size)
                clean.paste(image.convert('RGBA'))
                out = io.BytesIO(); clean.save(out, 'PNG')
                return {'image':'data:image/png;base64,' + base64.b64encode(out.getvalue()).decode(),
                        'width':image.width, 'height':image.height, 'model':data.model,
                        'style':data.style, 'prompt':final_prompt}
        except HTTPException:
            raise
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError, OSError, Image.DecompressionBombError) as error:
            raise HTTPException(502, '图像生成失败：' + str(error)[:180])

    @app.put('/api/bots/primary', dependencies=[Depends(auth)])
    async def replace_primary(data: PrimaryInput):
        try:
            bot = get_assistant(store, data.bot_id)
        except ValueError as error:
            raise HTTPException(404, str(error))
        canonical_id = str(bot['id'])
        store.state['primary_bot_id'] = canonical_id
        store.save()
        await publish({'type':'bots_changed', 'primary_bot_id':canonical_id})
        return {'primary_bot_id':canonical_id, 'bots':store.state['assistants']}

    @app.patch('/api/bots/{bot_id}/identity', dependencies=[Depends(auth)])
    async def update_identity(bot_id: str, data: IdentityInput):
        try:
            bot = get_assistant(store, bot_id)
        except ValueError as error:
            raise HTTPException(404, str(error))
        canonical_id = str(bot['id'])
        changes = data.model_dump(exclude_unset=True, exclude={'image'})
        old_version = bot.get('avatar_version', '')
        if 'image' in data.model_fields_set:
            if data.image:
                try:
                    version, image = clean_avatar(data.image)
                except ValueError as error:
                    raise HTTPException(422, str(error))
                avatar_dir.mkdir(exist_ok=True)
                atomic_write(avatar_dir / (canonical_id + '-' + version + '.txt'), image)
                changes['avatar_version'] = version
            else:
                changes['avatar_version'] = ''
        elif changes.get('shape') in ('whale', 'whalechan') and not old_version:
            preset_uri = resolve_preset_avatar_data_uri(changes['shape'])
            if preset_uri:
                version, image = clean_avatar(preset_uri)
                avatar_dir.mkdir(exist_ok=True)
                atomic_write(avatar_dir / (canonical_id + '-' + version + '.txt'), image)
                changes['avatar_version'] = version
        bot.update(changes, updated_at=time.time())
        store.save()
        if old_version and old_version != bot.get('avatar_version'):
            (avatar_dir / (canonical_id + '-' + old_version + '.txt')).unlink(missing_ok=True)
        await publish({'type':'bots_changed', 'bot_id':canonical_id})
        return bot

    @app.get('/api/bots/{bot_id}/avatar', dependencies=[Depends(auth)])
    async def get_avatar(bot_id: str):
        try:
            bot = get_assistant(store, bot_id)
        except ValueError as error:
            raise HTTPException(404, str(error))
        canonical_id = str(bot['id'])
        version = bot.get('avatar_version', '')
        path = avatar_dir / (canonical_id + '-' + version + '.txt')
        if not path.is_file() and bot_id and '/' not in bot_id and '\\' not in bot_id and '..' not in bot_id:
            alt_path = avatar_dir / (bot_id + '-' + version + '.txt')
            if alt_path.is_file():
                path = alt_path
        if not version or not path.is_file():
            if bot.get('shape') in ('whale', 'whalechan'):
                preset_uri = resolve_preset_avatar_data_uri(bot['shape'])
                if preset_uri:
                    v_hash, img_data = clean_avatar(preset_uri)
                    return {'version':v_hash, 'image':img_data}
            raise HTTPException(404, '尚未设置图片头像')
        return {'version':version, 'image':path.read_text('utf-8')}
