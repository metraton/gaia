#!/usr/bin/env python3
"""Cross-session approval-grant reuse regression suite (Brief 71).

The bug: the block-approve-retry flow legitimately spans sessions. A T3 command
is blocked under the subagent session S_sub (a pending approval + later a
semantic grant are minted there), the user approves under the orchestrator
session S_orch, and the subagent retries under S_sub. Every lookup on that path
used to be session-locked, so the retry never found the grant the approval
created, and insert_requested() minted a fresh P- on every miss.

The unified fix makes the whole authorization path session-agnostic while
keeping the real security boundaries (signature byte-binding, single-use
PENDING->CONSUMED replay guard, expires_at TTL):

  * check_db_semantic_grant()      -- session_id is audit metadata, not a filter.
  * _find_pending_in_db()          -- dedup queries all_sessions=True.
  * insert_requested()             -- fingerprint idempotency: identical payload
                                      reuses the existing pending id.
  * APPROVAL_GRANT_TTL_MINUTES = 5 -- grant-lifetime source (distinct from the
                                      24h pending TTL, which is unchanged).
  * _consumed_grant_exists()       -- session-agnostic CONSUMED replay guard.

These tests assert each invariant directly. They reuse the DB-isolation pattern
from test_activation_db_bridge.py (file-backed DB + patched _open_db /
get_pending / writer._connect) so nothing touches ~/.gaia/gaia.db.
"""

from __future__ import annotations

