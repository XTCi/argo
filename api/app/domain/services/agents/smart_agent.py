import logging
import uuid
from typing import AsyncGenerator

from app.domain.models.event import (
    BaseEvent,
    ErrorEvent,
    MessageEvent,
    ToolEvent,
    ToolEventStatus,
    WaitEvent,
)
from app.domain.models.file import File
from app.domain.models.message import Message
from app.domain.services.prompts.smart import SMART_SYSTEM_PROMPT, SMART_RUN_PROMPT
from .base import BaseAgent

logger = logging.getLogger(__name__)

# 每调用 N 个普通工具后触发一次记忆压缩，避免上下文无限膨胀
_COMPACT_EVERY_N_TOOLS = 8


class SmartAgent(BaseAgent):
    """智能体：单一 ReAct 循环，自主判断是否需要工具"""

    name: str = "smart"
    _system_prompt: str = SMART_SYSTEM_PROMPT

    async def run(self, message: Message) -> AsyncGenerator[BaseEvent, None]:
        """
        运行智能体，处理用户消息并以事件流形式返回结果。

        事件类型：
        - MessageEvent(role="assistant") → 通知或最终回复
        - ToolEvent(status=CALLING/CALLED)  → 工具调用过程
        - WaitEvent                         → 需要用户输入，暂停任务
        - ErrorEvent                        → 处理失败
        """
        # 1. 构建初始用户消息
        attachments_text = "\n".join(message.attachments) if message.attachments else "无"
        query = SMART_RUN_PROMPT.format(
            message=message.message,
            attachments=attachments_text,
        )

        # 2. 首次调用 LLM
        llm_message = await self._invoke_llm(
            [{"role": "user", "content": query}],
        )

        tool_call_count = 0
        # 记录本次任务中写入/修改过的文件路径，最终随 MessageEvent 作为附件返回
        # 对应 write_file / replace_in_file 两个会产生可交付文件的工具
        written_files: list[str] = []

        # 3. ReAct 主循环：最多 max_iterations 次迭代
        for _ in range(self._agent_config.max_iterations):
            # 4. 无工具调用 → LLM 直接给出文本回复，结束循环
            if not llm_message or not llm_message.get("tool_calls"):
                break

            # 5. 每次只取第一个工具调用（与 BaseAgent 保持一致）
            tool_call = llm_message["tool_calls"][0]
            if not tool_call.get("function"):
                break

            tool_call_id = tool_call.get("id") or str(uuid.uuid4())
            function_name = tool_call["function"]["name"]
            function_args = await self._json_parser.invoke(
                tool_call["function"]["arguments"]
            )

            # ── 特殊工具：message_notify_user ──────────────────────────────
            # 向用户发送进度通知，不等待，继续循环
            if function_name == "message_notify_user":
                yield MessageEvent(
                    role="assistant",
                    message=function_args.get("text", ""),
                )
                llm_message = await self._invoke_llm([{
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "function_name": function_name,
                    "content": '{"success": true, "data": "Continue"}',
                }])
                continue

            # ── 特殊工具：message_ask_user ────────────────────────────────
            # 向用户提问，暂停任务，等待用户回复
            if function_name == "message_ask_user":
                yield MessageEvent(
                    role="assistant",
                    message=function_args.get("text", ""),
                )
                yield WaitEvent()
                return

            # ── 普通工具 ──────────────────────────────────────────────────
            try:
                tool = self._get_tool(function_name)
            except ValueError:
                logger.warning(f"SmartAgent 未找到工具: {function_name}")
                llm_message = await self._invoke_llm([{
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "function_name": function_name,
                    "content": f'{{"success": false, "message": "工具 {function_name} 不存在"}}',
                }])
                continue

            # 6. 发送工具即将调用事件
            yield ToolEvent(
                tool_call_id=tool_call_id,
                tool_name=tool.name,
                function_name=function_name,
                function_args=function_args,
                status=ToolEventStatus.CALLING,
            )

            # 7. 执行工具
            result = await self._invoke_tool(tool, function_name, function_args)
            tool_call_count += 1

            # 8. 发送工具调用完成事件
            yield ToolEvent(
                tool_call_id=tool_call_id,
                tool_name=tool.name,
                function_name=function_name,
                function_args=function_args,
                function_result=result,
                status=ToolEventStatus.CALLED,
            )

            # 8.1 记录写入成功的文件路径，用于最终作为附件返回给用户下载
            if (
                result.success
                and function_name in ("write_file", "replace_in_file")
                and function_args.get("filepath")
            ):
                filepath = function_args["filepath"]
                if filepath not in written_files:
                    written_files.append(filepath)
                    logger.debug(f"SmartAgent 记录写入文件: {filepath}")

            # 9. 每 N 次工具调用后压缩记忆，避免上下文过长
            if tool_call_count % _COMPACT_EVERY_N_TOOLS == 0:
                logger.info(f"SmartAgent 压缩记忆（已调用 {tool_call_count} 个工具）")
                await self.compact_memory()

            # 10. 带上工具结果再次调用 LLM
            llm_message = await self._invoke_llm([{
                "role": "tool",
                "tool_call_id": tool_call_id,
                "function_name": function_name,
                "content": result.model_dump_json(),
            }])

        else:
            # 超过最大迭代次数
            yield ErrorEvent(
                error=f"SmartAgent 超过最大迭代次数（{self._agent_config.max_iterations}），任务处理失败"
            )
            return

        # 11. 输出最终文本回复，附带本次任务写入的文件（供前端下载）
        if llm_message and llm_message.get("content"):
            attachments = [File(filepath=fp) for fp in written_files]
            yield MessageEvent(
                role="assistant",
                message=llm_message["content"],
                attachments=attachments,
            )
        else:
            yield ErrorEvent(error="SmartAgent 未生成有效回复内容")
