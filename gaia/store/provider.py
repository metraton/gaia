"""
gaia.store.provider -- SELECT + serialize the workspace context to JSON.

Reads from the SQLite substrate (created by writer.py / schema.sql) and
returns a dict shape that agents consume.

The returned shape exposes ``workspace.projects`` for the list of
git-bearing projects within the workspace.

Patterns inspired by engram (https://github.com/koaning/engram), MIT License.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    """Open a read-only-style connection. Materializes schema if missing."""
    from gaia.store.writer import _connect as _writer_connect
    return _writer_connect(db_path)


def _row_to_dict(row: sqlite3.Row, *, drop_workspace: bool = True) -> dict:
    """Convert a sqlite3.Row to a plain dict. Optionally drop the `workspace`
    column since it's redundant when filtering by workspace."""
    d = dict(row)
    if drop_workspace and "workspace" in d:
        d.pop("workspace")
    return d


def get_context(workspace: str, *, db_path: Path | None = None) -> dict[str, Any]:
    """Return the JSON-shaped context for a workspace.

    Args:
        workspace: Workspace name (matches workspaces.name).
        db_path: Optional explicit DB path (used by tests).

    Returns:
        Dict with top-level keys ``identity``, ``stack``, ``environment``,
        ``git``, ``workspace``. ``workspace`` contains lists for each entity
        type filtered by the workspace; ``workspace.projects`` holds the
        git-bearing projects under the workspace.

        Returns None when the workspace has no row in `workspaces`.
    """
    con = _connect(db_path)
    try:
        # Resolve identity from workspaces table
        ws_row = con.execute(
            "SELECT name, identity, created_at FROM workspaces WHERE name = ?",
            (workspace,),
        ).fetchone()

        if ws_row is None:
            return None  # workspace not found -- caller emits exit 1

        identity = ws_row["name"]
        created_at = ws_row["created_at"]

        _ORDER_COL = {
            "gaia_installations": "machine",
        }

        def _select(table: str) -> list[dict]:
            order_col = _ORDER_COL.get(table, "name")
            cur = con.execute(
                f"SELECT * FROM {table} WHERE workspace = ? ORDER BY {order_col}",
                (workspace,),
            )
            return [_row_to_dict(r) for r in cur.fetchall()]

        # workspace.* lists, keyed by entity type.
        workspace_data: dict[str, Any] = {
            "projects": _select("projects"),
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
