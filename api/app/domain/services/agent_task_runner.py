import asyncio
import logging
from typing import Callable, Optional

from pydantic import TypeAdapter

from app.domain.external.json_parser import JSONParser
from app.domain.external.llm import LLM
from app.domain.external.task import TaskRunner, Task
from app.domain.models.app_config import AgentConfig
from app.domain.models.event import (
    ErrorEvent, Event, MessageEvent, BaseEvent,
    TitleEvent, WaitEvent, DoneEvent,
)
from app.domain.models.session import SessionStatus
from app.domain.repositories.uow import IUnitOfWork
from app.domain.services.agents.coding_agent import build_coding_agent
from app.domain.services.runtime.workspace import resolve_workspace

logger = logging.getLogger(__name__)


class AgentTaskRunner(TaskRunner):
    """基于 CodingAgent 的任务运行器（新 runtime）。

    职责：
    - 从任务输入流读取用户消息
    - 调用 CodingAgent.run() 获取事件流
    - 将事件写入任务输出流并持久化到会话 DB
    - 维护会话状态（RUNNING / WAITING / COMPLETED）
    """

    def __init__(
            self,
            uow_factory: Callable[[], IUnitOfWork],
            llm: LLM,
            agent_config: AgentConfig,
            session_id: str,
            json_parser: JSONParser,
            cwd: str = ".",
    ) -> None:
        """构造函数：构建 workspace 并装配 CodingAgent。"""
        self._uow_factory = uow_factory
        self._uow = uow_factory()
        self._session_id = session_id

        workspace = resolve_workspace(cwd)
        self._agent = build_coding_agent(
            llm=llm,
            json_parser=json_parser,
            agent_config=agent_config,
            uow_factory=uow_factory,
            session_id=session_id,
            workspace=workspace,
        )
        logger.info(f"AgentTaskRunner 初始化完成, 会话id: {session_id}, cwd: {cwd}")

    # ------------------------------------------------------------------
    # 内部辅助方法（stream I/O + DB 持久化）
    # ------------------------------------------------------------------

    async def _put_and_add_event(self, task: Task, event: BaseEvent) -> None:
        """将事件写入任务输出流并持久化到会话。"""
        event_id = await task.output_stream.put(event.model_dump_json())
        event.id = event_id
        async with self._uow:
            await self._uow.session.add_event(self._session_id, event)

    @classmethod
    async def _pop_event(cls, task: Task) -> Optional[Event]:
        """从任务输入流中读取一条事件。"""
        event_id, event_str = await task.input_stream.pop()
        if event_str is None:
            logger.warning("AgentTaskRunner 接收到空消息")
            return None
        event = TypeAdapter(Event).validate_json(event_str)
        event.id = event_id
        return event

    # ------------------------------------------------------------------
    # TaskRunner 接口实现
    # ------------------------------------------------------------------

    async def invoke(self, task: Task) -> None:
        """读取输入消息 → 调用 CodingAgent → 把事件写入输出流。"""
        try:
            logger.info("AgentTaskRunner 任务处理开始")

            # 循环读取输入消息队列（每次 chat 通常只有一条）
            while not await task.input_stream.is_empty():
                event = await self._pop_event(task)
                if event is None:
                    continue

                user_message = ""
                if isinstance(event, MessageEvent):
                    user_message = event.message or ""
                    logger.info(f"AgentTaskRunner 接收到新消息: {user_message[:80]}...")

                # 从 DB 加载历史会话消息（ThreeLayerMemory 是 in-memory，跨请求丢失）
                async with self._uow:
                    prior_memory = await self._uow.session.get_memory(
                        self._session_id, "base"
                    )
                session_messages = prior_memory.get_messages()

                # 调用 CodingAgent，传入历史会话消息
                async for out_event in self._agent.run(user_message, session_messages):
                    await self._put_and_add_event(task, out_event)

                    if isinstance(out_event, TitleEvent):
                        async with self._uow:
                            await self._uow.session.update_title(
                                self._session_id, out_event.title
                            )
                    elif isinstance(out_event, MessageEvent):
                        async with self._uow:
                            await self._uow.session.update_latest_message(
                                self._session_id,
                                out_event.message,
                                out_event.created_at,
                            )
                            await self._uow.session.increment_unread_message_count(
                                self._session_id
                            )
                    elif isinstance(out_event, WaitEvent):
                        # 等待用户输入：更新状态并提前返回
                        async with self._uow:
                            await self._uow.session.update_status(
                                self._session_id, SessionStatus.WAITING
                            )
                        return

                    # 如果输入队列中已有新消息，跳出内层循环先处理新消息
                    if not await task.input_stream.is_empty():
                        break

            # 所有消息处理完毕，标记会话为已完成
            async with self._uow:
                await self._uow.session.update_status(
                    self._session_id, SessionStatus.COMPLETED
                )
        except asyncio.CancelledError:
            logger.info("AgentTaskRunner 任务运行取消")
            await self._put_and_add_event(task, DoneEvent())
            async with self._uow:
                await self._uow.session.update_status(
                    self._session_id, SessionStatus.COMPLETED
                )
            raise
        except Exception as e:
            logger.exception(f"AgentTaskRunner 运行出错: {e}")
            await self._put_and_add_event(
                task, ErrorEvent(error=f"AgentTaskRunner 出错: {e}")
            )
            async with self._uow:
                await self._uow.session.update_status(
                    self._session_id, SessionStatus.COMPLETED
                )

    async def destroy(self) -> None:
        """销毁任务运行器（CodingAgent 无长期外部资源，无需额外清理）。"""
        logger.info("AgentTaskRunner 已销毁（no-op）")

    async def on_done(self, task: Task) -> None:
        """任务结束回调。"""
        logger.info("AgentTaskRunner 任务执行结束")
