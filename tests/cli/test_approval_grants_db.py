#!/usr/bin/env python3
"""Tests for the 5 writer functions for the approval_grants table.

Covers:
1. insert_approval_grant -- PENDING row created
2. update_approval_grant_status -- status transitions
3. mark_command_set_item_consumed -- index tracking + auto-CONSUMED
4. revoke_approval_grant -- REVOKED transition + no-op on terminal state
5. list_approval_grants -- filters by session_id, status; index presence
"""

import json
import sqlite3
import sys
from pathlib import Path

import pytest

GAIA_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(GAIA_ROOT))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_db(tmp_path):
    """Return a fresh SQLite DB path with the approval_grants table + indexes."""
    db = tmp_path / "test.db"
    con = sqlite3.connect(str(db))
    con.executescript("""
        CREATE TABLE IF NOT EXISTS approval_grants (
            approval_id          TEXT PRIMARY KEY,
            agent_id             TEXT,
            session_id           TEXT,
            command_set_json     TEXT NOT NULL,
            scope                TEXT NOT NULL DEFAULT 'COMMAND_SET',
            created_at           TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
            expires_at           TEXT,
            status               TEXT NOT NULL DEFAULT 'PENDING',
            consumed_indexes_json TEXT,
            consumed_at          TEXT,
            revoked_at           TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_approval_grants_agent   ON approval_grants(agent_id);
        CREATE INDEX IF NOT EXISTS idx_approval_grants_session ON approval_grants(session_id);
        CREATE INDEX IF NOT EXISTS idx_approval_grants_status  ON approval_grants(status);
    """)
    con.close()
    return db


SAMPLE_COMMAND_SET = [
    {"command": "git push origin main", "rationale": "deploy"},
    {"command": "helm upgrade app ./chart", "rationale": "upgrade chart"},
]


# ---------------------------------------------------------------------------
# 1. insert_approval_grant
# ---------------------------------------------------------------------------

class TestInsertApprovalGrant:
    def test_insert_creates_pending_row(self, tmp_db):
        from gaia.store.writer import insert_approval_grant
        result = insert_approval_grant(
            "approval-abc",
            SAMPLE_COMMAND_SET,
            agent_id="gaia-system",
            session_id="sess-100",
            scope="COMMAND_SET",
            db_path=tmp_db,
        )
        assert result.get("status") == "applied"

        con = sqlite3.connect(str(tmp_db))
        row = con.execute(
            "SELECT approval_id, status, scope, agent_id, session_id "
            "FROM approval_grants WHERE approval_id = ?",
            ("approval-abc",),
        ).fetchone()
        con.close()

        assert row is not None
        assert row[0] == "approval-abc"
        assert row[1] == "PENDING"
        assert row[2] == "COMMAND_SET"
        assert row[3] == "gaia-system"
        assert row[4] == "sess-100"

    def test_insert_stores_command_set_json(self, tmp_db):
        from gaia.store.writer import insert_approval_grant
        insert_approval_grant(
            "approval-json",
            SAMPLE_COMMAND_SET,
            session_id="sess-101",
            db_path=tmp_db,
        )
        con = sqlite3.connect(str(tmp_db))
        row = con.execute(
            "SELECT command_set_json FROM approval_grants WHERE approval_id = ?",
            ("approval-json",),
        ).fetchone()
        con.close()
        parsed = json.loads(row[0])
        assert len(parsed) == 2
        assert parsed[0]["command"] == "git push origin main"

    def test_duplicate_approval_id_is_error(self, tmp_db):
        from gaia.store.writer import insert_approval_grant
        insert_approval_grant("approval-dup", SAMPLE_COMMAND_SET, db_path=tmp_db)
        result2 = insert_approval_grant("approval-dup", SAMPLE_COMMAND_SET, db_path=tmp_db)
        assert result2.get("status") == "error"


# ---------------------------------------------------------------------------
# 2. update_approval_grant_status
# ---------------------------------------------------------------------------

