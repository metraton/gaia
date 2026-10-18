"""After a compaction both hosts give the orchestrator back the user's rows, as the session start does.

Runs against a bootstrapped copy of the schema in a temporary HOME, GAIA_DATA_DIR and GAIA_DB.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import copy_bootstrapped_db

_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(_ROOT), str(_ROOT / "hooks"), str(_ROOT / "opencode")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

SESSION = "ses-compacted"
USER_SCOPE = "_gaia_user"
PREFERENCE_BODY = "Always answer the user in Spanish."
ROWS = {
    "user_profile": ("Jorge runs DevOps for a consultancy.", "Jorge runs DevOps for a consultancy.", "any"),
    "user_pref_language": ("Preference: the answer language.", PREFERENCE_BODY, "orchestrator"),
}


@pytest.fixture
def db(tmp_path, monkeypatch, bootstrapped_db_template):
    data = tmp_path / "data"
    path = copy_bootstrapped_db(bootstrapped_db_template, data / "gaia.db")
    (tmp_path / "home").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GAIA_DATA_DIR", str(data))
    monkeypatch.setenv("GAIA_DB", str(path))
    monkeypatch.chdir(tmp_path)
    con = sqlite3.connect(path)
    try:
        con.execute("INSERT OR IGNORE INTO workspaces (name, identity) VALUES (?, ?)",
                    (USER_SCOPE, USER_SCOPE))
        for name, (description, body, audience) in ROWS.items():
            con.execute(
                "INSERT INTO memory (workspace, name, type, description, body, class, audience, "
                "updated_at) VALUES (?, ?, 'user', ?, ?, 'anchor', ?, '2026-09-01T00:00:00Z')",
                (USER_SCOPE, name, description, body, audience),
            )
        con.commit()
    finally:
        con.close()
    return path


def _injections(db: Path) -> dict[str, int]:
    con = sqlite3.connect(db)
    try:
        return dict(con.execute(
            "SELECT name, injection_count FROM memory WHERE workspace = ?", (USER_SCOPE,),
        ).fetchall())
    finally:
        con.close()


def _expected_user_sections() -> str:
    from gaia.store.reader import user_anchor_rows
    from modules.context.user_sections import render_user_sections

    sections = render_user_sections(user_anchor_rows())
    assert f"## User preferences\n\n{PREFERENCE_BODY}" in sections
    assert "## The user\n\nJorge runs DevOps for a consultancy." in sections
    return sections


def _assert_one_injection_per_delivery(db: Path, deliver_birth, deliver_refresh) -> tuple[str, str]:
    before = _injections(db)
    birth = deliver_birth()
    after_birth = _injections(db)
    refresh = deliver_refresh()

    assert after_birth == {name: count + 1 for name, count in before.items()}
    assert _injections(db) == {name: count + 1 for name, count in after_birth.items()}
    return birth, refresh


def _claude_start_context(source: str, cwd: Path) -> str:
    payload = json.dumps({"hook_event_name": "SessionStart", "session_id": SESSION, "source": source})
    proc = subprocess.run(
        [sys.executable, str(_ROOT / "hooks" / "session_start.py")],
        input=payload, capture_output=True, text=True, timeout=60, cwd=str(cwd), env=os.environ.copy(),
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]


def _opencode_start_context(source: str) -> str:
    import bridge

    event = {"event": "chat.message", "sessionID": SESSION}
    if source == "compact":
        event["source"] = "compact"
    return bridge.handle(event)["additional_context"]


def test_claude_code_compaction_refresh_carries_the_birth_user_sections_and_counts_them(db, tmp_path):
    sections = _expected_user_sections()

    birth, refresh = _assert_one_injection_per_delivery(
        db,
        lambda: _claude_start_context("startup", tmp_path),
        lambda: _claude_start_context("compact", tmp_path),
    )

    assert sections in birth
    assert "# Post-Compaction Context Refresh" in refresh
    assert sections in refresh


def test_opencode_compaction_refresh_carries_the_birth_user_sections_and_counts_them(db, monkeypatch):
    monkeypatch.setenv("GAIA_HOST", "opencode")
    sections = _expected_user_sections()

    birth, refresh = _assert_one_injection_per_delivery(
        db,
        lambda: _opencode_start_context("startup"),
        lambda: _opencode_start_context("compact"),
    )

    assert sections in birth
    assert "# Post-Compaction Context Refresh" in refresh
    assert sections in refresh
