from __future__ import annotations

import os
import sys
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
_API_PATH = PROJECT_ROOT / "api"

if str(_API_PATH) not in sys.path:
    sys.path.insert(0, str(_API_PATH))

from app.domain.models.app_config import AppConfig, LLMConfig, AgentConfig  # noqa: E402


def _config_path() -> Path:
    env = os.environ.get("ARGO_CONFIG_PATH")
    if env:
        p = Path(env)
        if not p.exists():
            raise FileNotFoundError(f"Config not found: {p}")
        return p
    p = PROJECT_ROOT / "api" / "config.yaml"
    if not p.exists():
        raise FileNotFoundError(
            f"Config file not found at {p}. "
            "Copy api/config.yaml.example to api/config.yaml and fill in your API key."
        )
    return p


def load_config() -> tuple[LLMConfig, AgentConfig]:
    raw = yaml.safe_load(_config_path().read_text())
    app_cfg = AppConfig(**raw)
    return app_cfg.llm_config, app_cfg.agent_config
