from __future__ import annotations

import argo.config  # noqa: F401

from app.domain.models.event import (
    BaseEvent, ToolEvent, ToolEventStatus, MessageEvent, ErrorEvent, DoneEvent,
)
from app.domain.models.tool_result import ToolResult

# Morandi-inspired color palette (24-bit, low-saturation)
RESET   = "\x1b[0m"
BOLD    = "\x1b[1m"
DIM     = "\x1b[2m"
ITALIC  = "\x1b[3m"

GRAY    = "\x1b[90m"
RED_ERR = "\x1b[38;2;180;100;100m"   # muted rose — errors
GREEN_OK= "\x1b[38;2;100;160;120m"   # muted sage green — success
YELLOW  = "\x1b[38;2;170;150;90m"    # muted mustard — calling spinner
PURPLE  = "\x1b[38;2;140;100;160m"   # muted plum — tool name
TEAL    = "\x1b[38;2;100;150;150m"   # muted teal — assistant text
OCHRE   = "\x1b[38;2;160;130;100m"   # warm ochre — user label


def _truncate_args(args: dict) -> str:
    preferred_keys = ["command", "filepath", "path", "query", "pattern"]
    for key in preferred_keys:
        if key in args:
            val = str(args[key])
            return val[:60] + "…" if len(val) > 60 else val
    if args:
        val = str(next(iter(args.values())))
        return val[:60] + "…" if len(val) > 60 else val
    return ""


def _truncate_output(text: str, max_lines: int = 10) -> str:
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text
    visible = lines[:max_lines]
    hidden = len(lines) - max_lines
    return "\n".join(visible) + f"\n{GRAY}  [+{hidden} more lines]{RESET}"


def render_event(event: BaseEvent) -> str | None:
    if isinstance(event, ToolEvent):
        arg_preview = _truncate_args(event.function_args)
        fname = event.function_name

        if event.status == ToolEventStatus.CALLING:
            return f"  {YELLOW}⟳{RESET} {PURPLE}{fname}{RESET} {DIM}{arg_preview}{RESET}"

        # CALLED
        result: ToolResult | None = event.function_result
        summary = ""
        if result and result.message:
            summary = _truncate_output(result.message, max_lines=10)

        if result and result.success:
            line = f"  {GREEN_OK}✓{RESET} {PURPLE}{fname}{RESET}"
            if summary:
                first_line = summary.split("\n")[0]
                rest = "\n".join(summary.split("\n")[1:])
                line += f" {DIM}→{RESET} {first_line}"
                if rest:
                    for l in rest.split("\n"):
                        line += f"\n    {DIM}{l}{RESET}"
            return line
        else:
            line = f"  {RED_ERR}✗{RESET} {PURPLE}{fname}{RESET}"
            if summary:
                line += f" {DIM}→{RESET} {RED_ERR}{summary.split(chr(10))[0]}{RESET}"
            return line

    if isinstance(event, MessageEvent):
        # Wrap assistant text with subtle teal tint
        return f"{TEAL}{event.message}{RESET}"

    if isinstance(event, ErrorEvent):
        return f"\n  {RED_ERR}{BOLD}✘ error:{RESET} {event.error}\n"

    if isinstance(event, DoneEvent):
        return None

    return None
