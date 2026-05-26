"""Tests for schema v5 migration: acceptance_criteria.status + milestones.status.

Coverage:
  * test_fresh_install_has_ac_status_column
      After bootstrap on an empty DB, acceptance_criteria has a status column.

  * test_fresh_install_has_milestone_status_column
      After bootstrap on an empty DB, milestones has a status column.

  * test_v4_db_ac_rows_receive_pending_on_migration
      Synthesize a v4-state DB carrying AC rows without the status column.
      Run bootstrap. Assert every AC row has status='pending'.

  * test_v4_db_milestone_rows_receive_pending_on_migration
      Same as above for milestones rows.

  * test_ac_status_check_constraint_rejects_invalid
      After bootstrap, INSERT into acceptance_criteria with status='invalid'
      raises IntegrityError.

  * test_milestone_status_check_constraint_rejects_invalid
      After bootstrap, INSERT into milestones with status='invalid' raises
      IntegrityError.

  * test_bootstrap_idempotent_at_v5
      Running bootstrap twice on a v5 DB: second run is a no-op and the
      schema_version count remains at 5 (not duplicated).

  * test_schema_version_advances_to_v5
      After bootstrap, MAX(version) in schema_version == 5.
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


# Minimal v4 DB stub: includes acceptance_criteria and milestones WITHOUT status
# column (simulating a v4-state live DB that needs to be migrated to v5).
_V4_SCHEMA_STUB = """
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

CREATE TABLE briefs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace    TEXT NOT NULL,
    name         TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'draft',
    surface_type TEXT,
    title        TEXT,
    objective    TEXT,
    context      TEXT,
    approach     TEXT,
    out_of_scope TEXT,
    topic_key    TEXT,
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (workspace, name),
    FOREIGN KEY (workspace) REFERENCES workspaces(name) ON DELETE CASCADE
);

CREATE TABLE acceptance_criteria (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    brief_id       INTEGER NOT NULL,
    ac_id          TEXT NOT NULL,
    description    TEXT,
    evidence_type  TEXT,
    evidence_shape TEXT,
    artifact_path  TEXT,
    FOREIGN KEY (brief_id) REFERENCES briefs(id) ON DELETE CASCADE
);

CREATE TABLE milestones (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    brief_id    INTEGER NOT NULL,
    order_num   INTEGER NOT NULL,
    name        TEXT NOT NULL,
    description TEXT,
    FOREIGN KEY (brief_id) REFERENCES briefs(id) ON DELETE CASCADE
);

-- Minimal tables needed for bootstrap guards
CREATE TABLE memory (
    workspace         TEXT NOT NULL,
    name              TEXT NOT NULL,
    type              TEXT NOT NULL,
    description       TEXT,
    body              TEXT NOT NULL,
    origin_session_id TEXT,
    updated_at        TEXT,
    class             TEXT,
    status            TEXT,
    PRIMARY KEY (workspace, name),
    FOREIGN KEY (workspace) REFERENCES workspaces(name) ON DELETE CASCADE
);

CREATE INDEX idx_memory_class_status ON memory(workspace, class, status);

CREATE TABLE memory_links (
    workspace  TEXT NOT NULL,
    src_name   TEXT NOT NULL,
    dst_name   TEXT NOT NULL,
    kind       TEXT NOT NULL CHECK (kind IN ('relates_to', 'supersedes', 'derived_from', 'graduated_to')),
    created_at TEXT,
    PRIMARY KEY (workspace, src_name, dst_name, kind),
    FOREIGN KEY (workspace) REFERENCES workspaces(name) ON DELETE CASCADE
);

CREATE INDEX memory_links_src ON memory_links(workspace, src_name);
CREATE INDEX idx_memory_links_dst_kind ON memory_links(workspace, dst_name, kind);

CREATE TABLE project_context_contracts (
    workspace     TEXT NOT NULL,
    contract_name TEXT NOT NULL,
    payload       TEXT NOT NULL,
    metadata      TEXT,
    updated_at    TEXT,
    PRIMARY KEY (workspace, contract_name),
    FOREIGN KEY (workspace) REFERENCES workspaces(name) ON DELETE CASCADE
);

CREATE TABLE agent_contract_permissions (
    agent_name    TEXT NOT NULL,
    contract_name TEXT NOT NULL,
    can_read      INTEGER NOT NULL DEFAULT 0,
    can_write     INTEGER NOT NULL DEFAULT 0,
    cloud_scope   TEXT,
    PRIMARY KEY (agent_name, contract_name, cloud_scope)
);

