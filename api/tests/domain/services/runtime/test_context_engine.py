from __future__ import annotations
import pytest
from unittest.mock import AsyncMock, MagicMock
from app.domain.services.runtime.context_engine import (
    DefaultContextEngine,
    ContextEngineConfig,
)

def make_messages(n: int) -> list[dict]:
    msgs = [{"role": "system", "content": "You are a coding agent."}]
    for i in range(n):
        msgs.append({"role": "user", "content": f"message {i}"})
        msgs.append({"role": "assistant", "content": f"response {i}"})
    return msgs

def test_should_not_compress_when_below_threshold():
    engine = DefaultContextEngine(ContextEngineConfig(
        context_length=10000, threshold_percent=0.75
    ), llm=MagicMock())
    engine.update_from_response({"prompt_tokens": 5000, "completion_tokens": 100, "total_tokens": 5100})
    assert engine.should_compress() is False

def test_should_compress_when_above_threshold():
    engine = DefaultContextEngine(ContextEngineConfig(
        context_length=10000, threshold_percent=0.75
    ), llm=MagicMock())
    engine.update_from_response({"prompt_tokens": 8000, "completion_tokens": 100, "total_tokens": 8100})
    assert engine.should_compress() is True

@pytest.mark.asyncio
async def test_compress_preserves_first_and_last():
    """compress 后应保留 protect_first_n 和 protect_last_n 条消息"""
    mock_llm = AsyncMock()
    mock_llm.invoke = AsyncMock(return_value={
        "role": "assistant", "content": "Summary of middle messages."
    })
    engine = DefaultContextEngine(
        ContextEngineConfig(context_length=10000, threshold_percent=0.75),
        llm=mock_llm,
        protect_first_n=2,
        protect_last_n=2,
    )
    messages = make_messages(10)  # system + 20 user/assistant = 21 messages
    compressed = await engine.compress(messages)
    # 系统 prompt 始终保留
    assert compressed[0]["role"] == "system"
    # 压缩后比原来短
    assert len(compressed) < len(messages)

def test_get_status():
    engine = DefaultContextEngine(ContextEngineConfig(
        context_length=8000, threshold_percent=0.75
    ), llm=MagicMock())
    engine.update_from_response({"prompt_tokens": 3000, "completion_tokens": 50, "total_tokens": 3050})
    status = engine.get_status()
    assert status["last_prompt_tokens"] == 3000
    assert status["threshold_tokens"] == 6000
    assert 0 <= status["usage_percent"] <= 100
