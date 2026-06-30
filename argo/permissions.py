from __future__ import annotations

import logging
from typing import Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

_SHELL_TOOLS = frozenset({"shell_execute", "shell_background"})


class PermissionGateway:
    """Three-tier permission check: deny → allow → ask.

    Only shell_execute and shell_background are subject to checks.
    All other tools are always allowed.
    """

    def __init__(
        self,
        config,                          # PermissionsConfig
        yolo: bool = False,
        confirm_fn: Optional[Callable[[str], Awaitable[str]]] = None,
    ) -> None:
        self._config = config
        self._yolo = yolo
        self._confirm_fn = confirm_fn
        self._session_allowlist: set[str] = set()

    async def check(self, tool_name: str, arguments: dict) -> bool:
        """Return True (allow) or False (deny)."""
        if tool_name not in _SHELL_TOOLS:
            return True

        command = arguments.get("command", "")

        # 1. Deny rules always apply (even in yolo mode)
        for pattern in self._config.deny:
            if pattern in command:
                logger.warning("Permission denied by rule %r: %s", pattern, command)
                return False

        # 2. Session allowlist (from previous "!" responses)
        for entry in self._session_allowlist:
            if entry in command:
                return True

        # 3. Allow rules bypass ask
        for pattern in self._config.allow:
            if pattern in command:
                return True

        # 4. Yolo: skip ask
        if self._yolo:
            return True

        # 5. Ask rules → trigger confirm_fn
        for pattern in self._config.ask:
            if pattern in command:
                return await self._ask(command)

        # 6. Fallthrough: mode=ask → confirm, mode=strict → deny
        if self._config.mode == "strict":
            return False
        if self._config.mode == "ask":
            return await self._ask(command)
        return True  # mode=yolo fallthrough

    async def _ask(self, command: str) -> bool:
        if self._confirm_fn is None:
            return True  # no TUI — allow by default
        response = await self._confirm_fn(command)
        if response == "!":
            # Find the matching ask pattern to add to allowlist
            for pattern in self._config.ask:
                if pattern in command:
                    self._session_allowlist.add(pattern)
                    break
            return True
        return response == "y"
