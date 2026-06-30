from __future__ import annotations
from datetime import datetime, timezone
from typing import Dict
from pydantic import BaseModel, Field
import uuid

class Checkpoint(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str
    turn_id: str
    filepath: str
    content: str          # 文件内容快照
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
