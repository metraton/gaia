"""A specialist dispatched to a project is told how many standing notes it has and how to read them, never the notes.

Runs against a temporary HOME, GAIA_DATA_DIR and GAIA_DB.
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

from modules.context.kernel_builder import MEMORY_HEADING, build_kernel_context  # noqa: E402


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


def _note(db: Path, name: str, description: str, *, initiative="alpha", cls="anchor", deleted=False) -> None:
    con = sqlite3.connect(db)
    try:
        con.execute("INSERT OR IGNORE INTO workspaces (name, identity) VALUES ('me', 'me')")
        con.execute(
            "INSERT INTO memory (workspace, name, type, description, body, class, initiative, updated_at, "
            "deleted_at) VALUES ('me', ?, 'project', ?, 'the full body', ?, ?, '2026-10-01T00:00:00Z', ?)",
            (name, description, cls, initiative, "2026-10-02T00:00:00Z" if deleted else None),
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


def test_project_notes_are_counted_with_the_verbs_that_read_them_and_never_listed(db):
    _note(db, "atom_alpha_login_sso", "Fact: alpha signs in through the corporate SSO.")
    _note(db, "atom_alpha_admin_url", "Fact: alpha admin lives at https://admin.alpha.test.")

    kernel = _kernel(db, "alpha (/work/alpha)")

    assert (
        f"{MEMORY_HEADING}: me > alpha (/work/alpha)\nother notes 2 — not preloaded: `gaia memory search '<term>' --workspace me`; "
        "`gaia memory show <slug>`; this project's, bodies whole: "
        "`gaia memory get-relevant --initiative alpha --sections anchor`\n"
    ) in kernel
    assert "atom_alpha_login_sso" not in kernel and "the full body" not in kernel


def test_project_without_anchors_adds_nothing(db):
    _note(db, "thread_alpha_pending", "Pending: something", cls="thread")
    _note(db, "atom_alpha_gone", "Fact: retired.", deleted=True)
    _note(db, "atom_beta_other", "Fact: belongs to beta.", initiative="beta")

    without_project = _kernel(db, None)
    kernel = _kernel(db, "alpha (/work/alpha)")

    assert MEMORY_HEADING not in kernel
    assert kernel.replace("\nproject: alpha (/work/alpha)", "") == without_project