class TestUpdateApprovalGrantStatus:
    def test_update_to_expired(self, tmp_db):
        from gaia.store.writer import insert_approval_grant, update_approval_grant_status
        insert_approval_grant("approval-upd", SAMPLE_COMMAND_SET, db_path=tmp_db)
        result = update_approval_grant_status("approval-upd", "EXPIRED", db_path=tmp_db)
        assert result.get("status") == "applied"

        con = sqlite3.connect(str(tmp_db))
        row = con.execute(
            "SELECT status FROM approval_grants WHERE approval_id = ?",
            ("approval-upd",),
        ).fetchone()
        con.close()
        assert row[0] == "EXPIRED"

    def test_update_nonexistent_is_applied_noop(self, tmp_db):
        from gaia.store.writer import update_approval_grant_status
        # UPDATE with no matching row is a SQL no-op (0 rows affected) but
        # writer returns "applied" because no exception was raised.
        result = update_approval_grant_status("no-such-id", "EXPIRED", db_path=tmp_db)
        assert result.get("status") == "applied"


# ---------------------------------------------------------------------------
# 3. mark_command_set_item_consumed
# ---------------------------------------------------------------------------

class TestMarkCommandSetItemConsumed:
    def test_partial_consumption_stays_pending(self, tmp_db):
        from gaia.store.writer import insert_approval_grant, mark_command_set_item_consumed
        insert_approval_grant("approval-partial", SAMPLE_COMMAND_SET, db_path=tmp_db)
        result = mark_command_set_item_consumed("approval-partial", 0, db_path=tmp_db)
        assert result.get("status") == "applied"
        assert result.get("all_consumed") is False

        con = sqlite3.connect(str(tmp_db))
        row = con.execute(
            "SELECT status, consumed_indexes_json FROM approval_grants WHERE approval_id = ?",
            ("approval-partial",),
        ).fetchone()
        con.close()
        assert row[0] == "PENDING"
        assert 0 in json.loads(row[1])

    def test_all_consumed_sets_consumed_status(self, tmp_db):
        from gaia.store.writer import insert_approval_grant, mark_command_set_item_consumed
        single_cmd = [{"command": "terraform apply", "rationale": "apply"}]
        insert_approval_grant("approval-full", single_cmd, db_path=tmp_db)
        result = mark_command_set_item_consumed("approval-full", 0, db_path=tmp_db)
        assert result.get("all_consumed") is True

        con = sqlite3.connect(str(tmp_db))
        row = con.execute(
            "SELECT status, consumed_at FROM approval_grants WHERE approval_id = ?",
            ("approval-full",),
        ).fetchone()
        con.close()
        assert row[0] == "CONSUMED"
        assert row[1] is not None  # consumed_at was stamped


# ---------------------------------------------------------------------------
# 4. revoke_approval_grant
# ---------------------------------------------------------------------------

class TestRevokeApprovalGrant:
    def test_revoke_pending_grant(self, tmp_db):
        from gaia.store.writer import insert_approval_grant, revoke_approval_grant
        insert_approval_grant("approval-rev", SAMPLE_COMMAND_SET, db_path=tmp_db)
        result = revoke_approval_grant("approval-rev", db_path=tmp_db)
        assert result.get("status") == "applied"

        con = sqlite3.connect(str(tmp_db))
        row = con.execute(
            "SELECT status, revoked_at FROM approval_grants WHERE approval_id = ?",
            ("approval-rev",),
        ).fetchone()
        con.close()
        assert row[0] == "REVOKED"
        assert row[1] is not None

    def test_revoke_already_revoked_is_noop(self, tmp_db):
        from gaia.store.writer import insert_approval_grant, revoke_approval_grant
        insert_approval_grant("approval-rev2", SAMPLE_COMMAND_SET, db_path=tmp_db)
        revoke_approval_grant("approval-rev2", db_path=tmp_db)
        result2 = revoke_approval_grant("approval-rev2", db_path=tmp_db)
        assert result2.get("status") == "no_op"

    def test_revoke_not_found(self, tmp_db):
        from gaia.store.writer import revoke_approval_grant
        result = revoke_approval_grant("no-such-id", db_path=tmp_db)
        assert result.get("status") == "not_found"


