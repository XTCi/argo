import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock
from app.domain.services.runtime.tool_executor import ToolExecutor, ToolResultKind
from app.domain.models.tool_result import ToolResult


@pytest.fixture
def executor():
    mock_tool = MagicMock()
    mock_tool.has_tool = MagicMock(return_value=True)
    mock_tool.invoke = AsyncMock(return_value=ToolResult(success=True, data="ok"))
    mock_engine = MagicMock()
    return ToolExecutor(tools=[mock_tool], context_engine=mock_engine)


@pytest.mark.asyncio
async def test_execute_batch_empty_returns_empty(executor):
    results = await executor.execute_batch([])
    assert results == []


@pytest.mark.asyncio
async def test_execute_batch_single_call_returns_list_of_one(executor):
    results = await executor.execute_batch([
        {"tool_name": "grep_files", "arguments": {"pattern": "foo", "path": "."}}
    ])
    assert len(results) == 1
    result, kind = results[0]
    assert isinstance(result, ToolResult)
    assert kind == ToolResultKind.SEARCH


@pytest.mark.asyncio
async def test_execute_batch_preserves_result_order(executor):
    calls = [
        {"tool_name": "grep_files", "arguments": {}},
        {"tool_name": "list_dir", "arguments": {}},
        {"tool_name": "grep_files", "arguments": {}},
    ]
    results = await executor.execute_batch(calls)
    assert len(results) == 3
    # All three are SEARCH kind
    for _, kind in results:
        assert kind == ToolResultKind.SEARCH


@pytest.mark.asyncio
async def test_execute_batch_mixed_calls_completes(executor):
    calls = [
        {"tool_name": "grep_files", "arguments": {}},    # SEARCH — concurrent
        {"tool_name": "shell_execute", "arguments": {}}, # TERMINAL — serial
    ]
    results = await executor.execute_batch(calls)
    assert len(results) == 2
    _, kind0 = results[0]
    _, kind1 = results[1]
    assert kind0 == ToolResultKind.SEARCH
    assert kind1 == ToolResultKind.TERMINAL
