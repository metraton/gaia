"""SessionStart names the fix when the database and the code disagree on schema.

Behind: the code expects structure the database does not have yet, and the one
command that adds it is `gaia migrate apply`. Ahead: the store refuses every
write, so the session is told up front instead of discovering it write by write.
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


def test_database_ahead_says_writes_are_refused(tmp_path):
    db = _database_at(tmp_path / "gaia.db", EXPECTED_SCHEMA_VERSION + 1)

    context = _session_context(tmp_path, db)

    assert "Install a Gaia whose schema version is at least" in context
    assert "gaia migrate" not in context


def test_database_level_adds_no_banner(tmp_path):
    db = _database_at(tmp_path / "gaia.db", EXPECTED_SCHEMA_VERSION)

    context = _session_context(tmp_path, db)

    assert "## Database schema" not in context
