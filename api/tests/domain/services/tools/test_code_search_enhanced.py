from __future__ import annotations
import pytest
import pytest_asyncio
from app.domain.services.tools.code_search import CodeSearchTool


@pytest_asyncio.fixture
async def search_tool(tmp_path):
    (tmp_path / "sample.py").write_text(
        "class MyClass:\n"
        "    pass\n"
        "\n"
        "def my_function():\n"
        "    return 42\n"
    )
    return CodeSearchTool(cwd=str(tmp_path))


@pytest.mark.asyncio
async def test_find_symbol_class(search_tool):
    result = await search_tool.find_symbol("MyClass")
    assert result.success
    assert "MyClass" in result.data
    assert "sample.py" in result.data


@pytest.mark.asyncio
async def test_find_symbol_function(search_tool):
    result = await search_tool.find_symbol("my_function")
    assert result.success
    assert "my_function" in result.data


@pytest.mark.asyncio
async def test_find_symbol_not_found(search_tool):
    result = await search_tool.find_symbol("NonExistent")
    assert not result.success
    assert "not found" in result.message


@pytest.mark.asyncio
async def test_read_file_range_returns_correct_lines(search_tool):
    result = await search_tool.read_file_range("sample.py", 1, 2)
    assert result.success
    assert "class MyClass:" in result.data
    # Line numbers must appear
    assert "1" in result.data
    assert "2" in result.data


@pytest.mark.asyncio
async def test_read_file_range_start_beyond_file(search_tool):
    result = await search_tool.read_file_range("sample.py", 999, 1000)
    assert not result.success
    assert "999" in result.message


@pytest.mark.asyncio
async def test_read_file_range_file_not_found(search_tool):
    result = await search_tool.read_file_range("nonexistent.py", 1, 5)
    assert not result.success
