from __future__ import annotations
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from app.domain.services.runtime.checkpoint import CheckpointService

@pytest.fixture
def mock_uow():
    uow = AsyncMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=None)
    uow.checkpoint = AsyncMock()
    uow.checkpoint.save = AsyncMock()
    uow.checkpoint.get_latest = AsyncMock(return_value=None)
    return uow

@pytest.fixture
def uow_factory(mock_uow):
    return lambda: mock_uow

@pytest.mark.asyncio
async def test_snapshot_saves_checkpoint(uow_factory, tmp_path):
    test_file = tmp_path / "main.py"
    test_file.write_text("def hello(): pass")

    svc = CheckpointService(session_id="s1", uow_factory=uow_factory)
    await svc.snapshot(str(test_file), turn_id="t1")

    uow_factory().checkpoint.save.assert_called_once()

@pytest.mark.asyncio
async def test_snapshot_nonexistent_file_is_noop(uow_factory):
    svc = CheckpointService(session_id="s1", uow_factory=uow_factory)
    await svc.snapshot("/nonexistent/path.py", turn_id="t1")
    uow_factory().checkpoint.save.assert_not_called()

@pytest.mark.asyncio
async def test_rewind_restores_content(uow_factory, tmp_path):
    test_file = tmp_path / "app.py"
    test_file.write_text("original content")

    from app.domain.models.checkpoint import Checkpoint
    mock_checkpoint = Checkpoint(
        session_id="s1", turn_id="t0",
        filepath=str(test_file), content="original content"
    )
    uow_factory().checkpoint.get_latest = AsyncMock(return_value=mock_checkpoint)

    # 模拟文件被修改
    test_file.write_text("modified content")

    svc = CheckpointService(session_id="s1", uow_factory=uow_factory)
    success = await svc.rewind(str(test_file))

    assert success is True
    assert test_file.read_text() == "original content"
