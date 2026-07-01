from __future__ import annotations
import pytest
from pathlib import Path
from app.domain.services.runtime.project_context import load_project_context


@pytest.mark.asyncio
async def test_empty_directory_returns_string(tmp_path):
    result = await load_project_context(str(tmp_path))
    assert isinstance(result, str)


@pytest.mark.asyncio
async def test_readme_is_included(tmp_path):
    (tmp_path / "README.md").write_text("# My Project\nThis is a test project.")
    result = await load_project_context(str(tmp_path))
    assert "# Project Context" in result
    assert "My Project" in result


@pytest.mark.asyncio
async def test_readme_truncated_at_3000_chars(tmp_path):
    (tmp_path / "README.md").write_text("x" * 5000)
    result = await load_project_context(str(tmp_path))
    assert result.count("x") == 3001


@pytest.mark.asyncio
async def test_result_ends_with_separator_when_content_found(tmp_path):
    (tmp_path / "README.md").write_text("# Test")
    result = await load_project_context(str(tmp_path))
    assert result.endswith("---\n\n")


@pytest.mark.asyncio
async def test_rst_readme_is_found(tmp_path):
    (tmp_path / "README.rst").write_text("My rst readme")
    result = await load_project_context(str(tmp_path))
    assert "My rst readme" in result
