"""SessionStart names the fix when the database and the code disagree on schema.

Behind: the code expects structure the database does not have yet, and the one
command that adds it is `gaia migrate apply`. Ahead: this install is behind the
database, so the session gets one line naming its channel's update command.
The hook is driven as Claude Code drives it: a SessionStart event on stdin, the
JSON response on stdout.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
HOOK_PATH = _REPO / "hooks" / "session_start.py"
if str(_REPO / "bin") not in sys.path:
    sys.path.insert(0, str(_REPO / "bin"))

from cli.doctor import EXPECTED_SCHEMA_VERSION  # noqa: E402

from gaia.store import writer  # noqa: E402


def _database_at(path: Path, version: int) -> Path:
    writer._connect(path).close()
    con = sqlite3.connect(str(path))
    try:
        con.execute("DELETE FROM schema_version")
        con.execute(
            "INSERT INTO schema_version (version, applied_at, description) "
            "VALUES (?, '2026-01-01T00:00:00Z', 'test')",
            (version,),
        )
        con.commit()
    finally:
        con.close()
    return path


def _session_context(tmp_path: Path, db: Path) -> str:
    workspace = tmp_path / "workspace"
    (workspace / ".claude").mkdir(parents=True)
    plugin_data = tmp_path / "plugin-data"
    plugin_data.mkdir()
    env = os.environ.copy()
    env.pop("CLAUDE_PLUGIN_ROOT", None)
    env.update(
        {
            "GAIA_DB": str(db),
            "HOME": str(tmp_path),
            "CLAUDE_PLUGIN_DATA": str(plugin_data),
        }
    )
    proc = subprocess.run(
        [sys.executable, str(HOOK_PATH)],
        input=json.dumps(
            {"hook_event_name": "SessionStart", "session_id": "s-banner", "source": "startup"}
        ),
        capture_output=True,
        text=True,
        env=env,
        cwd=str(workspace),
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    response = json.loads(proc.stdout.strip() or "{}")
    return response.get("hookSpecificOutput", {}).get("additionalContext", "")


def test_database_behind_names_gaia_migrate(tmp_path):
    db = _database_at(tmp_path / "gaia.db", EXPECTED_SCHEMA_VERSION - 1)

    context = _session_context(tmp_path, db)

    assert "gaia migrate apply" in context
    assert f"v{EXPECTED_SCHEMA_VERSION - 1}" in context
    assert f"v{EXPECTED_SCHEMA_VERSION}" in context


def _census(db: Path) -> dict[str, int]:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        names = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        return {name: con.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0] for name in names}
    finally:
        con.close()


def _behind_line(context: str) -> str:
    lines = context.split("## Database schema\n", 1)[1].splitlines()
    return lines[0]


def test_an_older_install_meeting_a_newer_database_is_told_the_update_and_writes_nothing(tmp_path):
    db = _database_at(tmp_path / "gaia.db", EXPECTED_SCHEMA_VERSION + 1)
    before = _census(db)

    line = _behind_line(_session_context(tmp_path, db))

    assert f"is behind gaia.db (schema v{EXPECTED_SCHEMA_VERSION + 1})" in line
    assert "refuses every write" in line
    assert "Deliver to the user: run `gaia dev" in line
    assert "gaia migrate" not in line
    assert _census(db) == before


def test_an_older_install_inside_the_window_is_told_it_still_writes(tmp_path):
    db = _database_at(tmp_path / "gaia.db", EXPECTED_SCHEMA_VERSION + 1)
    con = sqlite3.connect(str(db))
    con.execute("UPDATE schema_version SET min_code_version = ?", (EXPECTED_SCHEMA_VERSION,))
    con.commit()
    con.close()

    line = _behind_line(_session_context(tmp_path, db))

    assert "still writes" in line
    assert "Deliver to the user: run `" in line


def test_database_level_adds_no_banner(tmp_path):
    db = _database_at(tmp_path / "gaia.db", EXPECTED_SCHEMA_VERSION)

    context = _session_context(tmp_path, db)

    assert "## Database schema" not in context
