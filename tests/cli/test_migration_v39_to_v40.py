"""Migration v39 -> v40: the harness's per-run agent id on agent_contract_handoffs.

Covered here, mirroring test_migration_v37_to_v38.py's shape:

* the migration applies cleanly on a synthetic v39-shaped DB and adds both
  the column and its partial index;
* existing rows survive untouched (harness_agent_id NULL);
* replaying the migration on an already-migrated DB is a no-op (the floor
  model: every migration is replayed on every fresh install);
* a row stamped with a harness_agent_id is resolvable by that id via the
  exact SELECT list_agent_contract_handoffs(harness_agent_id=...) issues --
  the recovery property PASO 2b asks for.
"""

from __future__ import annotations

import importlib.util
import sqlite3
import tempfile
import unittest
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[2]
_BOOTSTRAP_PY = _REPO_ROOT / "scripts" / "bootstrap_database.py"
_MIGRATION = _REPO_ROOT / "scripts" / "migrations" / "v39_to_v40.sql"

_INDEX_NAME = "idx_agent_contract_handoffs_harness"

# The v39 shape of agent_contract_handoffs, verbatim from schema.sql at HEAD
# (before this migration's column), deliberately WITHOUT harness_agent_id --
# exactly what v39_to_v40 adds.
_V39_SCHEMA = """
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
    cut_reason        TEXT,
    raw_handoff_json  TEXT NOT NULL,
    created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX idx_agent_contract_handoffs_workspace ON agent_contract_handoffs(workspace);
CREATE INDEX idx_agent_contract_handoffs_brief     ON agent_contract_handoffs(brief_id);
CREATE INDEX idx_agent_contract_handoffs_session   ON agent_contract_handoffs(session_id);
CREATE UNIQUE INDEX idx_agent_contract_handoffs_contract_id ON agent_contract_handoffs(contract_id);
CREATE INDEX idx_agent_contract_handoffs_plan_task ON agent_contract_handoffs(plan_task_id);
CREATE INDEX idx_agent_contract_handoffs_cut ON agent_contract_handoffs(cut_reason) WHERE cut_reason IS NOT NULL;
"""

# The exact SELECT list_agent_contract_handoffs(harness_agent_id=...) issues
# (gaia/store/writer.py), so this test pins the real recovery query, not a
# paraphrase of it.
_HARNESS_LOOKUP_QUERY = (
    "SELECT * FROM agent_contract_handoffs WHERE harness_agent_id = ? "
    "ORDER BY created_at DESC LIMIT ?"
)


def _load_bootstrap_module():
    """Import scripts/bootstrap_database.py so the migration is applied through
    the real runner's ADD COLUMN idempotency guard, exactly as bootstrap does."""
    spec = importlib.util.spec_from_file_location("gaia_bootstrap_db", _BOOTSTRAP_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _build_v39_db(db_path: Path) -> list[tuple]:
    """Materialise a v39 DB with a mix of terminal and open rows."""
    rows = [
        ("a1.tok1", "a1", "sess1", "ws", "COMPLETE"),
        ("a2.tok2", "a2", "sess1", "ws", "DISPATCHED"),
        ("a3.tok3", "a3", "sess2", "ws", "IN_PROGRESS"),
    ]
    con = sqlite3.connect(str(db_path))
    try:
        con.executescript(_V39_SCHEMA)
        con.execute(
            "INSERT INTO schema_version (version, applied_at, description) "
            "VALUES (39, '2026-01-01T00:00:00Z', 'synthetic v39 DB')"
        )
        inserted: list[tuple] = []
        for contract_id, agent_id, session_id, workspace, state in rows:
            cur = con.execute(
                "INSERT INTO agent_contract_handoffs "
                "(contract_id, agent_id, session_id, workspace, agent_state, "
                " raw_handoff_json) VALUES (?, ?, ?, ?, ?, ?)",
                (contract_id, agent_id, session_id, workspace, state, "{}"),
            )
            inserted.append((cur.lastrowid, contract_id, None))
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


def _has_harness_column(con: sqlite3.Connection) -> bool:
    return any(
        row[1] == "harness_agent_id"
        for row in con.execute("PRAGMA table_info(agent_contract_handoffs)").fetchall()
    )


class TestMigrationV39ToV40(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "gaia.db"
        self.bootstrap = _load_bootstrap_module()

    def tearDown(self):
        self._tmp.cleanup()

    def test_migration_file_exists(self):
        self.assertTrue(
            _MIGRATION.is_file(),
            f"bootstrap cannot advance to v40 without {_MIGRATION}",
        )

    def test_column_is_absent_before_the_migration(self):
        """The regression this migration answers -- asserted, not assumed."""
        _build_v39_db(self.db_path)
        con = sqlite3.connect(str(self.db_path))
        try:
            self.assertFalse(_has_harness_column(con))
        finally:
            con.close()

    def test_migration_adds_the_column_and_the_partial_index(self):
        _build_v39_db(self.db_path)
        con = sqlite3.connect(str(self.db_path))
        try:
            _apply_migration(con, self.bootstrap)

            self.assertTrue(_has_harness_column(con))
            self.assertIn(_INDEX_NAME, _index_names(con))
        finally:
            con.close()

    def test_rows_survive_the_migration_with_harness_agent_id_null(self):
        expected = _build_v39_db(self.db_path)
        con = sqlite3.connect(str(self.db_path))
        try:
            _apply_migration(con, self.bootstrap)
            actual = con.execute(
                "SELECT id, contract_id, harness_agent_id FROM agent_contract_handoffs "
                "ORDER BY id"
            ).fetchall()
            self.assertEqual([tuple(r) for r in actual], expected)
        finally:
            con.close()

    def test_replay_on_an_already_migrated_db_is_a_noop(self):
        """The fresh-install path: schema.sql creates the column+index BEFORE
        the migration is replayed, so applying it again must not fail."""
        _build_v39_db(self.db_path)
        con = sqlite3.connect(str(self.db_path))
        try:
            _apply_migration(con, self.bootstrap)
            first_indexes = _index_names(con)

            _apply_migration(con, self.bootstrap)

            self.assertTrue(_has_harness_column(con))
            self.assertEqual(_index_names(con), first_indexes)
            self.assertIn(_INDEX_NAME, first_indexes)
        finally:
            con.close()

    def test_stamped_row_is_resolvable_by_harness_agent_id(self):
        """PASO 2b's property: recover a cut turn's row by the id the harness
        (not the CLI-minted identity space) reports, with the real query."""
        _build_v39_db(self.db_path)
        con = sqlite3.connect(str(self.db_path))
        try:
            _apply_migration(con, self.bootstrap)
            con.execute(
                "UPDATE agent_contract_handoffs SET harness_agent_id = ? "
                "WHERE contract_id = ?",
                ("harness-run-xyz", "a2.tok2"),
            )
            con.commit()

            con.row_factory = sqlite3.Row
            rows = con.execute(_HARNESS_LOOKUP_QUERY, ("harness-run-xyz", 100)).fetchall()

            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["contract_id"], "a2.tok2")

            miss = con.execute(_HARNESS_LOOKUP_QUERY, ("no-such-id", 100)).fetchall()
            self.assertEqual(miss, [])
        finally:
            con.close()


if __name__ == "__main__":
    unittest.main()
