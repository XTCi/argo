from __future__ import annotations

import logging
from typing import Callable, List

from app.domain.models.memory import Memory, EpisodicEntry, SemanticEntry
from app.domain.repositories.uow import IUnitOfWork

logger = logging.getLogger(__name__)


class ThreeLayerMemory:
    """三层记忆系统。

    WorkingMemory  — 当前 turn 活跃消息（in-memory，轻量）
    EpisodicMemory — 压缩后的历史摘要（DB 持久化，跨 turn）
    SemanticMemory — 代码库沉淀知识（DB 持久化，跨 session）
    """

    def __init__(self, session_id: str, uow_factory: Callable[[], IUnitOfWork]) -> None:
        # NOTE: Episodic and semantic entries are stored in-memory only for this session.
        # Cross-turn persistence (DB read/write for EpisodicEntry, SemanticEntry) is
        # a known gap — entries reset when AgentTaskRunner is re-instantiated per request.
        # Full persistence requires load() from DB on construction (future work).
        self.session_id = session_id
        self._uow_factory = uow_factory
        self.working = Memory()
        self.episodic_entries: List[EpisodicEntry] = []
        self.semantic_entries: List[SemanticEntry] = []

    async def add_episodic(self, summary: str, message_count: int) -> None:
        """将一段对话压缩为情节记忆条目（仅 in-memory，不持久化到 DB）。"""
        entry = EpisodicEntry(
            session_id=self.session_id,
            summary=summary,
            message_count=message_count,
        )
        self.episodic_entries.append(entry)
        logger.info(f"EpisodicMemory: added entry ({message_count} msgs compressed)")

    async def upsert_semantic(self, key: str, content: str) -> None:
        """更新或插入语义知识条目（仅 in-memory，不持久化到 DB）。"""
        existing = next((e for e in self.semantic_entries if e.key == key), None)
        if existing:
            existing.content = content
        else:
            self.semantic_entries.append(SemanticEntry(
                session_id=self.session_id, key=key, content=content,
            ))

    def build_context_block(self) -> str:
        """构建注入 system prompt 的记忆上下文块。"""
        lines: list[str] = []
        if self.episodic_entries:
            lines.append("## Session History (compressed)")
            for e in self.episodic_entries[-3:]:  # 最近 3 条 episodic
                lines.append(f"- {e.summary} [{e.message_count} msgs]")
        if self.semantic_entries:
            lines.append("## Codebase Knowledge")
            for s in self.semantic_entries:
                lines.append(f"- [{s.key}] {s.content}")
        return "\n".join(lines)
