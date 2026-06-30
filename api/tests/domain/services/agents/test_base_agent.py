from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.domain.services.agents.base import BaseAgent
from app.domain.models.event import MessageEvent, DoneEvent, ToolEvent, ErrorEvent
from app.domain.models.app_config import AgentConfig
from app.domain.models.tool_result import ToolResult


def build_agent(llm_response: dict) -> BaseAgent:
    """Helper: construct a fully-mocked BaseAgent for unit testing."""
    mock_llm = AsyncMock()
    mock_llm.invoke = AsyncMock(return_value=llm_response)

    mock_orchestrator = AsyncMock()
    mock_turn_ctx = MagicMock()
    mock_turn_ctx.messages = []
    mock_turn_ctx.active_system_prompt = "You are a coding agent."
    mock_turn_ctx.turn_id = "t1"
    mock_turn_ctx.iteration_budget = MagicMock()
    mock_turn_ctx.iteration_budget.consume.return_value = True
    mock_orchestrator.build_turn_context = AsyncMock(return_value=mock_turn_ctx)

    mock_executor = AsyncMock()
    mock_executor.reset_turn = MagicMock()

    # Use a real ToolResult so Pydantic validation passes in ToolEvent
    real_result = ToolResult(success=True, data="output")
    mock_executor.execute = AsyncMock(return_value=(real_result, MagicMock()))
    mock_executor.execute_batch = AsyncMock(return_value=[(real_result, MagicMock())])

    # AgentConfig — context_length is not yet in the model (added in Task 11)
    config = AgentConfig(max_iterations=10, max_retries=3)

    return BaseAgent(
        llm=mock_llm,
        json_parser=AsyncMock(invoke=AsyncMock(side_effect=lambda x: {})),
        agent_config=config,
        tools=[],
        turn_orchestrator=mock_orchestrator,
        tool_executor=mock_executor,
        checkpoint_service=AsyncMock(),
        context_engine=MagicMock(update_from_response=MagicMock()),
        memory=MagicMock(build_context_block=MagicMock(return_value="")),
        uow_factory=lambda: AsyncMock(),
        session_id="s1",
    )


@pytest.mark.asyncio
async def test_agent_emits_message_event_on_text_response():
    """Agent should yield exactly one MessageEvent and one DoneEvent for a plain text LLM response."""
    agent = build_agent({"role": "assistant", "content": "Here is the fix.", "tool_calls": []})
    events = []
    async for e in agent.run("fix the bug", []):
        events.append(e)

    message_events = [e for e in events if isinstance(e, MessageEvent)]
    done_events = [e for e in events if isinstance(e, DoneEvent)]

    assert len(message_events) == 1
    assert message_events[0].message == "Here is the fix."
    assert len(done_events) == 1


@pytest.mark.asyncio
async def test_agent_emits_done_event_always():
    """DoneEvent must always be the final event even when content is empty."""
    agent = build_agent({"role": "assistant", "content": "", "tool_calls": []})
    events = []
    async for e in agent.run("hello", []):
        events.append(e)

    assert isinstance(events[-1], DoneEvent)


@pytest.mark.asyncio
async def test_agent_emits_tool_events_for_tool_call():
    """Agent should emit CALLING + CALLED ToolEvents when LLM returns a tool_call,
    then call LLM again for the final text response."""
    tool_call_response = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call-1",
                "function": {"name": "shell_execute", "arguments": '{"command": "ls"}'},
            }
        ],
    }
    final_text_response = {
        "role": "assistant",
        "content": "Done.",
        "tool_calls": [],
    }

    mock_llm = AsyncMock()
    mock_llm.invoke = AsyncMock(side_effect=[tool_call_response, final_text_response])

    mock_orchestrator = AsyncMock()
    mock_turn_ctx = MagicMock()
    mock_turn_ctx.messages = []
    mock_turn_ctx.active_system_prompt = "sys"
    mock_turn_ctx.turn_id = "t2"
    mock_turn_ctx.iteration_budget = MagicMock()
    mock_turn_ctx.iteration_budget.consume.return_value = True
    mock_orchestrator.build_turn_context = AsyncMock(return_value=mock_turn_ctx)

    real_result = ToolResult(success=True, data="shell output")
    mock_executor = AsyncMock()
    mock_executor.reset_turn = MagicMock()
    mock_executor.execute = AsyncMock(return_value=(real_result, MagicMock()))
    mock_executor.execute_batch = AsyncMock(return_value=[(real_result, MagicMock())])

    config = AgentConfig(max_iterations=10, max_retries=3)

    agent = BaseAgent(
        llm=mock_llm,
        json_parser=AsyncMock(invoke=AsyncMock(return_value={"command": "ls"})),
        agent_config=config,
        tools=[],
        turn_orchestrator=mock_orchestrator,
        tool_executor=mock_executor,
        checkpoint_service=AsyncMock(),
        context_engine=MagicMock(update_from_response=MagicMock()),
        memory=MagicMock(build_context_block=MagicMock(return_value="")),
        uow_factory=lambda: AsyncMock(),
        session_id="s2",
    )

    events = []
    async for e in agent.run("run ls", []):
        events.append(e)

    tool_events = [e for e in events if isinstance(e, ToolEvent)]
    message_events = [e for e in events if isinstance(e, MessageEvent)]
    done_events = [e for e in events if isinstance(e, DoneEvent)]

    assert len(tool_events) == 2  # CALLING + CALLED
    assert len(message_events) == 1
    assert message_events[0].message == "Done."
    assert len(done_events) == 1


@pytest.mark.asyncio
async def test_agent_emits_error_on_iteration_limit():
    """Agent should emit ErrorEvent when iteration budget is exhausted."""
    mock_llm = AsyncMock()
    mock_llm.invoke = AsyncMock(return_value={
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": "c1", "function": {"name": "shell_execute", "arguments": "{}"}}
        ],
    })

    mock_orchestrator = AsyncMock()
    mock_turn_ctx = MagicMock()
    mock_turn_ctx.messages = []
    mock_turn_ctx.active_system_prompt = "sys"
    mock_turn_ctx.turn_id = "t3"
    mock_turn_ctx.iteration_budget = MagicMock()
    # First consume succeeds, second returns False (budget exhausted)
    mock_turn_ctx.iteration_budget.consume.side_effect = [True, False]
    mock_orchestrator.build_turn_context = AsyncMock(return_value=mock_turn_ctx)

    real_result = ToolResult(success=True, data="output")
    mock_executor = AsyncMock()
    mock_executor.reset_turn = MagicMock()
    mock_executor.execute = AsyncMock(return_value=(real_result, MagicMock()))
    mock_executor.execute_batch = AsyncMock(return_value=[(real_result, MagicMock())])

    config = AgentConfig(max_iterations=1, max_retries=3)

    agent = BaseAgent(
        llm=mock_llm,
        json_parser=AsyncMock(invoke=AsyncMock(return_value={})),
        agent_config=config,
        tools=[],
        turn_orchestrator=mock_orchestrator,
        tool_executor=mock_executor,
        checkpoint_service=AsyncMock(),
        context_engine=MagicMock(update_from_response=MagicMock()),
        memory=MagicMock(build_context_block=MagicMock(return_value="")),
        uow_factory=lambda: AsyncMock(),
        session_id="s3",
    )

    events = []
    async for e in agent.run("go forever", []):
        events.append(e)

    error_events = [e for e in events if isinstance(e, ErrorEvent)]
    done_events = [e for e in events if isinstance(e, DoneEvent)]

    assert len(error_events) >= 1
    assert "Iteration limit reached" in error_events[0].error
    assert len(done_events) == 1
