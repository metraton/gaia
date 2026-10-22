"""Signals: what other sessions changed in curated memory, read once through a per-session cursor file."""

from __future__ import annotations

import json
import os
import re
import secrets
from pathlib import Path
from typing import Any, Optional

VERB = "gaia signals"
MAX_SUBJECTS = 4

_KEY_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]")


def _cursor_path(session_id: str) -> Path:
    from gaia.paths import state_dir

    return state_dir() / "signals_cursor" / f"{_KEY_SAFE_RE.sub('-', session_id)[:120]}.json"


def read_cursor(session_id: str) -> Optional[dict]:
    """Return ``{"last_id", "shown_after"}`` for the session, or None when it has no cursor."""
    try:
        data = json.loads(_cursor_path(session_id).read_text(encoding="utf-8"))
        return {"last_id": int(data["last_id"]), "shown_after": int(data["shown_after"])}
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _write_cursor(session_id: str, last_id: int, shown_after: int) -> None:
    path = _cursor_path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    tmp.write_text(json.dumps({"last_id": last_id, "shown_after": shown_after}), encoding="utf-8")
    os.replace(tmp, path)


def baseline(session_id: str) -> None:
    """Start the session's cursor at the newest signal, unless it already has one."""
    from gaia.store.reader import latest_signal_id

    if not session_id or read_cursor(session_id) is not None:
        return
    newest = latest_signal_id()
    _write_cursor(session_id, newest, newest)


def forget(session_id: Optional[str]) -> None:
    """Drop the session's cursor when the session ends."""
    if session_id:
        _cursor_path(session_id).unlink(missing_ok=True)


def subject_label(subject: str) -> str:
    """The words a subject is shown with: ``user`` -> "user preference", ``project:k`` -> "project memory k"."""
    if subject == "user":
        return "user preference"
    if subject.startswith("project:"):
        return f"project memory {subject[len('project:'):]}"
    return subject


def render_line(rows: list[dict[str, Any]]) -> str:
    """One line counting ``rows`` by subject, the most recent subject first; "" for no rows."""
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["subject"]] = counts.get(row["subject"], 0) + 1
    if not counts:
        return ""
    parts = [f"{subject_label(subject)} ×{n}" for subject, n in list(counts.items())[:MAX_SUBJECTS]]
    if len(counts) > MAX_SUBJECTS:
        parts.append(f"+{len(counts) - MAX_SUBJECTS} more")
    return f"~ changed: {', '.join(parts)} — {VERB}"


def take_line(session_id: str) -> str:
    """Return the line for what others changed since the cursor, keeping the announced range, and move past it."""
    from gaia.store.reader import latest_signal_id, signals_from_others

    if not session_id:
        return ""
    cursor = read_cursor(session_id)
    if cursor is None:
        baseline(session_id)
        return ""
    newest = latest_signal_id()
    if newest <= cursor["last_id"]:
        return ""
    rows = signals_from_others(session_id, cursor["last_id"], newest)
    shown_after = cursor["last_id"] if rows else cursor["shown_after"]
    _write_cursor(session_id, newest, shown_after)
    return render_line(rows)


def opener(row: dict[str, Any]) -> str:
    """The command that opens the changed row a signal points at."""
    if row.get("source") == "memory":
        return f"gaia memory show {row['ref']} --workspace {row['workspace']}"
    return ""


def recent(session_id: str, since: Optional[int] = None, limit: int = 50) -> list[dict[str, Any]]:
    """Signals from others after ``since`` (default: the last announced range), with ``label`` and ``open``."""
    from gaia.store.reader import signals_from_others

    if since is None:
        cursor = read_cursor(session_id) if session_id else None
        since = cursor["shown_after"] if cursor else 0
    rows = signals_from_others(session_id, since, limit=limit)
    for row in rows:
        row["label"] = subject_label(row["subject"])
        row["open"] = opener(row)
    return rows
