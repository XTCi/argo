from __future__ import annotations

import asyncio
import atexit
import collections
import logging
import os
import signal
import uuid
from typing import Optional

logger = logging.getLogger(__name__)

_RING_MAXLEN = 10_000


class PersistentShellSession:
    """A single bash process that lives for the entire Argo session.

    Commands are sent over stdin; a sentinel line marks completion.
    Background processes are tracked in a ring-buffer keyed by process_id.
    """

    def __init__(self, cwd: str) -> None:
        self._cwd = cwd
        self._sentinel = f"__ARGO_DONE_{uuid.uuid4().hex}__"
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._bg_buffers: dict[str, collections.deque] = {}
        self._bg_tasks: dict[str, asyncio.Task] = {}
        self._bg_procs: dict[str, asyncio.subprocess.Process] = {}
        self._run_lock = asyncio.Lock()
        atexit.register(self._sync_close)

    async def start(self) -> None:
        self._proc = await asyncio.create_subprocess_exec(
            "bash", "--norc", "--noprofile",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=self._cwd,
        )

    async def _ensure_alive(self) -> None:
        if self._proc is None or self._proc.returncode is not None:
            logger.warning("Shell process died — restarting")
            await self.start()

    async def run(self, command: str, timeout: int = 30) -> tuple[str, int]:
        async with self._run_lock:
            await self._ensure_alive()
            wrapped = f'{command}\necho "{self._sentinel}:$?"\n'
            self._proc.stdin.write(wrapped.encode())
            await self._proc.stdin.drain()

            lines: list[str] = []
            exit_code = 0
            try:
                async with asyncio.timeout(timeout):
                    while True:
                        raw = await self._proc.stdout.readline()
                        if not raw:
                            # EOF — process died before emitting sentinel
                            await self._proc.wait()
                            exit_code = self._proc.returncode or 1
                            break
                        line = raw.decode(errors="replace")
                        if line.startswith(f"{self._sentinel}:"):
                            exit_code = int(line.split(":")[1].strip())
                            break
                        lines.append(line)
            except asyncio.TimeoutError:
                try:
                    self._proc.kill()
                except ProcessLookupError:
                    pass
                return f"[timed out after {timeout}s]\n" + "".join(lines), 124

            return "".join(lines), exit_code

    async def run_background(self, command: str, process_id: str) -> None:
        buf: collections.deque[str] = collections.deque(maxlen=_RING_MAXLEN)
        self._bg_buffers[process_id] = buf

        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=self._cwd,
        )

        async def _drain() -> None:
            assert proc.stdout is not None
            while True:
                raw = await proc.stdout.readline()
                if not raw:
                    break
                buf.append(raw.decode(errors="replace"))

        self._bg_procs[process_id] = proc
        task = asyncio.create_task(_drain())
        self._bg_tasks[process_id] = task

    def has_process(self, process_id: str) -> bool:
        return process_id in self._bg_buffers

    async def read_output(self, process_id: str, wait_seconds: float = 2.0) -> str:
        if process_id not in self._bg_buffers:
            return ""
        if wait_seconds > 0:
            await asyncio.sleep(wait_seconds)
        return "".join(self._bg_buffers[process_id])

    async def close(self) -> None:
        for task in self._bg_tasks.values():
            task.cancel()
        if self._bg_tasks:
            await asyncio.gather(*self._bg_tasks.values(), return_exceptions=True)
        for proc in self._bg_procs.values():
            try:
                proc.terminate()
            except ProcessLookupError:
                pass
        if self._proc and self._proc.returncode is None:
            try:
                self._proc.terminate()
                await asyncio.wait_for(self._proc.wait(), timeout=2.0)
            except (asyncio.TimeoutError, ProcessLookupError):
                try:
                    self._proc.kill()
                except ProcessLookupError:
                    pass

    def _sync_close(self) -> None:
        for proc in self._bg_procs.values():
            try:
                os.kill(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        if self._proc and self._proc.returncode is None:
            try:
                os.kill(self._proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
