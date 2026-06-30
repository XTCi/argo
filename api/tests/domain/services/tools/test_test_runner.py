import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.domain.services.tools.test_runner import TestRunnerTool, _parse_pytest_output


# --- Unit tests for the parser (no subprocess) ---

def test_parse_extracts_passed_count():
    output = "5 passed, 1 failed in 2.3s"
    result = _parse_pytest_output(output)
    assert result["passed"] == 5
    assert result["failed"] == 1


def test_parse_extracts_failure_details():
    output = (
        "===== FAILURES =====\n"
        "FAILED test_foo.py::test_bar - AssertionError\n"
        "===================="
    )
    result = _parse_pytest_output(output)
    assert "test_foo" in result["failure_details"]


def test_parse_returns_zeros_on_empty_output():
    result = _parse_pytest_output("")
    assert result["passed"] == 0
    assert result["failed"] == 0
    assert result["failure_details"] == ""


# --- Integration-style tests with mocked subprocess ---

@pytest.mark.asyncio
async def test_run_tests_returns_structured_result_on_success():
    tool = TestRunnerTool(cwd="/tmp")
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(b"3 passed in 0.4s", b""))

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
         patch("asyncio.wait_for", new=AsyncMock(return_value=(b"3 passed in 0.4s", b""))):
        result = await tool.invoke("run_tests", path=".")

    assert result.success
    assert isinstance(result.data, dict)
    assert result.data["passed"] == 3


@pytest.mark.asyncio
async def test_run_tests_returns_failure_on_nonzero_exit():
    tool = TestRunnerTool(cwd="/tmp")
    mock_proc = MagicMock()
    mock_proc.returncode = 1
    mock_proc.communicate = AsyncMock(return_value=(b"1 failed in 0.2s", b""))

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
         patch("asyncio.wait_for", new=AsyncMock(return_value=(b"1 failed in 0.2s", b""))):
        result = await tool.invoke("run_tests")

    assert not result.success
    assert result.data["failed"] == 1


@pytest.mark.asyncio
async def test_run_tests_kills_process_on_timeout():
    tool = TestRunnerTool(cwd="/tmp")
    mock_proc = MagicMock()
    mock_proc.kill = MagicMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
         patch("asyncio.wait_for", side_effect=asyncio.TimeoutError()):
        result = await tool.invoke("run_tests", timeout=1)

    assert not result.success
    assert "timed out" in result.message
    mock_proc.kill.assert_called_once()
