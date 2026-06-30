from __future__ import annotations
import pytest
from pathlib import Path
from app.domain.services.tools.file_edit import FileEditTool


@pytest.fixture
def tmp_file(tmp_path):
    f = tmp_path / "code.py"
    f.write_text("def hello():\n    return 42\n")
    return str(f)


@pytest.mark.asyncio
async def test_exact_match_replaces(tmp_file):
    tool = FileEditTool()
    result = await tool.invoke("patch_file", filepath=tmp_file,
                               old_str="return 42", new_str="return 99")
    assert result.success
    assert "return 99" in Path(tmp_file).read_text()


@pytest.mark.asyncio
async def test_line_trim_match_handles_indentation_drift(tmp_file):
    """LLM sometimes forgets indentation — line_trim strategy should catch it."""
    tool = FileEditTool()
    # old_str has wrong indentation (no leading spaces)
    result = await tool.invoke("patch_file", filepath=tmp_file,
                               old_str="return 42", new_str="return 99")
    assert result.success


@pytest.mark.asyncio
async def test_whitespace_norm_matches_extra_spaces(tmp_path):
    f = tmp_path / "f.py"
    f.write_text("x  =  1\n")
    tool = FileEditTool()
    # old_str has normalized spaces
    result = await tool.invoke("patch_file", filepath=str(f),
                               old_str="x = 1", new_str="x = 2")
    assert result.success
    assert "x = 2" in f.read_text()


@pytest.mark.asyncio
async def test_zero_matches_returns_difflib_hint(tmp_file):
    tool = FileEditTool()
    result = await tool.invoke("patch_file", filepath=tmp_file,
                               old_str="def goodbye():", new_str="pass")
    assert not result.success
    # Should mention the closest line as a hint
    assert "hello" in result.message or "Closest" in result.message


@pytest.mark.asyncio
async def test_multiple_matches_returns_not_unique_error(tmp_path):
    f = tmp_path / "f.py"
    f.write_text("x = 1\nx = 1\n")
    tool = FileEditTool()
    result = await tool.invoke("patch_file", filepath=str(f),
                               old_str="x = 1", new_str="x = 2")
    assert not result.success
    assert "2" in result.message  # mentions match count


@pytest.mark.asyncio
async def test_file_unchanged_on_zero_match(tmp_file):
    tool = FileEditTool()
    original = Path(tmp_file).read_text()
    await tool.invoke("patch_file", filepath=tmp_file,
                      old_str="DOES_NOT_EXIST", new_str="x")
    assert Path(tmp_file).read_text() == original


@pytest.mark.asyncio
async def test_whitespace_norm_multiline_file(tmp_path):
    """Regression: splice corruption when pre-match lines have extra whitespace."""
    f = tmp_path / "f.py"
    f.write_text("a  =  0\nx  =  1\n")
    tool = FileEditTool()
    result = await tool.invoke("patch_file", filepath=str(f),
                               old_str="x = 1", new_str="x = 2")
    assert result.success
    content = f.read_text()
    assert content == "a  =  0\nx = 2\n"
    assert "a  =  0" in content  # pre-match line must be intact
