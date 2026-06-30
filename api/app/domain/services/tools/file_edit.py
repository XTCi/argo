from __future__ import annotations
from pathlib import Path
from typing import Optional
from app.domain.models.tool_result import ToolResult
from .base import BaseTool, tool


class FileEditTool(BaseTool):
    """文件读写工具 —— 支持读取、完整写入、精确 patch（old_str → new_str）。"""
    name: str = "file_edit"

    @tool(
        name="read_file",
        description="读取文件内容。大文件可指定 start_line/end_line 范围。",
        parameters={
            "filepath": {"type": "string", "description": "文件路径"},
            "start_line": {"type": "integer", "description": "起始行号（1-based，可选）"},
            "end_line": {"type": "integer", "description": "结束行号（1-based，可选）"},
        },
        required=["filepath"],
    )
    async def read_file(
        self, filepath: str, start_line: Optional[int] = None, end_line: Optional[int] = None
    ) -> ToolResult:
        try:
            content = Path(filepath).read_text(encoding="utf-8", errors="replace")
            if start_line or end_line:
                lines = content.splitlines()
                s = (start_line or 1) - 1
                e = end_line or len(lines)
                content = "\n".join(lines[s:e])
            return ToolResult(success=True, data=content)
        except Exception as e:
            return ToolResult(success=False, message=str(e))

    @tool(
        name="write_file",
        description="写入文件（完整覆盖）。写入前应先用 read_file 确认当前内容。",
        parameters={
            "filepath": {"type": "string", "description": "文件路径"},
            "content": {"type": "string", "description": "写入的完整内容"},
        },
        required=["filepath", "content"],
    )
    async def write_file(self, filepath: str, content: str) -> ToolResult:
        try:
            path = Path(filepath)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            return ToolResult(success=True, data={"bytes_written": len(content.encode())})
        except Exception as e:
            return ToolResult(success=False, message=str(e))

    @tool(
        name="patch_file",
        description=(
            "Replace text in a file. Provide either:\n"
            "  - old_str + new_str for a single replacement, OR\n"
            "  - replacements: [{old_str, new_str}, ...] for multiple replacements in one call.\n"
            "Each old_str must appear exactly once. All replacements are validated before writing "
            "(fail-fast — file is unchanged if any replacement fails)."
        ),
        parameters={
            "filepath": {"type": "string", "description": "Path to the file to patch"},
            "old_str": {"type": "string", "description": "Text to replace (single replacement)"},
            "new_str": {"type": "string", "description": "Replacement text (single replacement)"},
            "replacements": {
                "type": "array",
                "description": "Multiple replacements applied in order",
                "items": {
                    "type": "object",
                    "properties": {
                        "old_str": {"type": "string"},
                        "new_str": {"type": "string"},
                    },
                    "required": ["old_str", "new_str"],
                },
            },
        },
        required=["filepath"],
    )
    async def patch_file(
        self,
        filepath: str,
        old_str: Optional[str] = None,
        new_str: Optional[str] = None,
        replacements: Optional[list] = None,
    ) -> ToolResult:
        try:
            # Normalise to replacements list
            if replacements is None:
                if old_str is None:
                    return ToolResult(
                        success=False,
                        message="Provide either old_str/new_str or replacements",
                    )
                replacements = [{"old_str": old_str, "new_str": new_str or ""}]

            content = Path(filepath).read_text(encoding="utf-8")

            # Validate ALL replacements before writing (fail-fast)
            seen_old_strs: set[str] = set()
            for i, rep in enumerate(replacements, 1):
                o = rep.get("old_str", "")
                if o in seen_old_strs:
                    return ToolResult(
                        success=False,
                        message=f"Replacement {i}: old_str is duplicated in replacements list",
                    )
                count = content.count(o)
                if count == 0:
                    return ToolResult(
                        success=False,
                        message=f"Replacement {i}: old_str not found in {filepath}",
                    )
                if count > 1:
                    return ToolResult(
                        success=False,
                        message=f"Replacement {i}: old_str appears {count} times — must be unique",
                    )
                seen_old_strs.add(o)

            # Apply all replacements
            for rep in replacements:
                content = content.replace(rep["old_str"], rep.get("new_str", ""), 1)

            Path(filepath).write_text(content, encoding="utf-8")
            return ToolResult(success=True, data={"replacements_applied": len(replacements)})
        except Exception as e:
            return ToolResult(success=False, message=str(e))
