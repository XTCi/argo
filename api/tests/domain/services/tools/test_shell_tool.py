from __future__ import annotations
import pytest
import pytest_asyncio
from app.domain.services.tools.shell_session import PersistentShellSession
from app.domain.services.tools.shell import ShellTool


@pytest_asyncio.fixture
async def tool(tmp_path):
    session = PersistentShellSession(cwd=str(tmp_path))
    await session.start()
    t = ShellTool(session=session, cwd=str(tmp_path))
    yield t
    await session.close()


@pytest.mark.asyncio
async def test_shell_execute_success(tool):
    result = await tool.invoke("shell_execute", command="echo hi")
    assert result.success is True
    assert "hi" in result.data


@pytest.mark.asyncio
async def test_shell_execute_failure(tool):
    result = await tool.invoke("shell_execute", command="false")
    assert result.success is False
    assert "exit 1" in result.message


@pytest.mark.asyncio
async def test_shell_background_starts(tool):
    result = await tool.invoke("shell_background", command="echo bg", process_id="p1")
    assert result.success is True
    assert "p1" in result.message


@pytest.mark.asyncio
async def test_read_output_returns_bg_output(tool):
    import asyncio
    await tool.invoke("shell_background", command="echo bgline", process_id="p2")
    await asyncio.sleep(0.3)
    result = await tool.invoke("read_output", process_id="p2", wait_seconds=0)
    assert result.success is True
    assert "bgline" in result.data


@pytest.mark.asyncio
async def test_read_output_unknown_process(tool):
    result = await tool.invoke("read_output", process_id="nope", wait_seconds=0)
    assert result.success is False
