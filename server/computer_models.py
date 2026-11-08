from typing import Any, Literal
from pydantic import BaseModel, Field

WIDTH, HEIGHT = 1280, 800


class RunInput(BaseModel):
    goal: str = Field(min_length=1, max_length=6000)
    base_url: str = Field('', max_length=500)
    api_key: str = Field('', max_length=4000)
    model: str = Field('', max_length=200)
    max_steps: int = Field(24, ge=1, le=60)


class InputAction(BaseModel):
    kind: Literal[
        'click', 'double_click', 'right_click', 'move', 'hover', 'drag',
        'scroll', 'key', 'hotkey', 'type', 'launch_app', 'bash', 'batch',
        'focus_window', 'close_window', 'tile_windows', 'clipboard',
        'click_ref', 'type_ref', 'wait', 'find_text',
    ]
    x: int = Field(0, ge=0, le=4096)
    y: int = Field(0, ge=0, le=4096)
    x2: int = Field(0, ge=0, le=4096)
    y2: int = Field(0, ge=0, le=4096)
    nx: int = Field(-1, ge=-1, le=1000)
    ny: int = Field(-1, ge=-1, le=1000)
    nx2: int = Field(-1, ge=-1, le=1000)
    ny2: int = Field(-1, ge=-1, le=1000)
    grid: str = Field('', max_length=16)
    grid2: str = Field('', max_length=16)
    ref: str = Field('', max_length=32)
    target: str = Field('', max_length=200)
    text: str = Field('', max_length=16000)
    command: str = Field('', max_length=16000)
    key: str = Field('', max_length=60)
    app: str = Field('', max_length=80)
    direction: Literal['up', 'down', 'left', 'right'] = 'down'
    amount: int = Field(3, ge=1, le=100)
    timeout: int = Field(35, ge=1, le=120)
    steps: list[dict[str, Any]] = Field(default_factory=list, max_length=25)
    include_frame: bool = True


class NavigateInput(BaseModel):
    url: str = Field(min_length=4, max_length=3000)
    include_frame: bool = True


class SteerInput(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class UserComputerTask(BaseModel):
    goal: str = Field(min_length=1, max_length=6000)
    bot_id: str = Field('research', max_length=40)
    max_steps: int = Field(24, ge=1, le=60)
