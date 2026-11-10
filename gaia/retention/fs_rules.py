"""
gaia.retention.fs_rules -- retention for what Gaia creates and never revisits
on its own: scratch, tmp, cache, and preserved rejected-turn text.

An entry directly under scratch, tmp or cache belongs to the turns that own
it: the contract id its name starts with (``<contract_id>`` or
``<contract_id>.<ext>``), or, for a tmp folder ``gaia.paths.dispatch_tmp_dir``
named, every contract row whose host identity hashes to that name. It is
collectible only when no owner is DISPATCHED, IN_PROGRESS or in a state this
module does not recognise, no process holds a file open inside it, and every
owner ended:

  * TERMINAL (``TERMINAL_PLAN_STATUSES``) -- quiet for the grace window
    (``GAIA_FS_RETENTION_GRACE_HOURS``, 24h);
  * any other state whose session ended (``liveness.session_ended``) -- quiet for the grace window;
  * PAUSED (APPROVAL_REQUEST, BLOCKED, NEEDS_INPUT, NEEDS_VERIFICATION) -- the
    turn can resume under the same id, so never on state alone: its session
    reads DEAD past the grace window, or, when the caller passes
    ``stale_days``, its session cannot be read and nothing inside the entry
    changed for that many days. An ALIVE session always keeps it.

The UNKNOWN lane exists because DEAD is almost never readable: SessionEnd
deletes the session's registry entry and SessionStart deletes entries stale
for 24h (``hooks/modules/session/session_registry.py::unregister_session``,
``cleanup_stale_entries``), so a finished session reads as absent, not stale.
A session still present with a stale heartbeat already reads DEAD after 30
minutes, so the stale window is the stricter of the two.

With ``stale_days``, an entry with no owner on record (``backups``,
``validator-replay-*``, a contract with no row) is collectible once nothing
inside it changed for that many days. ``gaia cleanup`` passes it for scratch
and tmp, not cache. With gaia.db unreadable nothing is selected, and where
open files cannot be listed (no ``/proc``) the age lanes select nothing.
Deletion happens only through an explicit, previewable ``gaia cleanup``.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Dict, List, Optional, Set

from gaia.contract.validator import AGENT_ID_MIN_HEX
from gaia.paths import dispatch_tmp_dir
from gaia.retention.infra import (
    DEFAULT_GRACE_HOURS,
    GRACE_HOURS_ENV,
    _ro_db_connect,
    resolve_grace_hours,
    resolve_stale_days,
)
from gaia.retention.liveness import (
    SessionLiveness,
    _session_id_for_contract,
    registered_sessions,
    session_dead_past_grace,
    session_ended_for_contract,
    session_liveness_for_contract,
)
from gaia.state import CLOSED_TURN_PLAN_STATUSES, TERMINAL_PLAN_STATUSES

_CLOSED_STATES = frozenset(TERMINAL_PLAN_STATUSES)
_PAUSED_STATES = frozenset(CLOSED_TURN_PLAN_STATUSES) - _CLOSED_STATES

_CONTRACT_ID_RE = re.compile(r"^a[0-9a-f]{%d,}\.[0-9a-f]{6,}$" % AGENT_ID_MIN_HEX)
_TMP_NAME_RE = re.compile(r"^[0-9a-f]{12}$")

_REJECTED_TURN_SUFFIX = ".txt"


def _entry_contract_id(name: str) -> Optional[str]:
    """The contract id a filesystem entry is named by, whole or with one trailing suffix, or None."""
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


def _contract_states(candidates: Set[str]) -> Optional[Dict[str, str]]:
    """``contract_id -> agent_state`` for the *candidates* that have a row; None when gaia.db cannot be read."""
    con = _ro_db_connect()
    if con is None:
        return None
    ids = sorted(candidates) or [""]
    try:
        rows = con.execute(
            "select contract_id, agent_state from agent_contract_handoffs "  # noqa: S608
            f"where contract_id in ({','.join('?' for _ in ids)})",
            ids,
        ).fetchall()
    except Exception:
        return None
    finally:
        try:
            con.close()
        except Exception:
            pass
    return {r[0]: r[1] for r in rows if r and r[0]}


def _tmp_owners(names: Set[str]) -> Dict[str, Set[str]]:
    """``tmp folder name -> contract ids`` whose host identity ``dispatch_tmp_dir`` turns into that name."""
    if not names:
        return {}
    con = _ro_db_connect()
    if con is None:
        return {}
    try:
        rows = con.execute(
            "select contract_id, harness_agent_id, agent_id, session_id "
            "from agent_contract_handoffs where contract_id is not null"
        ).fetchall()
    except Exception:
        return {}
    finally:
        try:
            con.close()
        except Exception:
            pass
    owners: Dict[str, Set[str]] = {}
    for contract_id, *identities in rows:
        for identity in {i for i in identities if i}:
            name = dispatch_tmp_dir(str(identity)).name
            if name in names:
                owners.setdefault(name, set()).add(contract_id)
    return owners


def _closed_turn_sessions(session_ids: Set[str]) -> Set[str]:
    """The subset of *session_ids* that already carry a terminal-verdict row; empty when gaia.db cannot be read."""
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


def _open_paths() -> Optional[Set[str]]:
    """Every path a process on this machine holds open or works in; None where ``/proc`` cannot be read."""
    proc = Path("/proc")
    if not proc.is_dir():
        return None
    held: Set[str] = set()
    for pid in proc.iterdir():
        if not pid.name.isdigit():
            continue
        links = [pid / "cwd"]
        try:
            links.extend((pid / "fd").iterdir())
        except OSError:
            pass
        for link in links:
            try:
                held.add(os.readlink(link))
            except OSError:
                continue
    return held


def _holds_open(entry: Path, held: Set[str]) -> bool:
    prefix = str(entry.resolve())
    return any(path == prefix or path.startswith(prefix + os.sep) for path in held)


def _newest_mtime(entry: Path) -> float:
    """The latest mtime of *entry* and everything below it, symlinks not followed."""
    newest = entry.lstat().st_mtime
    if entry.is_dir() and not entry.is_symlink():
        for dirpath, dirnames, filenames in os.walk(entry):
            for name in dirnames + filenames:
                try:
                    newest = max(newest, os.lstat(os.path.join(dirpath, name)).st_mtime)
                except OSError:
                    continue
    return newest


def collectable_turn_scoped(
    root: Path,
    *,
    label: str = "Gaia working files",
    grace_hours: Optional[int] = None,
    now: Optional[float] = None,
    stale_days: Optional[int] = None,
) -> List[Dict[str, object]]:
    """Entries directly under *root* whose owners all ended, per the lanes in the module docstring.

    ``stale_days`` None turns the two age lanes off.
    """
    return [
        {"action": v["action"], "path": v["path"], "label": label, "reason": v["reason"]}
        for v in judge_turn_scoped(root, grace_hours=grace_hours, now=now, stale_days=stale_days)
        if v["verdict"] == "remove"
    ]


def judge_turn_scoped(
    root: Path,
    *,
    grace_hours: Optional[int] = None,
    now: Optional[float] = None,
    stale_days: Optional[int] = None,
    spare_live_sessions: bool = False,
) -> List[Dict[str, object]]:
    """Each entry under *root* as ``remove``, ``keep`` or ``refuse`` (its owner cannot be read), with the reason."""
    hours = resolve_grace_hours() if grace_hours is None else grace_hours
    current = time.time() if now is None else now
    cutoff = current - hours * 3600
    stale_cutoff = None if stale_days is None else current - stale_days * 86400

    entries = _iter_entries(root)
    if not entries:
        return []
    named = {entry.name: _entry_contract_id(entry.name) for entry in entries}
    tmp_owners = _tmp_owners(
        {name for name, cid in named.items() if cid is None and _TMP_NAME_RE.match(name)}
    )
    owners = {name: ({cid} if cid else tmp_owners.get(name, set())) for name, cid in named.items()}
    states = _contract_states(set().union(*owners.values()))
    if states is None:
        return []
    held = _open_paths()
    registered = registered_sessions()

    out: List[Dict[str, object]] = []
    for entry in entries:
        known = {cid: states[cid] for cid in owners[entry.name] if cid in states}
        refusal = None
        try:
            mtime = entry.stat().st_mtime
            if known:
                reasons, aged, refusal = _owned_reasons(
                    entry, mtime, known, hours, current, cutoff, stale_cutoff, stale_days,
                    registered, spare_live_sessions,
                )
            elif stale_cutoff is not None and _newest_mtime(entry) < stale_cutoff:
                reasons, aged = [f"no owning contract on record, untouched {stale_days}d"], True
            else:
                reasons, aged, refusal = [], False, "no owning contract on record"
        except OSError:
            continue
        if held and _holds_open(entry, held):
            verdict, reason = "keep", "a running process holds a file or its cwd inside"
        elif reasons and not (held is None and aged):
            verdict, reason = "remove", "; ".join(reasons)
        elif refusal:
            verdict, reason = "refuse", refusal
        else:
            verdict, reason = "keep", "an owner may still be running"
        out.append({
            "verdict": verdict,
            "action": "delete-dir" if entry.is_dir() else "delete-file",
            "path": str(entry),
            "reason": reason,
        })
    return out


def _owned_reasons(entry, mtime, known, hours, current, cutoff, stale_cutoff, stale_days,
                   registered=None, spare_live=False):
    """``(reasons, aged, refusal)`` when every owner in *known* ended, else ``([], False, refusal-or-None)``; aged marks a stale-window lane."""
    reasons: List[str] = []
    aged = False
    for cid, state in sorted(known.items()):
        ended = None if state in _CLOSED_STATES else session_ended_for_contract(
            cid, grace_hours=hours, now=current, registered=registered,
        )
        if state in _CLOSED_STATES:
            live = spare_live and _session_id_for_contract(cid) in (registered or set())
            if mtime >= cutoff or live:
                return [], False, None
            reasons.append(f"owning contract {cid} reached a terminal verdict, quiet {hours}h")
        elif ended:
            if mtime >= cutoff:
                return [], False, None
            reasons.append(f"owning contract {cid} ({state}) ran in a session that ended, quiet {hours}h")
        elif state in _PAUSED_STATES:
            liveness = session_liveness_for_contract(cid, now=current)
            if liveness == SessionLiveness.DEAD:
                if not session_dead_past_grace(cid, mtime, grace_hours=hours, now=current):
                    return [], False, None
                reasons.append(f"owning contract {cid} paused ({state}) but its session is gone, quiet {hours}h")
            elif liveness == SessionLiveness.UNKNOWN and stale_cutoff is not None:
                if _newest_mtime(entry) >= stale_cutoff:
                    return [], False, _unreadable(cid, state, ended)
                aged = True
                reasons.append(
                    f"owning contract {cid} paused ({state}), its session cannot be read, untouched {stale_days}d"
                )
            else:
                return [], False, _unreadable(cid, state, ended)
        else:
            return [], False, _unreadable(cid, state, ended)
    return reasons, aged, None


def _unreadable(cid: str, state: str, ended: Optional[bool]) -> Optional[str]:
    if ended is None:
        return f"owning contract {cid} ({state}): its session cannot be read"
    return None


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
