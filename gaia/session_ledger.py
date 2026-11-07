"""One session's ledger, built from the database: PreCompact writes it, SessionStart renders it as ``## Resume point``."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

from gaia.paths.resolver import data_dir
from gaia.signals import change_phrase

logger = logging.getLogger(__name__)

RESUME_HEADER = "## Resume point"
LIST_CAPS = {"in_flight": 8, "pending": 6, "written": 5, "changed": 6, "branches": 5}
TEXT_MAX_CHARS = 80
_LEDGER_DIR = "session_ledger"
_LEDGER_MAX_AGE_SECONDS = 30 * 24 * 3600
_ISO = "%Y-%m-%dT%H:%M:%SZ"
_UNUSED_STATE = "approved/unused"
RECENT_WINDOW_SECONDS = 24 * 3600


def _ledger_path(session_id: str) -> Path:
    from gaia.session_snapshot import _resume_path

    return data_dir() / _LEDGER_DIR / f"{_resume_path(session_id).stem}.json"


def _in_flight(con, session_id: str, since: str) -> list[dict[str, Any]]:
    from gaia.session_snapshot import _open_contracts

    return [
        {"title": row["title"], "state": row["agent_state"], "contract_id": row["contract_id"] or row["agent_id"]}
        for row in _open_contracts(con, session_id)
    ]


def _pending(con, session_id: str, since: str) -> list[dict[str, Any]]:
    """Approvals still awaited, newest first: pending ones shown, named by an open turn or recently active; approved ones unused."""
    from gaia.approvals.reading import APPROVED, PENDING, UNUSED, live_sessions, read_on, sign_of_life
    from gaia.session_snapshot import _ago, _open_contracts

    rows = [dict(r) for r in con.execute(
        "SELECT a.id, a.payload_json, MAX(a.created_at, COALESCE(MAX(e.created_at), a.created_at)) AS last_activity, "
        "COALESCE(MAX(e.event_type = 'SHOWN'), 0) AS shown "
        "FROM approvals a LEFT JOIN approval_events e ON e.approval_id = a.id "
        "WHERE a.session_id = ? AND a.status IN ('pending', 'approved') "
        "GROUP BY a.id ORDER BY a.created_at DESC, a.id",
        (session_id,),
    )]
    readings = read_on(con, (r["id"] for r in rows), live=live_sessions())
    awaited = {row["approval_id"] for row in _open_contracts(con, session_id)
               if row["agent_state"] == "APPROVAL_REQUEST" and row["approval_id"]}
    alive_since = _ago(sign_of_life().total_seconds())
    pending = []
    for row in rows:
        reading = readings.get(row["id"], {})
        if reading.get("state") == PENDING:
            if not (row["shown"] or row["id"] in awaited or (row["last_activity"] or "") >= alive_since):
                continue
            state = PENDING
        elif reading.get("state") == APPROVED and reading.get("outcome") == UNUSED:
            state = _UNUSED_STATE
        else:
            continue
        try:
            what = json.loads(row["payload_json"] or "{}").get("what") or ""
        except (ValueError, AttributeError):
            what = ""
        pending.append({"id": row["id"], "what": str(what).strip(), "state": state})
    return pending


def _written(con, session_id: str, since: str) -> list[dict[str, Any]]:
    """Live memory rows this session created (``origin_session_id``) or changed (a signal it wrote), last updated at or after *since* or of unknown age, newest first."""
    rows = con.execute(
        "SELECT m.workspace, m.name FROM memory m "
        "LEFT JOIN (SELECT workspace, ref, MAX(id) AS last_signal FROM signals "
        "  WHERE session_id = ? AND source = 'memory' AND op != 'delete' GROUP BY workspace, ref) s "
        "  ON s.workspace = m.workspace AND s.ref = m.name "
        "WHERE m.deleted_at IS NULL AND (m.origin_session_id = ? OR s.last_signal IS NOT NULL) "
        "AND (m.updated_at IS NULL OR m.updated_at >= ?) "
        "ORDER BY m.updated_at DESC, m.name",
        (session_id, session_id, since),
    ).fetchall()
    return [{"workspace": r[0], "slug": r[1]} for r in rows]


def _changed(con, session_id: str, since: str) -> list[dict[str, Any]]:
    from gaia.signals import changed_since_birth

    return changed_since_birth(session_id)


def _sidecar(worktree: Path) -> Optional[dict[str, Any]]:
    """The identity a Gaia worktree records about itself, read from files alone so no git process runs."""
    from gaia.worktree import _LEGACY_METADATA_FILENAME, _METADATA_FILENAME

    candidates = [worktree / _LEGACY_METADATA_FILENAME]
    try:
        pointer = (worktree / ".git").read_text(encoding="utf-8").strip()
    except OSError:
        pointer = ""
    if pointer.startswith("gitdir:"):
        candidates.insert(0, Path(pointer.removeprefix("gitdir:").strip()) / _METADATA_FILENAME)
    for candidate in candidates:
        try:
            raw = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(raw, dict):
            return raw
    return None


def _branches(con, session_id: str, since: str) -> list[dict[str, Any]]:
    """Worktrees under a declared workspace whose sidecar names a contract of an open chain of this session, or one born at or after *since*."""
    from gaia.session_snapshot import _open_contracts

    contracts = {r[0] for r in con.execute(
        "SELECT contract_id FROM agent_contract_handoffs WHERE session_id = ? AND contract_id IS NOT NULL "
        "AND created_at >= ?",
        (session_id, since),
    )}
    contracts.update(cid for row in _open_contracts(con, session_id) for cid in row["chain_contract_ids"] if cid)
    if not contracts:
        return []
    roots = [Path(r[0]) / ".project-worktrees" for r in con.execute(
        "SELECT root_path FROM workspaces WHERE root_path IS NOT NULL ORDER BY name"
    )]
    found = []
    for root in roots:
        for worktree in sorted(root.glob("*/*")):
            meta = _sidecar(worktree) if worktree.is_dir() else None
            if meta and meta.get("contract_id") in contracts:
                found.append({
                    "branch": meta.get("branch") or worktree.name,
                    "commit": str(meta.get("commit") or "")[:12],
                    "path": str(worktree),
                    "contract_id": meta["contract_id"],
                })
    return found


_SOURCES = (("in_flight", _in_flight), ("pending", _pending), ("written", _written),
            ("changed", _changed), ("branches", _branches))


def _since(previous_built_at: Optional[str]) -> str:
    """Where ``written`` and ``branches`` start: the previous ledger, which already carried what came before, and never more than a day back."""
    floor = time.strftime(_ISO, time.gmtime(time.time() - RECENT_WINDOW_SECONDS))
    return max(previous_built_at or "", floor)


def build_ledger(session_id: str, compactions: int = 0, previous_built_at: Optional[str] = None) -> dict[str, Any]:
    """The ledger of *session_id* now; a source that fails contributes [] and is named in ``unavailable``."""
    ledger: dict[str, Any] = {
        "session_id": session_id,
        "compactions": compactions,
        "built_at": time.strftime(_ISO, time.gmtime()),
        "since": _since(previous_built_at),
        "unavailable": [],
        **{key: [] for key, _ in _SOURCES},
    }
    from gaia.store.reader import _connect

    con = _connect()
    try:
        for key, query in _SOURCES:
            try:
                ledger[key] = query(con, session_id, ledger["since"])
            except Exception:
                logger.warning("session ledger: %s unavailable", key, exc_info=True)
                ledger["unavailable"].append(key)
    finally:
        con.close()
    return ledger


def read_ledger(session_id: str) -> Optional[dict[str, Any]]:
    """The ledger PreCompact last wrote for *session_id*, or None when there is none or it cannot be read."""
    try:
        ledger = json.loads(_ledger_path(session_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return ledger if isinstance(ledger, dict) and ledger.get("session_id") == session_id else None


def write_ledger(session_id: str) -> Path:
    """Build *session_id*'s ledger with its compaction count raised by one, store it, and drop ledgers older than 30 days."""
    previous = read_ledger(session_id) or {}
    ledger = build_ledger(session_id, compactions=int(previous.get("compactions") or 0) + 1,
                          previous_built_at=previous.get("built_at"))
    path = _ledger_path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_suffix(".tmp")
    staging.write_text(json.dumps(ledger, indent=2), encoding="utf-8")
    staging.replace(path)
    cutoff = time.time() - _LEDGER_MAX_AGE_SECONDS
    for stale in path.parent.glob("*.json"):
        try:
            if stale != path and stale.stat().st_mtime < cutoff:
                stale.unlink()
        except OSError:
            continue
    return path


