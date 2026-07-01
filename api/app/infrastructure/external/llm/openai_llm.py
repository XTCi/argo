import logging
from typing import Callable, List, Dict, Any

from openai import AsyncOpenAI

from app.domain.external.llm import LLM
from app.domain.models.app_config import LLMConfig

logger = logging.getLogger(__name__)


class OpenAILLM(LLM):
    """基于OpenAI SDK/兼容OpenAI格式的LLM调用类"""

    def __init__(self, llm_config: LLMConfig, **kwargs) -> None:
        self._client = AsyncOpenAI(
            base_url=str(llm_config.base_url),
            api_key=llm_config.api_key,
            **kwargs,
        )
        self._model_name = llm_config.model_name
        self._temperature = llm_config.temperature
        self._max_tokens = llm_config.max_tokens
        self._timeout = 3600

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def temperature(self) -> float:
        return self._temperature

    @property
    def max_tokens(self) -> int:
        return self._max_tokens

    async def invoke(
            self,
            messages: List[Dict[str, Any]],
            tools: List[Dict[str, Any]] = None,
            response_format: Dict[str, Any] = None,
            tool_choice: str = None,
            text_callback: Callable[[str], None] | None = None,
    ) -> Dict[str, Any]:
        """调用LLM，text_callback 不为 None 时使用流式响应。"""
        try:
            if text_callback is not None:
                return await self._invoke_streaming(
                    messages, tools, response_format, tool_choice, text_callback
                )

            # Non-streaming path (unchanged)
            if tools:
                logger.info(f"调用OpenAI客户端向LLM发起请求并携带工具信息: {self._model_name}")
                response = await self._client.chat.completions.create(
                    model=self._model_name,
                    temperature=self._temperature,
                    max_tokens=self._max_tokens,
                    messages=messages,
                    response_format=response_format,
                    tools=tools,
                    tool_choice=tool_choice,
                    timeout=self._timeout,
                )
            else:
                logger.info(f"调用OpenAI客户端向LLM发起请求未携带: {self._model_name}")
                response = await self._client.chat.completions.create(
                    model=self._model_name,
                    temperature=self._temperature,
                    max_tokens=self._max_tokens,
                    messages=messages,
                    response_format=response_format,
                    timeout=self._timeout,
                )

            logger.info(f"OpenAI客户端返回内容: {response.model_dump()}")
            message_dict = response.choices[0].message.model_dump()
            if response.usage:
                message_dict["usage"] = {
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                }
            return message_dict

        except Exception as e:
            logger.error(f"调用OpenAI客户端发生错误: {str(e)}")
            raise RuntimeError(f"LLM request failed: {e}") from e

    async def _invoke_streaming(
            self,
            messages: List[Dict[str, Any]],
            tools: List[Dict[str, Any]] | None,
            response_format: Dict[str, Any] | None,
            tool_choice: str | None,
            text_callback: Callable[[str], None],
    ) -> Dict[str, Any]:
        """流式路径：每个文字 delta 立即调用 text_callback，工具调用 chunk 内部累积。"""
        kwargs: Dict[str, Any] = dict(
            model=self._model_name,
            temperature=self._temperature,
            max_tokens=self._max_tokens,
            messages=messages,
            stream=True,
            stream_options={"include_usage": True},
            timeout=self._timeout,
        )
        if tools:
            kwargs["tools"] = tools
        if tool_choice:
            kwargs["tool_choice"] = tool_choice
        if response_format:
            kwargs["response_format"] = response_format

        stream = await self._client.chat.completions.create(**kwargs)

        full_content = ""
        tool_calls_acc: dict[int, dict] = {}
        usage_data = None

        async for chunk in stream:
            if not chunk.choices:
                # Final usage-only chunk (no choices) — capture usage
                if hasattr(chunk, "usage") and chunk.usage is not None:
                    usage_data = chunk.usage
                continue
            delta = chunk.choices[0].delta

            if delta.content:
                full_content += delta.content
                text_callback(delta.content)

            if delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index
                    if idx not in tool_calls_acc:
                        tool_calls_acc[idx] = {
                            "id": tc.id or "",
                            "type": "function",
                            "function": {"name": "", "arguments": ""},
                        }
                    if tc.id:
                        tool_calls_acc[idx]["id"] = tc.id
                    if tc.function:
                        if tc.function.name:
                            tool_calls_acc[idx]["function"]["name"] += tc.function.name
                        if tc.function.arguments:
                            tool_calls_acc[idx]["function"]["arguments"] += tc.function.arguments

        tool_calls = list(tool_calls_acc.values()) if tool_calls_acc else None
        result: Dict[str, Any] = {
            "role": "assistant",
            "content": full_content or None,
            "tool_calls": tool_calls,
        }
        if usage_data is not None:
            result["usage"] = {
                "prompt_tokens": usage_data.prompt_tokens,
                "completion_tokens": usage_data.completion_tokens,
            }
        return result


if __name__ == "__main__":
    import asyncio

    async def main():
        llm = OpenAILLM(LLMConfig(
            base_url="https://api.deepseek.com",
            api_key="",
            model_name="deepseek-chat",
        ))
        response = await llm.invoke([{"role": "user", "content": "Hi"}])
        print(response)

    asyncio.run(main())
