import json
import pytest
from app.domain.services.runtime.todo_store import TodoStore
from app.domain.services.tools.todo import TodoTool


@pytest.fixture
def store():
    return TodoStore()


@pytest.fixture
def tool(store):
    return TodoTool(todo_store=store)


@pytest.mark.asyncio
async def test_todo_write_updates_store(tool, store):
    result = await tool.invoke("todo_write", todos=[
        {"id": "1", "content": "do something", "status": "pending"}
    ])
    assert result.success
    assert len(store.read()) == 1
    assert store.read()[0].id == "1"


@pytest.mark.asyncio
async def test_todo_write_returns_full_list_as_json(tool):
    result = await tool.invoke("todo_write", todos=[
        {"id": "a", "content": "task a", "status": "in_progress"},
        {"id": "b", "content": "task b", "status": "done"},
    ])
    assert result.success
    data = json.loads(result.data)
    assert len(data) == 2
    assert data[0]["id"] == "a"


@pytest.mark.asyncio
async def test_todo_read_returns_current_list(tool, store):
    store.write([{"id": "x", "content": "existing", "status": "pending"}])
    result = await tool.invoke("todo_read")
    assert result.success
    data = json.loads(result.data)
    assert data[0]["id"] == "x"


@pytest.mark.asyncio
async def test_todo_read_empty_store_returns_empty_list(tool):
    result = await tool.invoke("todo_read")
    assert result.success
    assert json.loads(result.data) == []
