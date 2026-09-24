"""The one reading of an approval: its state, its outcome and what was sealed with it.

Every reader (the approvals CLI, the contract persister, the OpenCode plugin
reads) names an approval's state through :func:`read`, so a row reads the same
wherever it is shown. The states are derived from the stored rows; none of them
is a stored value (PD1):

* ``pending`` / ``orphaned`` / ``expired`` -- an undecided request. It is
  orphaned when its requesting session shows no sign of life, and expired once
  it outlives the pending TTL, before or after a sweep records it.
* ``replaced`` -- withdrawn because its requester's phrased request replaced it
  (``core.REPLACED_REASON``); neither a user's rejection nor a failure.
* ``rejected`` / ``revoked`` / ``approved`` -- the recorded decision.

An approved request also carries an outcome read from its events and grant:
``executed``, ``failed``, ``no_result`` (a matched call whose window closed with
no terminal event -- never a failure), ``in_flight``, ``unused``, or, for an
event written before calls closed on their own terminal event (no
``tool_use_id``, e.g. the old Stop sweep), ``legacy_executed`` /
``legacy_failed``, which no count of the current model includes.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Optional

from gaia.approvals.core import REPLACED_REASON, WINDOW_MINUTES, _ensure_hooks_importable

PENDING = "pending"
ORPHANED = "orphaned"
EXPIRED = "expired"
REPLACED = "replaced"
REJECTED = "rejected"
REVOKED = "revoked"
APPROVED = "approved"

EXECUTED = "executed"
FAILED = "failed"
NO_RESULT = "no_result"
IN_FLIGHT = "in_flight"
UNUSED = "unused"
LEGACY_EXECUTED = "legacy_executed"
LEGACY_FAILED = "legacy_failed"

#: Requester values the pre-binding code wrote instead of a real identity.
_UNBOUND_VALUES = frozenset({"", "default", "unattributed"})
_ISO = "%Y-%m-%dT%H:%M:%SZ"


def _parse_time(value: object) -> Optional[datetime]:
    try:
        return datetime.strptime(str(value), _ISO).replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _json(value: object) -> dict:
    try:
        loaded = json.loads(value) if isinstance(value, str) and value else {}
    except (TypeError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def pending_ttl() -> timedelta:
    """How long an unanswered request stays pending before it is expired."""
    _ensure_hooks_importable()
    from modules.security.approval_grants import DEFAULT_PENDING_TTL_MINUTES

    return timedelta(minutes=DEFAULT_PENDING_TTL_MINUTES)


def sign_of_life() -> timedelta:
    """How recent a request's own activity must be to count its requester as alive."""
    _ensure_hooks_importable()
    from modules.session.session_registry import HEARTBEAT_TTL_SECONDS

    return timedelta(seconds=HEARTBEAT_TTL_SECONDS)


def live_sessions() -> Optional[set]:
    """The sessions the registry holds as live, or ``None`` when it cannot be read."""
    try:
        _ensure_hooks_importable()
        from modules.session.session_registry import get_live_sessions

        return set(get_live_sessions(include_headless=True))
    except Exception:
        return None


def _withdrawal_reason(events: list[Mapping[str, Any]]) -> str:
    for event in reversed(events):
        if event.get("event_type") == "REVOKED":
            return str(_json(event.get("metadata_json")).get("reason") or "")
    return ""


def _is_orphaned(
    approval: Mapping[str, Any], events: list[Mapping[str, Any]], live: Optional[set], now: datetime,
) -> bool:
    """No sign of life: the registry does not hold the requester live and the row saw no recent activity.

    The recent-activity half covers the host that does not heartbeat the
    session registry (OpenCode), whose requests would otherwise all read orphaned.
    """
    if live is not None and approval.get("session_id") in live:
        return False
    stamps = [_parse_time(e.get("created_at")) for e in events] + [_parse_time(approval.get("created_at"))]
    latest = max((s for s in stamps if s is not None), default=None)
    return latest is None or now - latest >= sign_of_life()


def decision_state(
    approval: Mapping[str, Any],
    events: list[Mapping[str, Any]],
    *,
    live: Optional[set] = None,
    now: Optional[datetime] = None,
) -> str:
    """Return the derived decision state of one ``approvals`` row given its event chain."""
    now = now or datetime.now(timezone.utc)
    status = approval.get("status")
    if status == "pending":
        created = _parse_time(approval.get("created_at"))
        if created is not None and now - created >= pending_ttl():
            return EXPIRED
        return ORPHANED if _is_orphaned(approval, events, live, now) else PENDING
    if status == "expired":
        return EXPIRED
    if status == "revoked":
        reason = _withdrawal_reason(events)
        if reason == REPLACED_REASON:
            return REPLACED
        if reason.startswith("expired"):
            return EXPIRED
        return REVOKED
    return str(status or "unknown")


def _window_closed(grant: Mapping[str, Any], now: datetime) -> bool:
    if grant.get("status") in ("EXPIRED", "REVOKED"):
        return True
    deadline = _parse_time(grant.get("expires_at"))
    if deadline is None:
        created = _parse_time(grant.get("created_at"))
        deadline = created + timedelta(minutes=WINDOW_MINUTES) if created else None
    return deadline is None or deadline <= now


def _matched(grant: Mapping[str, Any]) -> bool:
    try:
        consumed = json.loads(grant.get("consumed_indexes_json") or "[]")
    except (TypeError, ValueError):
        consumed = []
    return (
        grant.get("status") in ("CONSUMED", "FAILED")
        or grant.get("reservation_index") is not None
        or bool(consumed)
    )


