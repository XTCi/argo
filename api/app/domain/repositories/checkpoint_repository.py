from __future__ import annotations
from abc import ABC, abstractmethod
from typing import List, Optional
from app.domain.models.checkpoint import Checkpoint

class ICheckpointRepository(ABC):
    @abstractmethod
    async def save(self, checkpoint: Checkpoint) -> None: ...

    @abstractmethod
    async def get_by_session_and_path(
        self, session_id: str, filepath: str
    ) -> List[Checkpoint]: ...

    @abstractmethod
    async def get_latest(
        self, session_id: str, filepath: str
    ) -> Optional[Checkpoint]: ...

    @abstractmethod
    async def delete_by_session(self, session_id: str) -> None: ...
