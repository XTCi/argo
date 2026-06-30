from __future__ import annotations
import threading
import pytest
from app.domain.services.runtime.turn import IterationBudget

def test_iteration_budget_consume():
    budget = IterationBudget(max_total=3)
    assert budget.consume() is True
    assert budget.consume() is True
    assert budget.consume() is True
    assert budget.consume() is False   # 超出配额

def test_iteration_budget_refund():
    budget = IterationBudget(max_total=2)
    budget.consume()
    budget.consume()
    assert budget.consume() is False
    budget.refund()
    assert budget.consume() is True    # refund 后可再消费

def test_iteration_budget_remaining():
    budget = IterationBudget(max_total=5)
    budget.consume()
    budget.consume()
    assert budget.remaining == 3
    assert budget.used == 2

def test_iteration_budget_thread_safe():
    """并发 consume 不超出配额"""
    budget = IterationBudget(max_total=10)
    results = []
    def worker():
        results.append(budget.consume())
    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count(True) == 10
    assert results.count(False) == 10


# ---------------------------------------------------------------------------
# TurnOrchestrator tests
# ---------------------------------------------------------------------------

import uuid
from unittest.mock import MagicMock, AsyncMock, patch
from app.domain.services.runtime.turn import TurnOrchestrator, TurnContext
from app.domain.services.runtime.workspace import WorkspaceContext
from app.domain.services.runtime.memory import ThreeLayerMemory
from app.domain.services.runtime.context_engine import DefaultContextEngine, ContextEngineConfig


def make_workspace() -> WorkspaceContext:
    return WorkspaceContext(
        cwd="/tmp/proj", is_git_repo=True, branch="main",
        git_status="", recent_commits="", manifests=(), verify_commands=("pytest",),
    )


def make_memory():
    mem = MagicMock(spec=ThreeLayerMemory)
    mem.build_context_block.return_value = ""
    return mem


def make_engine():
    engine = MagicMock(spec=DefaultContextEngine)
    engine.should_compress.return_value = False
    return engine


@pytest.mark.asyncio
async def test_build_turn_context_returns_dataclass():
    orchestrator = TurnOrchestrator(
        workspace=make_workspace(),
        memory=make_memory(),
        context_engine=make_engine(),
        max_iterations=10,
    )
    ctx = await orchestrator.build_turn_context(
        user_message="fix the bug",
        session_messages=[],
        session_id="s1",
    )
    assert isinstance(ctx, TurnContext)
    assert ctx.user_message == "fix the bug"
    assert ctx.iteration_budget.max_total == 10
    assert "Workspace" in ctx.active_system_prompt


@pytest.mark.asyncio
async def test_build_turn_context_triggers_preflight_compression():
    engine = make_engine()
    engine.should_compress.return_value = True
    engine.compress = AsyncMock(return_value=[{"role": "system", "content": "compressed"}])
    orchestrator = TurnOrchestrator(
        workspace=make_workspace(),
        memory=make_memory(),
        context_engine=engine,
        max_iterations=10,
    )
    messages = [{"role": "user", "content": "old"}, {"role": "assistant", "content": "old"}]
    ctx = await orchestrator.build_turn_context("new msg", messages, "s1")
    engine.compress.assert_called_once()
    assert len(ctx.messages) <= len(messages) + 1
