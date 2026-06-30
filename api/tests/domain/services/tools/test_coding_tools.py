from __future__ import annotations
import pytest
from pathlib import Path
from app.domain.services.tools.file_edit import FileEditTool
from app.domain.services.tools.code_search import CodeSearchTool

@pytest.mark.asyncio
async def test_write_and_read_file(tmp_path):
    tool = FileEditTool()
    filepath = str(tmp_path / "test.py")
    write_result = await tool.write_file(filepath, "def hello(): return 42")
    assert write_result.success is True
    read_result = await tool.read_file(filepath)
    assert "def hello" in read_result.data

@pytest.mark.asyncio
async def test_patch_file_success(tmp_path):
    tool = FileEditTool()
    filepath = str(tmp_path / "app.py")
    Path(filepath).write_text("def old_name(): pass")
    result = await tool.patch_file(filepath, "old_name", "new_name")
    assert result.success is True
    assert "new_name" in Path(filepath).read_text()

@pytest.mark.asyncio
async def test_patch_file_not_found(tmp_path):
    tool = FileEditTool()
    filepath = str(tmp_path / "app.py")
    Path(filepath).write_text("def foo(): pass")
    result = await tool.patch_file(filepath, "nonexistent", "replacement")
    assert result.success is False
    assert "not found" in result.message

@pytest.mark.asyncio
async def test_grep_files(tmp_path):
    (tmp_path / "main.py").write_text("def main(): pass\nmain()")
    tool = CodeSearchTool(cwd=str(tmp_path))
    result = await tool.grep_files("def main", path=".")
    assert result.success is True
    assert "main.py" in result.data
