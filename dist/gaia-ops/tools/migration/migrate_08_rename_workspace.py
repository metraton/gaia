#!/usr/bin/env python3
"""
migrate_08_rename_workspace.py -- Rename the v1 ``projects``/``repos`` schema
to the v2 ``workspaces``/``projects`` vocabulary.

Vocabulary:
  * v1 ``projects``   -> v2 ``workspaces``  (organizational container)
  * v1 ``repos``      -> v2 ``projects``    (git-bearing source repo)
  * v1 child column ``project`` -> v2 child column ``workspace``

Behaviour:

1. Idempotency probe: if ``workspaces`` already exists and ``repos`` does
   not, the migration is a no-op (exit 0).
2. Sanity probe: if neither ``projects`` (v1) nor ``workspaces`` (v2) exist,
   the database is uninitialised -- exit 1.
3. Optional backup of ``~/.gaia/gaia.db`` to a timestamped sibling.
4. Transactional rename:
     - ALTER TABLE projects RENAME TO workspaces
     - ALTER TABLE repos    RENAME TO projects
     - For every child table that carries a ``project`` column pointing to
       the workspace identity, ALTER TABLE ... RENAME COLUMN project TO
       workspace. SQLite >= 3.25.0 (2018) supports column rename natively.
5. Identity cleanup: any workspace row whose ``identity`` is a remote URL
   shape (contains ``/``) but whose ``name`` is a flat directory name
   (no ``/``) collapses to ``identity = name``. Workspaces that are real
   git-bearing repos keep their ``host/owner/repo`` identity untouched.
6. FTS5 mirror tables and triggers are recreated to point at the renamed
   tables (SQLite does not automatically rewrite contentless FTS triggers
   when the underlying table is renamed).

The script NEVER touches ``~/.gaia/gaia.db`` automatically; the default
``--db`` value points there but the user must pass it explicitly or accept
the default. Tests invoke it with ``--db /tmp/...``.

Usage::

    python3 tools/migration/migrate_08_rename_workspace.py --db /path/to/gaia.db
    python3 tools/migration/migrate_08_rename_workspace.py --dry-run
    python3 tools/migration/migrate_08_rename_workspace.py --no-backup

Exit codes:
    0  Migration applied (or already in place).
    1  Pre-flight check failed (DB missing, schema unrecognised).
    2  Mid-migration failure -- restore the backup file manually.
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Iterable


# ---------------------------------------------------------------------------
# Table inventory
# ---------------------------------------------------------------------------

# Tables that in v1 carried a ``project`` column pointing at the
# organisational identity (i.e. the v1 ``projects`` table key). In v2 the
# column is renamed to ``workspace`` to match the new vocabulary.
#
# Derived by scanning gaia/store/schema.sql for ``FOREIGN KEY (workspace)
# REFERENCES workspaces(name)``. Includes both project-scoped tables
# (which also carry a per-project FK) and workspace-only tables.
#
# NOTE: ``projects`` appears in this list because it is examined AFTER the
# table rename step (v1 ``repos`` -> v2 ``projects``); the renamed table
# still carries its old ``project`` column that referenced the old
# workspace identity and which we now rename to ``workspace``.
_CHILD_TABLES_WITH_WORKSPACE_FK: tuple[str, ...] = (
    "projects",  # post-rename: was v1 `repos`, still has `project` column
    "apps",
    "libraries",
    "services",
    "features",
    "tf_modules",
    "tf_live",
    "releases",
    "workloads",
    "clusters_defined",
    "clusters",
    "integrations",
    "gaia_installations",
    "machines",
    "briefs",
    "episodes",
    "memory",
    "context_contracts",
    "harness_events",
)

# Tables that participate in FTS5 mirrors. When their base table is renamed,
# the triggers that keep the FTS index in sync still reference the OLD name
# inside their SQL body, so we recreate them.
_FTS_MIRRORS: tuple[tuple[str, str], ...] = (
    # (base_table, fts_table)
    ("projects", "projects_fts"),  # v2 projects (was v1 repos)
    ("apps", "apps_fts"),
    ("services", "services_fts"),
    ("briefs", "briefs_fts"),
    ("episodes", "episodes_fts"),
    ("memory", "memory_fts"),
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _default_db_path() -> Path:
    return Path.home() / ".gaia" / "gaia.db"


def _table_exists(con: sqlite3.Connection, name: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def _has_column(con: sqlite3.Connection, table: str, column: str) -> bool:
    rows = con.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row[1] == column for row in rows)


def _backup(db: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    bak = db.with_name(f"{db.name}.bak_migrate_08_{stamp}")
    shutil.copy2(db, bak)
    return bak


def _iter_existing_child_tables(con: sqlite3.Connection) -> Iterable[str]:
    for table in _CHILD_TABLES_WITH_WORKSPACE_FK:
        if _table_exists(con, table):
            yield table


# ---------------------------------------------------------------------------
# Core migration steps
# ---------------------------------------------------------------------------

def _is_already_migrated(con: sqlite3.Connection) -> bool:
    """v2 layout: `workspaces` exists, `repos` does not."""
    return _table_exists(con, "workspaces") and not _table_exists(con, "repos")


def _is_v1_layout(con: sqlite3.Connection) -> bool:
    """v1 layout: `projects` exists AND `repos` exists AND `workspaces` does not."""
    return (
        _table_exists(con, "projects")
        and _table_exists(con, "repos")
        and not _table_exists(con, "workspaces")
    )


def _rename_tables(con: sqlite3.Connection) -> None:
    """Step 1: rename v1 projects -> workspaces and v1 repos -> projects.

    Order matters: drop the dependent FTS triggers first (they reference the
    OLD table names by SQL body), rename, then recreate the triggers.
    """
    # Drop FTS triggers and their virtual tables first. They reference the
    # old base table names in their CREATE SQL and would otherwise become
    # stale after the rename. They are reconstructed in _recreate_fts().
    for base, fts in _FTS_MIRRORS:
        # NOTE: at this point `projects` still means the v1 organisational
        # table; the actual git-bearing repo FTS lives over `repos_fts` (if
        # it existed in v1) or `projects_fts` (if v1 only mirrored repos).
        # The drop is best-effort; missing triggers are fine.
        for suffix in ("_ai", "_ad", "_au", "_insert", "_delete", "_update", "_fts_insert", "_fts_delete", "_fts_update"):
            con.execute(f"DROP TRIGGER IF EXISTS {base}{suffix}")
        con.execute(f"DROP TABLE IF EXISTS {fts}")
    # Also drop the v1 ``repos_fts`` triggers and table (if any) -- its
    # content table is about to be renamed to ``projects`` and the triggers
    # still reference the old name ``repos`` in their SQL body.
    for suffix in ("_insert", "_delete", "_update", "_fts_insert", "_fts_delete", "_fts_update", "_ai", "_ad", "_au"):
        con.execute(f"DROP TRIGGER IF EXISTS repos_fts{suffix}")
        con.execute(f"DROP TRIGGER IF EXISTS repos{suffix}")
    con.execute("DROP TABLE IF EXISTS repos_fts")

    # Rename the two top-level tables.
    con.execute("ALTER TABLE projects RENAME TO workspaces")
    con.execute("ALTER TABLE repos RENAME TO projects")


def _rename_columns(con: sqlite3.Connection) -> None:
    """Step 2: rename `project` -> `workspace` on every child table that has it."""
    for table in _iter_existing_child_tables(con):
        if _has_column(con, table, "project") and not _has_column(con, table, "workspace"):
            con.execute(f"ALTER TABLE {table} RENAME COLUMN project TO workspace")


def _clean_contaminated_identity(con: sqlite3.Connection) -> int:
    """Step 3: collapse remote-URL-shaped identities on workspace rows whose
    `name` is a flat directory name (no ``/``).

    A workspace identity like ``github.com/foo/bar`` only makes sense when
    the workspace itself is a single git-bearing repo (its `name` matches
    the remote shape). For organisational workspaces whose `name` is just
    ``aaxis`` / ``me`` / ``qxo`` the identity should equal the name.

    Returns the number of rows updated.
    """
    cur = con.execute(
        """
        UPDATE workspaces
           SET identity = name
         WHERE identity IS NOT NULL
           AND identity LIKE '%/%'
           AND name NOT LIKE '%/%'
        """,
    )
    return cur.rowcount


def _recreate_fts(con: sqlite3.Connection) -> None:
    """Step 4: rebuild FTS5 mirrors + triggers for the renamed tables.

    The FTS shape mirrors gaia/store/schema.sql exactly. Only mirrors whose
    base table actually exists are recreated.
    """
    # --- projects_fts (v2 projects = git-bearing repos) -----------------
    if _table_exists(con, "projects"):
        con.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS projects_fts USING fts5(
                name,
                role,
                primary_language,
                content='projects',
                content_rowid='rowid'
            )
        """)
        con.execute("""
            CREATE TRIGGER IF NOT EXISTS projects_fts_insert AFTER INSERT ON projects BEGIN
                INSERT INTO projects_fts(rowid, name, role, primary_language)
                VALUES (new.rowid, new.name, new.role, new.primary_language);
            END
        """)
        con.execute("""
            CREATE TRIGGER IF NOT EXISTS projects_fts_delete AFTER DELETE ON projects BEGIN
                INSERT INTO projects_fts(projects_fts, rowid, name, role, primary_language)
                VALUES ('delete', old.rowid, old.name, old.role, old.primary_language);
            END
        """)
        con.execute("""
            CREATE TRIGGER IF NOT EXISTS projects_fts_update AFTER UPDATE ON projects BEGIN
                INSERT INTO projects_fts(projects_fts, rowid, name, role, primary_language)
                VALUES ('delete', old.rowid, old.name, old.role, old.primary_language);
                INSERT INTO projects_fts(rowid, name, role, primary_language)
                VALUES (new.rowid, new.name, new.role, new.primary_language);
            END
        """)
        # Rebuild contents from base table.
        con.execute("INSERT INTO projects_fts(projects_fts) VALUES ('rebuild')")

    # --- apps_fts -------------------------------------------------------
    if _table_exists(con, "apps"):
        con.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS apps_fts USING fts5(
                name,
                description,
                topic_key,
                content='apps',
                content_rowid='rowid'
            )
        """)
        con.execute("""
            CREATE TRIGGER IF NOT EXISTS apps_fts_insert AFTER INSERT ON apps BEGIN
                INSERT INTO apps_fts(rowid, name, description, topic_key)
                VALUES (new.rowid, new.name, new.description, new.topic_key);
            END
        """)
        con.execute("""
            CREATE TRIGGER IF NOT EXISTS apps_fts_delete AFTER DELETE ON apps BEGIN
                INSERT INTO apps_fts(apps_fts, rowid, name, description, topic_key)
                VALUES ('delete', old.rowid, old.name, old.description, old.topic_key);
            END
        """)
        con.execute("""
            CREATE TRIGGER IF NOT EXISTS apps_fts_update AFTER UPDATE ON apps BEGIN
                INSERT INTO apps_fts(apps_fts, rowid, name, description, topic_key)
                VALUES ('delete', old.rowid, old.name, old.description, old.topic_key);
                INSERT INTO apps_fts(rowid, name, description, topic_key)
                VALUES (new.rowid, new.name, new.description, new.topic_key);
            END
        """)
        con.execute("INSERT INTO apps_fts(apps_fts) VALUES ('rebuild')")

    # --- services_fts ---------------------------------------------------
    if _table_exists(con, "services"):
        con.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS services_fts USING fts5(
                name,
                description,
                topic_key,
                content='services',
                content_rowid='rowid'
            )
        """)
        con.execute("""
            CREATE TRIGGER IF NOT EXISTS services_fts_insert AFTER INSERT ON services BEGIN
                INSERT INTO services_fts(rowid, name, description, topic_key)
                VALUES (new.rowid, new.name, new.description, new.topic_key);
            END
        """)
        con.execute("""
            CREATE TRIGGER IF NOT EXISTS services_fts_delete AFTER DELETE ON services BEGIN
                INSERT INTO services_fts(services_fts, rowid, name, description, topic_key)
                VALUES ('delete', old.rowid, old.name, old.description, old.topic_key);
            END
        """)
        con.execute("""
            CREATE TRIGGER IF NOT EXISTS services_fts_update AFTER UPDATE ON services BEGIN
                INSERT INTO services_fts(services_fts, rowid, name, description, topic_key)
                VALUES ('delete', old.rowid, old.name, old.description, old.topic_key);
                INSERT INTO services_fts(rowid, name, description, topic_key)
                VALUES (new.rowid, new.name, new.description, new.topic_key);
            END
        """)
        con.execute("INSERT INTO services_fts(services_fts) VALUES ('rebuild')")

    # --- briefs_fts -----------------------------------------------------
    if _table_exists(con, "briefs"):
        con.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS briefs_fts USING fts5(
                objective,
                context,
                approach,
                content='briefs',
                content_rowid='id'
            )
        """)
        con.execute("""
            CREATE TRIGGER IF NOT EXISTS briefs_ai AFTER INSERT ON briefs BEGIN
                INSERT INTO briefs_fts(rowid, objective, context, approach)
                VALUES (new.id, new.objective, new.context, new.approach);
            END
        """)
        con.execute("""
            CREATE TRIGGER IF NOT EXISTS briefs_ad AFTER DELETE ON briefs BEGIN
                INSERT INTO briefs_fts(briefs_fts, rowid, objective, context, approach)
                VALUES ('delete', old.id, old.objective, old.context, old.approach);
            END
        """)
        con.execute("""
            CREATE TRIGGER IF NOT EXISTS briefs_au AFTER UPDATE ON briefs BEGIN
                INSERT INTO briefs_fts(briefs_fts, rowid, objective, context, approach)
                VALUES ('delete', old.id, old.objective, old.context, old.approach);
                INSERT INTO briefs_fts(rowid, objective, context, approach)
                VALUES (new.id, new.objective, new.context, new.approach);
            END
        """)
        con.execute("INSERT INTO briefs_fts(briefs_fts) VALUES ('rebuild')")

    # --- episodes_fts ---------------------------------------------------
    if _table_exists(con, "episodes"):
        con.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS episodes_fts USING fts5(
                episode_id UNINDEXED,
                prompt,
                enriched_prompt,
                tags,
                title,
                content='episodes',
                content_rowid='rowid'
            )
        """)
        con.execute("""
            CREATE TRIGGER IF NOT EXISTS episodes_ai AFTER INSERT ON episodes BEGIN
                INSERT INTO episodes_fts(rowid, episode_id, prompt, enriched_prompt, tags, title)
                VALUES (new.rowid, new.episode_id, new.prompt, new.enriched_prompt, new.tags, new.title);
            END
        """)
        con.execute("""
            CREATE TRIGGER IF NOT EXISTS episodes_ad AFTER DELETE ON episodes BEGIN
                INSERT INTO episodes_fts(episodes_fts, rowid, episode_id, prompt, enriched_prompt, tags, title)
                VALUES ('delete', old.rowid, old.episode_id, old.prompt, old.enriched_prompt, old.tags, old.title);
            END
        """)
        con.execute("""
            CREATE TRIGGER IF NOT EXISTS episodes_au AFTER UPDATE ON episodes BEGIN
                INSERT INTO episodes_fts(episodes_fts, rowid, episode_id, prompt, enriched_prompt, tags, title)
                VALUES ('delete', old.rowid, old.episode_id, old.prompt, old.enriched_prompt, old.tags, old.title);
                INSERT INTO episodes_fts(rowid, episode_id, prompt, enriched_prompt, tags, title)
                VALUES (new.rowid, new.episode_id, new.prompt, new.enriched_prompt, new.tags, new.title);
            END
        """)
        con.execute("INSERT INTO episodes_fts(episodes_fts) VALUES ('rebuild')")

    # --- memory_fts -----------------------------------------------------
    if _table_exists(con, "memory"):
        con.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
                workspace UNINDEXED,
                name UNINDEXED,
                description,
                body,
                content='memory',
                content_rowid='rowid'
            )
        """)
        con.execute("""
            CREATE TRIGGER IF NOT EXISTS memory_ai AFTER INSERT ON memory BEGIN
                INSERT INTO memory_fts(rowid, workspace, name, description, body)
                VALUES (new.rowid, new.workspace, new.name, new.description, new.body);
            END
        """)
        con.execute("""
            CREATE TRIGGER IF NOT EXISTS memory_ad AFTER DELETE ON memory BEGIN
                INSERT INTO memory_fts(memory_fts, rowid, workspace, name, description, body)
                VALUES ('delete', old.rowid, old.workspace, old.name, old.description, old.body);
            END
        """)
        con.execute("""
            CREATE TRIGGER IF NOT EXISTS memory_au AFTER UPDATE ON memory BEGIN
                INSERT INTO memory_fts(memory_fts, rowid, workspace, name, description, body)
                VALUES ('delete', old.rowid, old.workspace, old.name, old.description, old.body);
                INSERT INTO memory_fts(rowid, workspace, name, description, body)
                VALUES (new.rowid, new.workspace, new.name, new.description, new.body);
            END
        """)
        con.execute("INSERT INTO memory_fts(memory_fts) VALUES ('rebuild')")


def _migrate(con: sqlite3.Connection) -> dict:
    """Apply the full migration transactionally and return a stats dict."""
    con.execute("BEGIN")
    try:
        _rename_tables(con)
        _rename_columns(con)
        identity_fixed = _clean_contaminated_identity(con)
        _recreate_fts(con)
        con.commit()
    except Exception:
        con.rollback()
        raise
    return {"identity_rows_fixed": identity_fixed}


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(_default_db_path()),
                        help="Path to gaia.db (default: ~/.gaia/gaia.db)")
    parser.add_argument("--dry-run", action="store_true", default=False,
                        help="Inspect schema; do not write")
    parser.add_argument("--no-backup", action="store_true", default=False,
                        help="Skip the timestamped backup copy")
    args = parser.parse_args(argv)

    db = Path(args.db)
    if not db.exists():
        print(f"Error: DB not found at {db}", file=sys.stderr)
        return 1

    con = sqlite3.connect(str(db))
    try:
        if _is_already_migrated(con):
            print(f"[migrate_08] {db} already on v2 schema (workspaces present, repos absent); no-op")
            return 0

        if not _is_v1_layout(con):
            print(
                f"Error: unexpected schema at {db} -- expected v1 layout "
                f"(`projects` + `repos`) or v2 layout (`workspaces` only).",
                file=sys.stderr,
            )
            return 1

        if args.dry_run:
            print("[migrate_08] DRY-RUN -- would rename:")
            print("  ALTER TABLE projects RENAME TO workspaces")
            print("  ALTER TABLE repos    RENAME TO projects")
            for table in _iter_existing_child_tables(con):
                if _has_column(con, table, "project"):
                    print(f"  ALTER TABLE {table} RENAME COLUMN project TO workspace")
            print("  UPDATE workspaces SET identity = name WHERE identity LIKE '%/%' AND name NOT LIKE '%/%'")
            print("  + recreate FTS5 triggers for renamed base tables")
            return 0

        if not args.no_backup:
            bak = _backup(db)
            print(f"[migrate_08] backup -> {bak}")

        try:
            stats = _migrate(con)
        except Exception as exc:  # noqa: BLE001 -- we re-print and exit 2
            print(f"Error during migration: {exc}", file=sys.stderr)
            if not args.no_backup:
                print("Restore the backup manually.", file=sys.stderr)
            return 2

        print(
            f"[migrate_08] migration applied; identity rows cleaned: "
            f"{stats['identity_rows_fixed']}"
        )
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
