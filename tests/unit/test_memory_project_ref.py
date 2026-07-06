"""Unit tests for N3 -- forward-only `memory.project_ref` anchoring.

Exercises `gaia.store.writer.upsert_memory(project_ref=...)` and
`gaia.store.writer.resolve_project_ref()` directly against a temp DB
materialized from the real schema.sql (same pattern as
tests/unit/test_memory_resilience_sv3.py).

Context: the automatic backfill in scripts/migrations/v25_to_v26.sql (guarded
on "workspace hosts exactly one active project") is an already-applied,
immutable, one-time statement that populated 0 rows in practice -- the
memory-row-to-project mapping is genuinely ambiguous whenever a workspace
hosts more than one project. These tests cover the forward-only replacement:
anchoring at write time, by whoever names the project explicitly.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture()
def db(tmp_path, monkeypatch) -> Path:
    """Fresh DB from the real schema.sql; curator guard neutralised (human
    caller) by clearing GAIA_DISPATCH_AGENT."""
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    from gaia.paths import db_path
    from gaia.store.writer import _connect

    path = db_path()
    con = _connect(path)
    con.execute("INSERT INTO workspaces (name) VALUES ('me')")
    con.commit()
    con.close()
    return path


def _seed_project(db_path: Path, workspace: str, name: str,
                   project_identity: str | None = None,
                   status: str = "active") -> None:
    from gaia.store.writer import _connect
    con = _connect(db_path)
    try:
        con.execute(
            "INSERT INTO projects (workspace, name, project_identity, status) "
            "VALUES (?, ?, ?, ?)",
            (workspace, name, project_identity, status),
        )
        con.commit()
    finally:
        con.close()


# ---------------------------------------------------------------------------
# upsert_memory(project_ref=...)
# ---------------------------------------------------------------------------

def test_upsert_memory_with_project_ref_persists(db: Path) -> None:
    from gaia.store.writer import upsert_memory, _connect

    upsert_memory(
        "me", "project_x_notes", type="project", body="notes",
        project_ref="github.com/x/gaia", db_path=db,
    )

    con = _connect(db)
    try:
        row = con.execute(
            "SELECT project_ref FROM memory WHERE workspace='me' AND name='project_x_notes'"
        ).fetchone()
    finally:
        con.close()
    assert row["project_ref"] == "github.com/x/gaia"


def test_upsert_memory_without_project_ref_is_null(db: Path) -> None:
    from gaia.store.writer import upsert_memory, _connect

    upsert_memory("me", "project_y_notes", type="project", body="notes", db_path=db)

    con = _connect(db)
    try:
        row = con.execute(
            "SELECT project_ref FROM memory WHERE workspace='me' AND name='project_y_notes'"
        ).fetchone()
    finally:
        con.close()
    assert row["project_ref"] is None


def test_upsert_memory_update_without_project_ref_preserves_existing(db: Path) -> None:
    """coalesce-or-omit: an update call that does not pass project_ref must
    NOT null out a previously-anchored value."""
    from gaia.store.writer import upsert_memory, _connect

    upsert_memory(
        "me", "sticky", type="project", body="v1",
        project_ref="github.com/x/gaia", db_path=db,
    )
    upsert_memory("me", "sticky", type="project", body="v2", db_path=db)

    con = _connect(db)
    try:
        row = con.execute(
            "SELECT body, project_ref FROM memory WHERE workspace='me' AND name='sticky'"
        ).fetchone()
    finally:
        con.close()
    assert row["body"] == "v2", "the update itself must still land"
    assert row["project_ref"] == "github.com/x/gaia", (
        "omitting project_ref on a later upsert must not erase an existing anchor"
    )


def test_upsert_memory_update_with_new_project_ref_overwrites(db: Path) -> None:
    """An explicit new project_ref on a later call DOES overwrite (re-anchor)."""
    from gaia.store.writer import upsert_memory, _connect

    upsert_memory(
        "me", "reanchor", type="project", body="v1",
        project_ref="github.com/x/old", db_path=db,
    )
    upsert_memory(
        "me", "reanchor", type="project", body="v2",
        project_ref="github.com/x/new", db_path=db,
    )

    con = _connect(db)
    try:
        row = con.execute(
            "SELECT project_ref FROM memory WHERE workspace='me' AND name='reanchor'"
        ).fetchone()
    finally:
        con.close()
    assert row["project_ref"] == "github.com/x/new"


def test_get_memory_exposes_project_ref(db: Path) -> None:
    from gaia.store.writer import upsert_memory, get_memory

    upsert_memory(
        "me", "readable", type="project", body="notes",
        project_ref="github.com/x/gaia", db_path=db,
    )
    row = get_memory("me", "readable", db_path=db)
    assert row is not None
    assert row["project_ref"] == "github.com/x/gaia"


# ---------------------------------------------------------------------------
# resolve_project_ref()
# ---------------------------------------------------------------------------

def test_resolve_project_ref_returns_identity(db: Path) -> None:
    from gaia.store.writer import resolve_project_ref

    _seed_project(db, "me", "gaia", project_identity="github.com/x/gaia")
    assert resolve_project_ref("me", "gaia", db_path=db) == "github.com/x/gaia"


def test_resolve_project_ref_not_found_raises(db: Path) -> None:
    from gaia.store.writer import resolve_project_ref

    with pytest.raises(ValueError, match="not found"):
        resolve_project_ref("me", "does-not-exist", db_path=db)


def test_resolve_project_ref_no_identity_raises(db: Path) -> None:
    """A project row that exists but has no project_identity yet must never
    be guessed at -- this is the 'ambiguous, cannot resolve' case."""
    from gaia.store.writer import resolve_project_ref

    _seed_project(db, "me", "unscanned", project_identity=None)

    with pytest.raises(ValueError, match="project_identity"):
        resolve_project_ref("me", "unscanned", db_path=db)


def test_resolve_project_ref_scoped_to_workspace(db: Path) -> None:
    """A project name that exists in a DIFFERENT workspace does not resolve --
    resolution is scoped to the exact (workspace, name) the caller named."""
    from gaia.store.writer import resolve_project_ref, _connect

    con = _connect(db)
    try:
        con.execute("INSERT INTO workspaces (name) VALUES ('other')")
        con.commit()
    finally:
        con.close()
    _seed_project(db, "other", "gaia", project_identity="github.com/x/gaia")

    with pytest.raises(ValueError, match="not found"):
        resolve_project_ref("me", "gaia", db_path=db)
