"""Project context sections keyed by project, with the pre-v69 workspace rows as fallback."""

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


def _project(row) -> Optional[dict]:
    workspace, name, path, identity = row
    key = identity or path
    return {"identity": key, "workspace": workspace, "name": name} if key else None


def resolve_project(con: sqlite3.Connection, ref: Optional[str], workspace: Optional[str] = None) -> Optional[dict]:
    """``{identity, workspace, name}`` of the active project an identity, display, path or name names."""
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
    try:
        return json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return None


def _has_table(con: sqlite3.Connection, name: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,),
    ).fetchone() is not None


def _legacy_rows(con: sqlite3.Connection, workspace: Optional[str]) -> dict:
    if not workspace or not _has_table(con, "project_context_contracts"):
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


def _project_rows(con: sqlite3.Connection, where: str, value: str) -> list:
    if not _has_table(con, "project_context"):
        return []
    return con.execute(
        "SELECT pc.contract_name, pc.payload, pc.metadata, pc.updated_at, pc.project_identity "
        "FROM project_context pc LEFT JOIN projects p "
        "ON COALESCE(p.project_identity, p.path) = pc.project_identity "
        f"WHERE {where} ORDER BY pc.updated_at", (value,),
    ).fetchall()


def project_sections(con: sqlite3.Connection, project: dict) -> dict:
    """``{contract_name: {payload, metadata, updated_at, source}}`` of one project, falling back per section."""
    sections = _legacy_rows(con, project.get("workspace"))
    for name, payload, metadata, updated_at, identity in _project_rows(
        con, "pc.project_identity = ?", project["identity"],
    ):
        sections[name] = {"payload": _decode(payload), "metadata": metadata,
                          "updated_at": updated_at, "source": f"project:{identity}"}
    return sections


def workspace_sections(con: sqlite3.Connection, workspace: str) -> dict:
    """``{contract_name: {payload, metadata, updated_at, source}}`` as one workspace reads it."""
    sections = _legacy_rows(con, workspace)
    for name, payload, _metadata, updated_at, _identity in _project_rows(con, "p.workspace = ?", workspace):
        section = sections.setdefault(
            name, {"payload": {}, "metadata": None, "updated_at": None, "source": f"workspace:{workspace}"},
        )
        decoded = _decode(payload)
        if isinstance(decoded, dict):
            base = section["payload"] if isinstance(section["payload"], dict) else {}
            section["payload"] = {**base, **decoded}
        elif decoded is not None:
            section["payload"] = decoded
        section["updated_at"] = max(filter(None, (section["updated_at"], updated_at)), default=None)
    return sections


def workspaces_with_section(con: sqlite3.Connection, contract_name: str) -> list:
    """Sorted names of the workspaces whose view holds ``contract_name``."""
    names = set()
    if _has_table(con, "project_context_contracts"):
        names.update(r[0] for r in con.execute(
            "SELECT workspace FROM project_context_contracts WHERE contract_name = ?", (contract_name,),
        ))
    if _has_table(con, "project_context"):
        names.update(r[0] for r in con.execute(
            "SELECT p.workspace FROM project_context pc JOIN projects p "
            "ON COALESCE(p.project_identity, p.path) = pc.project_identity "
            "WHERE pc.contract_name = ?", (contract_name,),
        ))
    return sorted(names)


def write_section(
    con: sqlite3.Connection,
    identity: str,
    contract_name: str,
    payload_json: str,
    now: str,
    agent: Optional[str],
    proposed_by: Optional[str],
) -> None:
    """Upsert one project section and append its history row, inside the caller's transaction."""
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
