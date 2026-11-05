"""Synced personal Bot identity and the actual proactive point of contact."""
from __future__ import annotations
import base64, binascii, hashlib, io, time
import httpx
from typing import Literal
from fastapi import Depends, HTTPException
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from assistants import AssistantInput, get_assistant
from vault_manager import atomic_write


class IdentityInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(default='', max_length=40)
    title: str = Field(default='', max_length=80)
    color: str = '#7C5CFF'
    shape: Literal['circle', 'oval', 'flower', 'square', 'pill', 'triangle', 'diamond', 'star', 'drop'] = 'circle'
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

    @field_validator('prompt', 'model')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('描述和图像模型不能为空')
        return value.strip()


def clean_avatar(value: str) -> tuple[str, str]:
    """Only a small decoded raster reaches clients; EXIF/text metadata is discarded."""
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
        config = store.config.big_brain
        if not config.api_key:
            raise HTTPException(409, '先在执行大脑中保存图像服务的 API Key 和 Base URL')
        try:
            async with httpx.AsyncClient(timeout=150, trust_env=False) as client:
                response = await client.post(config.base_url + '/images/generations',
                    headers={'Authorization':'Bearer ' + config.api_key},
                    json={'model':data.model, 'prompt':data.prompt, 'n':1, 'size':'1024x1024'})
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
                        'width':image.width, 'height':image.height, 'model':data.model}
        except HTTPException:
            raise
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError, OSError, Image.DecompressionBombError) as error:
            raise HTTPException(502, '图像生成失败：' + str(error)[:180])

    @app.put('/api/bots/primary', dependencies=[Depends(auth)])
    async def replace_primary(data: PrimaryInput):
        try:
            get_assistant(store, data.bot_id)
        except ValueError as error:
            raise HTTPException(404, str(error))
        store.state['primary_bot_id'] = data.bot_id
        store.save()
        await publish({'type':'bots_changed', 'primary_bot_id':data.bot_id})
        return {'primary_bot_id':data.bot_id, 'bots':store.state['assistants']}

    @app.patch('/api/bots/{bot_id}/identity', dependencies=[Depends(auth)])
    async def update_identity(bot_id: str, data: IdentityInput):
        try:
            bot = get_assistant(store, bot_id)
        except ValueError as error:
            raise HTTPException(404, str(error))
        changes = data.model_dump(exclude_unset=True, exclude={'image'})
        old_version = bot.get('avatar_version', '')
        if 'image' in data.model_fields_set:
            if data.image:
                try:
                    version, image = clean_avatar(data.image)
                except ValueError as error:
                    raise HTTPException(422, str(error))
                avatar_dir.mkdir(exist_ok=True)
                atomic_write(avatar_dir / (bot_id + '-' + version + '.txt'), image)
                changes['avatar_version'] = version
            else:
                changes['avatar_version'] = ''
        bot.update(changes, updated_at=time.time())
        store.save()
        if old_version and old_version != bot.get('avatar_version'):
            (avatar_dir / (bot_id + '-' + old_version + '.txt')).unlink(missing_ok=True)
        await publish({'type':'bots_changed', 'bot_id':bot_id})
        return bot

    @app.get('/api/bots/{bot_id}/avatar', dependencies=[Depends(auth)])
    async def get_avatar(bot_id: str):
        try:
            bot = get_assistant(store, bot_id)
        except ValueError as error:
            raise HTTPException(404, str(error))
        version = bot.get('avatar_version', '')
        path = avatar_dir / (bot_id + '-' + version + '.txt')
        if not version or not path.is_file():
            raise HTTPException(404, '尚未设置图片头像')
        return {'version':version, 'image':path.read_text('utf-8')}
