import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import json
import time
import uuid
import pytest
from unittest.mock import patch

from argo.session import ArgoSession, new_session, save_session, load_session, list_sessions


@pytest.fixture
def tmp_sessions(tmp_path):
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    with patch("argo.session.SESSIONS_DIR", sessions_dir):
        yield sessions_dir


def test_new_session_has_correct_fields(tmp_sessions):
    s = new_session(cwd="/my/project")
    assert s.cwd == "/my/project"
    assert len(s.session_id) == 36  # UUID format
    assert s.messages == []
    assert s.checkpoints == []
    assert s.created_at > 0


def test_save_and_load_roundtrip(tmp_sessions):
    s = new_session(cwd="/my/project")
    s.messages.append({"role": "user", "content": "hello"})
    s.checkpoints.append({"filepath": "/f.py", "content": "old"})
    original_created_at = s.created_at
    save_session(s)

    loaded = load_session(s.session_id)
    assert loaded.session_id == s.session_id
    assert loaded.cwd == "/my/project"
    assert loaded.messages == [{"role": "user", "content": "hello"}]
    assert loaded.created_at == original_created_at
    assert loaded.updated_at >= original_created_at
    assert loaded.checkpoints == [{"filepath": "/f.py", "content": "old"}]


def test_save_is_atomic(tmp_sessions):
    s = new_session(cwd="/my/project")
    save_session(s)
    json_file = tmp_sessions / f"{s.session_id}.json"
    assert json_file.exists()
    # tmp file must not remain
    assert not (tmp_sessions / f"{s.session_id}.json.tmp").exists()


def test_list_sessions_filtered_by_cwd(tmp_sessions):
    s1 = new_session(cwd="/proj/a")
    s2 = new_session(cwd="/proj/b")
    s3 = new_session(cwd="/proj/a")
    for s in [s1, s2, s3]:
        save_session(s)

    result = list_sessions(cwd="/proj/a")
    ids = {r.session_id for r in result}
    assert s1.session_id in ids
    assert s3.session_id in ids
    assert s2.session_id not in ids


def test_list_sessions_sorted_desc_by_updated_at(tmp_sessions):
    sessions = []
    for i in range(3):
        s = new_session(cwd="/proj")
        s.updated_at = float(i)
        save_session(s)
        sessions.append(s)

    result = list_sessions(cwd="/proj")
    assert result[0].session_id == sessions[2].session_id


def test_list_sessions_max_5(tmp_sessions):
    for _ in range(7):
        s = new_session(cwd="/proj")
        save_session(s)
    result = list_sessions(cwd="/proj")
    assert len(result) <= 5


def test_load_corrupt_session_raises(tmp_sessions):
    bad_file = tmp_sessions / "corrupt.json"
    bad_file.write_text("not json {{{")
    with pytest.raises(Exception):
        load_session("corrupt")
