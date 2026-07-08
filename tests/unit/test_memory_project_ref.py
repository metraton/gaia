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


# ---------------------------------------------------------------------------
# resolve_project_ref_by_cwd() -- shared cwd->project default resolution
# ---------------------------------------------------------------------------

def _seed_project_with_path(
    db_path: Path, workspace: str, name: str, path: str,
    project_identity: str | None, status: str = "active",
) -> None:
    from gaia.store.writer import _connect
    con = _connect(db_path)
    try:
        con.execute(
            "INSERT INTO projects (workspace, name, path, project_identity, status) "
            "VALUES (?, ?, ?, ?, ?)",
            (workspace, name, path, project_identity, status),
        )
        con.commit()
    finally:
        con.close()


def test_by_cwd_resolves_when_cwd_inside_project(db: Path, tmp_path) -> None:
    """cwd sitting inside a project's path resolves to that project_identity."""
    from gaia.store.writer import resolve_project_ref_by_cwd

    proj = tmp_path / "gaia"
    (proj / "sub").mkdir(parents=True)
    _seed_project_with_path(db, "me", "gaia", str(proj), "github.com/x/gaia")

    # cwd == project root, and cwd inside a subdir both resolve.
    assert resolve_project_ref_by_cwd("me", cwd=proj, db_path=db) == "github.com/x/gaia"
    assert resolve_project_ref_by_cwd("me", cwd=proj / "sub", db_path=db) == "github.com/x/gaia"


def test_by_cwd_workspace_root_returns_none(db: Path, tmp_path) -> None:
    """At a workspace root ABOVE all project subdirs, no project path contains
    the cwd -> None (fallback to workspace-only behaviour)."""
    from gaia.store.writer import resolve_project_ref_by_cwd

    ws_root = tmp_path / "me"
    (ws_root / "proj-a").mkdir(parents=True)
    (ws_root / "proj-b").mkdir(parents=True)
    _seed_project_with_path(db, "me", "proj-a", str(ws_root / "proj-a"), "id/a")
    _seed_project_with_path(db, "me", "proj-b", str(ws_root / "proj-b"), "id/b")

    assert resolve_project_ref_by_cwd("me", cwd=ws_root, db_path=db) is None


def test_by_cwd_most_specific_nested_wins(db: Path, tmp_path) -> None:
    """When two project paths both contain the cwd (nested), the longest
    (most specific) path wins."""
    from gaia.store.writer import resolve_project_ref_by_cwd

    outer = tmp_path / "mono"
    inner = outer / "packages" / "inner"
    inner.mkdir(parents=True)
    _seed_project_with_path(db, "me", "mono", str(outer), "id/outer")
    _seed_project_with_path(db, "me", "inner", str(inner), "id/inner")

    assert resolve_project_ref_by_cwd("me", cwd=inner, db_path=db) == "id/inner"


def test_by_cwd_null_identity_not_resolved(db: Path, tmp_path) -> None:
    """A matching project with no project_identity is never the anchor."""
    from gaia.store.writer import resolve_project_ref_by_cwd

    proj = tmp_path / "unscanned"
    proj.mkdir()
    _seed_project_with_path(db, "me", "unscanned", str(proj), None)

    assert resolve_project_ref_by_cwd("me", cwd=proj, db_path=db) is None


def test_by_cwd_missing_status_not_resolved(db: Path, tmp_path) -> None:
    """A project whose status is 'missing' is never the anchor."""
    from gaia.store.writer import resolve_project_ref_by_cwd

    proj = tmp_path / "gone"
    proj.mkdir()
    _seed_project_with_path(db, "me", "gone", str(proj), "id/gone", status="missing")

    assert resolve_project_ref_by_cwd("me", cwd=proj, db_path=db) is None


def test_by_cwd_sibling_prefix_does_not_match(db: Path, tmp_path) -> None:
    """A shared string prefix is not a path-ancestor: /x/me must not match a
    cwd under /x/me-other (boundary safety)."""
    from gaia.store.writer import resolve_project_ref_by_cwd

    proj = tmp_path / "me"
    proj.mkdir()
    sibling = tmp_path / "me-other"
    sibling.mkdir()
    _seed_project_with_path(db, "me", "me", str(proj), "id/me")

    assert resolve_project_ref_by_cwd("me", cwd=sibling, db_path=db) is None


def test_by_cwd_scoped_to_workspace(db: Path, tmp_path) -> None:
    """Resolution only considers projects in the named workspace."""
    from gaia.store.writer import resolve_project_ref_by_cwd, _connect

    con = _connect(db)
    try:
        con.execute("INSERT INTO workspaces (name) VALUES ('other')")
        con.commit()
    finally:
        con.close()

    proj = tmp_path / "gaia"
    proj.mkdir()
    _seed_project_with_path(db, "other", "gaia", str(proj), "id/other-gaia")

    # Same cwd, but the project lives in workspace 'other', so 'me' sees nothing.
    assert resolve_project_ref_by_cwd("me", cwd=proj, db_path=db) is None
    assert resolve_project_ref_by_cwd("other", cwd=proj, db_path=db) == "id/other-gaia"
