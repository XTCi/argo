import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest
import asyncio

# config.py must be imported first so api/ is on sys.path
import argo.config  # noqa: F401


@pytest.mark.asyncio
async def test_inmemory_session_repo_save_and_get_memory():
    from argo.adapters.repos import InMemorySessionRepo
    from app.domain.models.memory import Memory

    repo = InMemorySessionRepo(initial_messages=[{"role": "user", "content": "hi"}])
    mem = Memory()
    mem.add_message({"role": "assistant", "content": "hello"})
    await repo.save_memory("sess1", "base", mem)

    result = await repo.get_memory("sess1", "base")
    assert result.messages == [{"role": "assistant", "content": "hello"}]


@pytest.mark.asyncio
async def test_inmemory_session_repo_get_memory_unknown_returns_empty():
    from argo.adapters.repos import InMemorySessionRepo
    from app.domain.models.memory import Memory

    repo = InMemorySessionRepo(initial_messages=[])
    result = await repo.get_memory("sess1", "unknown_agent")
    assert isinstance(result, Memory)
    assert result.messages == []


@pytest.mark.asyncio
async def test_checkpoint_repo_save_and_get_latest():
    from argo.adapters.repos import InMemoryCheckpointRepo
    from app.domain.models.checkpoint import Checkpoint

    repo = InMemoryCheckpointRepo()
    c = Checkpoint(session_id="s1", turn_id="t1", filepath="/f.py", content="old")
    await repo.save(c)
    latest = await repo.get_latest("s1", "/f.py")
    assert latest is not None
    assert latest.content == "old"


@pytest.mark.asyncio
async def test_checkpoint_repo_delete_by_session():
    from argo.adapters.repos import InMemoryCheckpointRepo
    from app.domain.models.checkpoint import Checkpoint

    repo = InMemoryCheckpointRepo()
    c = Checkpoint(session_id="s1", turn_id="t1", filepath="/f.py", content="x")
    await repo.save(c)
    await repo.delete_by_session("s1")
    assert await repo.get_latest("s1", "/f.py") is None


@pytest.mark.asyncio
async def test_inmemory_uow_context_manager():
    from argo.adapters.repos import InMemorySessionRepo, InMemoryCheckpointRepo, NoopFileRepo
    from argo.adapters.uow import InMemoryUoW

    session_repo = InMemorySessionRepo(initial_messages=[])
    checkpoint_repo = InMemoryCheckpointRepo()
    file_repo = NoopFileRepo()
    uow = InMemoryUoW(session_repo, checkpoint_repo, file_repo)

    async with uow as u:
        assert u is uow
        await u.commit()   # must not raise
        await u.rollback() # must not raise


@pytest.mark.asyncio
async def test_uow_factory_shares_same_repo_instances():
    from argo.adapters.repos import InMemorySessionRepo, InMemoryCheckpointRepo, NoopFileRepo
    from argo.adapters.uow import InMemoryUoW
    from app.domain.models.memory import Memory

    session_repo = InMemorySessionRepo(initial_messages=[])
    checkpoint_repo = InMemoryCheckpointRepo()
    uow_factory = lambda: InMemoryUoW(session_repo, checkpoint_repo, NoopFileRepo())

    mem = Memory()
    mem.add_message({"role": "user", "content": "test"})

    async with uow_factory() as u1:
        await u1.session.save_memory("s", "agent", mem)

    async with uow_factory() as u2:
        result = await u2.session.get_memory("s", "agent")
        assert result.messages[0]["content"] == "test"
