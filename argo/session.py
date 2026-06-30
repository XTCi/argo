from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path

ARGO_DIR = Path.home() / ".argo"
SESSIONS_DIR = ARGO_DIR / "sessions"


def _ensure_dirs() -> None:
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class ArgoSession:
    session_id: str
    cwd: str
    created_at: float
    updated_at: float
    messages: list = field(default_factory=list)
    checkpoints: list = field(default_factory=list)


def new_session(cwd: str) -> ArgoSession:
    now = time.time()
    return ArgoSession(
        session_id=str(uuid.uuid4()),
        cwd=cwd,
        created_at=now,
        updated_at=now,
    )


def save_session(session: ArgoSession) -> None:
    _ensure_dirs()
    target = SESSIONS_DIR / f"{session.session_id}.json"
    tmp = SESSIONS_DIR / f"{session.session_id}.json.tmp"
    session.updated_at = time.time()
    tmp.write_text(json.dumps(asdict(session), ensure_ascii=False), encoding="utf-8")
    tmp.replace(target)


def load_session(session_id: str) -> ArgoSession:
    path = SESSIONS_DIR / f"{session_id}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return ArgoSession(**data)


def list_sessions(cwd: str) -> list[ArgoSession]:
    _ensure_dirs()
    sessions: list[ArgoSession] = []
    for f in SESSIONS_DIR.glob("*.json"):
        try:
            s = load_session(f.stem)
            if s.cwd == cwd:
                sessions.append(s)
        except (json.JSONDecodeError, FileNotFoundError, ValueError, KeyError, TypeError):
            continue
    sessions.sort(key=lambda s: s.updated_at, reverse=True)
    return sessions[:5]
