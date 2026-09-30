"""`gaia session preview` shows the birth block and leaves the database untouched.

The preview is how the user sees what a new session would receive without
opening one, so running it must not count as an injection, bump any memory
telemetry, or change a scheduled-task suspension. Runs the real CLI in a
subprocess against a temporary HOME, GAIA_DATA_DIR and GAIA_DB.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

from tests.conftest import IsolatedRuntimeEnv, copy_bootstrapped_db

_REPO_ROOT = Path(__file__).resolve().parents[2]
_HOOKS_DIR = str(_REPO_ROOT / "hooks")
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

USER_SCOPE = "_gaia_user"


def _seed(db: Path, workspace_root: Path, project: Path) -> None:
    con = sqlite3.connect(db)
    try:
        for ws in (USER_SCOPE, "me"):
            con.execute("INSERT OR IGNORE INTO workspaces (name, identity) VALUES (?, ?)", (ws, ws))
        con.execute("UPDATE workspaces SET root_path = ? WHERE name = 'me'", (str(workspace_root),))
        con.execute("INSERT INTO projects (workspace, name, path) VALUES ('me', 'gaia', ?)",
                    (str(project),))
        con.execute(
            "INSERT INTO project_context_contracts (workspace, contract_name, payload) "
            "VALUES ('me', 'project_identity', ?)",
            (json.dumps({"gaia": {"name": "gaia", "local_path": str(project)}}),),
        )
        for name, description in (("user_jorge", "Hecho: Jorge runs DevOps."),
                                  ("user_pref_ghx", "Preferencia: GitHub through ghx.")):
            con.execute(
                "INSERT INTO memory (workspace, name, type, description, body, class) "
                "VALUES (?, ?, 'user', ?, ?, 'anchor')",
                (USER_SCOPE, name, description, description),
            )
        con.execute(
            "INSERT INTO memory (workspace, name, type, description, body, class, status, initiative) "
            "VALUES ('me', 'thread_gaia_pr', 'project', 'open PR', 'open PR', 'thread', 'open', 'gaia')"
        )
        con.execute(
            "INSERT INTO schedule_suspensions (workspace, until, reason) "
            "VALUES ('me', '2026-01-01T00:00:00Z', 'lapsed on purpose')"
        )
        con.commit()
    finally:
        con.close()


def _dump(db: Path) -> list:
    con = sqlite3.connect(db)
    try:
        return list(con.iterdump())
    finally:
        con.close()


def test_preview_prints_the_four_sections_and_writes_nothing(tmp_path, bootstrapped_db_template):
    env = IsolatedRuntimeEnv(tmp_path / "runtime")
    db = copy_bootstrapped_db(bootstrapped_db_template, Path(env["GAIA_DB"]))
    workspace_root = tmp_path / "ws" / "me"
    project = workspace_root / "gaia"
    project.mkdir(parents=True)
    _seed(db, workspace_root, project)
    before = _dump(db)

    result = subprocess.run(
        [sys.executable, str(_REPO_ROOT / "bin" / "gaia"), "session", "preview"],
        cwd=project, env=dict(env), capture_output=True, text=True, timeout=120,
    )

    assert result.returncode == 0, result.stderr
    assert _dump(db) == before, "preview must not write the database"
    from modules.session.session_manifest import BIRTH_SECTION_HEADERS
    positions = [result.stdout.find(header) for header in BIRTH_SECTION_HEADERS]
    assert -1 not in positions and positions == sorted(positions), result.stdout
