from __future__ import annotations
import asyncio
from typing import Optional
from app.domain.models.tool_result import ToolResult
from .base import BaseTool, tool


class GitTool(BaseTool):
    """Git 操作工具 —— 只读操作（status/diff/log），写操作需用户确认。"""
    name: str = "git"

    def __init__(self, cwd: str = ".") -> None:
        super().__init__()
        self._cwd = cwd

    async def _git(self, *args: str) -> ToolResult:
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=self._cwd,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            output = stdout.decode(errors="replace")
            return ToolResult(success=proc.returncode == 0, data=output)
        except asyncio.TimeoutError:
            try:
                if proc:
                    proc.kill()
                    await proc.communicate()
            except Exception:
                pass
            return ToolResult(success=False, message="Git command timed out")
        except Exception as e:
            return ToolResult(success=False, message=str(e))

    @tool(
        name="git_status",
        description="查看 git 工作区状态。",
        parameters={},
        required=[],
    )
    async def git_status(self) -> ToolResult:
        return await self._git("status", "--short")

    @tool(
        name="git_diff",
        description="查看文件变更 diff。",
        parameters={
            "filepath": {"type": "string", "description": "指定文件路径（可选）"},
        },
        required=[],
    )
    async def git_diff(self, filepath: Optional[str] = None) -> ToolResult:
        args = ["diff"]
        if filepath:
            args.append(filepath)
        return await self._git(*args)

    @tool(
        name="git_log",
        description="查看最近提交历史。",
        parameters={
            "n": {"type": "integer", "description": "显示最近 n 条提交（默认 10）"},
        },
        required=[],
    )
    async def git_log(self, n: int = 10) -> ToolResult:
        return await self._git("log", f"-{n}", "--pretty=%h %an %s")
