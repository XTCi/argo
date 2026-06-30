# api/tests/domain/services/runtime/test_workspace.py
from __future__ import annotations
import os
import pytest
from unittest.mock import patch, MagicMock
from app.domain.services.runtime.workspace import resolve_workspace, WorkspaceContext

def test_resolve_workspace_in_git_repo(tmp_path):
    """git repo 中应能解析出 branch 和状态"""
    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="main\n"),           # git branch
            MagicMock(returncode=0, stdout="M  foo.py\n"),      # git status
            MagicMock(returncode=0, stdout="abc1234 fix: bug\n"),# git log
        ]
        (tmp_path / ".git").mkdir()
        ctx = resolve_workspace(str(tmp_path))
    assert ctx.branch == "main"
    assert ctx.is_git_repo is True

def test_resolve_workspace_detects_pyproject(tmp_path):
    """存在 pyproject.toml 时应检测到 Python 项目"""
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        ctx = resolve_workspace(str(tmp_path))
    assert "pyproject.toml" in ctx.manifests

def test_resolve_workspace_detects_pytest_verify(tmp_path):
    """存在 pytest.ini 时 verify_commands 应包含 pytest"""
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        ctx = resolve_workspace(str(tmp_path))
    assert "pytest" in ctx.verify_commands

def test_workspace_system_block_contains_branch(tmp_path):
    """system_block 应包含 git branch 信息"""
    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="feature/ctx-engine\n"),
            MagicMock(returncode=0, stdout=""),
            MagicMock(returncode=0, stdout=""),
        ]
        (tmp_path / ".git").mkdir()
        ctx = resolve_workspace(str(tmp_path))
    block = ctx.system_block()
    assert "feature/ctx-engine" in block
    assert "Workspace" in block
