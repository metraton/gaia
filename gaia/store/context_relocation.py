"""Copy workspace-keyed context rows, or key slices of them, under the projects a reviewed manifest names."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


class RelocationError(Exception):
    """The manifest does not describe a safe, complete relocation."""


def load_manifest(path: Path | str) -> dict:
    """The manifest at ``path``, parsed."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _source_rows(con: sqlite3.Connection) -> dict:
    return {
        (ws, section): json.loads(payload) if payload else {}
        for ws, section, payload in con.execute(
            "SELECT workspace, contract_name, payload FROM project_context_contracts",
        )
    }


def _claimed_keys(entry: dict, payload: Any) -> list:
    keys = entry.get("keys")
    if keys is None:
        return list(payload) if isinstance(payload, dict) and payload else [None]
    return list(keys)


def _active_projects(con: sqlite3.Connection) -> set:
    return {r[0] for r in con.execute(
        "SELECT COALESCE(project_identity, path) FROM projects WHERE status = 'active'",
    )}


def plan_relocation(con: sqlite3.Connection, manifest: dict) -> dict:
    """``{inserts, already_present, unowned, errors}`` the manifest would produce; writes nothing."""
    rows = _source_rows(con)
    active = _active_projects(con)
    errors: list = []
    claims: dict = {}
    for kind in ("moves", "unowned"):
        for entry in manifest.get(kind, []):
            source = (entry["workspace"], entry["section"])
            label = "/".join(source)
            if source not in rows:
                errors.append(f"{label}: no such row in project_context_contracts")
                continue
            if entry.get("keys") == []:
                errors.append(f"{label}: empty keys list; omit keys to move the whole row")
                continue
            payload = rows[source]
            for key in _claimed_keys(entry, payload):
                if key is not None and not (isinstance(payload, dict) and key in payload):
                    errors.append(f"{label}: key {key!r} is not in the row")
                elif (source, key) in claims:
                    errors.append(f"{label}: key {key!r} is claimed twice")
                claims[(source, key)] = kind

    for source, payload in rows.items():
        for key in (list(payload) if isinstance(payload, dict) and payload else [None]):
            if (source, key) not in claims and (source, None) not in claims:
                errors.append(f"{'/'.join(source)}: key {key!r} is neither moved nor listed unowned")

    inserts, present, targets = [], [], set()
    for entry in manifest.get("moves", []):
        source = (entry["workspace"], entry["section"])
        target = entry["to"]
        if source not in rows:
            continue
        if target not in active:
            errors.append(f"{'/'.join(source)}: target {target} is not an active project")
            continue
        if (target, entry["section"]) in targets:
            errors.append(f"{target}/{entry['section']}: more than one move targets it")
            continue
        targets.add((target, entry["section"]))
        payload = rows[source]
        if entry.get("keys") is not None:
            payload = {key: payload[key] for key in entry["keys"] if key in payload}
        payload_json = json.dumps(payload, ensure_ascii=False)
        item = {"project": target, "section": entry["section"], "payload": payload_json,
                "from": "/".join(source), "keys": entry.get("keys"), "reason": entry.get("reason")}
        existing = con.execute(
            "SELECT payload FROM project_context WHERE project_identity = ? AND contract_name = ?",
            (target, entry["section"]),
        ).fetchone()
        if existing is None:
            inserts.append(item)
        elif json.loads(existing[0]) == payload:
            present.append(item)
        else:
            errors.append(f"{target}/{entry['section']}: already holds a different payload")
    return {"inserts": inserts, "already_present": present,
            "unowned": manifest.get("unowned", []), "errors": errors}


def _backup(con: sqlite3.Connection, db_file: Path) -> Path:
    backup_dir = db_file.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    target = backup_dir / f"{db_file.stem}-pre-context-relocate-{stamp}.db"
    dest = sqlite3.connect(str(target))
    try:
        con.backup(dest)
    finally:
        dest.close()
    return target


def apply_relocation(manifest: dict, *, db_path: Optional[Path] = None,
                     proposed_by: Optional[str] = None) -> dict:
    """Back up the database, then insert every planned row in one transaction."""
    from gaia.paths import db_path as _db_path
    from gaia.store.writer import _connect

    db_file = Path(db_path) if db_path is not None else _db_path()
    con = _connect(db_file)
    try:
        plan = plan_relocation(con, manifest)
        if plan["errors"]:
            raise RelocationError("; ".join(plan["errors"]))
        backup = _backup(con, db_file)
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with con:
            for item in plan["inserts"]:
                con.execute(
                    "INSERT INTO project_context (project_identity, contract_name, payload, updated_at) "
                    "VALUES (?, ?, ?, ?)",
                    (item["project"], item["section"], item["payload"], now),
                )
                con.execute(
                    "INSERT INTO project_context_history (project_identity, contract_name, "
                    "before_payload_json, after_payload_json, changed_at, changed_by_agent, proposed_by) "
                    "VALUES (?, ?, NULL, ?, ?, 'gaia context relocate', ?)",
                    (item["project"], item["section"], item["payload"], now, proposed_by),
                )
    finally:
        con.close()
    return {**plan, "backup": str(backup), "inserted": len(plan["inserts"])}
