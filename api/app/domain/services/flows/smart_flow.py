import logging
from typing import AsyncGenerator, Callable

from app.domain.external.browser import Browser
from app.domain.external.json_parser import JSONParser
from app.domain.external.llm import LLM
from app.domain.external.sandbox import Sandbox
from app.domain.external.search import SearchEngine
from app.domain.models.app_config import AgentConfig
from app.domain.models.event import BaseEvent, DoneEvent, TitleEvent
from app.domain.models.message import Message
from app.domain.models.session import SessionStatus
from app.domain.services.agents.smart_agent import SmartAgent
from app.domain.services.tools.a2a import A2ATool
from app.domain.services.tools.browser import BrowserTool
from app.domain.services.tools.file import FileTool
from app.domain.services.tools.mcp import MCPTool
from app.domain.services.tools.message import MessageTool
from app.domain.services.tools.search import SearchTool
from app.domain.services.tools.shell import ShellTool
from .base import BaseFlow, FlowStatus
from ...repositories.uow import IUnitOfWork

logger = logging.getLogger(__name__)


class SmartFlow(BaseFlow):
    """智能流：单 Agent 自主决策，直接回复或调用工具"""

    def __init__(
            self,
            uow_factory: Callable[[], IUnitOfWork],
            llm: LLM,
            agent_config: AgentConfig,
            session_id: str,
            json_parser: JSONParser,
            browser: Browser,
            sandbox: Sandbox,
            search_engine: SearchEngine,
            mcp_tool: MCPTool,
            a2a_tool: A2ATool,
    ) -> None:
        self._uow_factory = uow_factory
        self._uow = uow_factory()
        self._session_id = session_id
        self.status = FlowStatus.IDLE

        # 与 PlannerReActFlow 保持一致的工具列表
        tools = [
            FileTool(sandbox=sandbox),
            ShellTool(sandbox=sandbox),
            BrowserTool(browser=browser),
            SearchTool(search_engine=search_engine),
            MessageTool(),
            mcp_tool,
            a2a_tool,
        ]

        self.smart = SmartAgent(
            uow_factory=uow_factory,
            session_id=session_id,
            agent_config=agent_config,
            llm=llm,
            json_parser=json_parser,
            tools=tools,
        )
        logger.debug(f"创建 SmartAgent 成功, 会话id: {self._session_id}")

    async def invoke(self, message: Message) -> AsyncGenerator[BaseEvent, None]:
        """传递消息，运行 SmartFlow，返回事件流"""
        # 1. 检查会话是否存在
        async with self._uow:
            session = await self._uow.session.get_by_id(self._session_id)
        if not session:
            raise ValueError(f"会话[{self._session_id}]不存在，请核实后尝试")

        # 2. 非 PENDING 状态说明会话已有历史消息，需要回滚确保消息列表格式正确
        #    （与 PlannerReActFlow 保持一致）
        if session.status != SessionStatus.PENDING:
            logger.debug(f"会话[{self._session_id}]非空闲状态，执行回滚")
            await self.smart.roll_back(message)

        # 3. 更新会话状态为运行中
        async with self._uow:
            await self._uow.session.update_status(self._session_id, SessionStatus.RUNNING)

        self.status = FlowStatus.EXECUTING
        logger.info(f"SmartFlow 接收消息: {message.message[:50]}...")

        # 4. 首次消息（会话从未运行过）时自动生成标题
        #    PENDING 表示会话刚创建、尚未处理任何消息
        if session.status == SessionStatus.PENDING:
            title = message.message[:40].strip() or "新对话"
            yield TitleEvent(title=title)

        # 5. 运行 SmartAgent，将其产生的所有事件透传给上层
        async for event in self.smart.run(message):
            yield event

        # 6. 流程结束
        self.status = FlowStatus.COMPLETED
        self.status = FlowStatus.IDLE
        yield DoneEvent()
        logger.info(f"SmartFlow 处理完毕")

    @property
    def done(self) -> bool:
        return self.status == FlowStatus.IDLE
