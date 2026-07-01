from __future__ import annotations
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from app.domain.models.app_config import LLMConfig
from app.infrastructure.external.llm.openai_llm import OpenAILLM
from app.domain.models.event import MessageEvent


def _cfg() -> LLMConfig:
    return LLMConfig(
        base_url="http://localhost",
        api_key="test-key",
        model_name="test-model",
    )


class _FakeDelta:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _FakeChoice:
    def __init__(self, content=None, tool_calls=None):
        self.delta = _FakeDelta(content, tool_calls)


class _FakeChunk:
    def __init__(self, content=None, tool_calls=None):
        self.choices = [_FakeChoice(content, tool_calls)]


@pytest.mark.asyncio
async def test_text_callback_called_for_each_chunk():
    llm = OpenAILLM(_cfg())
    received = []

    async def fake_stream():
        for chunk in [_FakeChunk("Hello"), _FakeChunk(" world"), _FakeChunk("!")]:
            yield chunk

    with patch.object(
        llm._client.chat.completions, "create", new_callable=AsyncMock
    ) as mock_create:
        mock_create.return_value = fake_stream()
        await llm.invoke(
            messages=[{"role": "user", "content": "hi"}],
            text_callback=received.append,
        )

    assert received == ["Hello", " world", "!"]


@pytest.mark.asyncio
async def test_accumulated_content_returned():
    llm = OpenAILLM(_cfg())

    async def fake_stream():
        for chunk in [_FakeChunk("foo"), _FakeChunk("bar")]:
            yield chunk

    with patch.object(
        llm._client.chat.completions, "create", new_callable=AsyncMock
    ) as mock_create:
        mock_create.return_value = fake_stream()
        result = await llm.invoke(
            messages=[{"role": "user", "content": "hi"}],
            text_callback=lambda x: None,
        )

    assert result["content"] == "foobar"
    assert result["tool_calls"] is None


@pytest.mark.asyncio
async def test_no_callback_uses_non_streaming_path():
    """When text_callback is None, stream=True must NOT be passed to create()."""
    llm = OpenAILLM(_cfg())

    fake_msg = MagicMock()
    fake_msg.model_dump.return_value = {
        "role": "assistant", "content": "hi", "tool_calls": None
    }
    fake_response = MagicMock()
    fake_response.choices = [MagicMock(message=fake_msg)]
    fake_response.usage = None

    with patch.object(
        llm._client.chat.completions, "create", new_callable=AsyncMock
    ) as mock_create:
        mock_create.return_value = fake_response
        result = await llm.invoke(messages=[{"role": "user", "content": "hello"}])
        assert mock_create.call_args.kwargs.get("stream", False) is False

    assert result["content"] == "hi"


class _FakeFunction:
    def __init__(self, name=None, arguments=None):
        self.name = name
        self.arguments = arguments


class _FakeToolCall:
    def __init__(self, index, id=None, name=None, arguments=None):
        self.index = index
        self.id = id
        self.function = _FakeFunction(name, arguments)


@pytest.mark.asyncio
async def test_streaming_tool_call_name_not_doubled():
    """Tool call name must not be doubled when the first chunk sets the name."""
    llm = OpenAILLM(_cfg())

    async def fake_stream():
        # First chunk: id + name arrive together
        yield _FakeChunk(tool_calls=[_FakeToolCall(0, id="call_1", name="get_weather", arguments="")])
        # Second chunk: arguments arrive
        yield _FakeChunk(tool_calls=[_FakeToolCall(0, id=None, name=None, arguments='{"city": "Paris"}')])

    with patch.object(
        llm._client.chat.completions, "create", new_callable=AsyncMock
    ) as mock_create:
        mock_create.return_value = fake_stream()
        result = await llm.invoke(
            messages=[{"role": "user", "content": "weather?"}],
            text_callback=lambda x: None,
        )

    assert result["tool_calls"] is not None
    assert len(result["tool_calls"]) == 1
    tc = result["tool_calls"][0]
    assert tc["function"]["name"] == "get_weather"   # NOT "get_weatherget_weather"
    assert tc["function"]["arguments"] == '{"city": "Paris"}'
    assert tc["id"] == "call_1"


def test_message_event_streamed_defaults_false():
    event = MessageEvent(role="assistant", message="hello")
    assert event.streamed is False


def test_message_event_streamed_field_can_be_set():
    event = MessageEvent(role="assistant", message="hello", streamed=True)
    assert event.streamed is True
