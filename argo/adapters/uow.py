from __future__ import annotations

import argo.config  # noqa: F401 — ensures api/ on sys.path

from app.domain.repositories.uow import IUnitOfWork
from argo.adapters.repos import InMemorySessionRepo, InMemoryCheckpointRepo


class InMemoryUoW(IUnitOfWork):
    def __init__(
        self,
        session_repo: InMemorySessionRepo,
        checkpoint_repo: InMemoryCheckpointRepo,
    ) -> None:
        self.session = session_repo
        self.checkpoint = checkpoint_repo

    async def commit(self) -> None:
        ...

    async def rollback(self) -> None:
        ...

    async def __aenter__(self) -> "InMemoryUoW":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        ...