def ledger_for_start(session_id: str, source: str) -> Optional[dict[str, Any]]:
    """The ledger a ``source`` start renders: the stored one after a compaction, else rebuilt now with the stored count."""
    if not session_id:
        return None
    stored = read_ledger(session_id)
    if source == "compact" and stored:
        return stored
    try:
        return build_ledger(session_id, compactions=int((stored or {}).get("compactions") or 0),
                            previous_built_at=(stored or {}).get("since"))
    except Exception:
        logger.warning("session ledger not rebuilt for %s", session_id, exc_info=True)
        return stored


def _clip(text: str) -> str:
    text = " ".join(text.split()).replace('"', "'")
    return text if len(text) <= TEXT_MAX_CHARS else text[: TEXT_MAX_CHARS - 3].rstrip() + "..."


_LINES = {
    "in_flight": lambda c: f'in_flight: "{_clip(c["title"] or c["contract_id"])}" state={c["state"]} ref=contract:{c["contract_id"]}',
    "pending": lambda a: f'pending: {a["id"]} "{_clip(a["what"])}" state={a["state"]}',
    "written": lambda m: f'written: {m["slug"]} ref=memory:{m["workspace"]}/{m["slug"]}',
    "changed": lambda c: f'changed: {c["where"]} {change_phrase({**c, "title": _clip(c["title"])})}',
    "branches": lambda b: f'branches: {b["branch"]} at {b["commit"] or "unknown"}',
}


def render_resume_point(ledger: dict[str, Any], note: Optional[str] = None, header: str = RESUME_HEADER) -> str:
    """*header* then the ledger as values with refs, each list capped with a ``(+N)`` remainder, then the hand note whole."""
    lines = [header, f"session={ledger['session_id']} compactions={ledger.get('compactions') or 0}"]
    for key, line in _LINES.items():
        items = ledger.get(key) or []
        shown = [line(item) for item in items[: LIST_CAPS[key]]]
        if len(items) > len(shown):
            shown[-1] += f" (+{len(items) - len(shown)})"
        lines.extend(shown)
    if ledger.get("unavailable"):
        lines.append("unavailable: " + ",".join(ledger["unavailable"]))
    if note:
        lines.append(f'note: "{note.strip()}"')
    return "\n".join(lines)
