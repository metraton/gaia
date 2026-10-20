"""A specialist dispatched to a project is told which standing notes that project has.

What the user feels: the facts the orchestrator curated about a project (a tool
URL, a login method) reach every specialist dispatched there as an index it can
open on demand; a project with no notes adds nothing to the dispatch, and the
index never costs the user's own rows their place. Runs against a temporary
HOME, GAIA_DATA_DIR and GAIA_DB.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

from tests.conftest import copy_bootstrapped_db

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _path in (_REPO_ROOT, _REPO_ROOT / "hooks"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from modules.context import kernel_builder  # noqa: E402
from modules.context.kernel_builder import (  # noqa: E402
    PROJECT_NOTES_BUDGET,
    PROJECT_NOTES_HEADING,
    build_kernel_context,
)

USER_BODY = "Preference: versioned text is written in English."


@pytest.fixture()
def db(tmp_path, monkeypatch, bootstrapped_db_template):
    data = tmp_path / "data"
    db = copy_bootstrapped_db(bootstrapped_db_template, data / "gaia.db")
    (tmp_path / "home").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GAIA_DATA_DIR", str(data))
    monkeypatch.setenv("GAIA_DB", str(db))
    monkeypatch.chdir(tmp_path)
    return db


def _note(db: Path, name: str, description: str, *, initiative="alpha", cls="anchor",
          updated_at="2026-10-01T00:00:00Z", deleted=False) -> None:
    con = sqlite3.connect(db)
    try:
        con.execute(
            "INSERT OR IGNORE INTO workspaces (name, identity) VALUES ('me', 'me')"
        )
        con.execute(
            "INSERT INTO memory (workspace, name, type, description, body, class, "
            "initiative, updated_at, deleted_at) "
            "VALUES ('me', ?, 'project', ?, 'the full body', ?, ?, ?, ?)",
            (name, description, cls, initiative, updated_at,
             "2026-10-02T00:00:00Z" if deleted else None),
        )
        con.commit()
    finally:
        con.close()


def _user_row(db: Path) -> None:
    con = sqlite3.connect(db)
    try:
        con.execute(
            "INSERT OR IGNORE INTO workspaces (name, identity) "
            "VALUES ('_gaia_user', '_gaia_user')"
        )
        con.execute(
            "INSERT INTO memory (workspace, name, type, description, body, class, "
            "audience, updated_at) VALUES ('_gaia_user', 'user_pref', 'user', ?, ?, "
            "'anchor', 'any', '2026-09-01T00:00:00Z')",
            (USER_BODY, USER_BODY),
        )
        con.commit()
    finally:
        con.close()


def _kernel(db: Path, project: str | None) -> str:
    row = {
        "contract_id": "a0123456789abcdef.beefcafe0123",
        "agent_id": "a0123456789abcdef",
        "workspace": "me",
        "dispatch_prompt": "fix the build",
        "dispatch_project": project,
        "kernel_sections": '{"role": "primary", "surface": "app_ci"}',
    }
    return build_kernel_context(row, agent_name="developer", db_path=db)


def test_project_with_notes_carries_an_index_without_bodies(db):
    _user_row(db)
    _note(db, "atom_alpha_login_sso", "Fact: alpha signs in through the corporate SSO.")
    _note(db, "atom_alpha_admin_url", "Fact: alpha admin lives at https://admin.alpha.test.",
          updated_at="2026-10-05T00:00:00Z")

    kernel = _kernel(db, "alpha (/work/alpha)")

    assert PROJECT_NOTES_HEADING in kernel
    assert "- atom_alpha_admin_url: Fact: alpha admin lives at https://admin.alpha.test." in kernel
    assert "- atom_alpha_login_sso: Fact: alpha signs in through the corporate SSO." in kernel
    assert kernel.index("atom_alpha_admin_url") < kernel.index("atom_alpha_login_sso")
    assert "the full body" not in kernel
    assert "more —" not in kernel


def test_a_long_kernel_loses_the_cli_index_before_the_notes_and_the_notes_before_the_user_rows(db):
    _user_row(db)
    _note(db, "atom_alpha_login_sso", "Fact: alpha signs in through the corporate SSO.")

    kernel = _kernel(db, "alpha")

    order = [
        kernel.index(kernel_builder.KERNEL_HEADING),
        kernel.index(USER_BODY),
        kernel.index(PROJECT_NOTES_HEADING),
        kernel.index(kernel_builder.CLI_HEADING),
    ]
    assert order == sorted(order)


def test_index_is_capped_and_names_the_rest(db):
    for i in range(40):
        _note(db, f"atom_alpha_fact_number_{i:02d}", "Fact: " + "word " * 40,
              updated_at=f"2026-10-{(i % 28) + 1:02d}T00:00:00Z")

    block = kernel_builder.build_project_notes_block(
        {"workspace": "me", "dispatch_project": "alpha"}, db_path=db,
    )

    assert 0 < len(block) <= PROJECT_NOTES_BUDGET
    shown = block.count("\n- ")
    assert 0 < shown < 40
    assert block.endswith(
        f"+{40 - shown} more — `gaia memory get-relevant --initiative alpha --sections anchor`"
    )


def test_project_without_anchors_adds_nothing(db):
    _user_row(db)
    _note(db, "thread_alpha_pending", "Pending: something", cls="thread")
    _note(db, "atom_alpha_gone", "Fact: retired.", deleted=True)
    _note(db, "atom_beta_other", "Fact: belongs to beta.", initiative="beta")

    without_project = _kernel(db, None)
    kernel = _kernel(db, "alpha (/work/alpha)")

    assert PROJECT_NOTES_HEADING not in kernel
    assert "atom_beta_other" not in kernel
    assert kernel.replace("\nproject: alpha (/work/alpha)", "") == without_project
