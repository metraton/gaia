"""
gaia.store.provider -- SELECT + serialize the workspace context to JSON.

Reads from the SQLite substrate (created by writer.py / schema.sql) and
returns a dict with the same shape that agents already consume:

    {
        "identity": <str>,
        "stack": <dict>,
        "environment": <dict>,
        "git": <dict>,
        "workspace": {
            "repos": [...],
            "apps": [...],
            "services": [...],
            ...
        }
    }

Substrate is transparent to consumers -- agents see the same JSON they did
when the source was project-context.json. Only the storage backend changed.

Patterns inspired by engram (https://github.com/koaning/engram), MIT License.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    """Open a read-only-style connection (still a regular connection;
    callers must not write here). Materializes schema if missing."""
    from gaia.store.writer import _connect as _writer_connect
    return _writer_connect(db_path)


def _row_to_dict(row: sqlite3.Row, *, drop_project: bool = True) -> dict:
    """Convert a sqlite3.Row to a plain dict. Optionally drop the `project`
    column since it's redundant when filtering by project."""
    d = dict(row)
    if drop_project and "project" in d:
        d.pop("project")
    return d


def get_context(workspace: str, *, db_path: Path | None = None) -> dict[str, Any]:
    """Return the JSON-shaped context for a workspace.

    Args:
        workspace: Workspace identity (matches projects.name).
        db_path: Optional explicit DB path (used by tests).

    Returns:
        Dict with top-level keys ``identity``, ``stack``, ``environment``,
        ``git``, ``workspace``. ``workspace`` contains lists for each entity
        type (repos, apps, services, etc.) filtered by `workspace`.

        If the workspace has no rows in `projects`, returns the dict with
        empty lists and identity == workspace (not None) for safety.
    """
    con = _connect(db_path)
    try:
        # Resolve identity from projects table
        proj_row = con.execute(
            "SELECT name, identity, created_at FROM projects WHERE name = ?",
            (workspace,),
        ).fetchone()

        if proj_row is None:
            identity = workspace
            created_at = None
        else:
            identity = proj_row["identity"] or proj_row["name"]
            created_at = proj_row["created_at"]

        # Per-table ordering column (most use 'name'; gaia_installations uses 'machine')
        _ORDER_COL = {
            "gaia_installations": "machine",
        }

        def _select(table: str) -> list[dict]:
            order_col = _ORDER_COL.get(table, "name")
            cur = con.execute(
                f"SELECT * FROM {table} WHERE project = ? ORDER BY {order_col}",
                (workspace,),
            )
            return [_row_to_dict(r) for r in cur.fetchall()]

        # workspace.* lists
        workspace_data: dict[str, Any] = {
            "repos": _select("repos"),
            "apps": _select("apps"),
            "libraries": _select("libraries"),
            "services": _select("services"),
            "features": _select("features"),
            "tf_modules": _select("tf_modules"),
            "tf_live": _select("tf_live"),
            "releases": _select("releases"),
            "workloads": _select("workloads"),
            "clusters_defined": _select("clusters_defined"),
            "clusters": _select("clusters"),
            "integrations": _select("integrations"),
            "gaia_installations": _select("gaia_installations"),
            "machines": _select("machines"),
        }

        return {
            "identity": identity,
            "stack": {},        # populated by future scanners (B2+)
            "environment": {},  # populated by future scanners (B2+)
            "git": {
                "workspace_name": workspace,
                "created_at": created_at,
            },
            "workspace": workspace_data,
        }
    finally:
        con.close()