# ---------------------------------------------------------------------------
# 5. list_approval_grants + index presence
# ---------------------------------------------------------------------------

class TestListApprovalGrants:
    def test_list_all(self, tmp_db):
        from gaia.store.writer import insert_approval_grant, list_approval_grants
        insert_approval_grant("a1", SAMPLE_COMMAND_SET, session_id="s1", db_path=tmp_db)
        insert_approval_grant("a2", SAMPLE_COMMAND_SET, session_id="s2", db_path=tmp_db)
        rows = list_approval_grants(db_path=tmp_db)
        assert len(rows) == 2

    def test_list_filter_by_session(self, tmp_db):
        from gaia.store.writer import insert_approval_grant, list_approval_grants
        insert_approval_grant("b1", SAMPLE_COMMAND_SET, session_id="s-A", db_path=tmp_db)
        insert_approval_grant("b2", SAMPLE_COMMAND_SET, session_id="s-B", db_path=tmp_db)
        rows = list_approval_grants(session_id="s-A", db_path=tmp_db)
        assert len(rows) == 1
        assert rows[0]["approval_id"] == "b1"

    def test_list_filter_by_status(self, tmp_db):
        from gaia.store.writer import (
            insert_approval_grant,
            list_approval_grants,
            revoke_approval_grant,
        )
        insert_approval_grant("c1", SAMPLE_COMMAND_SET, db_path=tmp_db)
        insert_approval_grant("c2", SAMPLE_COMMAND_SET, db_path=tmp_db)
        revoke_approval_grant("c1", db_path=tmp_db)
        rows = list_approval_grants(status="PENDING", db_path=tmp_db)
        ids = [r["approval_id"] for r in rows]
        assert "c2" in ids
        assert "c1" not in ids

    def test_index_presence(self, tmp_db):
        """Verify the 3 required indexes were created."""
        con = sqlite3.connect(str(tmp_db))
        rows = con.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='approval_grants'"
        ).fetchall()
        con.close()
        index_names = {r[0] for r in rows}
        assert "idx_approval_grants_agent" in index_names
        assert "idx_approval_grants_session" in index_names
        assert "idx_approval_grants_status" in index_names


# ---------------------------------------------------------------------------
# 6. THREAD (c): consume-event consistency between SINGULAR and COMMAND_SET
# ---------------------------------------------------------------------------
#
# These tests LOCK IN a design decision rather than introduce new behavior.
#
# The premise of thread (c) was that the SINGULAR grant writes a "CONSUMED"
# event to approval_events on consume while COMMAND_SET does not. Investigation
# disproved that premise:
#
#   * consume_db_semantic_grant   (SINGULAR consume)      -> only flips the
#     approval_grants row to status=CONSUMED; writes NO approval_events row.
#   * mark_command_set_item_consumed (COMMAND_SET consume) -> only flips the
#     approval_grants row to status=CONSUMED; writes NO approval_events row.
#
# The two consume paths are therefore ALREADY consistent: neither emits a
# consume-time audit event. The approval_events hash-chain (gaia.approvals)
# records the REQUESTED -> SHOWN -> APPROVED lifecycle; the per-command consume
# is tracked exclusively in approval_grants. The approval_events.event_type
# CHECK constraint does not even include 'CONSUMED', so writing one would both
# break the schema AND be the inconsistency. We add no consume event; instead we
# pin the consistency so a future change cannot silently diverge one path.
#
# Uses the real gaia.store.writer._connect (full schema) rather than the
# tmp_db fixture, because we need the approval_events table to assert against.
# ---------------------------------------------------------------------------

