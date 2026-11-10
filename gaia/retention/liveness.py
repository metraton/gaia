"""
gaia.retention.liveness -- whether a contract's owning session is still
alive, cross-referencing ``agent_contract_handoffs.session_id`` against
``hooks.modules.session.session_registry``'s heartbeat.

Named ``liveness`` rather than ``session_liveness`` deliberately: the module
exports a function of that exact name, and ``from gaia.retention.liveness
import session_liveness`` re-exported from ``gaia/retention/__init__.py``
would otherwise shadow the submodule attribute on the ``gaia.retention``
package with the function -- a classic ``from pkg.mod import name_matching_
mod`` foot-gun where any later ``import gaia.retention.<module-name>``
resolves to the function instead of the module.

A contract's owning turn closing (TERMINAL or not) tells you nothing about
whether the *session* it ran inside is still around right now. A caller that
collects entries of a contract that has NOT necessarily closed (a worktree,
a paused turn's scratch) needs to know whether the turn running it is
still breathing.

Three-valued, not two: ``ALIVE``, ``DEAD``, ``UNKNOWN``. The governing rule,
enforced by construction rather than by convention: an illegible or absent
record reads ``UNKNOWN``, never ``DEAD``. Concretely, ``DEAD`` is returned
from exactly one shape of evidence -- a session id that IS present in a
readable registry, carrying a numeric ``last_heartbeat`` that IS older than
``HEARTBEAT_TTL_SECONDS``. Every other shape (no contract row, no
session_id, unreadable/absent registry file, no entry for this session_id,
an entry with no usable heartbeat) falls through to ``UNKNOWN`` -- including
a LEGACY registry entry that predates the heartbeat field, which
``session_registry.get_live_sessions()`` itself treats as dead-by-default
for its own (non-destructive, false-positive-tolerant) purpose. That is a
deliberate divergence, not an oversight: a consumer of THIS predicate acts
on ``DEAD`` by discarding something, so the missing-heartbeat case must
default the other way here than it does there.

A caller that only ever branches on "is this DEAD" and otherwise leaves the
entry alone gets the required asymmetry for free, because it treats
``UNKNOWN`` exactly like ``ALIVE`` -- do nothing -- without any extra code.

Public surface:
    SessionLiveness            -- ALIVE / DEAD / UNKNOWN
    session_liveness(session_id)            -- the registry-only cross-check
    session_liveness_for_contract(contract_id) -- resolves session_id from
                                                   the contract row first
    session_dead_past_grace(contract_id, mtime) -- the composite criterion
                                                   (DEAD + grace exceeded)
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from enum import Enum
from typing import Optional, Set

from gaia.retention.infra import _ro_db_connect, resolve_grace_hours


class SessionLiveness(str, Enum):
    """The three answers this module is allowed to give.

    ``str`` mixin so a caller that only wants the literal for logging or a
    contract's ``key_outputs`` gets it without an extra ``.value``.
    """

    ALIVE = "ALIVE"
    DEAD = "DEAD"
    UNKNOWN = "UNKNOWN"


def _session_id_for_contract(contract_id: str) -> Optional[str]:
    """The ``session_id`` recorded on *contract_id*'s row, or ``None``.

    ``None`` covers every shape of "cannot tell": no *contract_id* given, no
    DB, no table, no matching row, or a row whose ``session_id`` is NULL or
    empty. Every one of those is read by the caller as UNKNOWN, never as
    grounds to call the session DEAD -- there is no session id to even ask
    the registry about.
    """
    if not contract_id:
        return None
    con = _ro_db_connect()
    if con is None:
        return None
    try:
        row = con.execute(
            "select session_id from agent_contract_handoffs where contract_id = ?",
            (contract_id,),
        ).fetchone()
    except Exception:
        return None
    finally:
        try:
            con.close()
        except Exception:
            pass
    if not row or not row[0]:
        return None
    return str(row[0])


def session_liveness(
    session_id: Optional[str], *, now: Optional[float] = None
) -> SessionLiveness:
    """Cross *session_id* against the session registry's heartbeat.

    Reads ``hooks.modules.session.session_registry`` directly rather than
    re-implementing the heartbeat/TTL model here -- there must be exactly one
    place that decides what a fresh heartbeat looks like.

    Every failure mode -- the hooks package unimportable (partial install),
    a corrupt or absent registry file (``_load_registry`` already degrades
    those to an empty ``{"sessions": {}}`` on its own), no entry for this
    session id, or an entry with no numeric ``last_heartbeat`` (the legacy
    pid-only shape) -- resolves to ``UNKNOWN``. ``DEAD`` is reachable only
    through the one branch below that reads an actual stale heartbeat.
    """
    if not session_id:
        return SessionLiveness.UNKNOWN

    try:
        from hooks.modules.session.session_registry import (
            HEARTBEAT_TTL_SECONDS,
            _load_registry,
        )
    except ImportError:
        return SessionLiveness.UNKNOWN

    try:
        data = _load_registry()
    except Exception:
        return SessionLiveness.UNKNOWN

    sessions = data.get("sessions") if isinstance(data, dict) else None
    if not isinstance(sessions, dict):
        return SessionLiveness.UNKNOWN

    entry = sessions.get(session_id)
    if not isinstance(entry, dict):
        return SessionLiveness.UNKNOWN

    heartbeat = entry.get("last_heartbeat")
    if not isinstance(heartbeat, (int, float)) or heartbeat <= 0:
        return SessionLiveness.UNKNOWN

    current = time.time() if now is None else now
    if (current - heartbeat) < HEARTBEAT_TTL_SECONDS:
        return SessionLiveness.ALIVE
    return SessionLiveness.DEAD


def session_liveness_for_contract(
    contract_id: str, *, now: Optional[float] = None
) -> SessionLiveness:
    """``session_liveness()`` starting from a contract id instead of a session id.

    The entry point for callers that own entries named by contract id
    (``fs_rules.collectable_turn_scoped``'s naming convention, the worktree
    collector), not by session id directly.
    """
    session_id = _session_id_for_contract(contract_id)
    if session_id is None:
        return SessionLiveness.UNKNOWN
    return session_liveness(session_id, now=now)


def session_dead_past_grace(
    contract_id: str,
    mtime: float,
    *,
    grace_hours: Optional[int] = None,
    now: Optional[float] = None,
) -> bool:
    """True exactly when *contract_id*'s session reads DEAD AND *mtime* is
    at least *grace_hours* old.

    The one importable site for "is this collectible on liveness grounds
    alone" -- ``fs_rules.collectable_turn_scoped`` (a PAUSED turn whose
    scratch/tmp/cache entry outlived a dead session) and the
    worktree collector both call this rather than each composing
    ``session_liveness_for_contract`` with its own grace arithmetic, which
    would leave two independent guesses at "dead enough" free to drift apart.

    ALIVE and UNKNOWN both return False, unconditionally: UNKNOWN is never
    promoted to DEAD here, because ``session_liveness()`` itself never
    returns DEAD on uncertainty -- see that function's docstring.
    """
    if session_liveness_for_contract(contract_id, now=now) != SessionLiveness.DEAD:
        return False
    hours = resolve_grace_hours() if grace_hours is None else grace_hours
    current = time.time() if now is None else now
    return (current - mtime) >= hours * 3600


def registered_sessions() -> Optional[Set[str]]:
    """Session ids the registry holds, or None when the file is missing, unreadable or malformed."""
    try:
        from hooks.modules.session.session_registry import _get_registry_path
        data = json.loads(_get_registry_path().read_text(encoding="utf-8"))
    except (ImportError, OSError, ValueError):
        return None
    sessions = data.get("sessions") if isinstance(data, dict) else None
    return set(sessions) if isinstance(sessions, dict) else None


def _last_activity(session_id: str) -> Optional[float]:
    con = _ro_db_connect()
    if con is None:
        return None
    try:
        row = con.execute(
            "select max(created_at) from agent_contract_handoffs where session_id = ?",
            (session_id,),
        ).fetchone()
        return datetime.fromisoformat(str(row[0]).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None
    finally:
        con.close()


def session_ended(
    session_id: Optional[str],
    *,
    grace_hours: Optional[int] = None,
    now: Optional[float] = None,
    registered: Optional[Set[str]] = None,
) -> Optional[bool]:
    """True when *session_id* left a whole registry and the store saw nothing of it for the grace window; False when registered or recent; None when unreadable."""
    if not session_id:
        return None
    sessions = registered_sessions() if registered is None else registered
    if sessions is None:
        return None
    if session_id in sessions:
        return False
    last = _last_activity(session_id)
    if last is None:
        return None
    hours = resolve_grace_hours() if grace_hours is None else grace_hours
    current = time.time() if now is None else now
    return (current - last) >= hours * 3600


def session_ended_for_contract(
    contract_id: str,
    *,
    grace_hours: Optional[int] = None,
    now: Optional[float] = None,
    registered: Optional[Set[str]] = None,
) -> Optional[bool]:
    """``session_ended`` for the session recorded on *contract_id*'s row."""
    return session_ended(
        _session_id_for_contract(contract_id),
        grace_hours=grace_hours, now=now, registered=registered,
    )
