#!/usr/bin/env python3
"""Tests for the pending-approval session-liveness filter (T13).

Task E: liveness filtering moved OUT of the SessionStart block.
``build_pending_approvals_block()`` is now DB-only (``scan_pending_db``,
all_sessions=True) -- the DB is per-machine so cross-session leakage is
impossible and no liveness exclusion is applied there.

The liveness filter (``exclude_live_sessions=True``) now lives in the CLI
discovery path ``_scan_pending_shared`` (used by ``gaia approvals list
--orphans-only`` and ``reject-all``). These tests therefore validate the
liveness axis against that function -- the new home of the filter -- and
keep one DB-only assertion for the SessionStart block to confirm it no
longer applies a liveness filter.

The shape/format of the [ACTIONABLE] block is covered by
``tests/hooks/modules/session/test_session_manifest.py``.
"""

import os
import sys
import time
from pathlib import Path
from typing import Dict, List

import pytest

# Add hooks to path so imports mirror the production layout.
HOOKS_DIR = Path(__file__).parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))
# bin/ for the CLI module that now owns the liveness filter.
BIN_DIR = Path(__file__).parent.parent.parent / "bin"
sys.path.insert(0, str(BIN_DIR))

from modules.session.session_manifest import build_pending_approvals_block
from modules.core.paths import clear_path_cache
import cli.approvals as approvals_mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _sha256(value):
    import hashlib
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def _make_v12_schema(con):
    con.execute("PRAGMA foreign_keys = ON")
    con.create_function("gaia_sha256", 1, lambda v: _sha256(v), deterministic=True)
    con.executescript("""
        CREATE TABLE IF NOT EXISTS approvals (
            id TEXT PRIMARY KEY, agent_id TEXT, session_id TEXT,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending','approved','rejected','revoked','expired')),
            fingerprint TEXT, payload_json TEXT,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
            decided_at TEXT
        );
        CREATE TABLE IF NOT EXISTS approval_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, approval_id TEXT NOT NULL,
            event_type TEXT NOT NULL CHECK (event_type IN (
                'REQUESTED','SHOWN','APPROVED','REJECTED',
                'EXECUTED','FAILED','NOOP','REVOKED','REVERTED')),
            agent_id TEXT, session_id TEXT, payload_json TEXT, fingerprint TEXT,
            prev_hash TEXT, this_hash TEXT, metadata_json TEXT,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
            FOREIGN KEY (approval_id) REFERENCES approvals(id)
        );
        CREATE TRIGGER IF NOT EXISTS bu_approval_events_immutable
        BEFORE UPDATE ON approval_events
        BEGIN SELECT RAISE(ABORT, 'approval_events is append-only'); END;
        CREATE TRIGGER IF NOT EXISTS bd_approval_events_immutable
        BEFORE DELETE ON approval_events
        BEGIN SELECT RAISE(ABORT, 'approval_events is append-only'); END;
    """)


@pytest.fixture(autouse=True)
def setup_env(tmp_path, monkeypatch):
    """Isolate plugin data dir and pin session_id to a known value."""
    clear_path_cache()
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".claude").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".claude" / "cache" / "approvals").mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(
        "modules.core.paths.get_plugin_data_dir",
        lambda: tmp_path / ".claude",
    )
    monkeypatch.setenv("CLAUDE_SESSION_ID", "current-session")
    yield tmp_path


@pytest.fixture()
def db_store(tmp_path, monkeypatch):
    """File-backed DB with gaia.approvals.store patched to it.

    Yields insert_pending(command, session_id, approval_id) -- the helper that
    seeds DB pending rows the liveness filter (and the block) read.
    """
    import sqlite3
    db_path = tmp_path / "liveness.db"
    con = sqlite3.connect(str(db_path))
    _make_v12_schema(con)
    con.commit()
    con.close()

    monkeypatch.setattr(
        "gaia.approvals.store._open_db",
        lambda: sqlite3.connect(str(db_path)),
    )
    import gaia.approvals.store as store

    def insert_pending(command, session_id, approval_id):
        payload = {
            "operation": "GIT command intercepted: push",
            "exact_content": command,
            "scope": command.split()[0] if command.strip() else "x",
            "risk_level": "medium",
            "rollback_hint": None,
            "rationale": "test",
            "commands": [command],
        }
        return store.insert_requested(
            payload, agent_id="t", session_id=session_id, approval_id=approval_id,
        )

    yield store, insert_pending


# ---------------------------------------------------------------------------
# Core contract: fallback call passes exclude_live_sessions=True
# ---------------------------------------------------------------------------

def _approval_id(nonce_short: str) -> str:
    """Build a full P- approval_id from a short nonce (padded to 32 hex)."""
    return "P-" + nonce_short + "0" * (32 - len(nonce_short))


def _ids_from_shared(rows):
    """Collect P-<8> short ids from _scan_pending_shared output."""
    return {"P-" + r["nonce"][:8] for r in rows}


class TestBlockIsDbOnlyNoLivenessFilter:
    """Task E: the SessionStart block is DB-only and applies NO liveness filter.

    The DB is per-machine, so every pending row is the same user; surfacing
    them all in the [ACTIONABLE] block is correct. Liveness filtering moved to
    the CLI discovery path (``_scan_pending_shared``) -- see the classes below.
    """

    def test_block_surfaces_all_db_pendings_regardless_of_session(
        self, monkeypatch, db_store
    ):
        from unittest.mock import patch
        _store, insert_pending = db_store
        insert_pending("cmd-alive", "alive-session", _approval_id("alive001"))
        insert_pending("cmd-dead", "dead-session", _approval_id("dead0001"))

        # Even with a session reported alive, the block does NOT filter it out:
        # the block no longer calls get_live_sessions at all.
        with patch(
            "modules.session.session_registry.get_live_sessions",
            return_value={"alive-session"},
        ):
            result = build_pending_approvals_block()

        assert result.startswith("[ACTIONABLE]")
        assert "P-alive001" in result, (
            "Task E: the DB-only block surfaces every pending row; the "
            "per-machine DB means there is no cross-session leak to filter."
        )
        assert "P-dead0001" in result


