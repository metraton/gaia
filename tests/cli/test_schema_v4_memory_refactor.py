"""Tests for schema v4 migration: memory.class + memory.status + memory_links.

Coverage:
  * test_fresh_install_creates_memory_class_status_columns
      After bootstrap on an empty DB, memory has class and status columns.

  * test_fresh_install_creates_memory_links_table
      After bootstrap, memory_links exists with the expected PK and kind CHECK.

  * test_fresh_install_creates_memory_indexes
      After bootstrap, idx_memory_class_status, memory_links_src, and
      idx_memory_links_dst_kind are present in sqlite_master.

  * test_v3_db_with_36_rows_migrates_preserving_data
      Synthesize a v3-state DB carrying 36 me-workspace rows. Run bootstrap.
      Assert the row count is preserved, every body/type/description/origin
      survives byte-identical, and the new columns land NULL on every row.

  * test_memory_links_accepts_insert_and_enforces_pk
      After bootstrap, INSERT into memory_links works and a duplicate
      (workspace, src_name, dst_name, kind) raises IntegrityError.

  * test_memory_links_kind_check_constraint
      memory_links.kind only accepts the four documented values.

  * test_bootstrap_idempotent_at_v4
      Running bootstrap twice on a v4 DB: second run is a no-op and the
      schema_version count remains at 4 (not duplicated).

  * test_schema_version_advances_to_v4
      After bootstrap, MAX(version) in schema_version == 4.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import tempfile
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[2]
_BOOTSTRAP_SH = _REPO_ROOT / "scripts" / "bootstrap_database.sh"
_SCHEMA_SQL = _REPO_ROOT / "gaia" / "store" / "schema.sql"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_bootstrap(workspace: Path, env_overrides: dict | None = None) -> subprocess.CompletedProcess:
    tmp_db = workspace / "tmp_gaia.db"
    env = os.environ.copy()
    env["GAIA_DB"] = str(tmp_db)
    env["WORKSPACE"] = str(workspace)
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        ["bash", str(_BOOTSTRAP_SH)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


# Production-shaped v3 memory DDL: same shape as live ~/.gaia/gaia.db before v4,
# including the FTS5 mirror and triggers. Used to build a realistic v3 fixture.
_V3_SCHEMA_STUB = """
CREATE TABLE workspaces (
    name        TEXT NOT NULL PRIMARY KEY,
    identity    TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL,
    description TEXT
);

CREATE TABLE memory (
    workspace         TEXT NOT NULL,
    name              TEXT NOT NULL,
    type              TEXT NOT NULL CHECK (type IN ('project', 'user', 'feedback', 'atom', 'decision', 'negative')),
    description       TEXT,
    body              TEXT NOT NULL,
    origin_session_id TEXT,
    updated_at        TEXT,
    PRIMARY KEY (workspace, name),
    FOREIGN KEY (workspace) REFERENCES workspaces(name) ON DELETE CASCADE
);

CREATE INDEX idx_memory_workspace ON memory(workspace);
CREATE INDEX idx_memory_type ON memory(type);

CREATE VIRTUAL TABLE memory_fts USING fts5(
    workspace UNINDEXED,
    name UNINDEXED,
    description,
    body,
    content='memory',
    content_rowid='rowid'
);

CREATE TRIGGER memory_ai AFTER INSERT ON memory BEGIN
    INSERT INTO memory_fts(rowid, workspace, name, description, body)
    VALUES (new.rowid, new.workspace, new.name, new.description, new.body);
END;

CREATE TRIGGER memory_ad AFTER DELETE ON memory BEGIN
    INSERT INTO memory_fts(memory_fts, rowid, workspace, name, description, body)
    VALUES ('delete', old.rowid, old.workspace, old.name, old.description, old.body);
END;

CREATE TRIGGER memory_au AFTER UPDATE ON memory BEGIN
    INSERT INTO memory_fts(memory_fts, rowid, workspace, name, description, body)
    VALUES ('delete', old.rowid, old.workspace, old.name, old.description, old.body);
    INSERT INTO memory_fts(rowid, workspace, name, description, body)
    VALUES (new.rowid, new.workspace, new.name, new.description, new.body);
END;

