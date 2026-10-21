"""A specialist is handed only the user rows marked for executors."""

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
BODIES = {
    "orchestrator": "Preference: report progress with each task id beside its title.",
    "executor": "Preference: run the suite with a temporary HOME before committing.",
    "any": "Preference: versioned text is written in English.",
}


def _seed(db: Path) -> None:
    con = sqlite3.connect(db)
    try:
        con.execute("INSERT OR IGNORE INTO workspaces (name, identity) VALUES (?, ?)",
                    (USER_SCOPE, USER_SCOPE))
        for audience, body in BODIES.items():
            con.execute(
                "INSERT INTO memory (workspace, name, type, description, body, class, "
                "audience, updated_at) "
                "VALUES (?, ?, 'user', ?, ?, 'anchor', ?, '2026-09-01T00:00:00Z')",
                (USER_SCOPE, f"user_pref_{audience}", body, body, audience),
            )
        con.commit()
    finally:
        con.close()


def test_orchestrator_only_rows_reach_birth_but_not_a_specialist_kernel(
    tmp_path, monkeypatch, bootstrapped_db_template,
):
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

    assert all(body in birth for body in BODIES.values())
    assert BODIES["orchestrator"] not in kernel
    assert BODIES["any"] not in kernel
    assert BODIES["executor"] in kernel


def test_specialist_kernel_carries_exactly_the_executor_row(
    tmp_path, monkeypatch, bootstrapped_db_template,
):
    data = tmp_path / "data"
    db = copy_bootstrapped_db(bootstrapped_db_template, data / "gaia.db")
    (tmp_path / "home").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GAIA_DATA_DIR", str(data))
    monkeypatch.setenv("GAIA_DB", str(db))
    monkeypatch.chdir(tmp_path)
    _seed(db)

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

    assert {audience for audience, body in BODIES.items() if body in kernel} == {"executor"}


def test_no_executor_row_means_no_user_block(tmp_path, monkeypatch, bootstrapped_db_template):
    data = tmp_path / "data"
    db = copy_bootstrapped_db(bootstrapped_db_template, data / "gaia.db")
    (tmp_path / "home").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GAIA_DATA_DIR", str(data))
    monkeypatch.setenv("GAIA_DB", str(db))
    monkeypatch.chdir(tmp_path)
    _seed(db)
    con = sqlite3.connect(db)
    con.execute("DELETE FROM memory WHERE audience = 'executor'")
    con.commit()
    con.close()

    from modules.context.kernel_builder import MEMORY_HEADING, build_memory_block

    assert build_memory_block(db_path=db) == ""
    assert MEMORY_HEADING not in build_kernel_context(
        {
            "contract_id": "a0123456789abcdef.beefcafe0123",
            "agent_id": "a0123456789abcdef",
            "workspace": "me",
            "dispatch_prompt": "fix the build",
            "kernel_sections": '{"role": "primary", "surface": "app_ci"}',
        },
        agent_name="developer", agents_dir=tmp_path / "agents", db_path=db,
    )
