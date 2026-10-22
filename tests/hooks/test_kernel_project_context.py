"""A specialist dispatched to a project reads that project's context sections in its kernel, within the shared budget."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

from tests.conftest import copy_bootstrapped_db

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _path in (_REPO_ROOT, _REPO_ROOT / "hooks"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from modules.context.kernel_builder import (  # noqa: E402
    KERNEL_BUDGET,
    build_dispatch_kernel,
    build_kernel_context,
)

HEADING = "# Project context"
PROJECT = "alpha"
IDENTITY = "github.com/me/alpha"
DISPLAY = "alpha (/work/alpha)"


def _pointer(section: str) -> str:
    return f"`gaia context get-contract --section {section} --project {PROJECT}`"


@pytest.fixture()
def db(tmp_path, monkeypatch, bootstrapped_db_template):
    data = tmp_path / "data"
    db = copy_bootstrapped_db(bootstrapped_db_template, data / "gaia.db")
    (tmp_path / "home").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GAIA_DATA_DIR", str(data))
    monkeypatch.setenv("GAIA_DB", str(db))
    monkeypatch.chdir(tmp_path)
    con = sqlite3.connect(db)
    try:
        con.execute("INSERT OR IGNORE INTO workspaces (name, identity) VALUES ('me', 'me')")
        con.execute(
            "INSERT INTO projects (workspace, name, path, project_identity) VALUES ('me', ?, '/work/alpha', ?)",
            (PROJECT, IDENTITY),
        )
        con.commit()
    finally:
        con.close()
    return db


def _execute(db: Path, sql: str, *params) -> None:
    con = sqlite3.connect(db)
    try:
        con.execute(sql, params)
        con.commit()
    finally:
        con.close()


def _section(db: Path, name: str, payload) -> None:
    _execute(
        db, "INSERT INTO project_context (project_identity, contract_name, payload, updated_at) "
        "VALUES (?, ?, ?, '2026-10-01T00:00:00Z')", IDENTITY, name, json.dumps(payload),
    )


def _user_rows(db: Path, count: int, size: int) -> None:
    _execute(db, "INSERT OR IGNORE INTO workspaces (name, identity) VALUES ('_gaia_user', '_gaia_user')")
    for i in range(count):
        body = f"Preference {i}: " + "x" * size
        _execute(
            db, "INSERT INTO memory (workspace, name, type, description, body, class, audience, updated_at) "
            "VALUES ('_gaia_user', ?, 'user', ?, ?, 'anchor', 'executor', ?)",
            f"user_pref_{i}", body[:80], body, f"2026-09-{i + 1:02d}T00:00:00Z",
        )


def _row(project, can_read, prompt="fix the build") -> dict:
    return {
        "contract_id": "a0123456789abcdef.beefcafe0123",
        "agent_id": "a0123456789abcdef",
        "workspace": "me",
        "dispatch_prompt": prompt,
        "dispatch_project": project,
        "kernel_sections": json.dumps({"role": "primary", "surface": "iac", "can_read": can_read}),
    }


def _kernel(db: Path, row: dict) -> str:
    return build_kernel_context(row, agent_name="platform-architect", db_path=db)


def test_kernel_project_context_carries_readable_sections_whole(db):
    _section(db, "stack", {"language": "go", "framework": "gin"})
    _section(db, "environment", {"cloud": "gcp"})
    _section(db, "secrets_layout", {"vault": "unreadable here"})

    kernel = _kernel(db, _row(DISPLAY, ["stack", "environment"]))

    assert HEADING in kernel
    assert '"framework": "gin"' in kernel
    assert '"cloud": "gcp"' in kernel
    assert "unreadable here" not in kernel
    assert kernel.index("# Your Contract") < kernel.index(HEADING)
    assert len(kernel) <= KERNEL_BUDGET


def test_kernel_project_context_absent_when_the_project_has_no_rows(db):
    kernel = _kernel(db, _row(DISPLAY, ["stack"]))

    assert HEADING not in kernel


def test_kernel_project_context_section_larger_than_the_budget_becomes_a_pointer(db):
    _section(db, "cluster_details", {"nodes": ["n" * 200 for _ in range(970)]})
    _section(db, "stack", {"language": "go"})
    row = _row(DISPLAY, ["cluster_details", "stack"], prompt="do it " * 1300)

    kernel = _kernel(db, row)

    assert _pointer("cluster_details") in kernel
    assert "n" * 200 not in kernel
    assert '"language": "go"' in kernel
    assert kernel.startswith(build_dispatch_kernel(row, db_path=db))
    assert len(kernel) <= KERNEL_BUDGET


def test_kernel_project_context_many_sections_fit_or_point(db):
    names = [f"section_{i:02d}" for i in range(40)]
    for name in names:
        _section(db, name, {"value": name + " " + "v" * 600})

    kernel = _kernel(db, _row(DISPLAY, names))

    whole = [n for n in names if f"{n} " + "v" * 600 in kernel]
    pointed = [n for n in names if _pointer(n) in kernel]
    assert whole and names[0] in whole
    assert not set(whole) & set(pointed)
    assert len(whole) + len(pointed) == len(names) or "more" in kernel.split(HEADING)[1]
    assert len(kernel) <= KERNEL_BUDGET


def test_kernel_project_context_absent_for_a_project_matching_no_row(db):
    _section(db, "stack", {"language": "go"})

    kernel = _kernel(db, _row("ghost (/work/ghost)", ["stack"]))

    assert HEADING not in kernel
    assert '"language": "go"' not in kernel


def test_kernel_project_context_reads_a_workspace_scoped_legacy_row(db):
    _execute(
        db, "INSERT INTO project_context_contracts (workspace, contract_name, payload) VALUES ('me', 'stack', ?)",
        json.dumps({"language": "rust"}),
    )

    kernel = _kernel(db, _row(DISPLAY, ["stack"]))

    assert HEADING in kernel
    assert '"language": "rust"' in kernel


def test_kernel_project_context_absent_without_a_dispatch_project(db):
    _section(db, "stack", {"language": "go"})

    kernel = _kernel(db, _row(None, ["stack"]))

    assert HEADING not in kernel


def test_kernel_project_context_points_when_user_rows_fill_the_kernel(db):
    _user_rows(db, 9, 900)
    _section(db, "stack", {"language": "go", "notes": "s" * 1500})
    row = _row(DISPLAY, ["stack"])

    kernel = _kernel(db, row)

    assert _pointer("stack") in kernel
    assert "s" * 1500 not in kernel
    assert "Preference 8: " in kernel
    assert kernel.startswith(build_dispatch_kernel(row, db_path=db))
    assert len(kernel) <= KERNEL_BUDGET
