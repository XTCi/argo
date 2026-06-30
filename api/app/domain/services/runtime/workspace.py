from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

_GIT_TIMEOUT = 2.5
_PROJECT_MARKERS = (
    "pyproject.toml", "setup.py", "requirements.txt",
    "package.json", "Cargo.toml", "go.mod", "Makefile", "Dockerfile",
)
_VERIFY_TARGETS = ("test", "tests", "lint", "build")


def _run_git(cwd: str, *args: str) -> str:
    try:
        r = subprocess.run(
            ["git", "-C", cwd, *args],
            capture_output=True, text=True, timeout=_GIT_TIMEOUT,
        )
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


@dataclass(frozen=True)
class WorkspaceContext:
    """Session 开始时解析一次，不可变，cache-safe。"""
    cwd: str
    is_git_repo: bool
    branch: str
    git_status: str
    recent_commits: str
    manifests: tuple[str, ...]
    verify_commands: tuple[str, ...]

    def system_block(self) -> str:
        """生成注入 system prompt stable tier 的 workspace 快照。"""
        lines = [f"Workspace (snapshot at session start — re-check with git before acting):"]
        lines.append(f"- Root: {self.cwd}")
        if self.is_git_repo:
            if self.branch:
                lines.append(f"- Branch: {self.branch}")
            if self.git_status:
                lines.append(f"- Status: {self.git_status}")
            if self.recent_commits:
                lines.append("- Recent commits:")
                for c in self.recent_commits.splitlines():
                    lines.append(f"    {c}")
        if self.manifests:
            lines.append(f"- Project: {', '.join(self.manifests)}")
        if self.verify_commands:
            lines.append(f"- Verify: {'; '.join(self.verify_commands)}")
        return "\n".join(lines)


def _detect_manifests(root: Path) -> tuple[str, ...]:
    return tuple(m for m in _PROJECT_MARKERS if (root / m).is_file())


def _detect_verify_commands(root: Path) -> tuple[str, ...]:
    cmds: list[str] = []
    if (root / "pytest.ini").is_file() or (root / "pyproject.toml").is_file():
        cmds.append("pytest")
    if (root / "Makefile").is_file():
        try:
            content = (root / "Makefile").read_text()
            for t in _VERIFY_TARGETS:
                if f"{t}:" in content:
                    cmds.append(f"make {t}")
        except OSError:
            pass
    if (root / "package.json").is_file():
        cmds.append("npm test")
    return tuple(dict.fromkeys(cmds))


def resolve_workspace(cwd: str) -> WorkspaceContext:
    """解析工作空间上下文，每个 session 调用一次。"""
    root = Path(cwd)
    is_git = (root / ".git").exists()
    branch = _run_git(cwd, "branch", "--show-current") if is_git else ""
    status = _run_git(cwd, "status", "--short") if is_git else ""
    commits = _run_git(cwd, "log", "-3", "--pretty=%h %s") if is_git else ""
    return WorkspaceContext(
        cwd=cwd,
        is_git_repo=is_git,
        branch=branch,
        git_status=status,
        recent_commits=commits,
        manifests=_detect_manifests(root),
        verify_commands=_detect_verify_commands(root),
    )
