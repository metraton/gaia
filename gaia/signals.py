"""Signals: what other sessions changed in curated memory or auto-applied project context, read once per session cursor."""

from __future__ import annotations

import json
import os
import re
import secrets
from pathlib import Path
from typing import Any, Optional

VERB = "gaia signals"
PER_PLACE = 3
CHANGE_WORDS = {"insert": "added", "update": "updated", "delete": "retired"}

_KEY_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]")


def _cursor_path(session_id: str) -> Path:
    from gaia.paths import state_dir

    return state_dir() / "signals_cursor" / f"{_KEY_SAFE_RE.sub('-', session_id)[:120]}.json"


def read_cursor(session_id: str) -> Optional[dict]:
    """Return ``{"last_id", "shown_after", "born_after"}`` for the session, or None when it has no cursor.

    A cursor written before ``born_after`` existed reads its ``shown_after`` as the birth.
    """
    try:
        data = json.loads(_cursor_path(session_id).read_text(encoding="utf-8"))
        return {"last_id": int(data["last_id"]), "shown_after": int(data["shown_after"]),
                "born_after": int(data.get("born_after", data["shown_after"]))}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def _write_cursor(session_id: str, last_id: int, shown_after: int, born_after: int) -> None:
    path = _cursor_path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    tmp.write_text(json.dumps({"last_id": last_id, "shown_after": shown_after, "born_after": born_after}),
                   encoding="utf-8")
    os.replace(tmp, path)


def baseline(session_id: str) -> None:
    """Start the session's cursor at the newest signal, unless it already has one: a compaction keeps it."""
    from gaia.store.reader import latest_signal_id

    if not session_id or read_cursor(session_id) is not None:
        return
    newest = latest_signal_id()
    _write_cursor(session_id, newest, newest, newest)


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
    if subject.startswith("context:"):
        return f"project context {subject[len('context:'):]}"
    if subject.startswith("workspace:"):
        return f"workspace memory {subject[len('workspace:'):]}"
    return subject


def _place(row: dict[str, Any], projects: list[dict[str, Any]]) -> str:
    """Where a row lives, in the words ``## Workspaces I manage`` uses: ``ws/project``, ``ws/group`` or ``ws``."""
    from gaia.store.writer import normalize_initiative

    def name_of(project: dict[str, Any]) -> str:
        return os.path.basename(project["path"] or "") or project["name"]

    workspace = row.get("workspace") or ""
    if row.get("source") == "project_context":
        return f"{workspace}/{row['subject'][len('context:'):]}"
    local = [p for p in projects if p["workspace"] == workspace] + projects
    if row.get("project_ref"):
        match = next((p for p in local if p["project_identity"] == row["project_ref"]), None)
        if match:
            return f"{match['workspace']}/{name_of(match)}"
    initiative = row.get("initiative") or (row["subject"][len("project:"):] if row["subject"].startswith("project:") else "")
    if not initiative:
        return workspace
    match = next((p for p in local if normalize_initiative(name_of(p)) == initiative), None)
    if match:
        return f"{match['workspace']}/{name_of(match)}"
    group = next((p for p in local if normalize_initiative(p["group_name"]) == initiative), None)
    if group:
        return f"{group['workspace']}/{group['group_name']}"
    return f"{workspace}/{initiative}"


def _collapse(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per changed row, newest first; a row added within the range reads as added unless it was retired."""
    newest: dict[tuple, dict[str, Any]] = {}
    for row in rows:
        key = (row.get("source"), row.get("workspace"), row.get("ref"))
        if key not in newest:
            newest[key] = dict(row)
        elif row["op"] == "insert" and newest[key]["op"] == "update":
            newest[key]["op"] = "insert"
    return list(newest.values())


def items(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each changed row as ``{where, op, kind, title, ref}``, newest first; ``where`` is "user" or its place."""
    from gaia.store.memory_placement import coordinate_word, strip_kind_word
    from gaia.store.reader import active_projects

    collapsed = _collapse(rows)
    projects = active_projects() if any(r["subject"] != "user" for r in collapsed) else []
    out = []
    for row in collapsed:
        if row.get("source") == "project_context":
            title, ref = f"section {row['ref']}", f"context:{row['workspace']}/{row['ref']}"
        else:
            title = " ".join(strip_kind_word(row.get("description")).split()) or row["ref"]
            ref = f"memory:{coordinate_word(row['workspace'])}/{row['ref']}"
        out.append({
            "where": "user" if row["subject"] == "user" else _place(row, projects),
            "op": CHANGE_WORDS[row["op"]], "kind": row.get("kind"), "title": title, "ref": ref,
        })
    return out


def change_phrase(item: dict[str, Any], op_width: int = 0) -> str:
    """``<op> kind=<kind> "<title>"  ref=<ref>``, the op padded to ``op_width``; no ``kind=`` once the row is gone."""
    kind = f"kind={item['kind']} " if item.get("kind") else ""
    return f"{item['op'].ljust(op_width)} {kind}\"{item['title']}\"  ref={item['ref']}"


def render_line(rows: list[dict[str, Any]]) -> str:
    """The user and project change lines for ``rows``: user rows each itemized, project rows itemized per place up to a cap; "" for none."""
    changed = items(rows)
    user = [i for i in changed if i["where"] == "user"]
    places: dict[str, list[dict[str, Any]]] = {}
    for item in changed:
        if item["where"] != "user":
            places.setdefault(item["where"], []).append(item)
    lines = []
    if user:
        lines.append("~ user memory changed:")
        lines.extend(f"  {change_phrase(i, len('retired') + 1)}" for i in user)
    if places:
        lines.append("~ project memory changed:")
        for place, members in places.items():
            lines.extend(f"  {place}  {change_phrase(i)}" for i in members[:PER_PLACE])
            if len(members) > PER_PLACE:
                lines.append(f"  {place} ×{len(members) - PER_PLACE}")
        lines[-1] += f" — {VERB}"
    return "\n".join(lines)


def take_line(session_id: str) -> str:
    """Return the change lines for what others changed since the cursor, keeping the announced range, and move past it."""
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
    _write_cursor(session_id, newest, shown_after, cursor["born_after"])
    return render_line(rows)


def changed_since_birth(session_id: str) -> list[dict[str, Any]]:
    """What other sessions changed since ``session_id``'s cursor was born, as :func:`items`; [] without a cursor."""
    from gaia.store.reader import signals_from_others

    cursor = read_cursor(session_id) if session_id else None
    if cursor is None:
        return []
    return items(signals_from_others(session_id, cursor["born_after"]))


def opener(row: dict[str, Any]) -> str:
    """The command that opens the changed row a signal points at."""
    if row.get("source") == "memory":
        return f"gaia memory show {row['ref']} --workspace {row['workspace']}"
    if row.get("source") == "project_context":
        return (f"gaia context get-contract --workspace {row['workspace']} "
                f"--project {row['subject'][len('context:'):]} --section {row['ref']}")
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
