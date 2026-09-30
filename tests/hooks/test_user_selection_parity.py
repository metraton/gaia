"""Every subagent knows the user by the same rows the orchestrator was born with.

A rule the user established must reach a dispatched subagent exactly when it
reaches the session, and a row the user retired -- replaced through a
supersedes link, or kept only as a log -- must reach neither. Runs against a
temporary HOME, GAIA_DATA_DIR and GAIA_DB.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

from tests.conftest import copy_bootstrapped_db

_REPO_ROOT = Path(__file__).resolve().parents[2]
_HOOKS_DIR = str(_REPO_ROOT / "hooks")
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

from modules.context.kernel_builder import build_kernel_context  # noqa: E402
from modules.session import session_manifest  # noqa: E402

USER_SCOPE = "_gaia_user"

ROWS = {
    "user_jorge": ("anchor", "any", "Hecho: Jorge runs DevOps for a consultancy."),
    "user_pref_english": ("anchor", "orchestrator", "Preferencia: versioned text is in English."),
    "user_pref_ghx": ("anchor", "any", "Preferencia: GitHub goes through ghx per command."),
    "user_pref_old": ("anchor", "executor", "Preferencia: an older rule, since replaced."),
    "user_retired": ("log", "executor", "Preferencia: a retired note kept as history."),
}
LIVE = {"user_jorge", "user_pref_english", "user_pref_ghx"}


def _seed(db: Path) -> None:
    con = sqlite3.connect(db)
    try:
        con.execute("INSERT OR IGNORE INTO workspaces (name, identity) VALUES (?, ?)",
                    (USER_SCOPE, USER_SCOPE))
        for name, (class_, audience, body) in ROWS.items():
            con.execute(
                "INSERT INTO memory (workspace, name, type, description, body, class, "
                "audience, updated_at) VALUES (?, ?, 'user', ?, ?, ?, ?, '2026-09-01T00:00:00Z')",
                (USER_SCOPE, name, body, body, class_, audience),
            )
        con.execute(
            "INSERT INTO memory_links (workspace, src_name, dst_name, kind) "
            "VALUES (?, 'user_pref_english', 'user_pref_old', 'supersedes')",
            (USER_SCOPE,),
        )
        con.commit()
    finally:
        con.close()


def _present(text: str) -> set:
    return {name for name, (_, _, body) in ROWS.items() if body in text}


def test_the_kernel_carries_exactly_the_birth_blocks_user_rows(tmp_path, monkeypatch,
                                                               bootstrapped_db_template):
    data = tmp_path / "data"
    db = copy_bootstrapped_db(bootstrapped_db_template, data / "gaia.db")
    (tmp_path / "home").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GAIA_DATA_DIR", str(data))
    monkeypatch.setenv("GAIA_DB", str(db))
    monkeypatch.chdir(tmp_path)
    _seed(db)

    birth = session_manifest.build_session_context()
    kernel = build_kernel_context(
        {
            "contract_id": "a0123456789abcdef.beefcafe0123",
            "agent_id": "a0123456789abcdef",
            "workspace": "me",
            "dispatch_prompt": "fix the build",
            "kernel_sections": '{"role": "primary", "surface": "app_ci"}',
        },
        agent_name="developer", agents_dir=tmp_path / "agents", db_path=db,
    )

    assert _present(birth) == LIVE
    assert _present(kernel) == LIVE