INSERT INTO workspaces (name, identity) VALUES ('me', 'me');

INSERT INTO schema_version (version, applied_at, description)
VALUES (1, '2026-01-01T00:00:00Z', 'initial schema');
INSERT INTO schema_version (version, applied_at, description)
VALUES (2, '2026-01-01T00:00:00Z', 'v2 memory widen');
INSERT INTO schema_version (version, applied_at, description)
VALUES (3, '2026-01-01T00:00:00Z', 'v3 contracts rename');
INSERT INTO schema_version (version, applied_at, description)
VALUES (4, '2026-01-01T00:00:00Z', 'v4 memory class+status+links');
"""


def _build_v4_db(db_path: Path, *, seed_ac: int = 0, seed_ms: int = 0) -> None:
    """Build a minimal v4-state DB at db_path, optionally seeding AC and milestone rows."""
    con = sqlite3.connect(str(db_path))
    try:
        con.executescript(_V4_SCHEMA_STUB)
        if seed_ac > 0 or seed_ms > 0:
            # Need a brief row first
            con.execute(
                "INSERT INTO briefs (workspace, name, status) VALUES ('me', 'test-brief', 'draft')"
            )
            brief_id = con.execute(
                "SELECT id FROM briefs WHERE workspace='me' AND name='test-brief'"
            ).fetchone()[0]
            for i in range(seed_ac):
                con.execute(
                    "INSERT INTO acceptance_criteria (brief_id, ac_id, description) "
                    "VALUES (?, ?, ?)",
                    (brief_id, f"AC-{i+1}", f"AC description {i+1}"),
                )
            for i in range(seed_ms):
                con.execute(
                    "INSERT INTO milestones (brief_id, order_num, name) VALUES (?, ?, ?)",
                    (brief_id, i + 1, f"M{i+1}"),
                )
            con.commit()
    finally:
        con.close()


def _run_bootstrap_with_db(db_path: Path, workspace: Path) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["GAIA_DB"] = str(db_path)
    env["WORKSPACE"] = str(workspace)
    return subprocess.run(
        ["bash", str(_BOOTSTRAP_SH)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSchemaV5:

    def setup_method(self):
        if not _BOOTSTRAP_SH.is_file():
            pytest.skip(f"bootstrap script not found at {_BOOTSTRAP_SH}")
        if not _SCHEMA_SQL.is_file():
            pytest.skip(f"schema.sql not found at {_SCHEMA_SQL}")

    def test_fresh_install_has_ac_status_column(self):
        """Fresh bootstrap: acceptance_criteria has a status column."""
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
                    for row in con.execute("PRAGMA table_info(acceptance_criteria)")
                }
            finally:
                con.close()

            assert "status" in cols, f"acceptance_criteria.status missing; columns={cols}"

    def test_fresh_install_has_milestone_status_column(self):
        """Fresh bootstrap: milestones has a status column."""
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
                    for row in con.execute("PRAGMA table_info(milestones)")
                }
            finally:
                con.close()

            assert "status" in cols, f"milestones.status missing; columns={cols}"

    def test_v4_db_ac_rows_receive_pending_on_migration(self):
        """Migrating a v4 DB: existing AC rows get status='pending'."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            db_path = workspace / "tmp_gaia.db"
            _build_v4_db(db_path, seed_ac=3, seed_ms=0)

            res = _run_bootstrap_with_db(db_path, workspace)
            assert res.returncode == 0, (
                f"bootstrap failed:\nstdout:\n{res.stdout}\nstderr:\n{res.stderr}"
            )

            con = sqlite3.connect(str(db_path))
            try:
                rows = con.execute(
                    "SELECT status FROM acceptance_criteria"
                ).fetchall()
            finally:
                con.close()

            assert len(rows) == 3, f"expected 3 AC rows, got {len(rows)}"
            for row in rows:
                assert row[0] == "pending", (
                    f"expected status='pending', got {row[0]!r}"
                )

    def test_v4_db_milestone_rows_receive_pending_on_migration(self):
        """Migrating a v4 DB: existing milestone rows get status='pending'."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            db_path = workspace / "tmp_gaia.db"
            _build_v4_db(db_path, seed_ac=0, seed_ms=4)

            res = _run_bootstrap_with_db(db_path, workspace)
            assert res.returncode == 0, (
                f"bootstrap failed:\nstdout:\n{res.stdout}\nstderr:\n{res.stderr}"
            )

            con = sqlite3.connect(str(db_path))
            try:
                rows = con.execute(
                    "SELECT status FROM milestones"
                ).fetchall()
            finally:
                con.close()

            assert len(rows) == 4, f"expected 4 milestone rows, got {len(rows)}"
            for row in rows:
                assert row[0] == "pending", (
                    f"expected status='pending', got {row[0]!r}"
                )

    def test_ac_status_check_constraint_rejects_invalid(self):
        """After migration, acceptance_criteria.status rejects invalid values."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            res = _run_bootstrap(workspace)
            assert res.returncode == 0, (
                f"bootstrap failed:\nstdout:\n{res.stdout}\nstderr:\n{res.stderr}"
            )
            db = workspace / "tmp_gaia.db"
            con = sqlite3.connect(str(db))
            con.execute("PRAGMA foreign_keys = ON")
            try:
                # Insert a workspace + brief so we have a valid brief_id
                con.execute(
                    "INSERT OR IGNORE INTO workspaces (name) VALUES ('test-ws')"
                )
                con.execute(
                    "INSERT INTO briefs (workspace, name, status) "
                    "VALUES ('test-ws', 'test-brief', 'draft')"
                )
                brief_id = con.execute(
                    "SELECT id FROM briefs WHERE workspace='test-ws' AND name='test-brief'"
                ).fetchone()[0]

                with pytest.raises(sqlite3.IntegrityError):
                    con.execute(
                        "INSERT INTO acceptance_criteria "
                        "(brief_id, ac_id, description, status) "
                        "VALUES (?, 'AC-1', 'desc', 'invalid_status')",
                        (brief_id,),
                    )
            finally:
                con.close()

    def test_milestone_status_check_constraint_rejects_invalid(self):
        """After migration, milestones.status rejects invalid values."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            res = _run_bootstrap(workspace)
            assert res.returncode == 0, (
                f"bootstrap failed:\nstdout:\n{res.stdout}\nstderr:\n{res.stderr}"
            )
            db = workspace / "tmp_gaia.db"
            con = sqlite3.connect(str(db))
            con.execute("PRAGMA foreign_keys = ON")
            try:
                con.execute(
                    "INSERT OR IGNORE INTO workspaces (name) VALUES ('test-ws')"
                )
                con.execute(
                    "INSERT INTO briefs (workspace, name, status) "
                    "VALUES ('test-ws', 'test-brief', 'draft')"
                )
                brief_id = con.execute(
                    "SELECT id FROM briefs WHERE workspace='test-ws' AND name='test-brief'"
                ).fetchone()[0]

                with pytest.raises(sqlite3.IntegrityError):
                    con.execute(
                        "INSERT INTO milestones "
                        "(brief_id, order_num, name, status) "
                        "VALUES (?, 1, 'M1', 'invalid_status')",
                        (brief_id,),
                    )
            finally:
                con.close()

    def test_bootstrap_idempotent_at_v5(self):
        """Running bootstrap twice on a v5 DB: second run is a no-op."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            # First run
            res1 = _run_bootstrap(workspace)
            assert res1.returncode == 0, (
                f"first bootstrap failed:\nstdout:\n{res1.stdout}\nstderr:\n{res1.stderr}"
            )
            db = workspace / "tmp_gaia.db"

            # Second run
            res2 = _run_bootstrap_with_db(db, workspace)
            assert res2.returncode == 0, (
                f"second bootstrap failed:\nstdout:\n{res2.stdout}\nstderr:\n{res2.stderr}"
            )

            con = sqlite3.connect(str(db))
            try:
                count = con.execute(
                    "SELECT COUNT(*) FROM schema_version WHERE version = 5"
                ).fetchone()[0]
            finally:
                con.close()

            assert count == 1, (
                f"schema_version should have exactly 1 row for v5, got {count}"
            )

    def test_schema_version_advances_to_v5(self):
        """After fresh bootstrap, MAX(version) in schema_version >= 5."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            res = _run_bootstrap(workspace)
            assert res.returncode == 0, (
                f"bootstrap failed:\nstdout:\n{res.stdout}\nstderr:\n{res.stderr}"
            )
            db = workspace / "tmp_gaia.db"
            con = sqlite3.connect(str(db))
            try:
                max_version = con.execute(
                    "SELECT MAX(version) FROM schema_version"
                ).fetchone()[0]
            finally:
                con.close()

            assert max_version >= 5, (
                f"expected schema_version MAX>=5, got {max_version}"
            )
