import uuid
from datetime import datetime
from enum import Enum
from typing import Literal, Union, Optional, Any, Dict, Annotated

from pydantic import BaseModel, Field

from .tool_result import ToolResult


class ToolEventStatus(str, Enum):
    CALLING = "calling"
    CALLED = "called"


class BaseEvent(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    type: Literal[""] = ""
    created_at: datetime = Field(default_factory=datetime.now)


class MessageEvent(BaseEvent):
    type: Literal["message"] = "message"
    role: Literal["user", "assistant"] = "assistant"
    message: str = ""
    streamed: bool = False


class ToolEvent(BaseEvent):
    type: Literal["tool"] = "tool"
    tool_call_id: str
    tool_name: str
    function_name: str
    function_args: Dict[str, Any]
    function_result: Optional[ToolResult] = None
    status: ToolEventStatus = ToolEventStatus.CALLING


class ErrorEvent(BaseEvent):
    type: Literal["error"] = "error"
    error: str = ""


class DoneEvent(BaseEvent):
    type: Literal["done"] = "done"


Event = Annotated[
    Union[MessageEvent, ToolEvent, ErrorEvent, DoneEvent],
    Field(discriminator="type"),
]
