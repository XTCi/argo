from __future__ import annotations

from typing import Optional
import argo.config  # noqa: F401 — ensures api/ on sys.path

from app.domain.models.checkpoint import Checkpoint
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
