"""Migration v37 -> v38: the plan-task binding gets its own index.

The binding column ``agent_contract_handoffs.plan_task_id`` arrived in v37 as a
foreign key to ``tasks.id``. SQLite indexes only the PARENT side of a foreign
key, so a lookup keyed on the CHILD column scanned the whole table. This
migration adds the missing index.

Covered here:

* the migration creates the index on a live v37-shaped DB, and the query that
  motivated it stops being a full scan (asserted through EXPLAIN QUERY PLAN,
  not by reading the DDL back);
* replaying the migration on a DB that already carries the index is a no-op,
  which is what the floor model requires since every migration is replayed on
  every fresh install;
* rows survive the migration untouched (an index is derived state).
"""

from __future__ import annotations

import importlib.util
import sqlite3
import tempfile
import unittest
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[2]
_BOOTSTRAP_PY = _REPO_ROOT / "scripts" / "bootstrap_database.py"
_MIGRATION = _REPO_ROOT / "scripts" / "migrations" / "v37_to_v38.sql"

_INDEX_NAME = "idx_agent_contract_handoffs_plan_task"

# The v37 shape of the table, verbatim from v36_to_v37.sql's rebuild, plus the
# indexes that migration recreates -- deliberately WITHOUT the plan_task_id
# index, which is exactly what v37_to_v38 adds.
_V37_SCHEMA = """
CREATE TABLE schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL,
    description TEXT
);

CREATE TABLE agent_contract_handoffs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_id       TEXT,
    agent_id          TEXT NOT NULL,
    session_id        TEXT,
    workspace         TEXT NOT NULL,
    brief_id          INTEGER,
    plan_task_id      INTEGER,
    plan_id           INTEGER,
    parent_handoff_id INTEGER,
    kind              TEXT,
    agent_state       TEXT NOT NULL
                      CHECK (agent_state IN ('IN_PROGRESS', 'APPROVAL_REQUEST', 'COMPLETE', 'BLOCKED', 'NEEDS_INPUT', 'NEEDS_VERIFICATION', 'DISPATCHED')),
    raw_handoff_json  TEXT NOT NULL,
    created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX idx_agent_contract_handoffs_workspace ON agent_contract_handoffs(workspace);
CREATE INDEX idx_agent_contract_handoffs_brief     ON agent_contract_handoffs(brief_id);
CREATE INDEX idx_agent_contract_handoffs_session   ON agent_contract_handoffs(session_id);
CREATE UNIQUE INDEX idx_agent_contract_handoffs_contract_id ON agent_contract_handoffs(contract_id);
"""

# The read the closure condition performs on the identity axis: every handoff
# row bound to one plan task.
_BINDING_QUERY = (
    "SELECT id, agent_id, plan_task_id, kind, agent_state "
    "FROM agent_contract_handoffs WHERE plan_task_id = ? ORDER BY id"
)


def _load_bootstrap_module():
    """Import scripts/bootstrap_database.py so the migration is applied through
    the real runner's ADD COLUMN idempotency guard, exactly as bootstrap does."""
    spec = importlib.util.spec_from_file_location("gaia_bootstrap_db", _BOOTSTRAP_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _build_v37_db(db_path: Path) -> list[tuple]:
    """Materialise a v37 DB with handoff rows across several plan tasks."""
    rows = [
        ("a1.tok1", "a1", "sess1", "ws", 10, "COMPLETE"),
        ("a2.tok2", "a2", "sess1", "ws", 10, "DISPATCHED"),
        ("a3.tok3", "a3", "sess2", "ws", 11, "IN_PROGRESS"),
        # A free-standing turn: no plan task binding at all.
        ("a4.tok4", "a4", "sess2", "ws", None, "COMPLETE"),
    ]
    con = sqlite3.connect(str(db_path))
    try:
        con.executescript(_V37_SCHEMA)
        con.execute(
            "INSERT INTO schema_version (version, applied_at, description) "
            "VALUES (37, '2026-01-01T00:00:00Z', 'synthetic v37 DB')"
        )
        inserted: list[tuple] = []
        for contract_id, agent_id, session_id, workspace, plan_task_id, state in rows:
            cur = con.execute(
                "INSERT INTO agent_contract_handoffs "
                "(contract_id, agent_id, session_id, workspace, plan_task_id, "
                " agent_state, raw_handoff_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (contract_id, agent_id, session_id, workspace, plan_task_id, state, "{}"),
            )
            inserted.append((cur.lastrowid, contract_id, plan_task_id))
        con.commit()
        return inserted
    finally:
        con.close()


def _apply_migration(con: sqlite3.Connection, bootstrap) -> None:
    mig_sql = bootstrap._filter_add_column_idempotent(con, _MIGRATION)
    con.executescript(f"BEGIN;\n{mig_sql}\nCOMMIT;")


def _index_names(con: sqlite3.Connection) -> set[str]:
    return {
        r[0]
        for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND tbl_name='agent_contract_handoffs'"
        ).fetchall()
    }


def _query_plan(con: sqlite3.Connection) -> str:
    return " ".join(
        str(r[3])
        for r in con.execute(
            f"EXPLAIN QUERY PLAN {_BINDING_QUERY}", (10,)
        ).fetchall()
    )


class TestMigrationV37ToV38(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "gaia.db"
        self.bootstrap = _load_bootstrap_module()

    def tearDown(self):
        self._tmp.cleanup()

    def test_migration_file_exists(self):
        self.assertTrue(
            _MIGRATION.is_file(),
            f"bootstrap cannot advance to v38 without {_MIGRATION}",
        )

    def test_binding_lookup_is_a_full_scan_before_the_migration(self):
        """The regression this migration answers -- asserted, not assumed."""
        _build_v37_db(self.db_path)
        con = sqlite3.connect(str(self.db_path))
        try:
            self.assertIn("SCAN", _query_plan(con))
        finally:
            con.close()

    def test_migration_creates_the_index_and_removes_the_scan(self):
        _build_v37_db(self.db_path)
        con = sqlite3.connect(str(self.db_path))
        try:
            _apply_migration(con, self.bootstrap)

            self.assertIn(_INDEX_NAME, _index_names(con))
            plan = _query_plan(con)
            self.assertIn(_INDEX_NAME, plan)
            self.assertNotIn("SCAN agent_contract_handoffs", plan)
        finally:
            con.close()

    def test_rows_survive_the_migration(self):
        expected = _build_v37_db(self.db_path)
        con = sqlite3.connect(str(self.db_path))
        try:
            _apply_migration(con, self.bootstrap)
            actual = con.execute(
                "SELECT id, contract_id, plan_task_id FROM agent_contract_handoffs "
                "ORDER BY id"
            ).fetchall()
            self.assertEqual([tuple(r) for r in actual], expected)
        finally:
            con.close()

    def test_replay_on_an_already_migrated_db_is_a_noop(self):
        """The fresh-install path: schema.sql creates the index BEFORE the
        migration is replayed, so applying it again must not fail."""
        _build_v37_db(self.db_path)
        con = sqlite3.connect(str(self.db_path))
        try:
            _apply_migration(con, self.bootstrap)
            first = _index_names(con)

            _apply_migration(con, self.bootstrap)

            self.assertEqual(_index_names(con), first)
            self.assertIn(_INDEX_NAME, first)
        finally:
            con.close()


if __name__ == "__main__":
    unittest.main()