class TestConsumeEventConsistency:
    """SINGULAR and COMMAND_SET consume paths must both write zero approval_events."""

    @staticmethod
    def _full_db(tmp_path):
        """A fresh DB materialized through the real connect (whole schema)."""
        from gaia.store.writer import _connect
        db = tmp_path / "full.db"
        con = _connect(db)
        con.close()
        return db

    @staticmethod
    def _count_events(db, approval_id):
        con = sqlite3.connect(str(db))
        n = con.execute(
            "SELECT COUNT(*) FROM approval_events WHERE approval_id = ?",
            (approval_id,),
        ).fetchone()[0]
        con.close()
        return n

    def test_singular_consume_writes_no_approval_event(self, tmp_path):
        from gaia.store.writer import insert_approval_grant, consume_db_semantic_grant
        db = self._full_db(tmp_path)
        insert_approval_grant(
            "sing-evt",
            [{"command": "terraform apply", "rationale": "x"}],
            scope="SCOPE_SEMANTIC_SIGNATURE",
            db_path=db,
        )
        before = self._count_events(db, "sing-evt")
        assert consume_db_semantic_grant("sing-evt", db_path=db) is True
        after = self._count_events(db, "sing-evt")
        assert before == 0 and after == 0, (
            "SINGULAR consume must not write an approval_events row "
            f"(before={before}, after={after})"
        )

    def test_command_set_consume_writes_no_approval_event(self, tmp_path):
        from gaia.store.writer import insert_approval_grant, mark_command_set_item_consumed
        db = self._full_db(tmp_path)
        insert_approval_grant(
            "cs-evt",
            [{"command": "git push origin main", "rationale": "a"},
             {"command": "git push origin tags", "rationale": "b"}],
            scope="COMMAND_SET",
            db_path=db,
        )
        before = self._count_events(db, "cs-evt")
        mark_command_set_item_consumed("cs-evt", 0, db_path=db)
        result = mark_command_set_item_consumed("cs-evt", 1, db_path=db)
        after = self._count_events(db, "cs-evt")
        assert result.get("all_consumed") is True
        assert before == 0 and after == 0, (
            "COMMAND_SET consume must not write an approval_events row "
            f"(before={before}, after={after})"
        )

    def test_both_paths_are_consistent(self, tmp_path):
        """Side-by-side: both consume paths flip to CONSUMED with zero events."""
        from gaia.store.writer import (
            insert_approval_grant,
            consume_db_semantic_grant,
            mark_command_set_item_consumed,
        )
        db = self._full_db(tmp_path)

        insert_approval_grant(
            "both-sing", [{"command": "kubectl apply -f x.yaml", "rationale": "x"}],
            scope="SCOPE_SEMANTIC_SIGNATURE", db_path=db,
        )
        insert_approval_grant(
            "both-cs",
            [{"command": "git push origin main", "rationale": "a"},
             {"command": "helm upgrade app ./chart", "rationale": "b"}],
            scope="COMMAND_SET", db_path=db,
        )

        consume_db_semantic_grant("both-sing", db_path=db)
        mark_command_set_item_consumed("both-cs", 0, db_path=db)
        mark_command_set_item_consumed("both-cs", 1, db_path=db)

        con = sqlite3.connect(str(db))
        sing_status = con.execute(
            "SELECT status FROM approval_grants WHERE approval_id='both-sing'"
        ).fetchone()[0]
        cs_status = con.execute(
            "SELECT status FROM approval_grants WHERE approval_id='both-cs'"
        ).fetchone()[0]
        total_events = con.execute(
            "SELECT COUNT(*) FROM approval_events WHERE approval_id IN ('both-sing','both-cs')"
        ).fetchone()[0]
        con.close()

        # Both reach CONSUMED; neither emits any approval_events row.
        assert sing_status == "CONSUMED"
        assert cs_status == "CONSUMED"
        assert total_events == 0, (
            "consistency invariant: neither consume path writes approval_events "
            f"(got {total_events})"
        )

    def test_consumed_event_type_rejected_by_schema(self, tmp_path):
        """A 'CONSUMED' approval_events row is rejected by the CHECK constraint --
        proving a 'CONSUMED event' could not be added without a schema migration,
        and would itself be the inconsistency (the chain has no such event type)."""
        db = self._full_db(tmp_path)
        con = sqlite3.connect(str(db))
        try:
            with pytest.raises(sqlite3.IntegrityError):
                con.execute(
                    "INSERT INTO approval_events (approval_id, event_type) "
                    "VALUES ('x', 'CONSUMED')"
                )
        finally:
            con.close()
