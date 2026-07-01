from __future__ import annotations
import asyncio
from pathlib import Path


async def load_project_context(cwd: str) -> str:
    """Read README, recent git log, and key source files; return as a context string."""
    sections: list[str] = []

    # README — first 3000 chars, try common names in order
    for name in ("README.md", "README.rst", "README.txt", "README"):
        p = Path(cwd) / name
        if p.exists():
            text = p.read_text(errors="replace")[:3000]
            sections.append(f"## README\n{text}")
            break

    # Recent git log — 15 commits, 5 s timeout, silently skipped if not a git repo
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "log", "--oneline", "-15",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            cwd=cwd,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
        log = stdout.decode(errors="replace").strip()
        if log:
            sections.append(f"## Recent commits\n{log}")
    except (asyncio.TimeoutError, Exception):
        pass

    # Python file structure — top 3 levels, no __pycache__, first 50 matches
    try:
        proc = await asyncio.create_subprocess_shell(
            "find . -maxdepth 3 -name '*.py' | grep -v __pycache__ | sort | head -50",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            cwd=cwd,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
        files = stdout.decode(errors="replace").strip()
        if files:
            sections.append(f"## Key source files\n{files}")
    except (asyncio.TimeoutError, Exception):
        pass

    if not sections:
        return ""
    return "# Project Context\n\n" + "\n\n".join(sections) + "\n\n---\n\n"
