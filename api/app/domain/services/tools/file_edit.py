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

def _lines_stripped(text: str) -> str:
    return "\n".join(line.strip() for line in text.splitlines())


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


def _fuzzy_find_and_replace(content: str, old_str: str, new_str: str) -> tuple[str, str | None]:
    """Try 4 matching strategies in order. Return (new_content, error_or_None)."""

    strategies = [
        ("exact",           lambda c, o: _find_all(c, o)),
        ("line_trim",       lambda c, o: _find_all(_lines_stripped(c), _lines_stripped(o))),
        ("whitespace_norm", lambda c, o: _find_all(_ws_norm(c), _ws_norm(o))),
        ("escape_norm",     lambda c, o: _find_all(c, _escape_norm(o))),
    ]

    for strategy_name, find_fn in strategies:
        if strategy_name == "exact":
            matches = find_fn(content, old_str)
            if len(matches) == 1:
                start, end = matches[0]
                return content[:start] + new_str + content[end:], None
            if len(matches) > 1:
                return content, (
                    f"patch_file failed: old_str appears {len(matches)} times — "
                    "add more surrounding context to make it unique."
                )
        else:
            norm_content = (
                _ws_norm(content) if strategy_name == "whitespace_norm" else
                _lines_stripped(content) if strategy_name == "line_trim" else
                content
            )
            norm_old = (
                _ws_norm(old_str) if strategy_name == "whitespace_norm" else
                _lines_stripped(old_str) if strategy_name == "line_trim" else
                _escape_norm(old_str)
            )
            matches = _find_all(norm_content, norm_old)
            if len(matches) == 1:
                start, end = matches[0]
                # Recover position in original content via first line of old_str
                first_line = old_str.splitlines()[0].strip()
                for i, line in enumerate(content.splitlines()):
                    if first_line in line.strip():
                        line_start = sum(len(l) + 1 for l in content.splitlines()[:i])
                        block_end = line_start + len(
                            "\n".join(content.splitlines()[i: i + len(old_str.splitlines())])
                        )
                        return content[:line_start] + new_str + content[block_end:], None
                # Fallback: splice using normalised offsets
                return content[:start] + new_str + norm_content[end:], None
            if len(matches) > 1:
                return content, (
                    f"patch_file failed: old_str appears {len(matches)} times — "
                    "add more surrounding context to make it unique."
                )

    # All strategies failed — return difflib hint
    first_line = old_str.splitlines()[0] if old_str.splitlines() else old_str
    all_lines = content.splitlines()
    close = difflib.get_close_matches(first_line, all_lines, n=3, cutoff=0.4)
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
            # Normalise to replacements list
            if replacements is None:
                if old_str is None:
                    return ToolResult(
                        success=False,
                        message="Provide either old_str/new_str or replacements",
                    )
                replacements = [{"old_str": old_str, "new_str": new_str or ""}]

            content = Path(filepath).read_text(encoding="utf-8")

            # Validate ALL replacements before writing (fail-fast)
            seen_old_strs: set[str] = set()
            for i, rep in enumerate(replacements, 1):
                o = rep.get("old_str", "")
                if o in seen_old_strs:
                    return ToolResult(
                        success=False,
                        message=f"Replacement {i}: old_str is duplicated in replacements list",
                    )
                seen_old_strs.add(o)
                _, err = _fuzzy_find_and_replace(content, o, rep.get("new_str", ""))
                if err:
                    return ToolResult(
                        success=False,
                        message=f"Replacement {i}: {err}",
                    )

            # Apply all replacements
            for rep in replacements:
                content, err = _fuzzy_find_and_replace(content, rep["old_str"], rep.get("new_str", ""))
                if err:
                    return ToolResult(success=False, message=err)

            Path(filepath).write_text(content, encoding="utf-8")
            return ToolResult(success=True, data={"replacements_applied": len(replacements)})
        except Exception as e:
            return ToolResult(success=False, message=str(e))
