from __future__ import annotations
import asyncio
from app.domain.models.tool_result import ToolResult
from .base import BaseTool, tool


class ShellTool(BaseTool):
    """Shell 工具 —— 直接 subprocess 执行，无需 Docker sandbox。"""
    name: str = "shell"

    def __init__(self, cwd: str = ".") -> None:
        super().__init__()
        self._cwd = cwd

    @tool(
        name="shell_execute",
        description="在项目目录执行 shell 命令。用于运行测试、安装依赖、文件操作。",
        parameters={
            "command": {"type": "string", "description": "要执行的 shell 命令"},
            "timeout": {"type": "integer", "description": "超时秒数（默认 30）"},
        },
        required=["command"],
    )
    async def shell_execute(self, command: str, timeout: int = 30) -> ToolResult:
        proc = None
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=self._cwd,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            output = stdout.decode(errors="replace")
            return ToolResult(
                success=proc.returncode == 0,
                data=output,
                message=f"exit code {proc.returncode}",
            )
        except asyncio.TimeoutError:
            try:
                if proc:
                    proc.kill()
                    await proc.communicate()
            except Exception:
                pass
            return ToolResult(success=False, message=f"Command timed out after {timeout}s")
        except Exception as e:
            return ToolResult(success=False, message=str(e))
