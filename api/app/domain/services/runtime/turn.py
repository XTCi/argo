from __future__ import annotations
import threading
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List

if TYPE_CHECKING:
    from app.domain.services.runtime.workspace import WorkspaceContext
    from app.domain.services.runtime.memory import ThreeLayerMemory
    from app.domain.services.runtime.context_engine import ContextEngine
    from app.domain.services.runtime.todo_store import TodoStore


class IterationBudget:
    """线程安全的迭代配额计数器。

    refund() 用于程序化工具调用（不应消耗用户配额）后归还配额。
    """

    def __init__(self, max_total: int) -> None:
        self.max_total = max_total
        self._used = 0
        self._lock = threading.Lock()

    def consume(self) -> bool:
        """尝试消费一次配额，返回 True 表示成功。"""
        with self._lock:
            if self._used >= self.max_total:
                return False
            self._used += 1
            return True

    def refund(self) -> None:
        """归还一次配额（用于程序化工具调用）。"""
        with self._lock:
            if self._used > 0:
                self._used -= 1

    @property
    def used(self) -> int:
        with self._lock:
            return self._used

    @property
    def remaining(self) -> int:
        with self._lock:
            return max(0, self.max_total - self._used)


@dataclass
class TurnContext:
    """每一轮 prologue 产出的上下文对象，loop 只消费这个。

    设计原则（仿 hermes TurnContext）：prologue 和 loop 完全解耦，
    prologue 产出固定值，loop 不重新计算任何 prologue 逻辑。
    """
    user_message: str
    session_id: str
    turn_id: str
    messages: List[Dict[str, Any]]       # 已经过 preflight 压缩的工作消息列表
    active_system_prompt: str            # 本轮激活的 system prompt（workspace + memory block）
    iteration_budget: IterationBudget
    memory_context_block: str = ""       # 从 ThreeLayerMemory 提取的上下文块


class TurnOrchestrator:
    """每轮进入时运行 prologue，产出 TurnContext。

    职责：
    1. 构建 active_system_prompt（workspace snapshot + memory context block）
    2. 运行 preflight 压缩门（超阈值则压缩）
    3. 创建 IterationBudget
    4. 返回 TurnContext
    """

    def __init__(
        self,
        workspace: "WorkspaceContext",
        memory: "ThreeLayerMemory",
        context_engine: "ContextEngine",
        max_iterations: int = 40,
        guidance: str = "",
        todo_store: "TodoStore | None" = None,
    ) -> None:
        self._workspace = workspace
        self._memory = memory
        self._context_engine = context_engine
        self._max_iterations = max_iterations
        self._guidance = guidance
        self._todo_store = todo_store

    def _build_system_prompt(self) -> str:
        """构建 system prompt stable tier（workspace + memory + todos）。"""
        blocks = []
        if self._guidance:
            blocks.append(self._guidance)
        else:
            blocks.extend([
                "You are a production-grade coding agent pairing with the user inside their codebase.",
                "Operate like a careful senior engineer: read before writing, verify after changing.",
            ])
        blocks.append(self._workspace.system_block())
        memory_block = self._memory.build_context_block()
        if memory_block:
            blocks.append(memory_block)
        if self._todo_store:
            todo_block = self._todo_store.format_for_injection()
            if todo_block:
                blocks.append(todo_block)
        return "\n\n".join(filter(None, blocks))

    async def build_turn_context(
        self,
        user_message: str,
        session_messages: List[Dict[str, Any]],
        session_id: str,
    ) -> TurnContext:
        """运行 prologue，返回本轮 TurnContext。"""
        turn_id = f"{session_id}:{uuid.uuid4().hex[:8]}"
        active_system_prompt = self._build_system_prompt()

        # Preflight 压缩门：超阈值则执行一次压缩
        messages = list(session_messages)
        if self._context_engine.should_compress():
            messages = await self._context_engine.compress(messages)

        return TurnContext(
            user_message=user_message,
            session_id=session_id,
            turn_id=turn_id,
            messages=messages,
            active_system_prompt=active_system_prompt,
            iteration_budget=IterationBudget(self._max_iterations),
            memory_context_block=self._memory.build_context_block(),
        )
