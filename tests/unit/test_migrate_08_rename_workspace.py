"""
test_migrate_08_rename_workspace.py -- Bloque 4 verification.

The migration renames a v1 schema (``projects`` = organisational container,
``repos`` = git-bearing repo, child tables with a ``project`` FK column) to
the v2 schema (``workspaces``, ``projects``, child tables with a
``workspace`` FK column), cleans contaminated identities, and recreates
FTS5 mirrors / triggers.

All tests operate on synthetic SQLite files built under ``tmp_path``. None
of them touches ``~/.gaia/gaia.db``.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

# Ensure repo root is importable.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.migration.migrate_08_rename_workspace import (  # noqa: E402
    _is_already_migrated,
    _is_v1_layout,
    _migrate,
    main,
)


# ---------------------------------------------------------------------------
# Synthetic v1 schema builder
# ---------------------------------------------------------------------------

# Minimal v1 schema: just enough surface area to exercise table rename,
# column rename, identity cleanup, and FTS rebuild.
_V1_SCHEMA = """
PRAGMA foreign_keys = OFF;

CREATE TABLE projects (
    name       TEXT NOT NULL PRIMARY KEY,
    identity   TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE repos (
    project          TEXT NOT NULL,
    name             TEXT NOT NULL,
    role             TEXT,
    remote_url       TEXT,
    primary_language TEXT,
    scanner_ts       TEXT,
    PRIMARY KEY (project, name)
);

CREATE TABLE apps (
    project     TEXT NOT NULL,
    name        TEXT NOT NULL,
    kind        TEXT,
    description TEXT,
    topic_key   TEXT,
    PRIMARY KEY (project, name)
);

CREATE TABLE services (
    project     TEXT NOT NULL,
    name        TEXT NOT NULL,
    kind        TEXT,
    description TEXT,
    topic_key   TEXT,
    PRIMARY KEY (project, name)
);

CREATE TABLE briefs (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    project   TEXT NOT NULL,
    name      TEXT NOT NULL,
    objective TEXT,
    context   TEXT,
    approach  TEXT
);

CREATE TABLE episodes (
    episode_id     TEXT NOT NULL PRIMARY KEY,
    project        TEXT NOT NULL,
    timestamp      TEXT NOT NULL,
    prompt         TEXT,
    enriched_prompt TEXT,
    tags           TEXT,
    title          TEXT
);

CREATE TABLE memory (
    project     TEXT NOT NULL,
    name        TEXT NOT NULL,
    type        TEXT NOT NULL,
    description TEXT,
    body        TEXT NOT NULL,
    PRIMARY KEY (project, name)
);

CREATE TABLE harness_events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    project TEXT,
    ts      TEXT NOT NULL,
    type    TEXT NOT NULL,
    payload TEXT
);
"""


def _build_v1_db(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(path))
    con.executescript(_V1_SCHEMA)
    con.commit()
    return con


def _build_v2_db(path: Path) -> sqlite3.Connection:
    """Minimal v2 DB used to test the no-op idempotency branch."""
    con = sqlite3.connect(str(path))
    con.executescript("""
        CREATE TABLE workspaces (
            name     TEXT NOT NULL PRIMARY KEY,
            identity TEXT
        );
        CREATE TABLE projects (
            workspace TEXT NOT NULL,
            name      TEXT NOT NULL,
            PRIMARY KEY (workspace, name)
        );
    """)
    con.commit()
    return con


# ---------------------------------------------------------------------------
# Layout probes
# ---------------------------------------------------------------------------

def test_layout_probes_distinguish_v1_and_v2(tmp_path: Path) -> None:
    v1 = tmp_path / "v1.db"
    v2 = tmp_path / "v2.db"
    con_v1 = _build_v1_db(v1)
    con_v2 = _build_v2_db(v2)
    try:
        assert _is_v1_layout(con_v1) is True
        assert _is_already_migrated(con_v1) is False

        assert _is_v1_layout(con_v2) is False
        assert _is_already_migrated(con_v2) is True
    finally:
        con_v1.close()
        con_v2.close()


# ---------------------------------------------------------------------------
# Core rename
# ---------------------------------------------------------------------------

def test_migration_renames_tables(tmp_path: Path) -> None:
    db = tmp_path / "v1.db"
    con = _build_v1_db(db)
    try:
        # Seed two workspace rows + one repo row so the FK column rename has
        # data flowing through it.
        con.execute(
            "INSERT INTO projects (name, identity) VALUES (?, ?)",
            ("me", "me"),
        )
        con.execute(
            "INSERT INTO projects (name, identity) VALUES (?, ?)",
            ("github.com/jaguilar87/gaia", "github.com/jaguilar87/gaia"),
        )
        con.execute(
            "INSERT INTO repos (project, name, role) VALUES (?, ?, ?)",
            ("me", "gaia", "library"),
        )
        con.commit()

        stats = _migrate(con)
        assert isinstance(stats, dict)

        # v1 names gone, v2 names present
        tables = {row[0] for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "workspaces" in tables
        assert "projects" in tables  # now the v2 git-bearing table
        assert "repos" not in tables

        # workspaces still holds two rows
        ws_rows = con.execute("SELECT name FROM workspaces ORDER BY name").fetchall()
        assert {r[0] for r in ws_rows} == {"me", "github.com/jaguilar87/gaia"}

        # projects (v2) carries the renamed `workspace` column and the seeded row
        cols = {row[1] for row in con.execute("PRAGMA table_info(projects)").fetchall()}
        assert "workspace" in cols
        assert "project" not in cols
        proj_rows = con.execute(
            "SELECT workspace, name, role FROM projects"
        ).fetchall()
        assert proj_rows == [("me", "gaia", "library")]
    finally:
        con.close()


def test_migration_renames_child_columns(tmp_path: Path) -> None:
    db = tmp_path / "v1.db"
    con = _build_v1_db(db)
    try:
        _migrate(con)
        for table in ("apps", "services", "briefs", "episodes", "memory", "harness_events"):
            cols = {row[1] for row in con.execute(f"PRAGMA table_info({table})").fetchall()}
            assert "workspace" in cols, f"{table} missing workspace column after migration"
            assert "project" not in cols, f"{table} still has project column after migration"
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

def test_migration_is_idempotent_on_v2_db(tmp_path: Path) -> None:
    """Running the CLI against a DB already on v2 must be a no-op exit 0."""
    db = tmp_path / "v2.db"
    con = _build_v2_db(db)
    con.execute("INSERT INTO workspaces (name, identity) VALUES (?, ?)", ("me", "me"))
    con.execute("INSERT INTO projects (workspace, name) VALUES (?, ?)", ("me", "gaia"))
    con.commit()
    con.close()

    # CLI invocation (no backup so we don't pollute tmp_path with sidecars)
    rc = main(["--db", str(db), "--no-backup"])
    assert rc == 0

    # Schema is unchanged
    con2 = sqlite3.connect(str(db))
    try:
        tables = {row[0] for row in con2.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "workspaces" in tables and "projects" in tables
        assert "repos" not in tables
        # Data preserved
        row = con2.execute("SELECT name, identity FROM workspaces").fetchone()
        assert row == ("me", "me")
    finally:
        con2.close()


def test_migration_running_twice_is_idempotent(tmp_path: Path) -> None:
    """Run on v1, then run again on the already-migrated DB. Second run no-ops."""
    db = tmp_path / "v1.db"
    con = _build_v1_db(db)
    con.execute("INSERT INTO projects (name, identity) VALUES (?, ?)", ("me", "me"))
    con.commit()
    con.close()

    # First pass migrates v1 -> v2.
    rc1 = main(["--db", str(db), "--no-backup"])
    assert rc1 == 0

    # Second pass should be a no-op.
    rc2 = main(["--db", str(db), "--no-backup"])
    assert rc2 == 0

    # Data still intact.
    con = sqlite3.connect(str(db))
    try:
        row = con.execute("SELECT name, identity FROM workspaces").fetchone()
        assert row == ("me", "me")
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Identity cleanup
# ---------------------------------------------------------------------------

def test_migration_cleans_contaminated_identity(tmp_path: Path) -> None:
    db = tmp_path / "v1.db"
    con = _build_v1_db(db)
    try:
        # Workspace `me` is an organisational container whose identity got
        # contaminated with a remote URL shape.
        con.execute(
            "INSERT INTO projects (name, identity) VALUES (?, ?)",
            ("me", "github.com/foo/bar"),
        )
        # Workspace `github.com/jaguilar87/gaia` is a real single-repo
        # workspace and must keep its identity intact.
        con.execute(
            "INSERT INTO projects (name, identity) VALUES (?, ?)",
            ("github.com/jaguilar87/gaia", "github.com/jaguilar87/gaia"),
        )
        # Identity already clean -- must be untouched.
        con.execute(
            "INSERT INTO projects (name, identity) VALUES (?, ?)",
            ("aaxis", "aaxis"),
        )
        con.commit()

        stats = _migrate(con)

        rows = dict(con.execute("SELECT name, identity FROM workspaces").fetchall())
        # Contaminated row collapsed to name
        assert rows["me"] == "me"
        # Real repo identity preserved (name contains '/')
        assert rows["github.com/jaguilar87/gaia"] == "github.com/jaguilar87/gaia"
        # Untouched clean identity
        assert rows["aaxis"] == "aaxis"

        # Exactly one row should have been rewritten.
        assert stats["identity_rows_fixed"] == 1
    finally:
        con.close()


# ---------------------------------------------------------------------------
# FTS recreation
# ---------------------------------------------------------------------------

def test_migration_recreates_fts_mirrors(tmp_path: Path) -> None:
    """After migration, FTS5 mirrors for renamed base tables must exist and
    be queryable. We do not assert content (rebuild is best-effort); we
    assert the virtual tables and their triggers are present."""
    db = tmp_path / "v1.db"
    con = _build_v1_db(db)
    try:
        _migrate(con)
        tables = {row[0] for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        for fts in ("projects_fts", "apps_fts", "services_fts",
                    "briefs_fts", "episodes_fts", "memory_fts"):
            assert fts in tables, f"missing FTS mirror {fts} after migration"

        triggers = {row[0] for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger'"
        ).fetchall()}
        # projects FTS triggers (the renamed v1 repos table)
        assert "projects_fts_insert" in triggers
        assert "projects_fts_delete" in triggers
        assert "projects_fts_update" in triggers

        # Seed a row through the renamed `projects` table to confirm the
        # trigger fires against the renamed base.
        con.execute(
            "INSERT INTO projects (workspace, name, role, remote_url, primary_language) "
            "VALUES (?, ?, ?, ?, ?)",
            ("me", "gaia", "library", "git@github.com:x/y.git", "python"),
        )
        con.commit()
        hits = con.execute(
            "SELECT name FROM projects_fts WHERE projects_fts MATCH 'gaia'"
        ).fetchall()
        assert hits == [("gaia",)]
    finally:
        con.close()


# ---------------------------------------------------------------------------
# CLI guardrails
# ---------------------------------------------------------------------------

def test_cli_fails_when_db_is_missing(tmp_path: Path) -> None:
    rc = main(["--db", str(tmp_path / "does_not_exist.db"), "--no-backup"])
    assert rc == 1


def test_cli_fails_on_unrecognised_schema(tmp_path: Path) -> None:
    """A DB with neither v1 nor v2 layout exits 1, never silently."""
    db = tmp_path / "weird.db"
    con = sqlite3.connect(str(db))
    con.executescript("CREATE TABLE random (id INTEGER PRIMARY KEY)")
    con.commit()
    con.close()

    rc = main(["--db", str(db), "--no-backup"])
    assert rc == 1


def test_cli_dry_run_does_not_modify_db(tmp_path: Path) -> None:
    db = tmp_path / "v1.db"
    con = _build_v1_db(db)
    con.execute("INSERT INTO projects (name, identity) VALUES (?, ?)", ("me", "me"))
    con.commit()
    con.close()

    rc = main(["--db", str(db), "--dry-run", "--no-backup"])
    assert rc == 0

    con = sqlite3.connect(str(db))
    try:
        # v1 tables still there, no rename happened.
        tables = {row[0] for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "projects" in tables  # v1 organisational
        assert "repos" in tables
        assert "workspaces" not in tables
    finally:
        con.close()
