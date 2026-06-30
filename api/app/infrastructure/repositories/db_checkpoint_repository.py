from __future__ import annotations
from typing import List, Optional
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from app.domain.models.checkpoint import Checkpoint
from app.domain.repositories.checkpoint_repository import ICheckpointRepository
from app.infrastructure.models.checkpoint import CheckpointModel

class DbCheckpointRepository(ICheckpointRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(self, checkpoint: Checkpoint) -> None:
        model = CheckpointModel(
            id=checkpoint.id,
            session_id=checkpoint.session_id,
            turn_id=checkpoint.turn_id,
            filepath=checkpoint.filepath,
            content=checkpoint.content,
            created_at=checkpoint.created_at,
        )
        self._session.add(model)
        await self._session.flush()

    async def get_by_session_and_path(
        self, session_id: str, filepath: str
    ) -> List[Checkpoint]:
        result = await self._session.execute(
            select(CheckpointModel)
            .where(CheckpointModel.session_id == session_id)
            .where(CheckpointModel.filepath == filepath)
            .order_by(CheckpointModel.created_at.asc())
        )
        return [
            Checkpoint(
                id=m.id, session_id=m.session_id, turn_id=m.turn_id,
                filepath=m.filepath, content=m.content, created_at=m.created_at,
            )
            for m in result.scalars().all()
        ]

    async def get_latest(self, session_id: str, filepath: str) -> Optional[Checkpoint]:
        result = await self._session.execute(
            select(CheckpointModel)
            .where(CheckpointModel.session_id == session_id)
            .where(CheckpointModel.filepath == filepath)
            .order_by(CheckpointModel.created_at.desc())
            .limit(1)
        )
        m = result.scalars().first()
        if not m:
            return None
        return Checkpoint(
            id=m.id, session_id=m.session_id, turn_id=m.turn_id,
            filepath=m.filepath, content=m.content, created_at=m.created_at,
        )

    async def delete_by_session(self, session_id: str) -> None:
        await self._session.execute(
            delete(CheckpointModel).where(CheckpointModel.session_id == session_id)
        )
