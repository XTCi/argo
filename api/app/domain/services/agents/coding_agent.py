# api/app/domain/services/agents/coding_agent.py
from __future__ import annotations

from typing import Callable

from app.domain.external.llm import LLM
from app.domain.external.json_parser import JSONParser
from app.domain.models.app_config import AgentConfig
from app.domain.repositories.uow import IUnitOfWork
from app.domain.services.agents.base import BaseAgent
from app.domain.services.runtime.context_engine import DefaultContextEngine, ContextEngineConfig
from app.domain.services.runtime.memory import ThreeLayerMemory
from app.domain.services.runtime.todo_store import TodoStore
from app.domain.services.runtime.turn import TurnOrchestrator
from app.domain.services.runtime.tool_executor import ToolExecutor
from app.domain.services.runtime.checkpoint import CheckpointService
from app.domain.services.runtime.workspace import WorkspaceContext
from app.domain.services.tools.shell import ShellTool
from app.domain.services.tools.shell_session import PersistentShellSession
from app.domain.services.tools.file_edit import FileEditTool
from app.domain.services.tools.code_search import CodeSearchTool
from app.domain.services.tools.git import GitTool
from app.domain.services.tools.test_runner import TestRunnerTool
from app.domain.services.tools.todo import TodoTool
from app.domain.services.prompts.coding import CODING_AGENT_SYSTEM_PROMPT


def build_coding_agent(
    llm: LLM,
    json_parser: JSONParser,
    agent_config: AgentConfig,
    uow_factory: Callable[[], IUnitOfWork],
    session_id: str,
    workspace: WorkspaceContext,
    shell_session: PersistentShellSession | None = None,
    pre_execute_hook=None,
    project_context: str = "",
    stream_callback: Callable[[str], None] | None = None,   # NEW
    stream_reset: Callable[[], None] | None = None,         # NEW
) -> BaseAgent:
    """工厂函数：组装完整的 CodingAgent（所有 runtime 组件连线）。"""
    cwd = workspace.cwd

    todo_store = TodoStore()

    # Use provided session or create a temporary one (for backwards compat / tests)
    _session = shell_session or PersistentShellSession(cwd=cwd)

    tools = [
        ShellTool(session=_session, cwd=cwd),
        FileEditTool(),
        CodeSearchTool(cwd=cwd),
        GitTool(cwd=cwd),
        TestRunnerTool(cwd=cwd),
        TodoTool(todo_store=todo_store),
    ]

    context_length = agent_config.context_length

    context_engine = DefaultContextEngine(
        config=ContextEngineConfig(
            context_length=context_length,
            threshold_percent=0.75,
        ),
        llm=llm,
        protect_first_n=3,
        protect_last_n=6,
    )
    memory = ThreeLayerMemory(session_id=session_id, uow_factory=uow_factory)

    active_guidance = project_context + CODING_AGENT_SYSTEM_PROMPT

    turn_orchestrator = TurnOrchestrator(
        workspace=workspace,
        memory=memory,
        context_engine=context_engine,
        max_iterations=agent_config.max_iterations,
        guidance=active_guidance,
        todo_store=todo_store,
    )
    tool_executor = ToolExecutor(
        tools=tools,
        context_engine=context_engine,
        pre_execute_hook=pre_execute_hook,
    )
    checkpoint_service = CheckpointService(session_id=session_id, uow_factory=uow_factory)

    return BaseAgent(
        llm=llm,
        json_parser=json_parser,
        agent_config=agent_config,
        tools=tools,
        turn_orchestrator=turn_orchestrator,
        tool_executor=tool_executor,
        checkpoint_service=checkpoint_service,
        context_engine=context_engine,
        memory=memory,
        uow_factory=uow_factory,
        session_id=session_id,
        stream_callback=stream_callback,   # NEW
        stream_reset=stream_reset,         # NEW
    )
