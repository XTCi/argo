import sys
import os
from pathlib import Path
import pytest

# Patch sys.path so argo is importable from argo/
sys.path.insert(0, str(Path(__file__).parent.parent.parent))


def test_load_config_returns_llm_and_agent_config():
    from argo.config import load_config
    llm_cfg, agent_cfg, permissions_cfg = load_config()
    assert llm_cfg.model_name == "deepseek-chat"
    assert agent_cfg.max_iterations > 0
    assert permissions_cfg.mode in ("ask", "yolo", "strict")


def test_load_config_raises_on_missing_file(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGO_CONFIG_PATH", str(tmp_path / "nonexistent.yaml"))
    # Re-import forces re-read
    import importlib
    import argo.config as cfg_mod
    importlib.reload(cfg_mod)
    with pytest.raises(FileNotFoundError):
        cfg_mod.load_config()
    # Restore env
    monkeypatch.delenv("ARGO_CONFIG_PATH")
    importlib.reload(cfg_mod)


def test_api_on_sys_path_after_import():
    import argo.config  # noqa: F401 — side-effect: patches sys.path
    # If api/ is on path, this import works
    from app.domain.models.app_config import LLMConfig  # noqa: F401
