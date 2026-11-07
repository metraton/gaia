#!/usr/bin/env python3
"""Rename the v1 ``projects``/``repos`` schema to the v2 ``workspaces``/``projects`` vocabulary.

  * v1 ``projects`` becomes v2 ``workspaces`` (the organizational container)
  * v1 ``repos`` becomes v2 ``projects`` (a git-bearing source repo)
  * the ``project`` column of the child tables becomes ``workspace``

The run, in order:

1. Exits 0 with nothing done when ``workspaces`` exists and ``repos`` does not.
2. Exits 1 unless the layout is v1: ``projects`` and ``repos`` present,
   ``workspaces`` absent.
3. With --dry-run, prints the statements and stops.
4. Copies the database to ``<name>.bak_migrate_08_<timestamp>`` unless --no-backup.
5. In one transaction: drops the FTS triggers and tables, renames the two
   tables, renames ``project`` to ``workspace`` on each existing table of
   ``_CHILD_TABLES_WITH_WORKSPACE_FK`` that has it (SQLite >= 3.25.0 renames
   columns natively), sets ``identity = name`` on workspace rows whose
   identity contains ``/`` while the name does not, and recreates the FTS5
   tables and triggers.

``--db`` defaults to ``~/.gaia/gaia.db``, so a run without it migrates the
real database.

Usage::

    python3 tools/migration/migrate_08_rename_workspace.py --db /path/to/gaia.db
    python3 tools/migration/migrate_08_rename_workspace.py --dry-run
    python3 tools/migration/migrate_08_rename_workspace.py --no-backup

Exit codes:
    0  Migrated, dry run done, or already on the v2 layout.
    1  The database file is missing, or its layout is neither v1 nor migrated.
    2  The migration raised and was rolled back; restore the backup if one was taken.
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Iterable


# Tables whose v1 ``project`` column holds the workspace key. They are read after
# the table renames, so ``projects`` here is the former ``repos``.
_CHILD_TABLES_WITH_WORKSPACE_FK: tuple[str, ...] = (
    "projects",
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

# (base table, FTS table) pairs dropped before the renames and recreated after.
_FTS_MIRRORS: tuple[tuple[str, str], ...] = (
    ("projects", "projects_fts"),
    ("apps", "apps_fts"),
    ("services", "services_fts"),
    ("briefs", "briefs_fts"),
    ("episodes", "episodes_fts"),
    ("memory", "memory_fts"),
)


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
    """Drop the FTS triggers and tables, then rename projects to workspaces and repos to projects.

    _recreate_fts builds the FTS side again against the new names.
    """
    for base, fts in _FTS_MIRRORS:
        for suffix in ("_ai", "_ad", "_au", "_insert", "_delete", "_update", "_fts_insert", "_fts_delete", "_fts_update"):
            con.execute(f"DROP TRIGGER IF EXISTS {base}{suffix}")
        con.execute(f"DROP TABLE IF EXISTS {fts}")
    # repos_fts and the repos triggers name `repos`, which the rename retires.
    for suffix in ("_insert", "_delete", "_update", "_fts_insert", "_fts_delete", "_fts_update", "_ai", "_ad", "_au"):
        con.execute(f"DROP TRIGGER IF EXISTS repos_fts{suffix}")
        con.execute(f"DROP TRIGGER IF EXISTS repos{suffix}")
    con.execute("DROP TABLE IF EXISTS repos_fts")

    con.execute("ALTER TABLE projects RENAME TO workspaces")
    con.execute("ALTER TABLE repos RENAME TO projects")


def _rename_columns(con: sqlite3.Connection) -> None:
    """Rename `project` to `workspace` on each listed table that has `project` and no `workspace`."""
    for table in _iter_existing_child_tables(con):
        if _has_column(con, table, "project") and not _has_column(con, table, "workspace"):
            con.execute(f"ALTER TABLE {table} RENAME COLUMN project TO workspace")


def _clean_contaminated_identity(con: sqlite3.Connection) -> int:
    """Set identity = name on workspace rows whose identity contains ``/`` and whose name does not.

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
    """Recreate each FTS5 table and its triggers, then rebuild its index, for every base table present.

    The definitions are those of gaia/store/schema.sql; change one there and here together.
    """
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
        con.execute("INSERT INTO projects_fts(projects_fts) VALUES ('rebuild')")

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
        except Exception as exc:  # noqa: BLE001
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
