"""Gate 33 (plan 34 / brief 114, task 3): migration test for v36 -> v37,
the born-at-dispatch foundation on agent_contract_handoffs.

Starting from a copy of the DB at the v36 shape, this asserts that
scripts/migrations/v36_to_v37.sql:

  * applies without error;
  * renames the turn-state column task_status -> agent_state (the old name
    is GONE, the new one PRESENT);
  * adds the four binding columns plan_task_id / plan_id / parent_handoff_id /
    kind (all NULLABLE);
  * widens the CHECK so agent_state accepts the new ROW state DISPATCHED in
    addition to the six envelope verdicts;
  * backfills every legacy row, carrying its previous task_status value
    verbatim into agent_state (and leaving the binding columns NULL);
  * preserves the UNIQUE-ness of contract_id (the idempotency key finalize's
    ON CONFLICT(contract_id) DO NOTHING relies on);
  * preserves the three ordinary indexes.

It also proves the migration is idempotent across the rename: replayed against
a DB ALREADY at the v37 shape (the fresh-install case, where schema.sql has
produced agent_state and there is no task_status to SELECT), it is a harmless
no-op rather than a "no such column" abort. That idempotency depends on the
bootstrap runner's ADD COLUMN guard, so the migration is applied here through
the SAME helper the runner uses (bootstrap_database._filter_add_column_idempotent).
"""

from __future__ import annotations

import importlib.util
import sqlite3
import tempfile
import unittest
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION = _REPO_ROOT / "scripts" / "migrations" / "v36_to_v37.sql"
_BOOTSTRAP_PY = _REPO_ROOT / "scripts" / "bootstrap_database.py"

_SIX_VERDICTS = (
    "IN_PROGRESS",
    "APPROVAL_REQUEST",
    "COMPLETE",
    "BLOCKED",
    "NEEDS_INPUT",
    "NEEDS_VERIFICATION",
)
_BINDING_COLUMNS = ("plan_task_id", "plan_id", "parent_handoff_id", "kind")
_EXPECTED_INDEXES = {
    "idx_agent_contract_handoffs_workspace",
    "idx_agent_contract_handoffs_brief",
    "idx_agent_contract_handoffs_session",
    "idx_agent_contract_handoffs_contract_id",
}


def _load_bootstrap_module():
    """Import scripts/bootstrap_database.py to reuse the runner's ADD COLUMN
    idempotency guard verbatim (the migration is applied through it, exactly as
    bootstrap does, so the test exercises the real replay path)."""
    spec = importlib.util.spec_from_file_location("gaia_bootstrap_db", _BOOTSTRAP_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# v36 fixture: the agent_contract_handoffs table exactly as it was BEFORE this
# migration (task_status column, six-value CHECK, no binding columns), plus the
# schema_version ledger stamped at 36.
# ---------------------------------------------------------------------------

_V36_SCHEMA = """
CREATE TABLE schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL,
    description TEXT
);

CREATE TABLE agent_contract_handoffs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_id      TEXT,
    agent_id         TEXT NOT NULL,
    session_id       TEXT,
    workspace        TEXT NOT NULL,
    brief_id         INTEGER,
    task_status      TEXT NOT NULL
                     CHECK (task_status IN ('IN_PROGRESS', 'APPROVAL_REQUEST', 'COMPLETE', 'BLOCKED', 'NEEDS_INPUT', 'NEEDS_VERIFICATION')),
    raw_handoff_json TEXT NOT NULL,
    created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX idx_agent_contract_handoffs_workspace ON agent_contract_handoffs(workspace);
CREATE INDEX idx_agent_contract_handoffs_brief     ON agent_contract_handoffs(brief_id);
CREATE INDEX idx_agent_contract_handoffs_session   ON agent_contract_handoffs(session_id);
CREATE UNIQUE INDEX idx_agent_contract_handoffs_contract_id ON agent_contract_handoffs(contract_id);
"""


def _build_v36_db(db_path: Path) -> list[tuple]:
    """Materialise a v36 DB with a spread of legacy handoff rows.

    Returns the list of (id, contract_id, task_status) tuples inserted, so the
    backfill assertion can compare the post-migration agent_state against the
    exact legacy value each row started with.
    """
    # Legacy rows: cover several verdicts, and mix rows that carry a contract_id
    # with rows that leave it NULL (the pre-T7 / no-draft path the UNIQUE index
    # must keep tolerating).
    legacy = [
        ("a1.tok1", "a1", "sess1", "ws", "COMPLETE"),
        ("a2.tok2", "a2", "sess2", "ws", "IN_PROGRESS"),
        (None, "a3", "sess3", "ws", "BLOCKED"),
        (None, "a4", None, "ws", "NEEDS_VERIFICATION"),
        ("a5.tok5", "a5", "sess5", "ws", "APPROVAL_REQUEST"),
    ]
    con = sqlite3.connect(str(db_path))
    try:
        con.executescript(_V36_SCHEMA)
        con.execute(
            "INSERT INTO schema_version (version, applied_at, description) "
            "VALUES (36, '2026-01-01T00:00:00Z', 'synthetic v36 DB')"
        )
        expected: list[tuple] = []
        for contract_id, agent_id, session_id, workspace, task_status in legacy:
            cur = con.execute(
                "INSERT INTO agent_contract_handoffs "
                "(contract_id, agent_id, session_id, workspace, task_status, raw_handoff_json) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (contract_id, agent_id, session_id, workspace, task_status, "{}"),
            )
            expected.append((cur.lastrowid, contract_id, task_status))
        con.commit()
        return expected
    finally:
        con.close()


def _apply_migration(con: sqlite3.Connection, bootstrap) -> None:
    """Apply the migration exactly as the bootstrap runner does: filter ADD
    COLUMN lines against the live schema, then run inside one transaction."""
    mig_sql = bootstrap._filter_add_column_idempotent(con, _MIGRATION)
    con.executescript(f"BEGIN;\n{mig_sql}\nCOMMIT;")


def _columns(con: sqlite3.Connection, table: str) -> set[str]:
    return {
        r[1]
        for r in con.execute(f"PRAGMA table_info('{table}')").fetchall()
    }


def _index_names(con: sqlite3.Connection) -> set[str]:
    return {
        r[0]
        for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND tbl_name='agent_contract_handoffs'"
        ).fetchall()
    }