import hashlib
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Sys-path setup. parents[4] == gaia repo root (matches the sibling tests in
# this directory); the hooks dir must be on sys.path BEFORE the module-top
# `from modules.*` imports resolve at collection time.
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[4]
HOOKS_DIR = _REPO_ROOT / "hooks"
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sha256(value: str | None) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _build_sealed_payload(command: str) -> dict:
    """Minimal sealed_payload mirroring _build_sealed_payload in bash_validator."""
    return {
        "operation": "MUTATIVE command intercepted: apply",
        "exact_content": command,
        "scope": command.split()[0] if command.strip() else "unknown",
        "risk_level": "medium",
        "rollback_hint": None,
        "rationale": "Test approval",
        "commands": [command],
    }


def _semantic_signature(command: str, danger_verb: str) -> dict:
    """Build a SCOPE_SEMANTIC_SIGNATURE dict for the given command."""
    from modules.security.approval_scopes import (
        SCOPE_SEMANTIC_SIGNATURE,
        build_approval_signature,
    )

    sig = build_approval_signature(
        command,
        scope_type=SCOPE_SEMANTIC_SIGNATURE,
        danger_verb=danger_verb,
        danger_category="MUTATIVE",
    )
    assert sig is not None, f"signature build should succeed for: {command}"
    return sig.to_dict()


# ---------------------------------------------------------------------------
# Fixtures (DB isolation -- same approach as test_activation_db_bridge.py)
# ---------------------------------------------------------------------------

@pytest.fixture()
def iso_db(tmp_path, monkeypatch):
    """File-backed isolated DB shared by gaia.approvals.store and gaia.store.writer.

    Both planes are routed at the isolated file:
      * gaia.store.writer._connect is patched -> isolated DB (semantic grants).
      * gaia.approvals.store._open_db delegates to writer._connect in production;
        we patch it here too so the approvals chain reads the same DB.
      * gaia.approvals.store.get_pending is wrapped so calls without an explicit
        connection still hit the isolated file.

    Yields the Path to the isolated DB.
    """
    db_path = tmp_path / "cross_session.db"

    def _make_db() -> sqlite3.Connection:
        con = sqlite3.connect(str(db_path))
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        con.create_function("gaia_sha256", 1, lambda v: _sha256(v), deterministic=True)
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS approvals (
                id           TEXT PRIMARY KEY,
                agent_id     TEXT,
                session_id   TEXT,
                status       TEXT NOT NULL DEFAULT 'pending',
                fingerprint  TEXT,
                payload_json TEXT,
                created_at   TEXT NOT NULL
                    DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                decided_at   TEXT
            );

            CREATE TABLE IF NOT EXISTS approval_events (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                approval_id   TEXT NOT NULL,
                event_type    TEXT NOT NULL,
                agent_id      TEXT,
                session_id    TEXT,
                payload_json  TEXT,
                fingerprint   TEXT,
                prev_hash     TEXT,
                this_hash     TEXT,
                metadata_json TEXT,
                created_at    TEXT NOT NULL
                    DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                FOREIGN KEY (approval_id) REFERENCES approvals(id)
            );

            CREATE TABLE IF NOT EXISTS approval_grants (
                approval_id           TEXT PRIMARY KEY,
                agent_id              TEXT,
                session_id            TEXT,
                command_set_json      TEXT NOT NULL,
                scope                 TEXT NOT NULL DEFAULT 'COMMAND_SET',
                created_at            TEXT NOT NULL
                    DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                expires_at            TEXT,
                status                TEXT NOT NULL DEFAULT 'PENDING',
                consumed_indexes_json TEXT,
                consumed_at           TEXT,
                revoked_at            TEXT
            );
            """
        )
        con.commit()
        return con

    # Materialize the schema once.
    _make_db().close()

    import gaia.store.writer as swriter
    import gaia.approvals.store as astore

    monkeypatch.setattr(swriter, "_connect", lambda db_path_arg=None: _make_db())
    monkeypatch.setattr(astore, "_open_db", lambda: sqlite3.connect(str(db_path)))

    orig_get_pending = astore.get_pending

    def patched_get_pending(session_id=None, all_sessions=False, con=None):
        if con is None:
            con = sqlite3.connect(str(db_path))
        return orig_get_pending(session_id=session_id, all_sessions=all_sessions, con=con)

    monkeypatch.setattr(astore, "get_pending", patched_get_pending)

    yield db_path


@pytest.fixture(autouse=True)
def iso_grants_dir(tmp_path, monkeypatch):
    """Isolate filesystem grants dir and pin a session id (mirror bridge test)."""
    import modules.security.approval_grants as ag

    grants_dir = tmp_path / ".claude" / "cache" / "approvals"
    grants_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        "modules.security.approval_grants.get_plugin_data_dir",
        lambda: tmp_path / ".claude",
    )
    monkeypatch.setenv("CLAUDE_SESSION_ID", "S_default")
    ag._last_cleanup_time = 0.0
    yield grants_dir


def _insert_grant(command, *, session_id, danger_verb="apply", ttl_minutes=None,
                  expires_at=None, status="PENDING", agent_id="agent-x"):
    """Insert a semantic grant directly, optionally overriding expires_at/status.

    Uses insert_semantic_grant() for the happy path; for past-TTL or CONSUMED
    setups it post-patches the row so the test controls the exact column values.
    """
    from gaia.store.writer import insert_semantic_grant
    import gaia.store.writer as swriter

    approval_id = f"P-{_sha256(command + session_id)[:32]}"
    sig = _semantic_signature(command, danger_verb)
    kwargs = dict(
        approval_id=approval_id,
        command=command,
        scope_signature=sig,
        agent_id=agent_id,
        session_id=session_id,
    )
    if ttl_minutes is not None:
        kwargs["ttl_minutes"] = ttl_minutes
    res = insert_semantic_grant(**kwargs)
    assert res.get("status") == "applied", f"grant insert failed: {res}"

    if expires_at is not None or status != "PENDING":
        con = swriter._connect()
        try:
            if expires_at is not None:
                con.execute(
                    "UPDATE approval_grants SET expires_at = ? WHERE approval_id = ?",
                    (expires_at, approval_id),
                )
            if status != "PENDING":
                con.execute(
                    "UPDATE approval_grants SET status = ? WHERE approval_id = ?",
                    (status, approval_id),
                )
            con.commit()
        finally:
            con.close()
    return approval_id


# ---------------------------------------------------------------------------
# 1. The load-bearing regression: block S_sub -> approve S_orch -> retry S_sub
# ---------------------------------------------------------------------------

def test_cross_session_retry_passes(iso_db):
    """check_approval_grant finds a grant inserted under a DIFFERENT session."""
    from modules.security.approval_grants import check_approval_grant

    command = "terraform apply"
    # Grant minted under the subagent session (as activation does).
    _insert_grant(command, session_id="S_sub")

    # Retry arrives under the orchestrator/other session -- must still match.
    grant = check_approval_grant(command, session_id="S_orch")
    assert grant is not None, "cross-session retry must find the grant"
    assert grant.confirmed is True


# ---------------------------------------------------------------------------
# 2. check_db_semantic_grant ignores session_id for matching
# ---------------------------------------------------------------------------

def test_check_db_semantic_grant_ignores_session(iso_db):
    from gaia.store.writer import check_db_semantic_grant

    command = "git push origin main"
    _insert_grant(command, session_id="A", danger_verb="push")

    row = check_db_semantic_grant(command, session_id="B")
    assert row is not None, "grant inserted under session A must match check under B"
    assert row.get("status") == "PENDING"


# ---------------------------------------------------------------------------
# 3. TTL-expired grant rejected regardless of session
# ---------------------------------------------------------------------------

def test_ttl_expired_grant_rejected_cross_session(iso_db):
    from gaia.store.writer import check_db_semantic_grant

    command = "kubectl delete pod mypod"
    past = _iso(datetime.now(timezone.utc) - timedelta(minutes=5))
    _insert_grant(command, session_id="A", danger_verb="delete", expires_at=past)

    assert check_db_semantic_grant(command, session_id="A") is None
    assert check_db_semantic_grant(command, session_id="B") is None


# ---------------------------------------------------------------------------
# 4. Single-use replay rejected across differing sessions
# ---------------------------------------------------------------------------

def test_single_use_replay_rejected(iso_db):
    from gaia.store.writer import (
        check_db_semantic_grant,
        consume_db_semantic_grant,
    )

    command = "terraform apply"
    approval_id = _insert_grant(command, session_id="S_sub")

    # First check (one session) finds it.
    first = check_db_semantic_grant(command, session_id="S_sub")
    assert first is not None
    assert first.get("approval_id") == approval_id

    # Consume succeeds the first time.
    assert consume_db_semantic_grant(approval_id) is True

    # Second check (a DIFFERENT session) no longer finds it -- PENDING flipped.
    second = check_db_semantic_grant(command, session_id="S_orch")
    assert second is None, "consumed grant must not be re-matched in any session"

    # Second consume fails -- single-use.
    assert consume_db_semantic_grant(approval_id) is False


# ---------------------------------------------------------------------------
# 5. insert_requested fingerprint idempotency
# ---------------------------------------------------------------------------

def test_insert_requested_fingerprint_idempotent(iso_db):
    import gaia.approvals.store as astore

    payload = _build_sealed_payload("terraform apply")

    id1 = astore.insert_requested(payload, agent_id="a", session_id="S1")
    # Same payload again -> same id, no new row.
    id2 = astore.insert_requested(payload, agent_id="a", session_id="S1")
    assert id1 == id2, "identical payload must reuse the existing approval id"

    # Different payload -> new id.
    other = _build_sealed_payload("git push origin main")
    id3 = astore.insert_requested(other, agent_id="a", session_id="S1")
    assert id3 != id1, "different payload must mint a new approval id"

    # Cross-session, same payload -> original id (fingerprint is session-agnostic).
    id4 = astore.insert_requested(payload, agent_id="a", session_id="S_other")
    assert id4 == id1, "same payload from another session must reuse the original id"

    # Exactly one REQUESTED event for the reused approval (append-only chain, D15).
    con = sqlite3.connect(str(iso_db))
    try:
        n = con.execute(
            "SELECT COUNT(*) FROM approval_events "
            "WHERE approval_id = ? AND event_type = 'REQUESTED'",
            (id1,),
        ).fetchone()[0]
    finally:
        con.close()
    assert n == 1, f"reused approval must keep a single REQUESTED event, found {n}"


# ---------------------------------------------------------------------------
# 6. _find_pending_in_db finds a pending minted under another session
# ---------------------------------------------------------------------------

def test_find_pending_in_db_cross_session(iso_db):
    import gaia.approvals.store as astore
    from modules.tools.bash_validator import _find_pending_in_db

    command = "terraform apply"
    payload = _build_sealed_payload(command)
    approval_id = astore.insert_requested(payload, agent_id="a", session_id="S1")

    # Look up from a different session -- must find the S1 pending.
    found = _find_pending_in_db("S2", command)
    assert found == approval_id, "_find_pending_in_db must be cross-session"


# ---------------------------------------------------------------------------
# 7. insert_semantic_grant default TTL is 60 minutes
# ---------------------------------------------------------------------------

def test_grant_ttl_default_is_5_minutes(iso_db):
    from gaia.store.writer import insert_semantic_grant, APPROVAL_GRANT_TTL_MINUTES
    import gaia.store.writer as swriter

    assert APPROVAL_GRANT_TTL_MINUTES == 5

    command = "terraform apply"
    approval_id = "P-ttl-default-test"
    before = datetime.now(timezone.utc)
    res = insert_semantic_grant(
        approval_id=approval_id,
        command=command,
        scope_signature=_semantic_signature(command, "apply"),
        agent_id="a",
        session_id="S1",
        # ttl_minutes intentionally omitted -> default APPROVAL_GRANT_TTL_MINUTES.
    )
    after = datetime.now(timezone.utc)
    assert res.get("status") == "applied"

    con = swriter._connect()
    try:
        row = con.execute(
            "SELECT created_at, expires_at FROM approval_grants WHERE approval_id = ?",
            (approval_id,),
        ).fetchone()
    finally:
        con.close()
    assert row is not None
    expires = datetime.strptime(row["expires_at"], "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc
    )
    # expires_at should be ~5 min after the call instant (allow 2-min slack).
    lower = before + timedelta(minutes=5) - timedelta(minutes=2)
    upper = after + timedelta(minutes=5) + timedelta(minutes=2)
    assert lower <= expires <= upper, (
        f"expires_at {expires} not ~5min from now ({before}..{after})"
    )


# ---------------------------------------------------------------------------
# 8. list_pending staleness flag unchanged past the horizon
# ---------------------------------------------------------------------------

def test_staleness_flag_unchanged(iso_db):
    import gaia.approvals.store as astore

    command = "terraform apply"
    approval_id = astore.insert_requested(
        _build_sealed_payload(command), agent_id="a", session_id="S1"
    )

    # Backdate created_at to 61 minutes ago (past the 60-min horizon).
    stale_created = _iso(datetime.now(timezone.utc) - timedelta(minutes=61))
    con = sqlite3.connect(str(iso_db))
    try:
        con.execute(
            "UPDATE approvals SET created_at = ? WHERE id = ?",
            (stale_created, approval_id),
        )
        con.commit()
    finally:
        con.close()

    rows = astore.list_pending(all_sessions=True)
    target = next((r for r in rows if r["id"] == approval_id), None)
    assert target is not None, "pending approval should be listed"
    assert target["stale"] is True, "approval older than the horizon must be stale"
