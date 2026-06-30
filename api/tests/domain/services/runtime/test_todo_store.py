import pytest
from app.domain.services.runtime.todo_store import TodoStore


def test_write_replaces_full_list():
    store = TodoStore()
    store.write([{"id": "1", "content": "first", "status": "pending"}])
    store.write([{"id": "2", "content": "second", "status": "in_progress"}])
    items = store.read()
    assert len(items) == 1
    assert items[0].id == "2"


def test_read_returns_empty_list_initially():
    store = TodoStore()
    assert store.read() == []


def test_format_for_injection_renders_status_icons():
    store = TodoStore()
    store.write([
        {"id": "t1", "content": "write tests", "status": "done"},
        {"id": "t2", "content": "implement", "status": "in_progress"},
        {"id": "t3", "content": "review", "status": "pending"},
    ])
    block = store.format_for_injection()
    assert "## Current Tasks" in block
    assert "[x]" in block
    assert "[→]" in block
    assert "[ ]" in block


def test_format_for_injection_returns_empty_string_when_no_items():
    store = TodoStore()
    assert store.format_for_injection() == ""


def test_content_truncated_at_500_chars():
    store = TodoStore()
    store.write([{"id": "1", "content": "x" * 600, "status": "pending"}])
    item = store.read()[0]
    assert len(item.content) <= 501  # 500 chars + "…"
    assert item.content.endswith("…")


def test_max_50_items_enforced():
    store = TodoStore()
    todos = [{"id": str(i), "content": f"task {i}", "status": "pending"} for i in range(60)]
    store.write(todos)
    assert len(store.read()) == 50
