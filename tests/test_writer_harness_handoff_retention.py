"""Tests for automatic DB-side retention of harness_events and
agent_contract_handoffs (gaia.store.writer.prune_harness_events /
prune_handoffs).

Both tables previously had NO retention and grew unbounded. Retention mirrors
the episodes model: a single ``DELETE ... WHERE <ts col> < cutoff`` (the
testable unit), fired automatically behind a 1/N probabilistic gate on the
table's write path.

Two format subtleties these tests pin:
  * harness_events.ts and agent_contract_handoffs.created_at are written by
    _now_iso() (``%Y-%m-%dT%H:%M:%SZ``), NOT datetime.isoformat() (which
    episodes uses). The cutoff must match that format for the lexicographic
    comparison to be correct -- these tests age rows using the SAME format.
  * agent_contract_handoffs has a child table
    (agent_contract_handoff_approvals) with ON DELETE CASCADE; _connect sets
    PRAGMA foreign_keys=ON, so pruning a handoff must cascade-delete its join
    rows while leaving the referenced approval_grants row intact.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from gaia.store.writer import (  # noqa: E402
    HANDOFF_RETENTION_DAYS,
    HARNESS_EVENT_RETENTION_DAYS,
    finalize_agent_contract_handoff,
    prune_handoffs,
    prune_harness_events,
    write_harness_event,
)


@pytest.fixture()
def bootstrapped_db(tmp_path, monkeypatch):
    """Bootstrap a real gaia.db (full schema incl. all tables + triggers)."""
    bootstrap = _REPO_ROOT / "scripts" / "bootstrap_database.sh"
    db_path = tmp_path / "gaia.db"
    env = os.environ.copy()
    env["GAIA_DB"] = str(db_path)
    env["WORKSPACE"] = str(tmp_path)
    res = subprocess.run(
        ["bash", str(bootstrap)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert res.returncode == 0, (
        f"bootstrap failed:\nstdout:\n{res.stdout}\nstderr:\n{res.stderr}"
    )
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    # Keep prunes deterministic: never auto-fire the probabilistic gates unless
    # a test explicitly forces the rate to 1. (Rate very high => ~never.)
    monkeypatch.setenv("GAIA_HARNESS_EVENT_PRUNE_SAMPLE_RATE", "100000")
    monkeypatch.setenv("GAIA_HANDOFF_PRUNE_SAMPLE_RATE", "100000")
    # finalize_agent_contract_handoff is allowed only for CLI/hook context
    # (GAIA_DISPATCH_AGENT unset) or seeded fleet agents; unset it so the test
    # writes as the CLI/human path.
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    return db_path


def _iso_days_ago(days: int) -> str:
    """Timestamp in the _now_iso() format the two tables actually store."""
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


# --- harness_events ---------------------------------------------------------

def _insert_harness_event_raw(db_path, event_type, days_ago):
    """Insert a harness_events row with a CONTROLLED ts (aged)."""
    con = sqlite3.connect(str(db_path))
    try:
        con.execute(
            "INSERT INTO harness_events (workspace, ts, type, severity) "
            "VALUES (?, ?, ?, ?)",
            ("me", _iso_days_ago(days_ago), event_type, "info"),
        )
        con.commit()
    finally:
        con.close()


def _harness_event_types(db_path) -> list:
    con = sqlite3.connect(str(db_path))
    try:
        return sorted(r[0] for r in con.execute("SELECT type FROM harness_events"))
    finally:
        con.close()


class TestPruneHarnessEvents:
    def test_deletes_only_rows_older_than_cutoff(self, bootstrapped_db):
        db = bootstrapped_db
        _insert_harness_event_raw(db, "recent-1", days_ago=1)
        _insert_harness_event_raw(db, "recent-2", days_ago=89)
        _insert_harness_event_raw(db, "old-1", days_ago=91)
        _insert_harness_event_raw(db, "old-2", days_ago=365)

        result = prune_harness_events(cutoff_days=90, db_path=db)

        assert result["status"] == "applied"
        assert result["deleted"] == 2
        assert _harness_event_types(db) == ["recent-1", "recent-2"]

    def test_prune_on_empty_table_is_noop(self, bootstrapped_db):
        result = prune_harness_events(cutoff_days=90, db_path=bootstrapped_db)
        assert result["status"] == "applied"
        assert result["deleted"] == 0

    def test_default_retention_is_90_days(self):
        assert HARNESS_EVENT_RETENTION_DAYS == 90

    def test_auto_prune_fires_when_rate_forces_it(self, bootstrapped_db, monkeypatch):
        """With sample rate == 1, every write_harness_event triggers a sweep,
        so an aged row is removed on the next append -- the automatic path."""
        db = bootstrapped_db
        _insert_harness_event_raw(db, "stale", days_ago=300)
        monkeypatch.setenv("GAIA_HARNESS_EVENT_PRUNE_SAMPLE_RATE", "1")
        write_harness_event(event_type="fresh", workspace="me", db_path=db)
        assert _harness_event_types(db) == ["fresh"]


# --- agent_contract_handoffs ------------------------------------------------

def _insert_handoff_raw(db_path, contract_id, days_ago, *, status="COMPLETE"):
    """Insert an agent_contract_handoffs row with a CONTROLLED created_at.

    Uses a plain connection (foreign_keys default OFF) so a workspaces parent
    row is not required for this fixture insert.
    """
    con = sqlite3.connect(str(db_path))
    try:
        con.execute(
            "INSERT INTO agent_contract_handoffs "
            "  (contract_id, agent_id, workspace, agent_state, "
            "   raw_handoff_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (contract_id, "a1b2c3", "me", status, "{}", _iso_days_ago(days_ago)),
        )
        con.commit()
        return con.execute(
            "SELECT id FROM agent_contract_handoffs WHERE contract_id = ?",
            (contract_id,),
        ).fetchone()[0]
    finally:
        con.close()


def _handoff_contract_ids(db_path) -> set:
    con = sqlite3.connect(str(db_path))
    try:
        return {
            r[0]
            for r in con.execute(
                "SELECT contract_id FROM agent_contract_handoffs"
            )
        }
    finally:
        con.close()


class TestPruneHandoffs:
    def test_deletes_only_rows_older_than_cutoff(self, bootstrapped_db):
        db = bootstrapped_db
        _insert_handoff_raw(db, "recent-1", days_ago=1)
        _insert_handoff_raw(db, "recent-2", days_ago=89)
        _insert_handoff_raw(db, "old-1", days_ago=91)
        _insert_handoff_raw(db, "old-2", days_ago=365)

        result = prune_handoffs(cutoff_days=90, db_path=db)

        assert result["status"] == "applied"
        assert result["deleted"] == 2
        assert _handoff_contract_ids(db) == {"recent-1", "recent-2"}

    def test_prune_cascades_to_approval_join_rows(self, bootstrapped_db):
        """Deleting an expired handoff must cascade-delete its
        agent_contract_handoff_approvals join rows (ON DELETE CASCADE, with
        foreign_keys ON via _connect) while leaving the referenced
        approval_grants row untouched."""
        db = bootstrapped_db
        old_id = _insert_handoff_raw(db, "old-with-approval", days_ago=200)

        con = sqlite3.connect(str(db))
        try:
            con.execute(
                "INSERT INTO approval_grants (approval_id, command_set_json) "
                "VALUES (?, ?)",
                ("P-DEADBEEF", "[]"),
            )
            con.execute(
                "INSERT INTO agent_contract_handoff_approvals "
                "  (handoff_id, approval_id, decision, decided_at) "
                "VALUES (?, ?, ?, ?)",
                (old_id, "P-DEADBEEF", "APPROVED", _iso_days_ago(200)),
            )
            con.commit()
        finally:
            con.close()

        result = prune_handoffs(cutoff_days=90, db_path=db)
        assert result["status"] == "applied"
        assert result["deleted"] == 1

        con = sqlite3.connect(str(db))
        try:
            join_rows = con.execute(
                "SELECT COUNT(*) FROM agent_contract_handoff_approvals "
                "WHERE handoff_id = ?",
                (old_id,),
            ).fetchone()[0]
            grant_rows = con.execute(
                "SELECT COUNT(*) FROM approval_grants WHERE approval_id = ?",
                ("P-DEADBEEF",),
            ).fetchone()[0]
        finally:
            con.close()

        # Join row cascade-deleted; the approval_grants row itself survives.
        assert join_rows == 0
        assert grant_rows == 1

    def test_prune_on_empty_table_is_noop(self, bootstrapped_db):
        result = prune_handoffs(cutoff_days=90, db_path=bootstrapped_db)
        assert result["status"] == "applied"
        assert result["deleted"] == 0

    def test_default_retention_is_90_days(self):
        assert HANDOFF_RETENTION_DAYS == 90

    def test_auto_prune_fires_when_rate_forces_it(self, bootstrapped_db, monkeypatch):
        """With sample rate == 1, a successful finalize triggers a sweep, so an
        aged handoff is removed on the next finalize -- the automatic path."""
        db = bootstrapped_db
        _insert_handoff_raw(db, "stale", days_ago=300)
        monkeypatch.setenv("GAIA_HANDOFF_PRUNE_SAMPLE_RATE", "1")
        outcome = finalize_agent_contract_handoff(
            contract_id="a1b2c3.freshtoken",
            agent_id="a1b2c3",
            workspace="me",
            task_status="COMPLETE",
            raw_handoff_json="{}",
            db_path=db,
        )
        assert outcome["status"] == "applied"
        assert outcome["created"] is True
        assert _handoff_contract_ids(db) == {"a1b2c3.freshtoken"}
