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
    kind: Literal['click', 'double_click', 'right_click', 'move', 'scroll', 'key', 'type', 'launch_app', 'bash', 'batch']
    x: int = Field(0, ge=0, lt=WIDTH)
    y: int = Field(0, ge=0, lt=HEIGHT)
    text: str = Field('', max_length=16000)
    command: str = Field('', max_length=16000)
    key: str = Field('', max_length=40)
    app: str = Field('', max_length=60)
    direction: Literal['up', 'down'] = 'down'
    amount: int = Field(3, ge=1, le=12)
    timeout: int = Field(25, ge=1, le=90)
    steps: list[dict[str, Any]] = Field(default_factory=list, max_length=20)
    include_frame: bool = True

class NavigateInput(BaseModel):
    url: str = Field(min_length=8, max_length=3000)
    include_frame: bool = True

class SteerInput(BaseModel):
    text: str = Field(min_length=1, max_length=4000)

class UserComputerTask(BaseModel):
    goal: str = Field(min_length=1, max_length=6000)
    bot_id: str = Field('research', max_length=40)
    max_steps: int = Field(24, ge=1, le=60)
