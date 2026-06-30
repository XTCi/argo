from __future__ import annotations
import asyncio
import pytest
import pytest_asyncio
from app.domain.services.tools.shell_session import PersistentShellSession


@pytest_asyncio.fixture
async def session(tmp_path):
    s = PersistentShellSession(cwd=str(tmp_path))
    await s.start()
    yield s
    await s.close()


@pytest.mark.asyncio
async def test_run_simple_command(session):
    output, code = await session.run("echo hello")
    assert "hello" in output
    assert code == 0


@pytest.mark.asyncio
async def test_run_exit_code_failure(session):
    output, code = await session.run("exit 1 || true; false")
    assert code != 0


@pytest.mark.asyncio
async def test_cd_persists_across_calls(session, tmp_path):
    await session.run(f"cd {tmp_path}")
    output, code = await session.run("pwd")
    assert str(tmp_path) in output
    assert code == 0


@pytest.mark.asyncio
async def test_background_and_read_output(session):
    await session.run_background("for i in 1 2 3; do echo line$i; sleep 0.1; done", "bg1")
    await asyncio.sleep(0.5)
    out = await session.read_output("bg1", wait_seconds=0)
    assert "line1" in out


@pytest.mark.asyncio
async def test_read_output_unknown_process(session):
    out = await session.read_output("nonexistent", wait_seconds=0)
    assert out == ""


@pytest.mark.asyncio
async def test_auto_restart_after_process_death(session):
    # Kill the bash process directly
    session._proc.kill()
    await asyncio.sleep(0.1)
    # Next run should auto-restart transparently
    output, code = await session.run("echo alive")
    assert "alive" in output
    assert code == 0
