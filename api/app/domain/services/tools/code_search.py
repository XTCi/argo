from __future__ import annotations
import asyncio
from pathlib import Path
from typing import Optional
from app.domain.models.tool_result import ToolResult
from .base import BaseTool, tool


class CodeSearchTool(BaseTool):
    """代码搜索工具 —— grep 文本搜索 + 目录列举。"""
    name: str = "code_search"

    def __init__(self, cwd: str = ".") -> None:
        super().__init__()
        self._cwd = cwd

    @tool(
        name="grep_files",
        description="在代码库中搜索文本或正则。返回匹配行及文件位置。",
        parameters={
            "pattern": {"type": "string", "description": "搜索模式（支持正则）"},
            "path": {"type": "string", "description": "搜索目录（默认项目根目录）"},
            "file_pattern": {"type": "string", "description": "文件名过滤，如 '*.py'"},
        },
        required=["pattern"],
    )
    async def grep_files(
        self, pattern: str, path: str = ".", file_pattern: Optional[str] = None
    ) -> ToolResult:
        cmd = ["grep", "-rn", "--include", file_pattern or "*", pattern, path]
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self._cwd,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
            return ToolResult(success=True, data=stdout.decode(errors="replace"))
        except asyncio.TimeoutError:
            try:
                if proc:
                    proc.kill()
                    await proc.communicate()
            except Exception:
                pass
            return ToolResult(success=False, message="Search timed out")
        except Exception as e:
            return ToolResult(success=False, message=str(e))

    @tool(
        name="list_dir",
        description="列出目录结构。",
        parameters={
            "path": {"type": "string", "description": "目录路径"},
            "depth": {"type": "integer", "description": "展开深度（默认 2）"},
        },
        required=["path"],
    )
    async def list_dir(self, path: str, depth: int = 2) -> ToolResult:
        try:
            root = Path(self._cwd) / path
            lines: list[str] = []

            def walk(p: Path, current_depth: int, prefix: str = "") -> None:
                if current_depth > depth:
                    return
                for item in sorted(p.iterdir()):
                    if item.name.startswith("."):
                        continue
                    lines.append(f"{prefix}{item.name}{'/' if item.is_dir() else ''}")
                    if item.is_dir():
                        walk(item, current_depth + 1, prefix + "  ")

            walk(root, 1)
            return ToolResult(success=True, data="\n".join(lines))
        except Exception as e:
            return ToolResult(success=False, message=str(e))
