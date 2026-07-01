from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any, TypeVar

from .checkpoint_repository import ICheckpointRepository

T = TypeVar("T", bound="IUnitOfWork")


class IUnitOfWork(ABC):
    session: Any
    checkpoint: ICheckpointRepository

    @abstractmethod
    async def commit(self): ...

    @abstractmethod
    async def rollback(self): ...

    @abstractmethod
    async def __aenter__(self: T) -> T: ...

    @abstractmethod
    async def __aexit__(self, exc_type, exc_val, exc_tb): ...