class TestMigrationV36ToV37(unittest.TestCase):
    def setUp(self):
        if not _MIGRATION.is_file():
            self.skipTest(f"migration not found at {_MIGRATION}")
        if not _BOOTSTRAP_PY.is_file():
            self.skipTest(f"bootstrap_database.py not found at {_BOOTSTRAP_PY}")
        if sqlite3.sqlite_version_info < (3, 25, 0):
            self.skipTest(
                "ALTER TABLE ADD COLUMN + table rebuild requires SQLite >= 3.25; "
                f"have {sqlite3.sqlite_version}"
            )
        self.bootstrap = _load_bootstrap_module()

    def test_migration_applies_and_reshapes_the_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "v36.db"
            expected_rows = _build_v36_db(db)

            con = sqlite3.connect(str(db))
            con.row_factory = sqlite3.Row
            try:
                # Precondition: the fixture really is the pre-migration shape.
                cols_before = _columns(con, "agent_contract_handoffs")
                self.assertIn("task_status", cols_before)
                self.assertNotIn("agent_state", cols_before)
                for b in _BINDING_COLUMNS:
                    self.assertNotIn(b, cols_before, f"fixture already has {b}")

                # Apply the migration (no error expected).
                _apply_migration(con, self.bootstrap)

                # --- column reshape -------------------------------------------
                cols_after = _columns(con, "agent_contract_handoffs")
                self.assertIn(
                    "agent_state", cols_after,
                    "agent_state column missing after migration",
                )
                self.assertNotIn(
                    "task_status", cols_after,
                    "task_status column still present after rename",
                )
                for b in _BINDING_COLUMNS:
                    self.assertIn(
                        b, cols_after, f"binding column {b} missing after migration",
                    )

                # --- indexes preserved ----------------------------------------
                self.assertTrue(
                    _EXPECTED_INDEXES <= _index_names(con),
                    f"missing indexes: {_EXPECTED_INDEXES - _index_names(con)}",
                )
                # contract_id index must still be UNIQUE.
                idx_sql = con.execute(
                    "SELECT sql FROM sqlite_master WHERE type='index' "
                    "AND name='idx_agent_contract_handoffs_contract_id'"
                ).fetchone()[0]
                self.assertIn("UNIQUE", idx_sql.upper())

                # --- legacy rows backfilled -----------------------------------
                for row_id, contract_id, legacy_status in expected_rows:
                    row = con.execute(
                        "SELECT agent_state, plan_task_id, plan_id, "
                        "parent_handoff_id, kind, contract_id "
                        "FROM agent_contract_handoffs WHERE id = ?",
                        (row_id,),
                    ).fetchone()
                    self.assertIsNotNone(row, f"row id={row_id} lost in migration")
                    self.assertEqual(
                        row["agent_state"], legacy_status,
                        f"row id={row_id} not backfilled with its legacy status",
                    )
                    # binding columns default NULL for legacy rows
                    for b in _BINDING_COLUMNS:
                        self.assertIsNone(row[b], f"legacy row {row_id} has non-NULL {b}")
                    self.assertEqual(row["contract_id"], contract_id)

                # --- CHECK accepts DISPATCHED (the new ROW state) -------------
                con.execute(
                    "INSERT INTO agent_contract_handoffs "
                    "(contract_id, agent_id, workspace, agent_state, raw_handoff_json) "
                    "VALUES ('born.1', 'aborn', 'ws', 'DISPATCHED', '{}')"
                )
                con.commit()
                self.assertEqual(
                    con.execute(
                        "SELECT agent_state FROM agent_contract_handoffs "
                        "WHERE contract_id = 'born.1'"
                    ).fetchone()["agent_state"],
                    "DISPATCHED",
                )

                # --- CHECK still accepts the six verdicts ---------------------
                for i, verdict in enumerate(_SIX_VERDICTS):
                    con.execute(
                        "INSERT INTO agent_contract_handoffs "
                        "(contract_id, agent_id, workspace, agent_state, raw_handoff_json) "
                        "VALUES (?, 'averd', 'ws', ?, '{}')",
                        (f"verd.{i}", verdict),
                    )
                con.commit()

                # --- CHECK rejects a value outside the widened enum -----------
                with self.assertRaises(sqlite3.IntegrityError):
                    con.execute(
                        "INSERT INTO agent_contract_handoffs "
                        "(contract_id, agent_id, workspace, agent_state, raw_handoff_json) "
                        "VALUES ('bogus.1', 'abog', 'ws', 'NOT_A_STATE', '{}')"
                    )
                con.rollback()

                # --- contract_id uniqueness preserved -------------------------
                with self.assertRaises(sqlite3.IntegrityError):
                    con.execute(
                        "INSERT INTO agent_contract_handoffs "
                        "(contract_id, agent_id, workspace, agent_state, raw_handoff_json) "
                        "VALUES ('a1.tok1', 'adup', 'ws', 'COMPLETE', '{}')"
                    )
                con.rollback()
            finally:
                con.close()

    def test_migration_is_idempotent_on_fresh_v37_shape(self):
        """Replaying the migration against a DB already at the v37 shape (the
        fresh-install case, where the source column is agent_state and there is
        no task_status) must be a harmless no-op, not a 'no such column' abort."""
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "v36.db"
            _build_v36_db(db)

            con = sqlite3.connect(str(db))
            con.row_factory = sqlite3.Row
            try:
                # First pass brings the fixture to the v37 shape.
                _apply_migration(con, self.bootstrap)
                cols_v37 = _columns(con, "agent_contract_handoffs")
                rows_v37 = con.execute(
                    "SELECT id, agent_state, contract_id FROM agent_contract_handoffs "
                    "ORDER BY id"
                ).fetchall()

                # Second pass (replay) must not error and must not change the shape
                # or the data -- exactly what a fresh-install replay demands.
                _apply_migration(con, self.bootstrap)
                self.assertEqual(_columns(con, "agent_contract_handoffs"), cols_v37)
                rows_replay = con.execute(
                    "SELECT id, agent_state, contract_id FROM agent_contract_handoffs "
                    "ORDER BY id"
                ).fetchall()
                self.assertEqual(
                    [tuple(r) for r in rows_replay],
                    [tuple(r) for r in rows_v37],
                    "replay mutated rows (not idempotent)",
                )
                self.assertNotIn("task_status", _columns(con, "agent_contract_handoffs"))
                self.assertTrue(_EXPECTED_INDEXES <= _index_names(con))
            finally:
                con.close()


if __name__ == "__main__":
    unittest.main()
