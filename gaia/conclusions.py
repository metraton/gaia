"""Conclusions: each closed specialist's conclusion reaches its orchestrator session exactly once.

A synchronous dispatch carries it on the Agent/Task result line; a background
one closes after that line ran, so the session's next prompt carries it. Both
carriers claim a conclusion through one per-session ledger, and only the first
claim delivers. The ledger outlives SessionEnd, because a resumed session keeps
its id and a background close may still be pending; only age removes it, and a
tombstone then keeps the session's older closes from being delivered again.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

MAX_PER_PROMPT = 3
MAX_CHARS = 600
SCAN_LIMIT = 200
RETENTION_SECONDS = 30 * 24 * 3600
TOMBSTONE_SECONDS = 365 * 24 * 3600

_KEY_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]")
_UNFINALIZED = "DISPATCHED"


def _safe(key: str) -> str:
    return _KEY_SAFE_RE.sub("-", key)[:120]


def _ledger_dir(session_id: str) -> Path:
    from gaia.paths import state_dir

    return state_dir() / "conclusions_delivered" / _safe(session_id)


def _tombstone(session_id: str) -> Path:
    from gaia.paths import state_dir

    return state_dir() / "conclusions_pruned" / _safe(session_id)


def _marker(session_id: str, contract_id: str, conclusion: str) -> Path:
    digest = hashlib.sha256(conclusion.encode("utf-8")).hexdigest()[:16]
    return _ledger_dir(session_id) / f"{_safe(contract_id)}.{digest}"


def claim(session_id: str, contract_id: str, conclusion: str) -> bool:
    """Record this conclusion of ``contract_id`` as delivered; False when a carrier already delivered it.

    Without a session or contract id there is no ledger to consult, so the
    caller delivers.
    """
    if not session_id or not contract_id:
        return True
    _ledger_dir(session_id).mkdir(parents=True, exist_ok=True)
    try:
        os.close(os.open(_marker(session_id, contract_id, conclusion), os.O_CREAT | os.O_EXCL | os.O_WRONLY))
    except FileExistsError:
        return False
    return True


def _session_rows(session_id: str) -> list[dict[str, Any]]:
    from gaia.store.writer import list_agent_contract_handoffs

    return list(reversed(list_agent_contract_handoffs(session_id=session_id, limit=SCAN_LIMIT)))


def _conclusion(row: dict[str, Any]) -> str:
    from gaia.contract.validator import conclusion_of

    if not row.get("contract_id") or row.get("agent_state") == _UNFINALIZED:
        return ""
    try:
        envelope = json.loads(row.get("raw_handoff_json") or "null")
    except (TypeError, ValueError):
        return ""
    return (conclusion_of(envelope) or "") if isinstance(envelope, dict) else ""


def _open_ledger(session_id: str, up_to_id: int | None) -> None:
    """Create the ledger, counting as delivered every conclusion of a row with id <= ``up_to_id`` (None: all)."""
    _ledger_dir(session_id).mkdir(parents=True, exist_ok=True)
    for row in _session_rows(session_id):
        if (text := _conclusion(row)) and (up_to_id is None or int(row["id"]) <= up_to_id):
            claim(session_id, row["contract_id"], text)


def _reopen_pruned(session_id: str) -> bool:
    """Reopen a pruned session's ledger past the rows it held when pruned; False when it was never pruned."""
    tombstone = _tombstone(session_id)
    try:
        recorded = tombstone.read_text(encoding="utf-8").strip()
    except OSError:
        return False
    _open_ledger(session_id, int(recorded) if recorded.isdigit() else None)
    tombstone.unlink(missing_ok=True)
    return True


def baseline(session_id: str) -> None:
    """Prune aged ledgers; open a session's first ledger with every conclusion already closed counted as delivered."""
    prune()
    if not session_id or _ledger_dir(session_id).is_dir() or _reopen_pruned(session_id):
        return
    _open_ledger(session_id, None)


def prune(now: float | None = None) -> None:
    """Replace ledgers untouched for ``RETENTION_SECONDS`` by a tombstone holding the session's newest row id."""
    from gaia.paths import state_dir

    now = now if now is not None else time.time()
    root = state_dir() / "conclusions_delivered"
    for ledger in root.iterdir() if root.is_dir() else ():
        if ledger.is_dir() and ledger.stat().st_mtime < now - RETENTION_SECONDS:
            try:
                newest = str(max((int(row["id"]) for row in _session_rows(ledger.name)), default=0))
            except Exception:
                newest = ""
            _tombstone(ledger.name).parent.mkdir(parents=True, exist_ok=True)
            _tombstone(ledger.name).write_text(newest, encoding="utf-8")
            shutil.rmtree(ledger, ignore_errors=True)
    graves = state_dir() / "conclusions_pruned"
    for tombstone in graves.iterdir() if graves.is_dir() else ():
        if tombstone.stat().st_mtime < now - TOMBSTONE_SECONDS:
            tombstone.unlink(missing_ok=True)


def _reader(row: dict[str, Any]) -> str:
    harness_id = row.get("harness_agent_id")
    coordinate = f"--harness-id {harness_id}" if harness_id else f"--draft-id {row['contract_id']}"
    return f"gaia contract view {coordinate} --field conclusion"


def take_lines(session_id: str, notice_for: Callable[[dict[str, Any]], str] | None = None) -> str:
    """Return the conclusions closed in this session that no carrier delivered yet, claiming each one shown.

    At most ``MAX_PER_PROMPT`` are delivered; the rest are counted on one line
    naming the first one's reader and stay unclaimed for the next prompt.
    """
    if not session_id:
        return ""
    if not _ledger_dir(session_id).is_dir() and not _reopen_pruned(session_id):
        _ledger_dir(session_id).mkdir(parents=True, exist_ok=True)
    pending = [
        (row, text) for row in _session_rows(session_id)
        if (text := _conclusion(row)) and not _marker(session_id, row["contract_id"], text).exists()
    ]
    lines = []
    for row, text in pending[:MAX_PER_PROMPT]:
        if not claim(session_id, row["contract_id"], text):
            continue
        label = row.get("harness_agent_id") or row["contract_id"]
        if len(text) > MAX_CHARS:
            text = f"{text[:MAX_CHARS].rstrip()}... -- {_reader(row)}"
        line = f"{label}: closed {row['agent_state']}, conclusion: {text}"
        notice = notice_for(row) if notice_for else ""
        lines.append(f"{line}\n{notice}" if notice else line)
    overflow = [row for row, _ in pending[MAX_PER_PROMPT:]]
    if overflow:
        lines.append(f"+{len(overflow)} more closed, carried by the next prompts -- {_reader(overflow[0])}")
    return "\n".join(lines)
