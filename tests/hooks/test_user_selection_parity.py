"""The session birth indexes only the user's live rows, and a subagent kernel carries none of them."""

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
    "user_jorge": ("anchor", "fact", "Jorge runs DevOps for a consultancy."),
    "user_pref_english": ("anchor", "preference", "Versioned text is in English."),
    "user_pref_ghx": ("anchor", "preference", "GitHub goes through ghx per command."),
    "user_pref_old": ("anchor", "preference", "An older rule, since replaced."),
    "user_retired": ("log", "preference", "A retired note kept as history."),
}
LIVE = {"user_jorge", "user_pref_english", "user_pref_ghx"}


def _seed(db: Path) -> None:
    con = sqlite3.connect(db)
    try:
        con.execute("INSERT OR IGNORE INTO workspaces (name, identity) VALUES (?, ?)",
                    (USER_SCOPE, USER_SCOPE))
        for name, (class_, kind, title) in ROWS.items():
            con.execute(
                "INSERT INTO memory (workspace, name, type, description, body, class, kind, "
                "owner_level, updated_at) VALUES (?, ?, 'user', ?, ?, ?, ?, 'user', '2026-09-01T00:00:00Z')",
                (USER_SCOPE, name, title, title, class_, kind),
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
    return {name for name, (_, _, title) in ROWS.items() if title in text}


def test_the_birth_indexes_the_live_user_rows_and_the_kernel_none(tmp_path, monkeypatch,
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
    assert _present(kernel) == set()
