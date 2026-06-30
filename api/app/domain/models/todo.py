from __future__ import annotations
from typing import Literal
from pydantic import BaseModel


class TodoItem(BaseModel):
    """Agent 任务列表条目。"""
    id: str
    content: str
    status: Literal["pending", "in_progress", "done"]
