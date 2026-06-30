from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, AsyncGenerator, Callable, Dict, Any, List

from app.domain.external.llm import LLM
from app.domain.external.json_parser import JSONParser
from app.domain.models.app_config import AgentConfig
from app.domain.models.event import (
    BaseEvent, MessageEvent, ToolEvent, ToolEventStatus, ErrorEvent, DoneEvent
)
from app.domain.models.tool_result import ToolResult
from app.domain.repositories.uow import IUnitOfWork
from app.domain.services.runtime.context_engine import ContextEngine
from app.domain.services.runtime.memory import ThreeLayerMemory
from app.domain.services.runtime.turn import TurnOrchestrator
from app.domain.services.runtime.tool_executor import ToolExecutor, ToolResultKind, classify_tool_result
from app.domain.services.runtime.checkpoint import CheckpointService
from app.domain.services.tools.base import BaseTool

if TYPE_CHECKING:
    from app.domain.services.runtime.turn import TurnContext

logger = logging.getLogger(__name__)


class BaseAgent:
    """重写后的 BaseAgent —— 消费 runtime 组件而非直接管理状态。

    职责：
    1. 依赖 TurnOrchestrator 处理 prologue（context 压缩、系统 prompt 构建）
    2. 依赖 ToolExecutor 执行工具（分类 + 截断 + mutation 追踪）
    3. 依赖 CheckpointService 在 FILE_MUTATION 前做快照
    4. 依赖 ContextEngine 追踪 token 用量
    5. 发射类型化事件流（ToolEvent / MessageEvent / ErrorEvent / DoneEvent）
    """

    name: str = "base"

    def __init__(
        self,
        llm: LLM,
        json_parser: JSONParser,
        agent_config: AgentConfig,
        tools: List[BaseTool],
        turn_orchestrator: TurnOrchestrator,
        tool_executor: ToolExecutor,
        checkpoint_service: CheckpointService,
        context_engine: ContextEngine,
        memory: ThreeLayerMemory,
        uow_factory: Callable[[], IUnitOfWork],
        session_id: str,
    ) -> None:
        self._llm = llm
        self._json_parser = json_parser
        self._agent_config = agent_config
        self._tools = tools
        self._turn_orchestrator = turn_orchestrator
        self._tool_executor = tool_executor
        self._checkpoint_service = checkpoint_service
        self._context_engine = context_engine
        self._memory = memory
        self._uow_factory = uow_factory
        self._session_id = session_id

    def _get_tool_schemas(self) -> List[Dict[str, Any]]:
        schemas = []
        for t in self._tools:
            schemas.extend(t.get_tools())
        return schemas

    async def run(
        self,
        user_message: str,
        session_messages: List[Dict[str, Any]],
    ) -> AsyncGenerator[BaseEvent, None]:
        """主入口：运行一轮 agent 对话，发射事件流。"""
        # 1. 运行 prologue，获取 TurnContext
        turn_ctx = await self._turn_orchestrator.build_turn_context(
            user_message=user_message,
            session_messages=session_messages,
            session_id=self._session_id,
        )
        self._tool_executor.reset_turn()

        # 2. 把 user_message 加入工作消息列表
        messages = turn_ctx.messages + [{"role": "user", "content": user_message}]

        # 3. 系统 prompt 注入
        full_messages = [
            {"role": "system", "content": turn_ctx.active_system_prompt}
        ] + messages

        # 4. ReAct 主循环
        try:
            async for event in self._react_loop(full_messages, turn_ctx):
                yield event
            await self._save_turn_messages(full_messages[1:])
        except Exception as e:
            logger.exception("BaseAgent run error: %s", e)
            yield ErrorEvent(error=str(e))
        finally:
            yield DoneEvent()

    async def _save_turn_messages(self, messages: List[Dict[str, Any]]) -> None:
        """Persist conversation messages (excluding system prompt) for cross-turn continuity."""
        from app.domain.models.memory import Memory
        mem = Memory()
        mem.add_messages(messages)
        try:
            async with self._uow_factory() as uow:
                await uow.session.save_memory(self._session_id, self.name, mem)
        except Exception:
            logger.warning("Failed to persist session messages for session %s", self._session_id)

    async def _react_loop(
        self, messages: List[Dict[str, Any]], turn_ctx: "TurnContext"
    ) -> AsyncGenerator[BaseEvent, None]:
        """ReAct 循环：LLM 调用 → 工具执行 → 再次 LLM 调用，直到无工具调用或耗尽配额。"""
        while True:
            if not turn_ctx.iteration_budget.consume():
                yield ErrorEvent(error=f"Iteration limit reached ({self._agent_config.max_iterations})")
                return

            # LLM 调用
            response = await self._llm.invoke(
                messages=messages,
                tools=self._get_tool_schemas(),
            )

            # 追踪 token 用量（用于 should_compress 决策）
            if "usage" in response:
                self._context_engine.update_from_response(response["usage"])

            tool_calls = response.get("tool_calls", [])
            content = response.get("content")

            # 将 assistant 消息加入列表
            msg: Dict[str, Any] = {"role": "assistant", "content": content}
            if tool_calls:
                msg["tool_calls"] = tool_calls
            messages.append(msg)

            if not tool_calls:
                # 无工具调用 → 最终回答
                if content:
                    yield MessageEvent(role="assistant", message=content)
                return

            # 处理工具调用（支持批量并发）
            parsed_calls: list[tuple[str, str, str, dict]] = []  # (tc_id, tool_name, raw_args, arguments)
            for tool_call in tool_calls:
                tc_id = tool_call.get("id") or str(uuid.uuid4())
                func = tool_call.get("function", {})
                tool_name = func.get("name", "")
                raw_args = func.get("arguments", "{}")
                arguments = await self._json_parser.invoke(raw_args)
                parsed_calls.append((tc_id, tool_name, raw_args, arguments))

                yield ToolEvent(
                    tool_call_id=tc_id,
                    tool_name=tool_name,
                    function_name=tool_name,
                    function_args=arguments,
                    status=ToolEventStatus.CALLING,
                )

                # FILE_MUTATION 前做 checkpoint（按工具名分类，而非 argument key）
                pre_kind = classify_tool_result(tool_name, ToolResult(success=True))
                if pre_kind == ToolResultKind.FILE_MUTATION:
                    filepath = (
                        arguments.get("filepath") or arguments.get("path") or
                        arguments.get("filename") or arguments.get("target") or ""
                    )
                    if filepath:
                        await self._checkpoint_service.snapshot(filepath, turn_id=turn_ctx.turn_id)

            # 批量执行（SEARCH 并发，FILE_MUTATION/TERMINAL 串行）
            batch_calls = [
                {"tool_name": tn, "arguments": args}
                for _, tn, _, args in parsed_calls
            ]
            batch_results = await self._tool_executor.execute_batch(batch_calls)

            tool_messages = []
            for (tc_id, tool_name, _, arguments), (result, _) in zip(parsed_calls, batch_results):
                yield ToolEvent(
                    tool_call_id=tc_id,
                    tool_name=tool_name,
                    function_name=tool_name,
                    function_args=arguments,
                    function_result=result,
                    status=ToolEventStatus.CALLED,
                )
                tool_messages.append({
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "function_name": tool_name,
                    "content": result.model_dump_json(),
                })

            messages.extend(tool_messages)