def outcome(
    events: list[Mapping[str, Any]],
    grant: Optional[Mapping[str, Any]],
    *,
    now: Optional[datetime] = None,
) -> Optional[str]:
    """Return what became of an approved request, or ``None`` when nothing can be said."""
    now = now or datetime.now(timezone.utc)
    terminal = [e for e in events if e.get("event_type") in ("EXECUTED", "FAILED")]
    current = [e for e in terminal if _json(e.get("metadata_json")).get("tool_use_id")]
    if any(e["event_type"] == "FAILED" for e in current):
        return FAILED
    if current:
        return EXECUTED
    if terminal:
        return LEGACY_FAILED if any(e["event_type"] == "FAILED" for e in terminal) else LEGACY_EXECUTED
    if grant is None:
        return None
    if _matched(grant):
        return NO_RESULT if _window_closed(grant, now) else IN_FLIGHT
    return None if _window_closed(grant, now) else UNUSED


def _grant_window_minutes(grant: Optional[Mapping[str, Any]]) -> Optional[int]:
    """The window the grant actually runs, from its creation to its expiry, in minutes."""
    if not grant:
        return None
    created, expires = _parse_time(grant.get("created_at")), _parse_time(grant.get("expires_at"))
    if created is None or expires is None or expires <= created:
        return None
    return round((expires - created).total_seconds() / 60)


def _sealed(payload: Mapping[str, Any]) -> dict:
    requester = payload.get("requested_by")
    items = payload.get("items") if isinstance(payload.get("items"), list) else []
    return {
        "requester": dict(requester) if isinstance(requester, dict) else None,
        "window_minutes": payload.get("window_minutes"),
        "cwd": [item.get("cwd") for item in items if isinstance(item, dict) and item.get("cwd")] or None,
    }


def read(
    approval: Mapping[str, Any],
    events: Iterable[Mapping[str, Any]],
    grant: Optional[Mapping[str, Any]] = None,
    *,
    live: Optional[set] = None,
    now: Optional[datetime] = None,
) -> dict:
    """Read one approval: its state, outcome, withdrawal reason and what its request sealed.

    ``bound`` is whether the row names a real requester; a row written before
    requesters were sealed, or under the ``default``/``unattributed``
    placeholders, is unbound. ``window_minutes`` is the grant's own window when
    there is a grant: a runtime older than the seal minted grants longer than
    the window its request sealed, and the grant is what bounds the use.
    """
    now = now or datetime.now(timezone.utc)
    chain = list(events)
    state = decision_state(approval, chain, live=live, now=now)
    sealed = _sealed(_json(approval.get("payload_json")))
    sealed["window_minutes"] = _grant_window_minutes(grant) or sealed["window_minutes"]
    session = approval.get("session_id") or ""
    agent = approval.get("agent_id") or ""
    return {
        "state": state,
        "outcome": outcome(chain, grant, now=now) if state == APPROVED else None,
        "reason": _withdrawal_reason(chain) or None,
        "bound": sealed["requester"] is not None
        or (session not in _UNBOUND_VALUES and agent not in _UNBOUND_VALUES),
        **sealed,
    }


def label(reading: Mapping[str, Any]) -> str:
    """One word-pair for a table cell: the state, and the outcome when there is one."""
    if reading.get("outcome"):
        return f"{reading['state']}/{reading['outcome']}"
    return str(reading["state"])


def read_ids(
    ids: Iterable[str],
    *,
    live: Optional[set] = None,
    now: Optional[datetime] = None,
    db_path=None,
) -> dict[str, dict]:
    """Read many approvals at once, keyed by id: three queries, not three per row.

    An id with no ``approvals`` row (a grant older than that table) is absent.
    """
    from gaia.approvals.store import _open_db

    ids = sorted({i for i in ids if i})
    if not ids:
        return {}
    con = _open_db(db_path) if db_path is not None else _open_db()
    try:
        return _read_ids(con, ids, live=live, now=now or datetime.now(timezone.utc))
    finally:
        con.close()


def _select(con: sqlite3.Connection, sql: str, ids: list[str]) -> list[dict]:
    """Rows of ``sql`` for ``ids``; none when the table predates this database's schema."""
    try:
        return [dict(row) for row in con.execute(sql.format(marks=",".join("?" for _ in ids)), ids)]
    except sqlite3.OperationalError:
        return []


def _read_ids(con: sqlite3.Connection, ids: list[str], *, live: Optional[set], now: datetime) -> dict[str, dict]:
    con.row_factory = sqlite3.Row
    rows = _select(
        con,
        "SELECT id, agent_id, session_id, status, payload_json, created_at, decided_at "
        "FROM approvals WHERE id IN ({marks})",
        ids,
    )
    events: dict[str, list] = {i: [] for i in ids}
    for event in _select(
        con,
        "SELECT approval_id, event_type, session_id, metadata_json, created_at "
        "FROM approval_events WHERE approval_id IN ({marks}) ORDER BY id",
        ids,
    ):
        events[event["approval_id"]].append(event)
    grants = {
        grant["approval_id"]: grant
        for grant in _select(con, "SELECT * FROM approval_grants WHERE approval_id IN ({marks})", ids)
    }
    return {
        row["id"]: read(row, events[row["id"]], grants.get(row["id"]), live=live, now=now)
        for row in rows
    }
