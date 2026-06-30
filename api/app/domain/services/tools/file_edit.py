from __future__ import annotations
import difflib
import re as _re
from pathlib import Path
from typing import Optional
from app.domain.models.tool_result import ToolResult
from .base import BaseTool, tool


# ---------------------------------------------------------------------------
# Private fuzzy-matching helpers
# ---------------------------------------------------------------------------

def _ws_norm(text: str) -> str:
    return _re.sub(r"[ \t]+", " ", text)


def _escape_norm(text: str) -> str:
    return text.replace("\\n", "\n").replace("\\t", "\t")


def _find_all(content: str, pattern: str) -> list[tuple[int, int]]:
    results = []
    start = 0
    while True:
        idx = content.find(pattern, start)
        if idx == -1:
            break
        results.append((idx, idx + len(pattern)))
        start = idx + 1
    return results


def _apply_via_lines(
    content: str, old_str: str, new_str: str, norm_fn
) -> str | None:
    """Find old_str block in content using norm_fn for line comparison; replace in original."""
    old_lines = old_str.splitlines()
    if not old_lines:
        return None
    norm_old = [norm_fn(l) for l in old_lines]
    content_lines = content.splitlines(keepends=True)
    for i in range(len(content_lines) - len(old_lines) + 1):
        window = content_lines[i : i + len(old_lines)]
        if all(norm_fn(w.rstrip("\r\n")) == norm_old[j] for j, w in enumerate(window)):
            before = "".join(content_lines[:i])
            after = "".join(content_lines[i + len(old_lines) :])
            # Preserve trailing newline of matched block when new_str omits it
            last_line_ending = ""
            last_win_line = window[-1]
            if last_win_line.endswith("\n") and not new_str.endswith("\n"):
                last_line_ending = "\n"
            return before + new_str + last_line_ending + after
    return None


def _fuzzy_find_and_replace(content: str, old_str: str, new_str: str) -> tuple[str, str | None]:
    """Try 4 matching strategies in order. Return (new_content, error_or_None)."""
    # Strategy 1: exact (offset-based is safe here — no normalisation)
    matches = _find_all(content, old_str)
    if len(matches) == 1:
        s, e = matches[0]
        return content[:s] + new_str + content[e:], None
    if len(matches) > 1:
        return content, (
            f"patch_file failed: old_str appears {len(matches)} times — "
            "add more surrounding context to make it unique."
        )

    # Strategy 2: line_trim — strip each line before comparing
    result = _apply_via_lines(content, old_str, new_str, lambda l: l.strip())
    if result is not None:
        return result, None

    # Strategy 3: whitespace_norm — collapse internal whitespace runs
    result = _apply_via_lines(content, old_str, new_str, _ws_norm)
    if result is not None:
        return result, None

    # Strategy 4: escape_norm — convert literal \n and \t
    norm_old = _escape_norm(old_str)
    matches = _find_all(content, norm_old)
    if len(matches) == 1:
        s, e = matches[0]
        return content[:s] + new_str + content[e:], None

    # All strategies failed — return difflib hint
    first_line = old_str.splitlines()[0] if old_str.splitlines() else old_str
    close = difflib.get_close_matches(first_line, content.splitlines(), n=3, cutoff=0.4)
    hint = ""
    if close:
        hint = "\n\nClosest lines in file:\n" + "\n".join(f"  {l}" for l in close)
        hint += "\n\nSuggestion: expand old_str to include surrounding lines for a unique match."
    return content, f"patch_file failed: old_str not found in file.{hint}"


# ---------------------------------------------------------------------------
# FileEditTool
# ---------------------------------------------------------------------------

class FileEditTool(BaseTool):
    """文件读写工具 —— 支持读取、完整写入、精确 patch（old_str → new_str）。"""
    name: str = "file_edit"

    @tool(
        name="read_file",
        description="读取文件内容。大文件可指定 start_line/end_line 范围。",
        parameters={
            "filepath": {"type": "string", "description": "文件路径"},
            "start_line": {"type": "integer", "description": "起始行号（1-based，可选）"},
            "end_line": {"type": "integer", "description": "结束行号（1-based，可选）"},
        },
        required=["filepath"],
    )
    async def read_file(
        self, filepath: str, start_line: Optional[int] = None, end_line: Optional[int] = None
    ) -> ToolResult:
        try:
            content = Path(filepath).read_text(encoding="utf-8", errors="replace")
            if start_line or end_line:
                lines = content.splitlines()
                s = (start_line or 1) - 1
                e = end_line or len(lines)
                content = "\n".join(lines[s:e])
            return ToolResult(success=True, data=content)
        except Exception as e:
            return ToolResult(success=False, message=str(e))

    @tool(
        name="write_file",
        description="写入文件（完整覆盖）。写入前应先用 read_file 确认当前内容。",
        parameters={
            "filepath": {"type": "string", "description": "文件路径"},
            "content": {"type": "string", "description": "写入的完整内容"},
        },
        required=["filepath", "content"],
    )
    async def write_file(self, filepath: str, content: str) -> ToolResult:
        try:
            path = Path(filepath)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            return ToolResult(success=True, data={"bytes_written": len(content.encode())})
        except Exception as e:
            return ToolResult(success=False, message=str(e))

    @tool(
        name="patch_file",
        description=(
            "Replace text in a file. Provide either:\n"
            "  - old_str + new_str for a single replacement, OR\n"
            "  - replacements: [{old_str, new_str}, ...] for multiple replacements in one call.\n"
            "Each old_str must appear exactly once. All replacements are validated before writing "
            "(fail-fast — file is unchanged if any replacement fails).\n"
            "Fuzzy matching (indentation drift, whitespace normalisation, escape sequences) is "
            "attempted automatically when exact match fails."
        ),
        parameters={
            "filepath": {"type": "string", "description": "Path to the file to patch"},
            "old_str": {"type": "string", "description": "Text to replace (single replacement)"},
            "new_str": {"type": "string", "description": "Replacement text (single replacement)"},
            "replacements": {
                "type": "array",
                "description": "Multiple replacements applied in order",
                "items": {
                    "type": "object",
                    "properties": {
                        "old_str": {"type": "string"},
                        "new_str": {"type": "string"},
                    },
                    "required": ["old_str", "new_str"],
                },
            },
        },
        required=["filepath"],
    )
    async def patch_file(
        self,
        filepath: str,
        old_str: Optional[str] = None,
        new_str: Optional[str] = None,
        replacements: Optional[list] = None,
    ) -> ToolResult:
        try:
            if replacements is None:
                if old_str is None:
                    return ToolResult(success=False, message="Provide either old_str/new_str or replacements")
                replacements = [{"old_str": old_str, "new_str": new_str or ""}]

            content = Path(filepath).read_text(encoding="utf-8")
            seen: set[str] = set()

            for i, rep in enumerate(replacements, 1):
                o = rep.get("old_str", "")
                if o in seen:
                    return ToolResult(success=False,
                                      message=f"Replacement {i}: old_str is duplicated in replacements list")
                seen.add(o)
                content, err = _fuzzy_find_and_replace(content, o, rep.get("new_str", ""))
                if err:
                    return ToolResult(success=False, message=f"Replacement {i}: {err}")

            Path(filepath).write_text(content, encoding="utf-8")
            return ToolResult(success=True, data={"replacements_applied": len(replacements)})
        except Exception as e:
            return ToolResult(success=False, message=str(e))
