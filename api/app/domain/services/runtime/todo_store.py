from __future__ import annotations
from app.domain.models.todo import TodoItem

_MAX_ITEMS = 50
_MAX_CONTENT_CHARS = 500
_STATUS_ICONS = {"pending": "[ ]", "in_progress": "[→]", "done": "[x]"}


class TodoStore:
    """In-session 任务列表状态。每轮注入 system prompt，session 结束清空。"""

    def __init__(self) -> None:
        self._items: list[TodoItem] = []

    def write(self, todos: list[dict]) -> list[TodoItem]:
        """替换整个任务列表（截断过长内容和超限条目数）。"""
        validated: list[TodoItem] = []
        for t in todos[:_MAX_ITEMS]:
            content = str(t.get("content", ""))
            if len(content) > _MAX_CONTENT_CHARS:
                content = content[:_MAX_CONTENT_CHARS] + "…"
            validated.append(TodoItem(
                id=str(t.get("id", "")),
                content=content,
                status=t.get("status", "pending"),
            ))
        self._items = validated
        return list(self._items)

    def read(self) -> list[TodoItem]:
        """返回当前任务列表副本。"""
        return list(self._items)

    def format_for_injection(self) -> str:
        """格式化为可注入 system prompt 的 markdown 块。"""
        if not self._items:
            return ""
        lines = ["## Current Tasks"]
        for item in self._items:
            icon = _STATUS_ICONS.get(item.status, "[ ]")
            lines.append(f"- {icon} {item.id}: {item.content}")
        return "\n".join(lines)
