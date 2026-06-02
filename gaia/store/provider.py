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


# Child tables whose rows belong to a project (PK = (workspace, project, name))
# and therefore must be filtered through the parent project's status so that a
# missing project's children never contaminate active queries (AC-4, soft-delete).
_PROJECT_CHILD_TABLES = frozenset({
    "apps",
    "libraries",
    "services",
    "features",
    "tf_modules",
    "tf_live",
    "releases",
    "workloads",
    "clusters_defined",
})


def get_context(
    workspace: str,
    *,
    db_path: Path | None = None,
    include_missing: bool = False,
) -> dict[str, Any]:
    """Return the JSON-shaped context for a workspace.

    Args:
        workspace: Workspace name (matches workspaces.name).
        db_path: Optional explicit DB path (used by tests).
        include_missing: When False (default), projects with
            ``status='missing'`` are filtered out, AND the child rows
            (apps/services/features/...) of those missing projects are filtered
            out too, so a soft-deleted project never contaminates the normal
            active view. When True, every row is returned regardless of status
            -- the "existed but no longer on disk" view, which keeps soft-deleted
            data consultable (AC-4, soft-delete).

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

        # Names of projects in this workspace that are soft-deleted (missing).
        # Used to filter child rows of missing projects out of the active view.
        missing_projects: set[str] = set()
        if not include_missing:
            missing_projects = {
                r["name"]
                for r in con.execute(
                    "SELECT name FROM projects "
                    "WHERE workspace = ? AND status = 'missing'",
                    (workspace,),
                ).fetchall()
            }

        def _select(table: str) -> list[dict]:
            order_col = _ORDER_COL.get(table, "name")
            cur = con.execute(
                f"SELECT * FROM {table} WHERE workspace = ? ORDER BY {order_col}",
                (workspace,),
            )
            rows = cur.fetchall()
            if include_missing:
                return [_row_to_dict(r) for r in rows]
            # Active view: drop missing projects, and drop child rows whose
            # parent project is missing (filter through the parent status).
            out: list[dict] = []
            for r in rows:
                keys = r.keys()
                if table == "projects":
                    if "status" in keys and r["status"] == "missing":
                        continue
                elif table in _PROJECT_CHILD_TABLES and "project" in keys:
                    if r["project"] in missing_projects:
                        continue
                out.append(_row_to_dict(r))
            return out

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
