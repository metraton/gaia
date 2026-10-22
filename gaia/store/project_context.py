"""Project context sections keyed by the project they describe (schema v68).

``project_context`` holds one row per (project_identity, contract_name). The
v9-era ``project_context_contracts`` table, keyed by workspace, is read-only
until its rows are moved and it is retired; every reader here falls back to it
section by section, under the workspace that owns the project, so no section
disappears while the move is pending.
"""

from __future__ import annotations

import copy
import json
import sqlite3
from typing import Any, Optional


def merge_section_payload(stored: Any, delta: dict) -> dict:
    """``delta`` deep-merged into ``stored``: dicts merge, every other value replaces."""
    merged = copy.deepcopy(stored) if isinstance(stored, dict) else {}
    for key, value in delta.items():
        current = merged.get(key)
        if isinstance(value, dict) and isinstance(current, dict):
            merged[key] = merge_section_payload(current, value)
        else:
            merged[key] = value
    return merged


def name_variants(name: str) -> list:
    """``name`` followed by its underscore and hyphen spellings, without repeats."""
    return list(dict.fromkeys((name, name.replace("_", "-"), name.replace("-", "_"))))


def _project(row: tuple) -> Optional[dict]:
    workspace, name, path, identity = row
    key = identity or path
    if not key:
        return None
    return {"identity": key, "workspace": workspace, "name": name}


def resolve_project(
    con: sqlite3.Connection, ref: Optional[str], workspace: Optional[str] = None,
) -> Optional[dict]:
    """``{identity, workspace, name}`` of the active project ``ref`` names, or None.

    ``ref`` is a project identity, a dispatch display ``"name (/abs/path)"``, a
    path, or a name in either spelling. A project whose row carries no
    ``project_identity`` is keyed by its path, the last term of the identity
    derivation. A name several workspaces hold resolves only inside ``workspace``.
    """
    if not ref or not str(ref).strip():
        return None
    ref = str(ref).strip()
    name, _, rest = ref.partition(" (")
    if rest.endswith(")"):
        lookups = [("path = ?", rest[:-1])]
    else:
        lookups = [("project_identity = ?", ref), ("path = ?", ref)] + [
            ("name = ? COLLATE NOCASE", variant) for variant in name_variants(name)
        ]
    for where, value in lookups:
        rows = con.execute(
            "SELECT workspace, name, path, project_identity FROM projects "
            f"WHERE {where} AND status = 'active'", (value,),
        ).fetchall()
        if len(rows) > 1:
            rows = [r for r in rows if r[0] == workspace]
        if len(rows) == 1:
            return _project(rows[0])
    return None


def _decode(raw: Optional[str]) -> Any:
    """The stored payload, ``{}`` when empty, None when it is not JSON."""
    try:
        return json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return None


def _legacy_rows(con: sqlite3.Connection, workspace: Optional[str]) -> dict:
    if not workspace:
        return {}
    rows = con.execute(
        "SELECT contract_name, payload, metadata, updated_at "
        "FROM project_context_contracts WHERE workspace = ?", (workspace,),
    ).fetchall()
    return {
        name: {"payload": _decode(payload), "metadata": metadata,
               "updated_at": updated_at, "source": f"workspace:{workspace}"}
        for name, payload, metadata, updated_at in rows
    }


def project_sections(con: sqlite3.Connection, project: dict) -> dict:
    """``{contract_name: {payload, metadata, updated_at, source}}`` of one project.

    A section the project has no row for is read from the retired table under
    the project's workspace, so ``source`` names where each section came from.
    """
    sections = _legacy_rows(con, project.get("workspace"))
    for name, payload, metadata, updated_at in con.execute(
        "SELECT contract_name, payload, metadata, updated_at FROM project_context "
        "WHERE project_identity = ?", (project["identity"],),
    ):
        sections[name] = {"payload": _decode(payload), "metadata": metadata,
                          "updated_at": updated_at, "source": f"project:{project['identity']}"}
    return sections


def workspace_sections(con: sqlite3.Connection, workspace: str) -> dict:
    """``{contract_name: {payload, metadata, updated_at}}`` as one workspace sees it.

    The retired row is the base and each of the workspace's projects' rows is
    merged over it, which is what a workspace-scoped reader saw before v68.
    """
    sections = _legacy_rows(con, workspace)
    for name, payload, updated_at in con.execute(
        "SELECT pc.contract_name, pc.payload, pc.updated_at FROM project_context pc "
        "JOIN projects p ON COALESCE(p.project_identity, p.path) = pc.project_identity "
        "WHERE p.workspace = ? AND p.status = 'active' ORDER BY pc.updated_at",
        (workspace,),
    ):
        section = sections.setdefault(name, {"payload": {}, "metadata": None, "updated_at": None})
        decoded = _decode(payload)
        if isinstance(decoded, dict):
            section["payload"] = merge_section_payload(section["payload"], decoded)
        elif decoded is not None:
            section["payload"] = decoded
        section["updated_at"] = max(filter(None, (section["updated_at"], updated_at)), default=None)
    return sections


def write_section(
    con: sqlite3.Connection,
    identity: str,
    contract_name: str,
    payload_json: str,
    now: str,
    agent: Optional[str],
    proposed_by: Optional[str],
) -> None:
    """Upsert one project section and record the change in ``project_context_history``.

    History is written here rather than by a trigger so the first insert is
    recorded too and the row names who proposed it. Runs inside the caller's
    transaction.
    """
    before = con.execute(
        "SELECT payload FROM project_context WHERE project_identity = ? AND contract_name = ?",
        (identity, contract_name),
    ).fetchone()
    con.execute(
        """
        INSERT INTO project_context (project_identity, contract_name, payload, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(project_identity, contract_name) DO UPDATE SET
            payload = excluded.payload, updated_at = excluded.updated_at
        """,
        (identity, contract_name, payload_json, now),
    )
    con.execute(
        "INSERT INTO project_context_history (project_identity, contract_name, "
        "before_payload_json, after_payload_json, changed_at, changed_by_agent, proposed_by) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (identity, contract_name, before[0] if before else None, payload_json, now,
         agent, proposed_by),
    )