# ---------------------------------------------------------------------------
# Liveness filter now lives in the CLI discovery path (_scan_pending_shared)
# ---------------------------------------------------------------------------

class TestExcludeLiveSessionsInCliScan:
    """``_scan_pending_shared(exclude_live_sessions=True)`` filters live sessions.

    This is the new home of the liveness filter (Task E): it backs
    ``gaia approvals list --orphans-only`` and ``reject-all``. The pendings
    are read from the DB; sessions reported alive by session_registry are
    excluded.
    """

    def test_live_session_pending_excluded(self, monkeypatch, db_store):
        from unittest.mock import patch
        _store, insert_pending = db_store
        insert_pending("cmd-alive", "alive-session", _approval_id("alive001"))
        insert_pending("cmd-dead", "dead-session", _approval_id("dead0001"))

        with patch(
            "modules.session.session_registry.get_live_sessions",
            return_value={"alive-session"},
        ):
            rows = approvals_mod._scan_pending_shared(exclude_live_sessions=True)

        ids = _ids_from_shared(rows)
        assert "P-dead0001" in ids, (
            "Orphan pending (dead session) must still be shown."
        )
        assert "P-alive001" not in ids, (
            "Pending owned by a live parallel session must be filtered out "
            "by the --orphans-only path."
        )

    def test_registry_error_falls_back_to_all_pendings(self, monkeypatch, db_store):
        from unittest.mock import patch
        _store, insert_pending = db_store
        insert_pending("cmd-a", "session-a", _approval_id("a" * 8))
        insert_pending("cmd-b", "session-b", _approval_id("b" * 8))

        with patch(
            "modules.session.session_registry.get_live_sessions",
            side_effect=RuntimeError("registry unavailable"),
        ):
            rows = approvals_mod._scan_pending_shared(exclude_live_sessions=True)

        ids = _ids_from_shared(rows)
        # Both survive -- losing real pendings on a registry bug is worse.
        assert "P-" + ("a" * 8) in ids
        assert "P-" + ("b" * 8) in ids


# ---------------------------------------------------------------------------
# AC4 -- liveness filter works end-to-end with heartbeat tracking
# ---------------------------------------------------------------------------

class TestLivenessFilterByHeartbeat:
    """Exercise the liveness filter end-to-end via _scan_pending_shared against
    a real session_registry.

    Heartbeat-only model: a session is live if its ``last_heartbeat`` is
    within ``HEARTBEAT_TTL_SECONDS``. When a sibling Claude Code process
    crashes without firing SessionEnd its heartbeat goes stale, the
    registry entry stops being live, and the pending surfaces in the
    --orphans-only CLI scan.
    """

    def _register_with_heartbeat(
        self, tmp_path, monkeypatch, session_id, heartbeat_age_seconds,
        is_headless=False,
    ):
        import json
        import time
        from modules.session import session_registry

        registry_file = tmp_path / "session_registry_live.json"
        monkeypatch.setattr(
            session_registry, "_get_registry_path", lambda: registry_file,
        )
        session_registry.register_session(session_id, is_headless=is_headless)
        data = json.loads(registry_file.read_text())
        data["sessions"][session_id]["last_heartbeat"] = (
            time.time() - heartbeat_age_seconds
        )
        registry_file.write_text(json.dumps(data))

    def test_stale_heartbeat_session_surfaces_its_pendings(
        self, tmp_path, monkeypatch, db_store
    ):
        _store, insert_pending = db_store
        self._register_with_heartbeat(
            tmp_path, monkeypatch, "crashed-session", heartbeat_age_seconds=3600
        )
        insert_pending("cmd-cr", "crashed-session", _approval_id("cr00001a"))

        rows = approvals_mod._scan_pending_shared(exclude_live_sessions=True)
        assert "P-cr00001a" in _ids_from_shared(rows), (
            "Pending from a session with stale heartbeat must surface in the "
            "--orphans-only scan -- the registry no longer reports it alive."
        )

    def test_fresh_heartbeat_session_keeps_its_pendings_hidden(
        self, tmp_path, monkeypatch, db_store
    ):
        _store, insert_pending = db_store
        self._register_with_heartbeat(
            tmp_path, monkeypatch, "alive-sibling", heartbeat_age_seconds=30
        )
        insert_pending("cmd-liv", "alive-sibling", _approval_id("liv00001"))

        rows = approvals_mod._scan_pending_shared(exclude_live_sessions=True)
        assert "P-liv00001" not in _ids_from_shared(rows), (
            "Sibling session with a fresh heartbeat is alive -- its pending "
            "must be excluded by the --orphans-only scan."
        )

    def test_headless_session_with_fresh_heartbeat_surfaces_its_pendings(
        self, tmp_path, monkeypatch, db_store
    ):
        _store, insert_pending = db_store
        self._register_with_heartbeat(
            tmp_path, monkeypatch, "headless-sibling",
            heartbeat_age_seconds=30, is_headless=True,
        )
        insert_pending("cmd-hl", "headless-sibling", _approval_id("hl000001"))

        rows = approvals_mod._scan_pending_shared(exclude_live_sessions=True)
        assert "P-hl000001" in _ids_from_shared(rows), (
            "Headless session pending must surface -- include_headless=False "
            "excludes it from the live-set so it is treated as an orphan."
        )
