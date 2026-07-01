from __future__ import annotations
import asyncio
import re
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

    @tool(
        name="find_symbol",
        description="按名称查找函数或类定义的位置。返回文件路径和行号。用于快速定位代码定义，避免搜索整个目录。",
        parameters={
            "name": {"type": "string", "description": "函数名或类名"},
            "path": {"type": "string", "description": "搜索目录（默认 '.'）"},
        },
        required=["name"],
    )
    async def find_symbol(self, name: str, path: str = ".") -> ToolResult:
        pattern = rf"(def|class)\s+{re.escape(name)}\b"
        cmd = ["grep", "-rn", "-E", pattern, path]
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self._cwd,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
            result = stdout.decode(errors="replace").strip()
            if not result:
                return ToolResult(success=False, message=f"Symbol '{name}' not found")
            return ToolResult(success=True, data=result)
        except asyncio.TimeoutError:
            if proc:
                try:
                    proc.kill()
                    await proc.communicate()
                except Exception:
                    pass
            return ToolResult(success=False, message="Search timed out")
        except Exception as e:
            return ToolResult(success=False, message=str(e))

    @tool(
        name="read_file_range",
        description="读取文件指定行范围（含行号）。用于查看大文件的特定函数或片段，避免读取整个文件。",
        parameters={
            "filepath": {"type": "string", "description": "文件路径（相对于项目根目录或绝对路径）"},
            "start_line": {"type": "integer", "description": "起始行（从 1 开始）"},
            "end_line": {"type": "integer", "description": "结束行（含）"},
        },
        required=["filepath", "start_line", "end_line"],
    )
    async def read_file_range(self, filepath: str, start_line: int, end_line: int) -> ToolResult:
        try:
            p = (
                Path(filepath)
                if Path(filepath).is_absolute()
                else Path(self._cwd) / filepath
            )
            lines = p.read_text(errors="replace").splitlines()
            s = max(0, start_line - 1)
            e = min(len(lines), end_line)
            if s >= len(lines):
                return ToolResult(
                    success=False,
                    message=f"start_line {start_line} exceeds file length {len(lines)}",
                )
            selected = lines[s:e]
            numbered = "\n".join(
                f"{s + i + 1:4d}  {line}" for i, line in enumerate(selected)
            )
            return ToolResult(success=True, data=numbered)
        except FileNotFoundError:
            return ToolResult(success=False, message=f"File not found: {filepath}")
        except Exception as e:
            return ToolResult(success=False, message=str(e))
