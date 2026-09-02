#!/usr/bin/env python3
"""Age bound on pending-approval reuse -- the stale-pending capture defect.

The defect: approval dedup is blind to BOTH session and age, while presentation
is strictly session-owned and no code path ever re-homes ``approvals.session_id``.
So a pending minted under a session that has since died is undecidable forever,
and -- because both dedup layers match it -- it captures every future request for
the same effect until the 24h sweep. Reproduced on two separate hosts against a
protected-path FILE_WRITE.

Two layers had to be bounded, and bounding either one alone changes nothing:

  * modules.security.approval_grants.find_pending_for_file -- matches a pending
    by (scope=SCOPE_FILE_PATH, payload.exact_content == path) over
    list_pending(all_sessions=True).
  * gaia.approvals.store.insert_requested -- fingerprint idempotency (Brief 71).
    A FILE_WRITE sealed_payload derives purely from the path, so every request
    for a path collides on one fingerprint; a fresh mint would return the stale
    row even with the first layer fixed.

The property under test is not a literal duration: reuse must serve the retry
that happens WHILE the user is deciding, and only that. A request the user can
never decide must not own the path. PENDING_REUSE_WINDOW_MINUTES is that bound;
past it a new request mints its own row and supersedes the stale one in the same
atomic unit, so ``ORDER BY created_at ASC`` cannot re-favour the stale row.

What must NOT regress is why the dedup exists (Brief 71): a cross-session retry
inside the window still folds into the one pending, with exactly one REQUESTED
event (D15 append-only hash chain).

DB isolation mirrors test_cross_session_grant_reuse.py (file-backed DB + patched
_open_db / get_pending / writer._connect) so nothing touches ~/.gaia/gaia.db.
"""

from __future__ import annotations

import hashlib
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[4]
HOOKS_DIR = _REPO_ROOT / "hooks"
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# The real path from the two reproductions: a protected-path Write/Edit target.
TARGET_PATH = "/home/jorge/ws/me/gaia/hooks/adapters/opencode.py"

DEAD_SESSION = "ses_fa68f9e0fffeKKmgCWFzAhBJeO"
LIVE_SESSION = "79d46c41-48c5-47a7-8411-b12b8788f4e1"


def _sha256(value: str | None) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _file_write_payload(file_path: str) -> dict:
    """Build a FILE_WRITE sealed_payload with the REAL producer's shape.

    write_pending_approval_for_file derives every field from file_path alone,
    which is exactly why one path maps to one fingerprint.
    """
    from modules.security.approval_grants import (
        SCOPE_FILE_PATH,
        build_file_path_signature,
    )

    signature = build_file_path_signature(file_path)
    assert signature is not None, "signature producer must accept an absolute path"
    return {
        "operation": "FILE_WRITE command intercepted: write",
        "exact_content": file_path,
        "scope": SCOPE_FILE_PATH,
        "scope_signature": signature.to_dict(),
        "risk_level": "medium",
        "rollback_hint": None,
        "rationale": f"Protected-path write to {file_path!r} requires user approval.",
        "commands": [file_path],
    }


