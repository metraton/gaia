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


def _ledger_path(session_id: str) -> Path:
    from gaia.session_snapshot import _resume_path

    return data_dir() / _LEDGER_DIR / f"{_resume_path(session_id).stem}.json"


def _in_flight(con, session_id: str) -> list[dict[str, Any]]:
    from gaia.session_snapshot import _open_contracts

    open_rows, quiet_rows = _open_contracts(con, session_id)
    return [
        {
            "title": (row.get("dispatch_description") or row.get("kind") or "").strip(),
            "state": row["agent_state"],
            "contract_id": row["contract_id"] or row["agent_id"],
        }
        for row in open_rows + quiet_rows
    ]


def _pending(con, session_id: str) -> list[dict[str, Any]]:
    """This session's approvals still waiting for a decision, or approved and not yet used."""
    from gaia.approvals.reading import APPROVED, PENDING, UNUSED, live_sessions, read_on

    rows = [dict(r) for r in con.execute(
        "SELECT id, payload_json FROM approvals WHERE session_id = ? "
        "AND status IN ('pending', 'approved') ORDER BY created_at DESC, id",
        (session_id,),
    )]
    readings = read_on(con, (r["id"] for r in rows), live=live_sessions())
    pending = []
    for row in rows:
        reading = readings.get(row["id"], {})
        if reading.get("state") == PENDING:
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


def _written(con, session_id: str) -> list[dict[str, Any]]:
    """Live memory rows this session created (``origin_session_id``) or changed (a signal it wrote), newest first."""
    rows = con.execute(
        "SELECT m.workspace, m.name FROM memory m "
        "LEFT JOIN (SELECT workspace, ref, MAX(id) AS last_signal FROM signals "
        "  WHERE session_id = ? AND source = 'memory' AND op != 'delete' GROUP BY workspace, ref) s "
        "  ON s.workspace = m.workspace AND s.ref = m.name "
        "WHERE m.deleted_at IS NULL AND (m.origin_session_id = ? OR s.last_signal IS NOT NULL) "
        "ORDER BY m.updated_at DESC, m.name",
        (session_id, session_id),
    ).fetchall()
    return [{"workspace": r[0], "slug": r[1]} for r in rows]


def _changed(con, session_id: str) -> list[dict[str, Any]]:
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


def _branches(con, session_id: str) -> list[dict[str, Any]]:
    """Worktrees under a declared workspace whose sidecar names one of this session's contracts; the database records none."""
    contracts = {r[0] for r in con.execute(
        "SELECT contract_id FROM agent_contract_handoffs WHERE session_id = ? AND contract_id IS NOT NULL",
        (session_id,),
    )}
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


def build_ledger(session_id: str, compactions: int = 0) -> dict[str, Any]:
    """The ledger of *session_id* now; a source that fails contributes [] and is named in ``unavailable``."""
    ledger: dict[str, Any] = {
        "session_id": session_id,
        "compactions": compactions,
        "built_at": time.strftime(_ISO, time.gmtime()),
        "unavailable": [],
        **{key: [] for key, _ in _SOURCES},
    }
    from gaia.store.reader import _connect

    con = _connect()
    try:
        for key, query in _SOURCES:
            try:
                ledger[key] = query(con, session_id)
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
    ledger = build_ledger(session_id, compactions=int(previous.get("compactions") or 0) + 1)
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
        return build_ledger(session_id, compactions=int((stored or {}).get("compactions") or 0))
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
