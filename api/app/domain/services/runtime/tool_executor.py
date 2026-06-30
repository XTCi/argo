from __future__ import annotations

import asyncio
import logging
from enum import Enum
from typing import Awaitable, Callable, List, Optional, Tuple

from app.domain.models.tool_result import ToolResult
from app.domain.services.runtime.context_engine import ContextEngine
from app.domain.services.tools.base import BaseTool

logger = logging.getLogger(__name__)

_FILE_MUTATION_TOOLS = frozenset({"write_file", "patch_file"})
_TERMINAL_TOOLS = frozenset({"shell_execute", "shell_background", "read_output", "run_tests"})
_SEARCH_TOOLS = frozenset({"grep_files", "find_symbol", "list_dir", "code_search"})

# 工具输出超过此 token 估算则触发截断（1 token ≈ 4 chars）
_COMPRESS_THRESHOLD_CHARS = 4000


class ToolResultKind(str, Enum):
    """工具结果分类。

    FILE_MUTATION — 文件写入类（触发 CheckpointService）
    TERMINAL      — shell 输出（大输出触发截断）
    SEARCH        — 代码搜索结果（结构感知截断）
    OTHER         — 其他（git、message 等）
    """
    FILE_MUTATION = "file_mutation"
    TERMINAL = "terminal"
    SEARCH = "search"
    OTHER = "other"


def classify_tool_result(tool_name: str, result: ToolResult) -> ToolResultKind:
    """根据工具名分类结果类型。"""
    if tool_name in _FILE_MUTATION_TOOLS:
        return ToolResultKind.FILE_MUTATION
    if tool_name in _TERMINAL_TOOLS:
        return ToolResultKind.TERMINAL
    if tool_name in _SEARCH_TOOLS:
        return ToolResultKind.SEARCH
    return ToolResultKind.OTHER


class ToolExecutor:
    """统一工具执行器，在工具调用后做分类与后处理。

    后处理策略：
    - FILE_MUTATION：记录 mutation path（供 CheckpointService 追踪）
    - TERMINAL/SEARCH：输出过大时截断并标注（避免 context 爆炸）
    - 所有结果：返回 (ToolResult, ToolResultKind) 二元组
    """

    def __init__(
        self,
        tools: List[BaseTool],
        context_engine: ContextEngine,
        pre_execute_hook: Optional[Callable[[str, dict], Awaitable[bool]]] = None,
    ) -> None:
        self._tools = tools
        self._context_engine = context_engine
        self._pre_execute_hook = pre_execute_hook
        self._turn_mutation_paths: set[str] = set()

    def reset_turn(self) -> None:
        """每轮开始时重置 mutation 追踪。"""
        self._turn_mutation_paths.clear()

    @property
    def turn_mutation_paths(self) -> frozenset[str]:
        return frozenset(self._turn_mutation_paths)

    def _find_tool(self, tool_name: str) -> BaseTool:
        for t in self._tools:
            if t.has_tool(tool_name):
                return t
        raise ValueError(f"Tool not found: {tool_name}")

    def _maybe_truncate(self, result: ToolResult, kind: ToolResultKind) -> ToolResult:
        """大输出截断，避免单个工具结果撑爆 context。"""
        if kind not in (ToolResultKind.TERMINAL, ToolResultKind.SEARCH):
            return result
        content = str(result.data or "")
        if len(content) > _COMPRESS_THRESHOLD_CHARS:
            truncated = content[:_COMPRESS_THRESHOLD_CHARS]
            logger.info(f"Tool output truncated: {len(content)} → {_COMPRESS_THRESHOLD_CHARS} chars")
            return ToolResult(
                success=result.success,
                message=result.message,
                data=truncated + f"\n\n[... truncated, {len(content)} chars total]",
            )
        return result

    async def execute(
        self, tool_name: str, arguments: dict
    ) -> Tuple[ToolResult, ToolResultKind]:
        """执行工具并返回 (result, kind)。"""
        if self._pre_execute_hook is not None:
            allowed = await self._pre_execute_hook(tool_name, arguments)
            if not allowed:
                return (
                    ToolResult(success=False, message=f"Permission denied for: {arguments.get('command', tool_name)}"),
                    ToolResultKind.OTHER,
                )
        tool = self._find_tool(tool_name)
        result = await tool.invoke(tool_name, **arguments)
        kind = classify_tool_result(tool_name, result)

        if kind == ToolResultKind.FILE_MUTATION:
            filepath = arguments.get("filepath") or arguments.get("path", "")
            if filepath:
                self._turn_mutation_paths.add(filepath)

        result = self._maybe_truncate(result, kind)
        return result, kind

    async def execute_batch(
        self, calls: list[dict]
    ) -> list[tuple[ToolResult, ToolResultKind]]:
        """Execute multiple tool calls preserving original ordering semantics.

        Contiguous runs of SEARCH tools execute concurrently via asyncio.gather.
        FILE_MUTATION and TERMINAL tools execute serially, breaking concurrent runs.
        Results are returned in original call order.
        """
        if not calls:
            return []
        if len(calls) == 1:
            return [await self.execute(calls[0]["tool_name"], calls[0]["arguments"])]

        results: list[tuple[ToolResult, ToolResultKind] | None] = [None] * len(calls)
        i = 0
        while i < len(calls):
            pre_kind = classify_tool_result(calls[i]["tool_name"], ToolResult(success=True))
            if pre_kind == ToolResultKind.SEARCH:
                # Collect contiguous SEARCH run
                run_indices = []
                while i < len(calls):
                    k = classify_tool_result(calls[i]["tool_name"], ToolResult(success=True))
                    if k != ToolResultKind.SEARCH:
                        break
                    run_indices.append(i)
                    i += 1
                # Execute the run concurrently
                concurrent_results = await asyncio.gather(
                    *[
                        self.execute(calls[j]["tool_name"], calls[j]["arguments"])
                        for j in run_indices
                    ],
                    return_exceptions=False,
                )
                for j, res in zip(run_indices, concurrent_results):
                    results[j] = res
            else:
                # Serial: execute one at a time
                results[i] = await self.execute(calls[i]["tool_name"], calls[i]["arguments"])
                i += 1

        return results  # type: ignore[return-value]
