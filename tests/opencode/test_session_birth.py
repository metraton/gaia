"""OpenCode's session start returns Claude Code's birth block and counts the user rows it carried.

Runs the bridge in process against a temporary HOME, GAIA_DATA_DIR and GAIA_DB,
and collects the Bun driver that asserts which sessions the plugin asks for it.
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import copy_bootstrapped_db

_ROOT = Path(__file__).resolve().parents[2]
for _path in (str(_ROOT), str(_ROOT / "hooks"), str(_ROOT / "opencode")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

USER_SCOPE = "_gaia_user"
USER_ROW = "user_jorge"
USER_BODY = "Hecho: Jorge runs DevOps for a consultancy."


@pytest.fixture
def seeded_db(tmp_path, monkeypatch, bootstrapped_db_template):
    data = tmp_path / "data"
    db = copy_bootstrapped_db(bootstrapped_db_template, data / "gaia.db")
    (tmp_path / "home").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GAIA_DATA_DIR", str(data))
    monkeypatch.setenv("GAIA_DB", str(db))
    monkeypatch.setenv("GAIA_HOST", "opencode")
    monkeypatch.chdir(tmp_path)
    con = sqlite3.connect(db)
    try:
        con.execute("INSERT OR IGNORE INTO workspaces (name, identity) VALUES (?, ?)",
                    (USER_SCOPE, USER_SCOPE))
        con.execute(
            "INSERT INTO memory (workspace, name, type, description, body, class, audience, "
            "updated_at) VALUES (?, ?, 'user', ?, ?, 'anchor', 'any', '2026-09-01T00:00:00Z')",
            (USER_SCOPE, USER_ROW, USER_BODY, USER_BODY),
        )
        con.commit()
    finally:
        con.close()
    return db


def _injection_count(db: Path) -> int:
    con = sqlite3.connect(db)
    try:
        return con.execute(
            "SELECT injection_count FROM memory WHERE workspace = ? AND name = ?",
            (USER_SCOPE, USER_ROW),
        ).fetchone()[0]
    finally:
        con.close()


def test_session_birth_bridge_returns_the_claude_code_block_and_marks_user_rows(seeded_db):
    import bridge
    from modules.session.session_manifest import build_session_context

    response = bridge.handle({"event": "chat.message", "sessionID": "ses-main"})

    assert response["action"] == "allow"
    assert USER_BODY in response["additional_context"]
    assert response["additional_context"] == build_session_context()
    assert _injection_count(seeded_db) == 1


def test_session_birth_plugin_delivers_once_to_the_main_session_and_never_to_a_child():
    result = subprocess.run(
        ["bun", "test", str(_ROOT / "tests/opencode/session_birth.test.ts")],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
