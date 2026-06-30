import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import argo.config  # noqa: F401

from app.domain.models.event import ToolEvent, ToolEventStatus, MessageEvent, ErrorEvent, DoneEvent
from app.domain.models.tool_result import ToolResult


def test_tool_event_calling_renders_spinner():
    from argo.renderer import render_event
    event = ToolEvent(
        tool_call_id="tc1",
        tool_name="shell",
        function_name="shell_execute",
        function_args={"command": "ls -la"},
        status=ToolEventStatus.CALLING,
    )
    result = render_event(event)
    assert result is not None
    assert "shell_execute" in result
    assert "⟳" in result


def test_tool_event_called_success_renders_checkmark():
    from argo.renderer import render_event
    event = ToolEvent(
        tool_call_id="tc1",
        tool_name="shell",
        function_name="shell_execute",
        function_args={"command": "ls"},
        function_result=ToolResult(success=True, message="ok"),
        status=ToolEventStatus.CALLED,
    )
    result = render_event(event)
    assert "✓" in result


def test_tool_event_called_failure_renders_x():
    from argo.renderer import render_event
    event = ToolEvent(
        tool_call_id="tc1",
        tool_name="shell",
        function_name="shell_execute",
        function_args={"command": "bad"},
        function_result=ToolResult(success=False, message="error: not found"),
        status=ToolEventStatus.CALLED,
    )
    result = render_event(event)
    assert "✗" in result


def test_message_event_renders_content():
    from argo.renderer import render_event
    event = MessageEvent(role="assistant", message="Here is the answer.")
    result = render_event(event)
    assert "Here is the answer." in result


def test_error_event_renders_error():
    from argo.renderer import render_event
    event = ErrorEvent(error="something went wrong")
    result = render_event(event)
    assert "something went wrong" in result


def test_done_event_returns_none():
    from argo.renderer import render_event
    event = DoneEvent()
    assert render_event(event) is None


def test_truncate_args_limits_to_60_chars():
    from argo.renderer import _truncate_args
    long_cmd = "x" * 100
    result = _truncate_args({"command": long_cmd})
    assert len(result) <= 63  # 60 + "..."


def test_truncate_output_folds_at_10_lines():
    from argo.renderer import _truncate_output
    text = "\n".join([f"line {i}" for i in range(20)])
    result = _truncate_output(text, max_lines=10)
    assert result.count("\n") < 15
    assert "+10 more" in result
