"""The replay corpus: truth-table rows, curated cases, and the shell commands and approvals of a store opened read-only."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Iterable, List, Optional

REPO = Path(__file__).resolve().parents[2]
DEFAULT_TABLE = REPO / "tests" / "hooks" / "modules" / "security" / "data" / "effect_truth_table.json"
DEFAULT_CASES = Path(__file__).with_name("cases.json")
SHELL_TOOLS = ("Bash", "bash", "shell")


def open_read_only(path: Path) -> sqlite3.Connection:
    """A connection that cannot write *path* and refuses to create it when absent."""
    return sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True)


def history_commands(con: sqlite3.Connection, since: Optional[str] = None) -> List[str]:
    """The distinct shell commands recorded as ``tool.called``, optionally from timestamp *since*."""
    sql = ("SELECT DISTINCT json_extract(payload, '$.command') FROM harness_events WHERE type = 'tool.called' "
           f"AND json_extract(payload, '$.tool') IN ({', '.join('?' * len(SHELL_TOOLS))})")
    params: list = list(SHELL_TOOLS)
    if since:
        sql += " AND ts >= ?"
        params.append(since)
    return sorted({row[0] for row in con.execute(sql, params) if isinstance(row[0], str) and row[0].strip()})


def _sealed_commands(payload: dict) -> Iterable[str]:
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        items = [{"command": c} for c in payload.get("commands") or [payload.get("command") or payload.get("exact_content")]]
    return (item.get("command") for item in items if isinstance(item, dict))


def approval_commands(con: sqlite3.Connection, since: Optional[str] = None) -> List[str]:
    """The distinct commands sealed in approval requests; file-write requests carry a path and are skipped."""
    sql = "SELECT payload_json FROM approvals" + (" WHERE created_at >= ?" if since else "")
    commands = set()
    for (raw,) in con.execute(sql, [since] if since else []):
        try:
            payload = json.loads(raw or "{}")
        except ValueError:
            continue
        if isinstance(payload, dict):
            commands.update(c for c in _sealed_commands(payload) if isinstance(c, str) and c.strip())
    return sorted(commands)


def truth_rows(path: Path = DEFAULT_TABLE) -> List[dict]:
    return json.loads(Path(path).read_text(encoding="utf-8"))["rows"]


def curated_cases(path: Path = DEFAULT_CASES) -> List[dict]:
    return json.loads(Path(path).read_text(encoding="utf-8"))["cases"]