@pytest.fixture()
def iso_db(tmp_path, monkeypatch):
    """File-backed isolated DB shared by gaia.approvals.store and gaia.store.writer."""
    db_path = tmp_path / "stale_pending.db"

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
            """
        )
        con.commit()
        return con

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
    """Isolate the filesystem grants dir and pin a session id."""
    import modules.security.approval_grants as ag

    grants_dir = tmp_path / ".claude" / "cache" / "approvals"
    grants_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        "modules.security.approval_grants.get_plugin_data_dir",
        lambda: tmp_path / ".claude",
    )
    monkeypatch.setenv("CLAUDE_SESSION_ID", LIVE_SESSION)
    ag._last_cleanup_time = 0.0
    yield grants_dir


def _backdate(db_path: Path, approval_id: str, minutes: int) -> None:
    con = sqlite3.connect(str(db_path))
    try:
        con.execute(
            "UPDATE approvals SET created_at = ? WHERE id = ?",
            (_iso(datetime.now(timezone.utc) - timedelta(minutes=minutes)), approval_id),
        )
        con.commit()
    finally:
        con.close()


def _row(db_path: Path, approval_id: str) -> sqlite3.Row:
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        return con.execute(
            "SELECT id, status, session_id FROM approvals WHERE id = ?", (approval_id,)
        ).fetchone()
    finally:
        con.close()


def _events(db_path: Path, approval_id: str) -> list[sqlite3.Row]:
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        return con.execute(
            "SELECT event_type, metadata_json FROM approval_events "
            "WHERE approval_id = ? ORDER BY id ASC",
            (approval_id,),
        ).fetchall()
    finally:
        con.close()


# ---------------------------------------------------------------------------
# The window itself
# ---------------------------------------------------------------------------

def test_reuse_window_is_a_decision_episode_not_the_human_wait():
    """The bound must be the decidability window, never the 24h human wait."""
    from gaia.approvals.store import PENDING_REUSE_WINDOW_MINUTES
    from modules.security.approval_grants import DEFAULT_PENDING_TTL_MINUTES

    assert PENDING_REUSE_WINDOW_MINUTES == 30
    assert PENDING_REUSE_WINDOW_MINUTES < DEFAULT_PENDING_TTL_MINUTES


# ---------------------------------------------------------------------------
# Layer 2 -- gaia.approvals.store.insert_requested
# ---------------------------------------------------------------------------

def test_immediate_retry_inside_the_window_still_reuses(iso_db):
    """Brief 71 must not regress: a retry while the user decides folds into one row."""
    import gaia.approvals.store as astore

    payload = _file_write_payload(TARGET_PATH)

    first = astore.insert_requested(payload, agent_id="a", session_id=LIVE_SESSION)
    same_session = astore.insert_requested(payload, agent_id="a", session_id=LIVE_SESSION)
    other_session = astore.insert_requested(payload, agent_id="a", session_id="S_other")

    assert same_session == first, "an immediate retry must reuse the pending row"
    assert other_session == first, (
        "a cross-session retry inside the window must still reuse -- this is the "
        "duplicate-flood the dedup exists to prevent"
    )

    requested = [e["event_type"] for e in _events(iso_db, first)]
    assert requested == ["REQUESTED"], (
        f"one REQUESTED per approval (D15 hash chain), found {requested}"
    )


def test_stale_pending_does_not_capture_a_new_request(iso_db):
    """THE DEFECT: an undecidable pending from a dead session must not own the path."""
    import gaia.approvals.store as astore
    from gaia.approvals.store import PENDING_REUSE_WINDOW_MINUTES

    payload = _file_write_payload(TARGET_PATH)

    stale = astore.insert_requested(payload, agent_id="a", session_id=DEAD_SESSION)
    _backdate(iso_db, stale, PENDING_REUSE_WINDOW_MINUTES + 1)

    fresh = astore.insert_requested(payload, agent_id="a", session_id=LIVE_SESSION)

    assert fresh != stale, (
        "a pending past the reuse window must not capture a new request for the "
        "same effect"
    )
    assert _row(iso_db, fresh)["session_id"] == LIVE_SESSION, (
        "the new row must be owned by the requesting session, or presentation "
        "cannot reach it"
    )


def test_superseded_pending_is_expired_in_the_same_unit(iso_db):
    """Two pendings for one effect would let ORDER BY created_at ASC re-favour the stale."""
    import gaia.approvals.store as astore
    from gaia.approvals.store import PENDING_REUSE_WINDOW_MINUTES

    payload = _file_write_payload(TARGET_PATH)

    stale = astore.insert_requested(payload, agent_id="a", session_id=DEAD_SESSION)
    _backdate(iso_db, stale, PENDING_REUSE_WINDOW_MINUTES + 1)
    fresh = astore.insert_requested(payload, agent_id="a", session_id=LIVE_SESSION)

    assert _row(iso_db, stale)["status"] == "expired", (
        "the superseded pending must leave 'pending' in the same atomic unit"
    )

    con = sqlite3.connect(str(iso_db))
    try:
        n_pending = con.execute(
            "SELECT COUNT(*) FROM approvals WHERE status = 'pending'"
        ).fetchone()[0]
    finally:
        con.close()
    assert n_pending == 1, f"exactly one pending must survive for one effect, found {n_pending}"

    types = [e["event_type"] for e in _events(iso_db, stale)]
    assert types == ["REQUESTED", "REVOKED"], (
        f"expiry is audited as REVOKED (no EXPIRED event_type exists), found {types}"
    )
    assert "superseded" in (_events(iso_db, stale)[-1]["metadata_json"] or ""), (
        "the expiry event must record WHY it fired"
    )

    # And the third request folds into the fresh row rather than minting again.
    again = astore.insert_requested(payload, agent_id="a", session_id=LIVE_SESSION)
    assert again == fresh


# ---------------------------------------------------------------------------
# Layer 1 -- modules.security.approval_grants.find_pending_for_file
# ---------------------------------------------------------------------------

def test_find_pending_for_file_reuses_inside_the_window(iso_db):
    import gaia.approvals.store as astore
    from modules.security.approval_grants import find_pending_for_file

    approval_id = astore.insert_requested(
        _file_write_payload(TARGET_PATH), agent_id="a", session_id=DEAD_SESSION
    )

    found = find_pending_for_file(LIVE_SESSION, TARGET_PATH)
    assert found == approval_id[2:], (
        "a fresh pending must still be reused across sessions (subagent session "
        "ids differ from the orchestrator's by construction)"
    )


def test_find_pending_for_file_ignores_a_stale_pending(iso_db):
    import gaia.approvals.store as astore
    from gaia.approvals.store import PENDING_REUSE_WINDOW_MINUTES
    from modules.security.approval_grants import find_pending_for_file

    approval_id = astore.insert_requested(
        _file_write_payload(TARGET_PATH), agent_id="a", session_id=DEAD_SESSION
    )
    _backdate(iso_db, approval_id, PENDING_REUSE_WINDOW_MINUTES + 1)

    assert find_pending_for_file(LIVE_SESSION, TARGET_PATH) is None, (
        "a pending past the reuse window must not be handed back for reuse"
    )


# ---------------------------------------------------------------------------
# End to end through the real producer -- the disputed claim
# ---------------------------------------------------------------------------

def test_stale_pending_does_not_deadlock_a_protected_path_write(iso_db):
    """The reported deadlock, driven through the production mint path.

    A 2h-old pending from a dead session sits on the protected adapter path.
    A new protected-path write must obtain a FRESH approval_id owned by the
    live session, with no manual intervention on the forensic row.
    """
    import gaia.approvals.store as astore
    from modules.security.approval_grants import (
        find_pending_for_file,
        generate_nonce,
        write_pending_approval_for_file,
    )

    stale = astore.insert_requested(
        _file_write_payload(TARGET_PATH), agent_id="a", session_id=DEAD_SESSION
    )
    _backdate(iso_db, stale, 120)

    assert find_pending_for_file(LIVE_SESSION, TARGET_PATH) is None

    nonce = generate_nonce()
    sentinel = write_pending_approval_for_file(
        nonce=nonce, file_path=TARGET_PATH, session_id=LIVE_SESSION
    )
    assert sentinel is not None, "the fresh mint must persist"

    persisted_id = sentinel.name
    assert persisted_id == f"P-{nonce}", (
        "with the stale row out of the way the mint keeps the caller's nonce"
    )
    assert persisted_id != stale
    assert _row(iso_db, persisted_id)["session_id"] == LIVE_SESSION
    assert _row(iso_db, stale)["status"] == "expired"


def test_write_pending_returns_the_id_the_db_actually_used(iso_db):
    """Piece 3's precondition: the persisted id is the one the caller must report.

    Inside the window the mint deduplicates, so the id the DB used is NOT the
    caller's nonce. A banner built from the local nonce names a row that does
    not exist.
    """
    from modules.security.approval_grants import (
        generate_nonce,
        write_pending_approval_for_file,
    )

    first = write_pending_approval_for_file(
        nonce=generate_nonce(), file_path=TARGET_PATH, session_id=LIVE_SESSION
    )
    assert first is not None

    second_nonce = generate_nonce()
    second = write_pending_approval_for_file(
        nonce=second_nonce, file_path=TARGET_PATH, session_id=LIVE_SESSION
    )
    assert second is not None

    assert second.name == first.name, "the in-window retry deduplicates"
    assert second.name != f"P-{second_nonce}", (
        "the DB kept the original id; a caller reporting its own nonce reports a "
        "ghost approval_id"
    )
