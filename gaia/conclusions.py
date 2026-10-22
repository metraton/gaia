"""Conclusions: each closed specialist's conclusion reaches its orchestrator session exactly once.

A synchronous dispatch carries it on the Agent/Task result line; a background
one closes after that line ran, so the session's next prompt carries it. Both
carriers claim a contract through one per-session ledger, and only the first
claim delivers. The ledger outlives SessionEnd, because a resumed session keeps
its id and a background close may still be pending; only age removes it.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any

MAX_PER_PROMPT = 3
MAX_CHARS = 600
SCAN_LIMIT = 200
RETENTION_SECONDS = 30 * 24 * 3600

_KEY_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]")
_UNFINALIZED = "DISPATCHED"


def _ledger_dir(session_id: str) -> Path:
    from gaia.paths import state_dir

    return state_dir() / "conclusions_delivered" / _KEY_SAFE_RE.sub("-", session_id)[:120]


def _marker(session_id: str, contract_id: str) -> Path:
    return _ledger_dir(session_id) / _KEY_SAFE_RE.sub("-", contract_id)


def claim(session_id: str, contract_id: str) -> bool:
    """Record ``contract_id`` as delivered to the session; False when a carrier already delivered it.

    Without a session or contract id there is no ledger to consult, so the
    caller delivers.
    """
    if not session_id or not contract_id:
        return True
    _ledger_dir(session_id).mkdir(parents=True, exist_ok=True)
    try:
        os.close(os.open(_marker(session_id, contract_id), os.O_CREAT | os.O_EXCL | os.O_WRONLY))
    except FileExistsError:
        return False
    return True


def _closed_rows(session_id: str) -> list[dict[str, Any]]:
    from gaia.store.writer import list_agent_contract_handoffs

    rows = list_agent_contract_handoffs(session_id=session_id, limit=SCAN_LIMIT)
    return [row for row in reversed(rows) if row.get("contract_id") and row.get("agent_state") != _UNFINALIZED]


def _conclusion(row: dict[str, Any]) -> str:
    from gaia.contract.validator import conclusion_of

    try:
        envelope = json.loads(row.get("raw_handoff_json") or "null")
    except (TypeError, ValueError):
        return ""
    return (conclusion_of(envelope) or "") if isinstance(envelope, dict) else ""


def baseline(session_id: str) -> None:
    """Prune aged ledgers; open a session's first ledger with every conclusion already closed counted as delivered."""
    prune()
    if not session_id or _ledger_dir(session_id).is_dir():
        return
    _ledger_dir(session_id).mkdir(parents=True, exist_ok=True)
    for row in _closed_rows(session_id):
        claim(session_id, row["contract_id"])


def prune(now: float | None = None) -> None:
    """Remove the ledgers no claim has touched for ``RETENTION_SECONDS``."""
    from gaia.paths import state_dir

    cutoff = (now if now is not None else time.time()) - RETENTION_SECONDS
    root = state_dir() / "conclusions_delivered"
    for ledger in root.iterdir() if root.is_dir() else ():
        if ledger.is_dir() and ledger.stat().st_mtime < cutoff:
            shutil.rmtree(ledger, ignore_errors=True)


def _reader(row: dict[str, Any]) -> str:
    harness_id = row.get("harness_agent_id")
    coordinate = f"--harness-id {harness_id}" if harness_id else f"--draft-id {row['contract_id']}"
    return f"gaia contract view {coordinate} --field conclusion"


def take_lines(session_id: str) -> str:
    """Return the conclusions closed in this session that no carrier delivered yet, claiming each one shown.

    At most ``MAX_PER_PROMPT`` are delivered; the rest are counted on one line
    naming the first one's reader and stay unclaimed for the next prompt.
    """
    if not session_id:
        return ""
    _ledger_dir(session_id).mkdir(parents=True, exist_ok=True)
    pending = [
        (row, text) for row in _closed_rows(session_id)
        if (text := _conclusion(row)) and not _marker(session_id, row["contract_id"]).exists()
    ]
    lines = []
    for row, text in pending[:MAX_PER_PROMPT]:
        if not claim(session_id, row["contract_id"]):
            continue
        label = row.get("harness_agent_id") or row["contract_id"]
        if len(text) > MAX_CHARS:
            text = f"{text[:MAX_CHARS].rstrip()}... -- {_reader(row)}"
        lines.append(f"{label}: closed {row['agent_state']}, conclusion: {text}")
    overflow = [row for row, _ in pending[MAX_PER_PROMPT:]]
    if overflow:
        lines.append(f"+{len(overflow)} more closed, carried by the next prompts -- {_reader(overflow[0])}")
    return "\n".join(lines)
