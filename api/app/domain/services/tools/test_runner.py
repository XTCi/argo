from __future__ import annotations
import asyncio
import re
from typing import Optional

from app.domain.models.tool_result import ToolResult
from .base import BaseTool, tool


class TestRunnerTool(BaseTool):
    """测试运行工具 —— 直接调用 pytest，返回结构化结果。"""
    name: str = "test_runner"

    def __init__(self, cwd: str = ".") -> None:
        super().__init__()
        self._cwd = cwd

    @tool(
        name="run_tests",
        description=(
            "Run pytest and return structured results: passed, failed, errors, skipped, failure_details. "
            "Use after making code changes to verify correctness. "
            "Check failure_details to understand what broke and fix it."
        ),
        parameters={
            "path": {
                "type": "string",
                "description": "Directory or test file to run (default: '.' runs all tests)",
            },
            "pattern": {
                "type": "string",
                "description": "Filter tests by name substring (passed to pytest -k)",
            },
            "timeout": {
                "type": "integer",
                "description": "Max seconds to wait before killing pytest (default: 60)",
            },
            "verbose": {
                "type": "boolean",
                "description": "Include full pytest output in result (default: false)",
            },
        },
        required=[],
    )
    async def run_tests(
        self,
        path: str = ".",
        pattern: Optional[str] = None,
        timeout: int = 60,
        verbose: bool = False,
    ) -> ToolResult:
        cmd = ["python", "-m", "pytest", path, "-v", "--tb=short", "--no-header"]
        if pattern:
            cmd.extend(["-k", pattern])

        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=self._cwd,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            output = stdout.decode(errors="replace")
            parsed = _parse_pytest_output(output)
            if verbose:
                parsed["full_output"] = output[:5000]
            return ToolResult(
                success=proc.returncode == 0,
                data=parsed,
                message=f"exit code {proc.returncode}",
            )
        except asyncio.TimeoutError:
            if proc is not None:
                try:
                    proc.kill()
                    await proc.communicate()
                except Exception:
                    pass
            return ToolResult(success=False, data={}, message=f"Tests timed out after {timeout}s")
        except Exception as e:
            return ToolResult(success=False, data={}, message=str(e))


def _parse_pytest_output(output: str) -> dict:
    """从 pytest 输出中提取结构化数据。"""
    result = {
        "passed": 0,
        "failed": 0,
        "errors": 0,
        "skipped": 0,
        "failure_details": "",
    }
    for pattern, key in [
        (r"(\d+) passed", "passed"),
        (r"(\d+) failed", "failed"),
        (r"(\d+) error", "errors"),
        (r"(\d+) skipped", "skipped"),
    ]:
        m = re.search(pattern, output)
        if m:
            result[key] = int(m.group(1))
    fail_match = re.search(
        r"={5,} FAILURES ={5,}\n(.*?)(?=\n={5,}|\Z)", output, re.DOTALL
    )
    if fail_match:
        result["failure_details"] = fail_match.group(1)[:3000]
    return result
