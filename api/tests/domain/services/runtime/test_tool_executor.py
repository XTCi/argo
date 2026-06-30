from __future__ import annotations
import pytest
from unittest.mock import AsyncMock, MagicMock
from app.domain.services.runtime.tool_executor import (
    ToolResultKind, classify_tool_result, ToolExecutor
)
from app.domain.models.tool_result import ToolResult

def test_classify_file_mutation():
    assert classify_tool_result("write_file", ToolResult(success=True)) == ToolResultKind.FILE_MUTATION
    assert classify_tool_result("patch_file", ToolResult(success=True)) == ToolResultKind.FILE_MUTATION

def test_classify_terminal():
    assert classify_tool_result("shell_execute", ToolResult(success=True)) == ToolResultKind.TERMINAL

def test_classify_search():
    assert classify_tool_result("grep_files", ToolResult(success=True)) == ToolResultKind.SEARCH
    assert classify_tool_result("find_symbol", ToolResult(success=True)) == ToolResultKind.SEARCH

def test_classify_other():
    assert classify_tool_result("git_status", ToolResult(success=True)) == ToolResultKind.OTHER

@pytest.mark.asyncio
async def test_executor_invokes_tool():
    mock_tool = MagicMock()
    mock_tool.name = "shell"
    mock_tool.has_tool = MagicMock(return_value=True)
    mock_tool.invoke = AsyncMock(return_value=ToolResult(success=True, data="output"))

    executor = ToolExecutor(tools=[mock_tool], context_engine=MagicMock())
    result, kind = await executor.execute("shell_execute", {"command": "ls"})

    mock_tool.invoke.assert_called_once_with("shell_execute", command="ls")
    assert result.success is True
    assert kind == ToolResultKind.TERMINAL
