import pytest
from pathlib import Path
from app.domain.services.tools.file_edit import FileEditTool


@pytest.fixture
def tmp_file(tmp_path):
    f = tmp_path / "sample.py"
    f.write_text("foo = 1\nbar = 2\nbaz = 3\n")
    return str(f)


@pytest.mark.asyncio
async def test_batch_applies_all_replacements(tmp_file):
    tool = FileEditTool()
    result = await tool.invoke("patch_file", filepath=tmp_file, replacements=[
        {"old_str": "foo = 1", "new_str": "foo = 10"},
        {"old_str": "bar = 2", "new_str": "bar = 20"},
    ])
    assert result.success
    assert result.data["replacements_applied"] == 2
    content = Path(tmp_file).read_text()
    assert "foo = 10" in content
    assert "bar = 20" in content


@pytest.mark.asyncio
async def test_batch_fails_fast_and_leaves_file_unchanged(tmp_file):
    tool = FileEditTool()
    result = await tool.invoke("patch_file", filepath=tmp_file, replacements=[
        {"old_str": "NOT_THERE", "new_str": "x"},
        {"old_str": "foo = 1", "new_str": "foo = 99"},
    ])
    assert not result.success
    assert "not found" in result.message
    # File must be unchanged because fail-fast happens before writing
    assert "foo = 1" in Path(tmp_file).read_text()


@pytest.mark.asyncio
async def test_backward_compat_old_str_new_str(tmp_file):
    tool = FileEditTool()
    result = await tool.invoke("patch_file", filepath=tmp_file,
                               old_str="baz = 3", new_str="baz = 30")
    assert result.success
    assert "baz = 30" in Path(tmp_file).read_text()


@pytest.mark.asyncio
async def test_batch_error_message_includes_index(tmp_file):
    tool = FileEditTool()
    result = await tool.invoke("patch_file", filepath=tmp_file, replacements=[
        {"old_str": "foo = 1", "new_str": "foo = 10"},
        {"old_str": "MISSING", "new_str": "y"},
    ])
    assert not result.success
    assert "2" in result.message  # "Replacement 2: ..."
