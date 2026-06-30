from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Protocol, runtime_checkable

from app.domain.external.llm import LLM

logger = logging.getLogger(__name__)


@dataclass
class ContextEngineConfig:
    context_length: int
    threshold_percent: float = 0.75

    @property
    def threshold_tokens(self) -> int:
        return int(self.context_length * self.threshold_percent)


@runtime_checkable
class ContextEngine(Protocol):
    """可插拔的 context 压缩引擎协议。

    所有引擎必须实现：
      - update_from_response()：从 API 响应追踪 token 用量
      - should_compress()：判断是否需要触发压缩
      - compress()：执行压缩，返回新消息列表
    """
    last_prompt_tokens: int
    threshold_tokens: int
    context_length: int
    compression_count: int

    def update_from_response(self, usage: Dict[str, Any]) -> None: ...
    def should_compress(self) -> bool: ...
    async def compress(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]: ...
    def get_status(self) -> Dict[str, Any]: ...


class DefaultContextEngine:
    """基于 LLM 摘要的默认 context 压缩引擎。

    设计原则（仿 hermes ContextEngine）：
    - protect_first_n: system prompt 之后保留的头部消息数（cache-safe 的 stable tier）
    - protect_last_n: 末尾保留的消息数（最近对话，绝不压缩）
    - 中间消息：调用 LLM 生成摘要，替换为单条 summary message
    """

    def __init__(
        self,
        config: ContextEngineConfig,
        llm: LLM,
        protect_first_n: int = 3,
        protect_last_n: int = 6,
    ) -> None:
        self._config = config
        self._llm = llm
        self.protect_first_n = protect_first_n
        self.protect_last_n = protect_last_n
        self.last_prompt_tokens: int = 0
        self.compression_count: int = 0

    @property
    def threshold_tokens(self) -> int:
        return self._config.threshold_tokens

    @property
    def context_length(self) -> int:
        return self._config.context_length

    def update_from_response(self, usage: Dict[str, Any]) -> None:
        """从 API usage 字段更新 token 追踪。"""
        self.last_prompt_tokens = usage.get("prompt_tokens", 0)

    def should_compress(self) -> bool:
        """当 prompt tokens 超过阈值时触发压缩。"""
        return self.last_prompt_tokens >= self.threshold_tokens

    async def compress(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """压缩消息列表，保留头尾，摘要中间部分。"""
        if not messages:
            return messages

        # 系统 prompt 始终放在最前（位置 0）
        system_msgs = [m for m in messages if m.get("role") == "system"]
        non_system = [m for m in messages if m.get("role") != "system"]

        # 头部保护区 + 尾部保护区
        head = non_system[: self.protect_first_n]
        tail = non_system[-self.protect_last_n :] if self.protect_last_n else []
        middle = non_system[self.protect_first_n : len(non_system) - self.protect_last_n]

        if not middle:
            return messages  # 没有可压缩的中间部分

        # 用 LLM 生成中间部分摘要
        summary_prompt = (
            "Summarize the following conversation history concisely, "
            "preserving all key decisions, code changes, and findings:\n\n"
            + "\n".join(
                f"{m['role'].upper()}: {str(m.get('content', ''))[:500]}"
                for m in middle
            )
        )
        try:
            result = await self._llm.invoke(
                messages=[{"role": "user", "content": summary_prompt}],
                tools=[],
            )
            summary_text = result.get("content", "[summary unavailable]")
        except Exception as e:
            logger.warning(f"Context compression LLM call failed: {e}")
            summary_text = f"[{len(middle)} messages compressed — LLM unavailable]"

        summary_msg = {
            "role": "user",
            "content": f"[Compressed history — {len(middle)} messages summarized]\n{summary_text}",
        }
        self.compression_count += 1
        logger.info(
            f"Context compressed: {len(messages)} → "
            f"{len(system_msgs) + len(head) + 1 + len(tail)} messages "
            f"(compression #{self.compression_count})"
        )
        return system_msgs + head + [summary_msg] + tail

    def get_status(self) -> Dict[str, Any]:
        return {
            "last_prompt_tokens": self.last_prompt_tokens,
            "threshold_tokens": self.threshold_tokens,
            "context_length": self.context_length,
            "usage_percent": (
                min(100, self.last_prompt_tokens / self.context_length * 100)
                if self.context_length else 0
            ),
            "compression_count": self.compression_count,
        }
