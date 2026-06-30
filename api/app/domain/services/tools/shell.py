from __future__ import annotations

from app.domain.models.tool_result import ToolResult
from app.domain.services.tools.base import BaseTool, tool
from app.domain.services.tools.shell_session import PersistentShellSession


class ShellTool(BaseTool):
    """Shell 工具 — 三个工具共享一个持久 bash session。"""
    name: str = "shell"

    def __init__(self, session: PersistentShellSession, cwd: str = ".") -> None:
        super().__init__()
        self._session = session
        self._cwd = cwd

    @tool(
        name="shell_execute",
        description="在项目目录执行 shell 命令。命令在同一个持久 bash session 中运行，cd/export 等状态跨调用保持。",
        parameters={
            "command": {"type": "string", "description": "要执行的 shell 命令"},
            "timeout": {"type": "integer", "description": "超时秒数（默认 30）"},
        },
        required=["command"],
    )
    async def shell_execute(self, command: str, timeout: int = 30) -> ToolResult:
        output, code = await self._session.run(command, timeout=timeout)
        return ToolResult(
            success=(code == 0),
            data=output,
            message=f"exit {code}",
        )

    @tool(
        name="shell_background",
        description="在后台启动一个长时间运行的命令（如开发服务器）。用 process_id 标识，之后用 read_output 获取输出。",
        parameters={
            "command": {"type": "string", "description": "要在后台执行的命令"},
            "process_id": {"type": "string", "description": "自定义标识符，如 'dev-server'"},
        },
        required=["command", "process_id"],
    )
    async def shell_background(self, command: str, process_id: str) -> ToolResult:
        await self._session.run_background(command, process_id)
        return ToolResult(success=True, message=f"started as {process_id}")

    @tool(
        name="read_output",
        description="读取后台进程的当前输出缓冲区。",
        parameters={
            "process_id": {"type": "string", "description": "shell_background 时使用的标识符"},
            "wait_seconds": {"type": "number", "description": "等待新输出的秒数（默认 2.0）"},
        },
        required=["process_id"],
    )
    async def read_output(self, process_id: str, wait_seconds: float = 2.0) -> ToolResult:
        out = await self._session.read_output(process_id, wait_seconds=wait_seconds)
        if out == "" and not self._session.has_process(process_id):
            return ToolResult(success=False, message=f"Unknown process_id: {process_id}")
        return ToolResult(success=True, data=out)
