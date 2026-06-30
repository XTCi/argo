from __future__ import annotations
import pytest
from unittest.mock import AsyncMock
from argo.permissions import PermissionGateway
from app.domain.models.app_config import PermissionsConfig


def make_cfg(**kwargs):
    defaults = dict(mode="ask", deny=["rm -rf /"], ask=["rm ", "sudo "], allow=["git log"])
    defaults.update(kwargs)
    return PermissionsConfig(**defaults)


@pytest.mark.asyncio
async def test_deny_rule_blocks():
    gw = PermissionGateway(config=make_cfg(), yolo=False)
    result = await gw.check("shell_execute", {"command": "rm -rf /"})
    assert result is False


@pytest.mark.asyncio
async def test_allow_rule_bypasses_ask():
    gw = PermissionGateway(config=make_cfg(), yolo=False)
    result = await gw.check("shell_execute", {"command": "git log --oneline"})
    assert result is True


@pytest.mark.asyncio
async def test_ask_rule_calls_confirm_fn_and_allows_on_y():
    confirm = AsyncMock(return_value="y")
    gw = PermissionGateway(config=make_cfg(), yolo=False, confirm_fn=confirm)
    result = await gw.check("shell_execute", {"command": "rm dist/"})
    assert result is True
    confirm.assert_called_once()


@pytest.mark.asyncio
async def test_ask_rule_denies_on_n():
    confirm = AsyncMock(return_value="n")
    gw = PermissionGateway(config=make_cfg(), yolo=False, confirm_fn=confirm)
    result = await gw.check("shell_execute", {"command": "rm dist/"})
    assert result is False


@pytest.mark.asyncio
async def test_bang_adds_to_session_allowlist():
    confirm = AsyncMock(return_value="!")
    gw = PermissionGateway(config=make_cfg(), yolo=False, confirm_fn=confirm)
    # First call: confirm triggered, user picks "!"
    await gw.check("shell_execute", {"command": "rm dist/"})
    # Second call: should be allowed without calling confirm again
    confirm.reset_mock()
    result = await gw.check("shell_execute", {"command": "rm dist/"})
    assert result is True
    confirm.assert_not_called()


@pytest.mark.asyncio
async def test_yolo_skips_ask_rules():
    confirm = AsyncMock(return_value="n")
    gw = PermissionGateway(config=make_cfg(), yolo=True, confirm_fn=confirm)
    result = await gw.check("shell_execute", {"command": "rm dist/"})
    assert result is True
    confirm.assert_not_called()


@pytest.mark.asyncio
async def test_yolo_still_blocks_deny_rules():
    gw = PermissionGateway(config=make_cfg(), yolo=True)
    result = await gw.check("shell_execute", {"command": "rm -rf /"})
    assert result is False


@pytest.mark.asyncio
async def test_non_shell_tools_always_allowed():
    gw = PermissionGateway(config=make_cfg(), yolo=False)
    result = await gw.check("read_file", {"filepath": "/etc/passwd"})
    assert result is True