-- v3-only context table; presence makes the bootstrap guard probe see state 2
-- (rename already complete) for the v2->v3 step.
CREATE TABLE project_context_contracts (
    workspace     TEXT NOT NULL,
    contract_name TEXT NOT NULL,
    payload       TEXT NOT NULL,
    metadata      TEXT,
    updated_at    TEXT,
    PRIMARY KEY (workspace, contract_name),
    FOREIGN KEY (workspace) REFERENCES workspaces(name) ON DELETE CASCADE
);

CREATE INDEX idx_project_context_contracts_workspace ON project_context_contracts(workspace);

CREATE TABLE agent_contract_permissions (
    agent_name    TEXT NOT NULL,
    contract_name TEXT NOT NULL,
    can_read      INTEGER NOT NULL DEFAULT 0,
    can_write     INTEGER NOT NULL DEFAULT 0,
    cloud_scope   TEXT,
    PRIMARY KEY (agent_name, contract_name, cloud_scope)
);

CREATE INDEX idx_agent_contract_perms_agent ON agent_contract_permissions(agent_name);

INSERT INTO workspaces (name, identity) VALUES ('me', 'me');

INSERT INTO schema_version (version, applied_at, description)
VALUES (1, '2026-01-01T00:00:00Z', 'initial schema');
INSERT INTO schema_version (version, applied_at, description)
VALUES (2, '2026-01-01T00:00:00Z', 'v2 memory widen');
INSERT INTO schema_version (version, applied_at, description)
VALUES (3, '2026-01-01T00:00:00Z', 'v3 contracts rename');
"""


# Production-shaped row distribution for the 36 me-workspace rows:
#   16 project + 9 atom + 5 negative + 3 decision + 2 feedback + 1 user = 36
_ME_ROW_DISTRIBUTION = (
    ("project",  16),
    ("atom",      9),
    ("negative",  5),
    ("decision",  3),
    ("feedback",  2),
    ("user",      1),
)


def _seed_36_me_rows(con: sqlite3.Connection) -> list[tuple]:
    """Seed exactly 36 me-workspace memory rows mirroring the production split.

    Returns the list of inserted (workspace, name, type, description, body,
    origin_session_id) tuples, in insertion order, for later diff'ing.
    """
    rows: list[tuple] = []
    counter = 0
    for type_name, count in _ME_ROW_DISTRIBUTION:
        for i in range(count):
            counter += 1
            name = f"{type_name}_row_{counter:02d}"
            description = f"desc for {name}"
            body = f"body for {name}\nline2\nline3"
            origin = f"session-{counter:02d}"
            rows.append(("me", name, type_name, description, body, origin))

    for row in rows:
        con.execute(
            "INSERT INTO memory "
            "(workspace, name, type, description, body, origin_session_id, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, '2026-01-01T00:00:00Z')",
            row,
        )
    con.commit()
    assert len(rows) == 36, f"fixture sanity: expected 36 rows, got {len(rows)}"
    return rows


def _build_v3_db_with_36_rows(db_path: Path) -> list[tuple]:
    con = sqlite3.connect(str(db_path))
    try:
        con.executescript(_V3_SCHEMA_STUB)
        return _seed_36_me_rows(con)
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSchemaV4:

    def setup_method(self):
        if not _BOOTSTRAP_SH.is_file():
            pytest.skip(f"bootstrap script not found at {_BOOTSTRAP_SH}")
        if not _SCHEMA_SQL.is_file():
            pytest.skip(f"schema.sql not found at {_SCHEMA_SQL}")

    def test_fresh_install_creates_memory_class_status_columns(self):
        """Fresh bootstrap: memory has class and status columns."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            res = _run_bootstrap(workspace)
            assert res.returncode == 0, (
                f"bootstrap failed:\nstdout:\n{res.stdout}\nstderr:\n{res.stderr}"
            )
            db = workspace / "tmp_gaia.db"
            con = sqlite3.connect(str(db))
            try:
                cols = {
                    row[1]
                    for row in con.execute("PRAGMA table_info(memory)")
                }
            finally:
                con.close()

            assert "class" in cols, f"memory.class missing; columns={cols}"
            assert "status" in cols, f"memory.status missing; columns={cols}"

    def test_fresh_install_creates_memory_links_table(self):
        """Fresh bootstrap: memory_links table exists with correct PK + CHECK."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            res = _run_bootstrap(workspace)
            assert res.returncode == 0, (
                f"bootstrap failed:\nstdout:\n{res.stdout}\nstderr:\n{res.stderr}"
            )
            db = workspace / "tmp_gaia.db"
            con = sqlite3.connect(str(db))
            try:
                ddl_row = con.execute(
                    "SELECT sql FROM sqlite_master "
                    "WHERE type='table' AND name='memory_links'"
                ).fetchone()
            finally:
                con.close()

            assert ddl_row is not None, "memory_links table not found"
            ddl = ddl_row[0]
            assert "workspace" in ddl
            assert "src_name" in ddl
            assert "dst_name" in ddl
            assert "kind" in ddl
            # PK on all four columns
            assert "PRIMARY KEY" in ddl
            # CHECK on kind enumeration
            assert "relates_to" in ddl
            assert "supersedes" in ddl
            assert "derived_from" in ddl
            assert "graduated_to" in ddl

    def test_fresh_install_creates_memory_indexes(self):
        """Fresh bootstrap: required indexes exist on memory and memory_links."""
        expected = {
            "idx_memory_class_status",
            "memory_links_src",
            "idx_memory_links_dst_kind",
        }
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            res = _run_bootstrap(workspace)
            assert res.returncode == 0, (
                f"bootstrap failed:\nstdout:\n{res.stdout}\nstderr:\n{res.stderr}"
            )
            db = workspace / "tmp_gaia.db"
            con = sqlite3.connect(str(db))
            try:
                idx_names = {
                    row[0]
                    for row in con.execute(
                        "SELECT name FROM sqlite_master WHERE type='index'"
                    )
                }
            finally:
                con.close()

            missing = expected - idx_names
            assert not missing, f"missing indexes: {missing} (live indexes: {idx_names})"

    def test_v3_db_with_36_rows_migrates_preserving_data(self):
        """v3 DB with 36 me-rows: migration preserves PK + body + type + origin."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            db = workspace / "tmp_gaia.db"
            pre_rows = _build_v3_db_with_36_rows(db)

            # Sanity: pre-migration row count is 36.
            con = sqlite3.connect(str(db))
            try:
                pre_count = con.execute(
                    "SELECT COUNT(*) FROM memory WHERE workspace='me'"
                ).fetchone()[0]
            finally:
                con.close()
            assert pre_count == 36

            res = _run_bootstrap(workspace)
            assert res.returncode == 0, (
                f"bootstrap failed:\nstdout:\n{res.stdout}\nstderr:\n{res.stderr}"
            )

            con = sqlite3.connect(str(db))
            try:
                post_rows = con.execute(
                    "SELECT workspace, name, type, description, body, "
                    "origin_session_id, class, status "
                    "FROM memory WHERE workspace='me' ORDER BY name"
                ).fetchall()
            finally:
                con.close()

            assert len(post_rows) == 36, (
                f"expected 36 rows post-migration, got {len(post_rows)}"
            )

            pre_by_name = {r[1]: r for r in pre_rows}
            for (ws, name, type_, desc, body, origin, klass, status) in post_rows:
                pre = pre_by_name[name]
                # Pre-existing data is byte-identical.
                assert ws == pre[0]
                assert type_ == pre[2]
                assert desc == pre[3]
                assert body == pre[4]
                assert origin == pre[5]
                # v4: new columns land NULL on legacy rows.
                # v11: the v10->v11 migration coalesces any remaining NULL
                # class values to 'log' (the DEFAULT) and the column is now
                # NOT NULL. Status remains NULL for non-thread rows.
                assert klass == "log", (
                    f"row {name}: class should be 'log' (v11 default), got {klass!r}"
                )
                assert status is None, (
                    f"row {name}: status should be NULL on legacy rows, got {status!r}"
                )

    def test_memory_links_accepts_insert_and_enforces_pk(self):
        """memory_links accepts a valid edge; duplicate (ws,src,dst,kind) raises."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            res = _run_bootstrap(workspace)
            assert res.returncode == 0, res.stderr
            db = workspace / "tmp_gaia.db"

            con = sqlite3.connect(str(db))
            try:
                con.execute("PRAGMA foreign_keys = ON")
                con.execute(
                    "INSERT OR IGNORE INTO workspaces (name, identity) VALUES ('me', 'me')"
                )
                # First insert succeeds.
                con.execute(
                    "INSERT INTO memory_links "
                    "(workspace, src_name, dst_name, kind, created_at) "
                    "VALUES ('me', 'atom_a', 'atom_b', 'relates_to', '2026-05-22T00:00:00Z')"
                )
                con.commit()

                count = con.execute(
                    "SELECT COUNT(*) FROM memory_links WHERE workspace='me'"
                ).fetchone()[0]
                assert count == 1

                # Duplicate PK raises IntegrityError.
                with pytest.raises(sqlite3.IntegrityError):
                    con.execute(
                        "INSERT INTO memory_links "
                        "(workspace, src_name, dst_name, kind, created_at) "
                        "VALUES ('me', 'atom_a', 'atom_b', 'relates_to', '2026-05-22T00:00:01Z')"
                    )
                    con.commit()
                con.rollback()

                # Same (src,dst) but different kind is allowed.
                con.execute(
                    "INSERT INTO memory_links "
                    "(workspace, src_name, dst_name, kind, created_at) "
                    "VALUES ('me', 'atom_a', 'atom_b', 'supersedes', '2026-05-22T00:00:02Z')"
                )
                con.commit()

                count = con.execute(
                    "SELECT COUNT(*) FROM memory_links WHERE workspace='me'"
                ).fetchone()[0]
                assert count == 2
            finally:
                con.close()

    def test_memory_links_kind_check_constraint(self):
        """memory_links.kind rejects values outside the four-element enum."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            res = _run_bootstrap(workspace)
            assert res.returncode == 0, res.stderr
            db = workspace / "tmp_gaia.db"

            con = sqlite3.connect(str(db))
            try:
                con.execute(
                    "INSERT OR IGNORE INTO workspaces (name, identity) VALUES ('me', 'me')"
                )
                with pytest.raises(sqlite3.IntegrityError):
                    con.execute(
                        "INSERT INTO memory_links "
                        "(workspace, src_name, dst_name, kind, created_at) "
                        "VALUES ('me', 'a', 'b', 'made_up_kind', '2026-05-22T00:00:00Z')"
                    )
                    con.commit()
            finally:
                con.close()

    def test_bootstrap_idempotent_at_v4(self):
        """Running bootstrap twice does not duplicate schema_version rows at v4."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            res1 = _run_bootstrap(workspace)
            assert res1.returncode == 0, res1.stderr

            res2 = _run_bootstrap(workspace)
            assert res2.returncode == 0, res2.stderr

            db = workspace / "tmp_gaia.db"
            con = sqlite3.connect(str(db))
            try:
                count = con.execute(
                    "SELECT COUNT(*) FROM schema_version"
                ).fetchone()[0]
                max_ver = con.execute(
                    "SELECT MAX(version) FROM schema_version"
                ).fetchone()[0]
            finally:
                con.close()

            # Fresh install path stamps v1 baseline + auto-stamps each later
            # version when the live DDL is already at target. After v5 landed
            # the count is 5 (v1, v2, v3, v4, v5) and MAX == 5. This test was
            # originally written against v4 baseline; the assertions now use
            # MAX >= 4 to remain robust across schema bumps.
            assert count >= 4, f"expected at least 4 schema_version rows, got {count}"
            assert max_ver >= 4
            assert "up-to-date" in res2.stdout

    def test_schema_version_advances_to_v4(self):
        """After v3->v4 migration, schema_version MAX == 4."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            db = workspace / "tmp_gaia.db"
            _build_v3_db_with_36_rows(db)

            res = _run_bootstrap(workspace)
            assert res.returncode == 0, (
                f"bootstrap failed:\nstdout:\n{res.stdout}\nstderr:\n{res.stderr}"
            )

            con = sqlite3.connect(str(db))
            try:
                max_ver = con.execute(
                    "SELECT MAX(version) FROM schema_version"
                ).fetchone()[0]
                versions = [
                    r[0]
                    for r in con.execute(
                        "SELECT version FROM schema_version ORDER BY version"
                    )
                ]
            finally:
                con.close()

            # After v5 landed, fresh bootstrap reaches MAX >= 4 (currently 5).
            # The legacy test was written when v4 was the latest version.
            assert max_ver >= 4, f"expected schema_version MAX>=4, got {max_ver}"
            assert 4 in versions
