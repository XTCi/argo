from __future__ import annotations
import json
from typing import TYPE_CHECKING

from app.domain.models.tool_result import ToolResult
from .base import BaseTool, tool

if TYPE_CHECKING:
    from app.domain.services.runtime.todo_store import TodoStore


class TodoTool(BaseTool):
    """Agent 任务列表工具 —— 写入/读取 in-session todo 状态。"""
    name: str = "todo"

    def __init__(self, todo_store: "TodoStore") -> None:
        super().__init__()
        self._store = todo_store

    @tool(
        name="todo_write",
        description=(
            "Replace the full task list. Use at the start of a task to create your plan, "
            "and update status (pending → in_progress → done) as you work. "
            "Always call todo_write before starting a new sub-task."
        ),
        parameters={
            "todos": {
                "type": "array",
                "description": "Full replacement list of tasks",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "Unique short identifier, e.g. '1' or 'write-tests'"},
                        "content": {"type": "string", "description": "Task description (max 500 chars)"},
                        "status": {
                            "type": "string",
                            "enum": ["pending", "in_progress", "done"],
                            "description": "Current status",
                        },
                    },
                    "required": ["id", "content", "status"],
                },
            }
        },
        required=["todos"],
    )
    async def todo_write(self, todos: list) -> ToolResult:
        items = self._store.write(todos)
        return ToolResult(success=True, data=json.dumps([i.model_dump() for i in items]))

    @tool(
        name="todo_read",
        description="Read the current task list to check progress.",
        parameters={},
        required=[],
    )
    async def todo_read(self) -> ToolResult:
        items = self._store.read()
        return ToolResult(success=True, data=json.dumps([i.model_dump() for i in items]))
