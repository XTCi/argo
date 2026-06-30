from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from app.domain.models.checkpoint import Checkpoint
from app.domain.repositories.uow import IUnitOfWork

logger = logging.getLogger(__name__)


class CheckpointService:
    """文件编辑安全层 —— 写入前快照，支持 rewind。

    设计原则（仿 hermes file_safety）：
    每次 FILE_MUTATION 类工具调用前，先对目标文件做内容快照并持久化到 DB。
    rewind() 从 DB 取最新快照恢复文件内容，保证任意时刻可回退。
    """

    def __init__(
        self, session_id: str, uow_factory: Callable[[], IUnitOfWork]
    ) -> None:
        self._session_id = session_id
        self._uow_factory = uow_factory

    async def snapshot(self, filepath: str, turn_id: str) -> None:
        """对指定文件做内容快照（文件不存在则跳过）。"""
        path = Path(filepath)
        if not path.is_file():
            logger.debug(f"CheckpointService: skip snapshot, file not found: {filepath}")
            return
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
            checkpoint = Checkpoint(
                session_id=self._session_id,
                turn_id=turn_id,
                filepath=filepath,
                content=content,
            )
            async with self._uow_factory() as uow:
                await uow.checkpoint.save(checkpoint)
            logger.info(f"CheckpointService: snapshotted {filepath} (turn={turn_id})")
        except Exception as e:
            logger.warning(f"CheckpointService: snapshot failed for {filepath}: {e}")

    async def rewind(self, filepath: str) -> bool:
        """将文件恢复到最新快照。返回 True 表示成功。"""
        try:
            async with self._uow_factory() as uow:
                checkpoint = await uow.checkpoint.get_latest(self._session_id, filepath)
            if not checkpoint:
                logger.warning(f"CheckpointService: no checkpoint found for {filepath}")
                return False
            Path(filepath).write_text(checkpoint.content, encoding="utf-8")
            logger.info(f"CheckpointService: rewound {filepath} to turn={checkpoint.turn_id}")
            return True
        except Exception as e:
            logger.error(f"CheckpointService: rewind failed for {filepath}: {e}")
            return False
