from __future__ import annotations

from typing import Optional
import argo.config  # noqa: F401 — ensures api/ on sys.path

from app.domain.models.checkpoint import Checkpoint
from app.domain.models.file import File
from app.domain.models.memory import Memory


class InMemorySessionRepo:
    def __init__(self, initial_messages: list[dict]) -> None:
        self._initial_messages = initial_messages
        self._memory_store: dict[str, Memory] = {}

    async def save_memory(self, session_id: str, agent_name: str, memory: Memory) -> None:
        self._memory_store[agent_name] = memory

    async def get_memory(self, session_id: str, agent_name: str) -> Memory:
        if agent_name in self._memory_store:
            return self._memory_store[agent_name]
        mem = Memory()
        if self._initial_messages:
            mem.add_messages(self._initial_messages)
            self._memory_store[agent_name] = mem
        return mem

    # Protocol no-ops
    async def save(self, session): ...
    async def get_all(self): return []
    async def get_by_id(self, session_id): return None
    async def delete_by_id(self, session_id): ...
    async def update_title(self, session_id, title): ...
    async def update_latest_message(self, session_id, message, timestamp): ...
    async def update_unread_message_count(self, session_id, count): ...
    async def increment_unread_message_count(self, session_id): ...
    async def decrement_unread_message_count(self, session_id): ...
    async def update_status(self, session_id, status): ...
    async def add_event(self, session_id, event): ...
    async def add_file(self, session_id, file): ...
    async def remove_file(self, session_id, file_id): ...
    async def get_file_by_path(self, session_id, filepath): return None


class InMemoryCheckpointRepo:
    def __init__(self) -> None:
        self._store: dict[str, list[Checkpoint]] = {}

    def _key(self, session_id: str, filepath: str) -> str:
        return f"{session_id}::{filepath}"

    async def save(self, checkpoint: Checkpoint) -> None:
        k = self._key(checkpoint.session_id, checkpoint.filepath)
        self._store.setdefault(k, []).append(checkpoint)

    async def get_by_session_and_path(self, session_id: str, filepath: str) -> list[Checkpoint]:
        return self._store.get(self._key(session_id, filepath), [])

    async def get_latest(self, session_id: str, filepath: str) -> Optional[Checkpoint]:
        checkpoints = self._store.get(self._key(session_id, filepath), [])
        return checkpoints[-1] if checkpoints else None

    async def delete_by_session(self, session_id: str) -> None:
        keys_to_delete = [k for k in self._store if k.startswith(f"{session_id}::")]
        for k in keys_to_delete:
            del self._store[k]


class NoopFileRepo:
    async def save(self, file: File) -> None:
        ...

    async def get_by_id(self, file_id: str) -> Optional[File]:
        return None
