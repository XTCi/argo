from __future__ import annotations
import pytest
from unittest.mock import AsyncMock, MagicMock
from app.domain.services.runtime.memory import ThreeLayerMemory
from app.domain.models.memory import EpisodicEntry


@pytest.fixture
def mock_uow():
    uow = AsyncMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=None)
    uow.session = AsyncMock()
    uow.session.get_memory = AsyncMock(return_value=None)
    uow.session.save_memory = AsyncMock()
    return uow


@pytest.fixture
def mock_uow_factory(mock_uow):
    return lambda: mock_uow


def test_working_memory_add_and_get(mock_uow_factory):
    memory = ThreeLayerMemory(session_id="s1", uow_factory=mock_uow_factory)
    memory.working.add_message({"role": "user", "content": "hello"})
    msgs = memory.working.get_messages()
    assert len(msgs) == 1
    assert msgs[0]["content"] == "hello"


def test_working_memory_rollback(mock_uow_factory):
    memory = ThreeLayerMemory(session_id="s1", uow_factory=mock_uow_factory)
    memory.working.add_message({"role": "user", "content": "msg1"})
    memory.working.add_message({"role": "assistant", "content": "msg2"})
    memory.working.roll_back()
    assert len(memory.working.get_messages()) == 1


@pytest.mark.asyncio
async def test_add_episodic_entry(mock_uow_factory):
    memory = ThreeLayerMemory(session_id="s1", uow_factory=mock_uow_factory)
    await memory.add_episodic("Summary of first task", message_count=10)
    assert len(memory.episodic_entries) == 1
    assert memory.episodic_entries[0].summary == "Summary of first task"


@pytest.mark.asyncio
async def test_upsert_semantic_entry(mock_uow_factory):
    memory = ThreeLayerMemory(session_id="s1", uow_factory=mock_uow_factory)
    await memory.upsert_semantic("auth_design", "JWT with refresh tokens")
    await memory.upsert_semantic("auth_design", "JWT with refresh tokens v2")
    assert len(memory.semantic_entries) == 1
    assert memory.semantic_entries[0].content == "JWT with refresh tokens v2"


def test_build_context_block(mock_uow_factory):
    """context_block 应把 episodic + semantic 拼成可注入 prompt 的文本"""
    memory = ThreeLayerMemory(session_id="s1", uow_factory=mock_uow_factory)
    memory.episodic_entries.append(
        EpisodicEntry(session_id="s1", summary="Did X", message_count=5)
    )
    block = memory.build_context_block()
    assert "Did X" in block
