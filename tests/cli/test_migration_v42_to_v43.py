"""Migration v42 -> v43: dispatch correlation + kernel payload columns.

Seven nullable columns (dispatch_prompt_id / dispatch_tool_use_id /
dispatch_description / dispatch_prompt / claimed_at / context_anchors /
kernel_sections) plus the partial unclaimed index on
``agent_contract_handoffs``. Covered here:

* the migration adds all seven columns and the index to a v42-shaped table,
  and existing rows survive with NULLs (nullable, never destructive);
* replaying it through the bootstrap runner's ADD COLUMN idempotency guard is
  a no-op, which is what the floor model requires (every migration replays on
  every fresh install);
* the migration file keeps the one-ALTER-per-line shape both bootstrap layers
  (pre-schema reconcile, idempotency filter) parse.
"""

from __future__ import annotations

import importlib.util
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BOOTSTRAP_PY = _REPO_ROOT / "scripts" / "bootstrap_database.py"
_MIGRATION = _REPO_ROOT / "scripts" / "migrations" / "v42_to_v43.sql"

_NEW_COLUMNS = (
    "dispatch_prompt_id",
    "dispatch_tool_use_id",
    "dispatch_description",
    "dispatch_prompt",
    "claimed_at",
    "context_anchors",
    "kernel_sections",
)
_INDEX_NAME = "idx_agent_contract_handoffs_unclaimed"

# The v42 shape of the table -- the pre-migration columns only.
_V42_SCHEMA = """
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
    agent_state       TEXT NOT NULL,
    cut_reason        TEXT,
    harness_agent_id  TEXT,
    raw_handoff_json  TEXT NOT NULL,
    created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
"""


def _load_bootstrap_module():
    spec = importlib.util.spec_from_file_location("gaia_bootstrap_db", _BOOTSTRAP_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _columns(con: sqlite3.Connection) -> set:
    return {
        r[1] for r in con.execute(
            "PRAGMA table_info('agent_contract_handoffs')"
        )
    }


class TestMigrationV42ToV43(unittest.TestCase):
    def setUp(self):
        if not _MIGRATION.is_file():
            self.skipTest(f"migration not found at {_MIGRATION}")

    def test_adds_columns_and_index_preserving_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "gaia.db"
            con = sqlite3.connect(str(db))
            try:
                con.executescript(_V42_SCHEMA)
                con.execute(
                    "INSERT INTO agent_contract_handoffs "
                    "(contract_id, agent_id, workspace, agent_state, raw_handoff_json) "
                    "VALUES ('c.1', 'a0', 'me', 'DISPATCHED', '{}')"
                )
                con.commit()
                self.assertFalse(_NEW_COLUMNS[0] in _columns(con))

                con.executescript(_MIGRATION.read_text())
                con.commit()

                cols = _columns(con)
                for column in _NEW_COLUMNS:
                    self.assertIn(column, cols, f"missing column {column}")

                idx = con.execute(
                    "SELECT sql FROM sqlite_master WHERE type='index' AND name=?",
                    (_INDEX_NAME,),
                ).fetchone()
                self.assertIsNotNone(idx, "unclaimed partial index missing")
                self.assertIn("claimed_at IS NULL", idx[0])

                row = con.execute(
                    "SELECT contract_id, claimed_at, kernel_sections "
                    "FROM agent_contract_handoffs"
                ).fetchone()
                self.assertEqual(row[0], "c.1", "existing row must survive")
                self.assertIsNone(row[1])
                self.assertIsNone(row[2])
            finally:
                con.close()

    def test_replay_through_bootstrap_guard_is_a_noop(self):
        bootstrap = _load_bootstrap_module()
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "gaia.db"
            con = sqlite3.connect(str(db))
            try:
                con.isolation_level = None
                con.executescript(_V42_SCHEMA)
                con.executescript(_MIGRATION.read_text())

                filtered = bootstrap._filter_add_column_idempotent(con, _MIGRATION)
                for column in _NEW_COLUMNS:
                    self.assertIn(
                        f"skipped (column agent_contract_handoffs.{column} "
                        "already present)",
                        filtered,
                    )
                con.executescript(filtered)  # must not raise
                self.assertEqual(
                    sum(1 for c in _columns(con) if c in _NEW_COLUMNS),
                    len(_NEW_COLUMNS),
                )
            finally:
                con.close()

    def test_one_alter_per_line_shape(self):
        """Both bootstrap layers parse ALTER statements line-by-line."""
        alter_re = re.compile(
            r"^ALTER TABLE agent_contract_handoffs ADD COLUMN \w+ TEXT;$"
        )
        alters = [
            line for line in _MIGRATION.read_text().splitlines()
            if line.startswith("ALTER TABLE")
        ]
        self.assertEqual(len(alters), len(_NEW_COLUMNS))
        for line in alters:
            self.assertRegex(line, alter_re)


if __name__ == "__main__":
    unittest.main()
