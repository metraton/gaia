"""
gaia.retention.fs_rules -- state-based retention for what Gaia creates and
never revisits on its own: scratch, tmp, cache, and preserved rejected-turn
text.

Mirrors the criterion ``gaia.contract.drafts.collectable_drafts`` already
established for contract drafts -- an entry is collectible because its OWNER
already closed, past a grace window, never because it merely aged -- so the
same shape of question (what state, from what source) answers every rule
here too:

  * scratch / tmp / cache (``collectable_turn_scoped``) -- an entry directly
    under one of these Gaia-owned directories is collectible only when its
    name embeds a recognizable contract id (the ``mint_draft_id`` shape,
    ``<agent_id>.<token>``) AND ``agent_contract_handoffs`` holds a row for
    that id whose verdict already reached a TERMINAL state (``gaia.state.
    TERMINAL_PLAN_STATUSES``). Source: a strictly read-only query against
    gaia.db. An entry whose name carries no recognizable contract id is left
    untouched -- there is no state to consult for it.
  * rejected turns (``collectable_rejected_turns``) -- a preserved file is
    keyed by ``{session_id}.{agent}`` (see
    ``hooks/modules/agents/rejected_turn_relay.py``). It is collectible when
    ``agent_contract_handoffs`` already holds ANY terminal row for that
    session id -- the session moved on to a turn that will never be
    superseded, so the preserved text is redundant regardless of which turn
    within the session reached that verdict. Source: the same read-only
    query, filtered by ``session_id``.

THE DELIBERATE DIVERGENCE from ``collectable_drafts`` runs on two axes, and
losing either one reopens the hole this module exists to close.

Axis one -- AGE. That policy has a SECOND, DB-independent lane (``aged``)
that collects purely by age once a draft outlives ``max_age_days``, reasoned
there because a draft is a COPY of a row that (if it exists) is durably
recorded elsewhere -- losing the copy loses nothing durable. None of the
entries this module governs have that property: a scratch working file, a
tmp artifact, a cache entry, or a preserved rejected-turn text is the ONLY
copy of whatever it holds. So there is NO age-only fallback lane here, on
purpose. When the DB cannot be read, ``_closed_contract_ids`` /
``_closed_turn_sessions`` return an empty set (the same fail-closed posture
``gaia.contract.drafts.spent_draft_ids`` already uses), and an empty set
here means every candidate is left alone -- absence of evidence can only
ever mean "leave it," never "old enough to drop anyway."

Axis two -- WHAT "CLOSED" MEANS. ``collectable_drafts`` treats a draft as
spent once its OWNING TURN declared any close at all
(``gaia.state.CLOSED_TURN_PLAN_STATUSES``) -- safe there because a draft is
that same copy-of-a-durable-row: even a paused turn (one that asked for
approval, input, or verification and will resume) can re-mint its draft from
the durable row it copies. This module's entries have no durable row to
re-mint from, so ``collectable_turn_scoped`` never widens to
``CLOSED_TURN_PLAN_STATUSES`` outright: a turn that closed
``APPROVAL_REQUEST``, ``NEEDS_INPUT``, ``BLOCKED``, or ``NEEDS_VERIFICATION``
has PAUSED, not ended, and will read its scratch again the moment it
resumes -- which may be well past any grace window (an approval granted the
next morning is a routine case, not an edge one). So a row's own
``TERMINAL_PLAN_STATUSES`` verdict (today, only ``COMPLETE``) remains the
question "can this contract_id still come back," and a paused row alone is
never enough. A resumed turn also mints its continuation under a brand-new
contract_id (``gaia.store.writer.open_contract_continuation``), so once a
row does reach COMPLETE there is no later turn that could still be reading
the old id's scratch under a different name -- collecting on
TERMINAL_PLAN_STATUSES loses nothing a resumption would need.

A paused row is stranded scratch's one legitimate way back in, though, and
it is answered by a SECOND, independent signal rather than by widening the
state check: ``gaia.retention.liveness.session_dead_past_grace``. A producer
turn that closed ``NEEDS_VERIFICATION`` never reaches COMPLETE itself -- the
verifier promotes the TASK's row, not the producer's -- so without this
second signal that scratch is stranded at PAUSED forever. A paused entry
becomes collectible only when its owning session ALSO reads DEAD (never on
UNKNOWN, which the liveness predicate treats identically to ALIVE) and the
entry has additionally sat quiet past the grace window; a turn whose session
is still breathing, or whose liveness cannot be determined, is left alone no
matter how old the entry is. Reading a paused row as collectible on its
state alone -- without cross-checking liveness -- would silently reopen the
exact hole this module was hardened against.

Threshold: ``GAIA_FS_RETENTION_GRACE_HOURS`` (default 24 hours), resolved by
``resolve_grace_hours()`` -- defined in ``gaia.retention.infra`` (the generic
infrastructure module both retention submodules depend on) and re-exported
here, the single place this number lives. ``gaia cleanup`` (the only current
caller) reads it through this function rather than keeping a local constant,
so a preview and a real sweep can never disagree about the window in effect.

Wiring note: as of this module's introduction, ONLY ``gaia cleanup --prune``
calls into it. There is deliberately no SessionStart (or other automatic)
hook wired to these rules yet -- unlike ``hooks/modules/session/
contract_drafts_gc.py``'s automatic sweep of drafts, deletion here stays
behind an explicit, previewable CLI invocation until the rules have been
through adversarial hardening.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Dict, List, Optional, Set

from gaia.contract.validator import AGENT_ID_MIN_HEX
from gaia.retention.infra import (
    DEFAULT_GRACE_HOURS,
    GRACE_HOURS_ENV,
    _ro_db_connect,
    resolve_grace_hours,
)
from gaia.retention.liveness import session_dead_past_grace
from gaia.state import CLOSED_TURN_PLAN_STATUSES, TERMINAL_PLAN_STATUSES

_CLOSED_STATES = frozenset(TERMINAL_PLAN_STATUSES)

# The turn declared a close but the VERDICT can still be replaced -- it will
# resume under the same contract_id. Collectible only through the second,
# liveness-gated path in collectable_turn_scoped(), never through
# _CLOSED_STATES. See the module docstring's axis-two section.
_PAUSED_STATES = frozenset(CLOSED_TURN_PLAN_STATUSES) - _CLOSED_STATES

# A contract id has the shape mint_draft_id() mints: "<agent_id>.<token>",
# where agent_id matches gaia.contract.validator.AGENT_ID_PATTERN_TEXT and
# token is mint_draft_id's secrets.token_hex() output (hex, no fixed width
# asserted here beyond "at least a few characters" -- the exact byte count is
# an implementation detail of the minter, not a property this regex should
# pin down).
_CONTRACT_ID_RE = re.compile(r"^a[0-9a-f]{%d,}\.[0-9a-f]{6,}$" % AGENT_ID_MIN_HEX)

_REJECTED_TURN_SUFFIX = ".txt"


def _entry_contract_id(name: str) -> Optional[str]:
    """The contract id embedded in a filesystem entry's name, or None.

    Checks the whole name first (a directory named exactly by its contract
    id) and, when the name carries one trailing suffix (a file such as
    ``<contract_id>.json``), the name with that suffix stripped. A name that
    matches neither yields None -- it carries no recognizable owner, so no
    rule here may act on it.
    """
    if _CONTRACT_ID_RE.match(name):
        return name
    if "." in name:
        stem = name.rsplit(".", 1)[0]
        if _CONTRACT_ID_RE.match(stem):
            return stem
    return None


def _session_from_rejected_key(stem: str) -> Optional[str]:
    """The session id portion of a rejected-turn preservation key.

    The key is minted as ``f"{session_id}.{agent}"`` (see
    ``rejected_turn_relay.preservation_key``); the session id is the segment
    before the first dot. Returns None for an empty segment rather than
    guessing.
    """
    if not stem:
        return None
    session_id = stem.split(".", 1)[0]
    return session_id or None


def _closed_contract_ids(candidates: Set[str]) -> Set[str]:
    """The subset of *candidates* whose row already reached a TERMINAL verdict.

    Deliberately narrower than "the turn declared a close": a paused turn
    (APPROVAL_REQUEST, NEEDS_INPUT, BLOCKED, NEEDS_VERIFICATION) closed its
    contract too, but will resume under the SAME id, so it is excluded here
    -- see the module docstring's second divergence axis.

    Fail-closed on any uncertainty (no DB, no table, a query error): returns
    an empty set, which every caller reads as "consult nothing further about
    these," never as "old enough regardless."
    """
    if not candidates:
        return set()
    con = _ro_db_connect()
    if con is None:
        return set()
    try:
        id_placeholders = ",".join("?" for _ in candidates)
        state_placeholders = ",".join("?" for _ in _CLOSED_STATES)
        rows = con.execute(
            "select contract_id from agent_contract_handoffs "  # noqa: S608
            f"where contract_id in ({id_placeholders}) "
            f"and agent_state in ({state_placeholders})",
            (*sorted(candidates), *sorted(_CLOSED_STATES)),
        ).fetchall()
    except Exception:
        return set()
    finally:
        try:
            con.close()
        except Exception:
            pass
    return {r[0] for r in rows if r and r[0]}


def _paused_contract_states(candidates: Set[str]) -> Dict[str, str]:
    """``contract_id -> agent_state`` for *candidates* whose row is PAUSED.

    PAUSED means the turn declared a close (``_PAUSED_STATES``:
    ``APPROVAL_REQUEST``, ``BLOCKED``, ``NEEDS_INPUT``,
    ``NEEDS_VERIFICATION``) but not a terminal verdict -- these are exactly
    the candidates ``collectable_turn_scoped`` must cross-check against
    session liveness before it may collect them, never on this state alone.

    Same fail-closed posture as ``_closed_contract_ids``: no DB, no table, or
    a query error returns an empty dict, which the caller reads as "nothing
    to re-check via liveness," never as license to collect.
    """
    if not candidates:
        return {}
    con = _ro_db_connect()
    if con is None:
        return {}
    try:
        id_placeholders = ",".join("?" for _ in candidates)
        state_placeholders = ",".join("?" for _ in _PAUSED_STATES)
        rows = con.execute(
            "select contract_id, agent_state from agent_contract_handoffs "  # noqa: S608
            f"where contract_id in ({id_placeholders}) "
            f"and agent_state in ({state_placeholders})",
            (*sorted(candidates), *sorted(_PAUSED_STATES)),
        ).fetchall()
    except Exception:
        return {}
    finally:
        try:
            con.close()
        except Exception:
            pass
    return {r[0]: r[1] for r in rows if r and r[0]}


def _closed_turn_sessions(session_ids: Set[str]) -> Set[str]:
    """The subset of *session_ids* that already carry a terminal-verdict row.

    Same fail-closed posture as ``_closed_contract_ids``.
    """
    if not session_ids:
        return set()
    con = _ro_db_connect()
    if con is None:
        return set()
    try:
        id_placeholders = ",".join("?" for _ in session_ids)
        state_placeholders = ",".join("?" for _ in _CLOSED_STATES)
        rows = con.execute(
            "select distinct session_id from agent_contract_handoffs "  # noqa: S608
            f"where session_id in ({id_placeholders}) "
            f"and agent_state in ({state_placeholders})",
            (*sorted(session_ids), *sorted(_CLOSED_STATES)),
        ).fetchall()
    except Exception:
        return set()
    finally:
        try:
            con.close()
        except Exception:
            pass
    return {r[0] for r in rows if r and r[0]}


def _iter_entries(root: Path) -> List[Path]:
    if not root.exists():
        return []
    try:
        return list(root.iterdir())
    except OSError:
        return []


def collectable_turn_scoped(
    root: Path,
    *,
    label: str = "Gaia working files",
    grace_hours: Optional[int] = None,
    now: Optional[float] = None,
) -> List[Dict[str, object]]:
    """Entries directly under *root* whose owning contract is collectible.

    Used identically for scratch, tmp, and cache: each is a Gaia-owned
    directory that may hold entries named by the contract id of the turn
    that created them. An entry qualifies through exactly one of two paths:

      * TERMINAL -- its contract's row already reached a TERMINAL verdict
        (not merely a closed one -- a paused turn can still return to the
        SAME id), and it has sat untouched for at least ``grace_hours``; or
      * PAUSED-BUT-DEAD -- its contract's row is PAUSED (``_PAUSED_STATES``)
        AND its owning session already reads DEAD past the same grace
        window, per ``gaia.retention.liveness.session_dead_past_grace``.
        UNKNOWN liveness is never treated as DEAD, so an illegible session
        registry leaves the entry alone exactly like a still-live one.

    See the module docstring for why there is no age-only fallback, and for
    the two-axis divergence from ``collectable_drafts``.
    """
    hours = resolve_grace_hours() if grace_hours is None else grace_hours
    current = time.time() if now is None else now
    cutoff = current - hours * 3600

    owned: Dict[str, Path] = {}
    ids_by_name: Dict[str, str] = {}
    for entry in _iter_entries(root):
        cid = _entry_contract_id(entry.name)
        if cid:
            owned[entry.name] = entry
            ids_by_name[entry.name] = cid

    if not owned:
        return []

    all_ids = set(ids_by_name.values())
    closed = _closed_contract_ids(all_ids)
    paused = _paused_contract_states(all_ids - closed)
    if not closed and not paused:
        return []

    out: List[Dict[str, object]] = []
    for name, entry in owned.items():
        cid = ids_by_name[name]
        try:
            mtime = entry.stat().st_mtime
        except OSError:
            continue

        if cid in closed:
            if mtime >= cutoff:
                continue
            out.append({
                "action": "delete-dir" if entry.is_dir() else "delete-file",
                "path": str(entry),
                "label": label,
                "reason": f"owning contract {cid} reached a terminal verdict, quiet {hours}h",
            })
            continue

        if cid in paused:
            if not session_dead_past_grace(cid, mtime, grace_hours=hours, now=current):
                continue
            out.append({
                "action": "delete-dir" if entry.is_dir() else "delete-file",
                "path": str(entry),
                "label": label,
                "reason": (
                    f"owning contract {cid} paused ({paused[cid]}) but its "
                    f"session is gone, quiet {hours}h"
                ),
            })
    return out


def collectable_rejected_turns(
    root: Path,
    *,
    label: str = "Rejected-turn text",
    grace_hours: Optional[int] = None,
    now: Optional[float] = None,
) -> List[Dict[str, object]]:
    """Preserved rejected-turn files whose harness session already reached a terminal verdict.

    A file surviving here means ``rejected_turn_relay.on_accepted`` never
    cleared it for that exact preservation key. That does not by itself mean
    the session is done -- only that no terminal row named THIS session id
    does; a session that only paused (an approval, an input, a verification
    still pending) leaves the preserved text in place. See the module
    docstring for the fail-closed posture when the DB cannot be read.
    """
    hours = resolve_grace_hours() if grace_hours is None else grace_hours
    current = time.time() if now is None else now
    cutoff = current - hours * 3600

    owned: Dict[str, Path] = {}
    sessions_by_name: Dict[str, str] = {}
    for entry in _iter_entries(root):
        if not entry.is_file() or not entry.name.endswith(_REJECTED_TURN_SUFFIX):
            continue
        stem = entry.name[: -len(_REJECTED_TURN_SUFFIX)]
        session_id = _session_from_rejected_key(stem)
        if session_id:
            owned[entry.name] = entry
            sessions_by_name[entry.name] = session_id

    if not owned:
        return []

    closed_sessions = _closed_turn_sessions(set(sessions_by_name.values()))
    if not closed_sessions:
        return []

    out: List[Dict[str, object]] = []
    for name, entry in owned.items():
        session_id = sessions_by_name[name]
        if session_id not in closed_sessions:
            continue
        try:
            mtime = entry.stat().st_mtime
        except OSError:
            continue
        if mtime >= cutoff:
            continue
        out.append({
            "action": "delete-file",
            "path": str(entry),
            "label": label,
            "reason": f"session {session_id} already reached a terminal verdict, quiet {hours}h",
        })
    return out
