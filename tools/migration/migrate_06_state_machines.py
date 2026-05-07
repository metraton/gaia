#!/usr/bin/env python3
"""
migrate_06_state_machines.py -- One-shot migration that:

1. Backs up ``~/.gaia/gaia.db`` to a timestamped sibling.
2. Cleans legacy data in ``episodes.plan_status``:
     * REVIEW          -> APPROVAL_REQUEST   (deprecated -> canonical rename)
     * <STATUS>        -> BLOCKED            (placeholder leak -> closest semantic)
     * '' (empty str)  -> COMPLETE           (pre-substrate backfill per brief)
3. Recreates the four state-machine-bearing tables with CHECK constraints
   driven by the canonical Python tuples in ``gaia.state``:
     * episodes.plan_status   (NULL allowed for forward compatibility)
     * briefs.status
     * plans.status
     * tasks.status
4. Preserves indexes, triggers, and FTS5 mirrors via the SQLite recreate-
   table pattern (CREATE new with CHECK -> INSERT INTO new SELECT FROM old
   -> DROP old -> RENAME new -> reapply indexes/triggers).

The script is idempotent only insofar as a backup is always created; it
expects to find the pre-CHECK schema and will refuse to run a second time
once the CHECK constraint is already in place (detected via ``sqlite_master.sql``
inspection).

Usage::

    python3 tools/migration/migrate_06_state_machines.py
    python3 tools/migration/migrate_06_state_machines.py --db /path/to/gaia.db
    python3 tools/migration/migrate_06_state_machines.py --dry-run

Exit codes:
    0  Migration succeeded (or already applied + --idempotent).
    1  Pre-flight check failed (DB missing, schema unexpected).
    2  Mid-migration failure -- restore the backup file manually.
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# Ensure gaia package is importable when run from repo root or `tools/migration`
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from gaia.state import (  # noqa: E402
    STATE_MACHINE_REGISTRY,
    VALID_BRIEF_STATUSES,
    VALID_PLAN_LIFECYCLE_STATUSES,
    VALID_PLAN_STATUSES,
    VALID_TASK_STATUSES,
)
from gaia.state.check_clauses import build_check_clause  # noqa: E402


DEFAULT_DB = Path.home() / ".gaia" / "gaia.db"


# ---------------------------------------------------------------------------
# CREATE TABLE statements with CHECK constraints applied. These match the
# upstream schema in gaia/store/schema.sql with the new CHECK clauses.
# ---------------------------------------------------------------------------

def _episodes_create_sql() -> str:
    check = build_check_clause("plan_status", VALID_PLAN_STATUSES, allow_null=True)
    return f"""
    CREATE TABLE episodes_new (
        episode_id            TEXT NOT NULL PRIMARY KEY,
        project               TEXT NOT NULL,
        timestamp             TEXT NOT NULL,
        session_id            TEXT,
        task_id               TEXT,
        agent                 TEXT,
        type                  TEXT,
        title                 TEXT,
        prompt                TEXT,
        enriched_prompt       TEXT,
        wf_prompt             TEXT,
        clarifications        TEXT,
        keywords              TEXT,
        tags                  TEXT,
        commands_executed     TEXT,
        context_metrics       TEXT,
        relevance_score       REAL,
        outcome               TEXT,
        duration_seconds      REAL,
        exit_code             INTEGER,
        plan_status           TEXT,
        output_length         INTEGER,
        output_tokens_approx  INTEGER,
        {check},
        FOREIGN KEY (project) REFERENCES projects(name) ON DELETE CASCADE
    );
    """


def _briefs_create_sql() -> str:
    check = build_check_clause("status", VALID_BRIEF_STATUSES, allow_null=False)
    return f"""
    CREATE TABLE briefs_new (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        project      TEXT NOT NULL,
        name         TEXT NOT NULL,
        status       TEXT NOT NULL DEFAULT 'draft' {check},
        surface_type TEXT,
        title        TEXT,
        objective    TEXT,
        context      TEXT,
        approach     TEXT,
        out_of_scope TEXT,
        topic_key    TEXT,
        created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
        updated_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
        UNIQUE (project, name),
        FOREIGN KEY (project) REFERENCES projects(name) ON DELETE CASCADE
    );
    """


def _plans_create_sql() -> str:
    check = build_check_clause("status", VALID_PLAN_LIFECYCLE_STATUSES, allow_null=False)
    return f"""
    CREATE TABLE plans_new (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        brief_id   INTEGER NOT NULL UNIQUE,
        status     TEXT NOT NULL DEFAULT 'draft' {check},
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
        FOREIGN KEY (brief_id) REFERENCES briefs(id) ON DELETE CASCADE
    );
    """


def _tasks_create_sql() -> str:
    check = build_check_clause("status", VALID_TASK_STATUSES, allow_null=False)
    return f"""
    CREATE TABLE tasks_new (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        plan_id       INTEGER NOT NULL,
        order_num     INTEGER NOT NULL,
        goal          TEXT,
        status        TEXT NOT NULL DEFAULT 'pending' {check},
        evidence_path TEXT,
        FOREIGN KEY (plan_id) REFERENCES plans(id) ON DELETE CASCADE
    );
    """


# ---------------------------------------------------------------------------
# Index + trigger DDL (re-applied after rename). Mirrors gaia/store/schema.sql.
# ---------------------------------------------------------------------------

_REAPPLY_INDEXES_TRIGGERS = """
    CREATE INDEX IF NOT EXISTS idx_episodes_project_timestamp ON episodes(project, timestamp DESC);
    CREATE INDEX IF NOT EXISTS idx_episodes_session ON episodes(session_id);

    CREATE INDEX IF NOT EXISTS idx_briefs_project ON briefs(project);
    CREATE INDEX IF NOT EXISTS idx_briefs_status ON briefs(status);
    CREATE INDEX IF NOT EXISTS idx_briefs_topic_key ON briefs(topic_key);

    CREATE INDEX IF NOT EXISTS idx_tasks_plan ON tasks(plan_id);

    CREATE TRIGGER IF NOT EXISTS episodes_ai AFTER INSERT ON episodes BEGIN
        INSERT INTO episodes_fts(rowid, episode_id, prompt, enriched_prompt, tags, title)
        VALUES (new.rowid, new.episode_id, new.prompt, new.enriched_prompt, new.tags, new.title);
    END;

    CREATE TRIGGER IF NOT EXISTS episodes_ad AFTER DELETE ON episodes BEGIN
        INSERT INTO episodes_fts(episodes_fts, rowid, episode_id, prompt, enriched_prompt, tags, title)
        VALUES ('delete', old.rowid, old.episode_id, old.prompt, old.enriched_prompt, old.tags, old.title);
    END;

    CREATE TRIGGER IF NOT EXISTS episodes_au AFTER UPDATE ON episodes BEGIN
        INSERT INTO episodes_fts(episodes_fts, rowid, episode_id, prompt, enriched_prompt, tags, title)
        VALUES ('delete', old.rowid, old.episode_id, old.prompt, old.enriched_prompt, old.tags, old.title);
        INSERT INTO episodes_fts(rowid, episode_id, prompt, enriched_prompt, tags, title)
        VALUES (new.rowid, new.episode_id, new.prompt, new.enriched_prompt, new.tags, new.title);
    END;

    CREATE TRIGGER IF NOT EXISTS briefs_ai AFTER INSERT ON briefs BEGIN
        INSERT INTO briefs_fts(rowid, objective, context, approach)
        VALUES (new.id, new.objective, new.context, new.approach);
    END;

    CREATE TRIGGER IF NOT EXISTS briefs_ad AFTER DELETE ON briefs BEGIN
        INSERT INTO briefs_fts(briefs_fts, rowid, objective, context, approach)
        VALUES ('delete', old.id, old.objective, old.context, old.approach);
    END;

    CREATE TRIGGER IF NOT EXISTS briefs_au AFTER UPDATE ON briefs BEGIN
        INSERT INTO briefs_fts(briefs_fts, rowid, objective, context, approach)
        VALUES ('delete', old.id, old.objective, old.context, old.approach);
        INSERT INTO briefs_fts(rowid, objective, context, approach)
        VALUES (new.id, new.objective, new.context, new.approach);
    END;
"""


# ---------------------------------------------------------------------------
# Cleanup queries
# ---------------------------------------------------------------------------

_CLEANUP_SQL_PRE_RECREATE = """
    -- Cleanup #1: REVIEW -> APPROVAL_REQUEST (deprecated rename)
    UPDATE episodes SET plan_status = 'APPROVAL_REQUEST' WHERE plan_status = 'REVIEW';

    -- Cleanup #2: <STATUS> placeholder -> BLOCKED (closest semantic; agent
    -- failed to substitute the template, so the contract is broken)
    UPDATE episodes SET plan_status = 'BLOCKED' WHERE plan_status = '<STATUS>';

    -- Cleanup #3: empty string -> COMPLETE (pre-substrate backfill; episodes
    -- predate plan_status enforcement and were closed when written)
    UPDATE episodes SET plan_status = 'COMPLETE' WHERE plan_status = '';

    -- Cleanup #4: NULL -> COMPLETE (same rationale as #3; AC-3 of the
    -- gaia-state-machines brief requires zero NULL legacy rows post-
    -- migration. Future writers may legitimately write NULL -- the CHECK
    -- clause still admits it -- but the historical backlog is cleaned.)
    UPDATE episodes SET plan_status = 'COMPLETE' WHERE plan_status IS NULL;

    -- Sanity: there should be no remaining values outside the canonical enum.
    -- (No explicit DELETE here -- the recreate step's CHECK would refuse the
    -- copy if any drift slipped through.)
"""


# ---------------------------------------------------------------------------
# Idempotency: detect whether the migration has already been applied.
# ---------------------------------------------------------------------------

def _already_migrated(con: sqlite3.Connection) -> bool:
    """Return True if any of the four target tables already carries a CHECK
    on its state column. We probe by inspecting ``sqlite_master.sql`` for the
    string 'CHECK' near the column name."""
    rows = con.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='table' AND name IN ('episodes','briefs','plans','tasks')"
    ).fetchall()
    found = {r[0]: (r[1] or "") for r in rows}
    # Heuristic: the new schema contains 'CHECK' inside the table DDL.
    for tbl in ("episodes", "briefs", "plans", "tasks"):
        if "CHECK" in found.get(tbl, "").upper():
            return True
    return False


# ---------------------------------------------------------------------------
# Audit + migration drivers
# ---------------------------------------------------------------------------

def _audit(con: sqlite3.Connection) -> dict:
    """Snapshot of legacy / canonical row counts. Returned for reporting."""
    out: dict = {"episodes": {}, "briefs": {}, "plans": {}, "tasks": {}}
    rows = con.execute(
        "SELECT IFNULL(plan_status,'(NULL)') AS v, COUNT(*) FROM episodes GROUP BY plan_status"
    ).fetchall()
    out["episodes"] = {r[0]: r[1] for r in rows}
    rows = con.execute("SELECT status, COUNT(*) FROM briefs GROUP BY status").fetchall()
    out["briefs"] = {r[0]: r[1] for r in rows}
    rows = con.execute("SELECT status, COUNT(*) FROM plans GROUP BY status").fetchall()
    out["plans"] = {r[0]: r[1] for r in rows}
    rows = con.execute("SELECT status, COUNT(*) FROM tasks GROUP BY status").fetchall()
    out["tasks"] = {r[0]: r[1] for r in rows}
    return out


def _backup_db(db_path: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = db_path.with_name(f"{db_path.name}.bak.{ts}")
    shutil.copy2(db_path, backup)
    return backup


def _recreate_table(
    con: sqlite3.Connection,
    *,
    table: str,
    create_new_sql: str,
    column_list_sql: str,
) -> None:
    """Recreate-table pattern: CREATE new -> INSERT SELECT -> DROP -> RENAME.

    The ``column_list_sql`` is the explicit column projection used in both the
    INSERT INTO and SELECT FROM clauses, so the migration is order-independent.
    """
    con.executescript(create_new_sql)
    con.execute(
        f"INSERT INTO {table}_new ({column_list_sql}) "
        f"SELECT {column_list_sql} FROM {table};"
    )
    con.execute(f"DROP TABLE {table};")
    con.execute(f"ALTER TABLE {table}_new RENAME TO {table};")


def _migrate(con: sqlite3.Connection, *, dry_run: bool = False) -> dict:
    pre = _audit(con)

    if dry_run:
        return {"status": "dry-run", "pre_audit": pre, "post_audit": None}

    con.execute("PRAGMA foreign_keys = OFF;")  # required for recreate pattern
    con.execute("BEGIN")
    try:
        # ---- Cleanup ---------------------------------------------------
        con.executescript(_CLEANUP_SQL_PRE_RECREATE)

        # ---- Drop FTS triggers & legacy table-bound triggers BEFORE rename ----
        # Triggers reference the OLD table by name; SQLite drops triggers on
        # their parent table automatically when the parent is dropped, but
        # we re-create them explicitly after the rename via the schema script.
        for trig in (
            "episodes_ai", "episodes_ad", "episodes_au",
            "briefs_ai", "briefs_ad", "briefs_au",
        ):
            con.execute(f"DROP TRIGGER IF EXISTS {trig};")

        # ---- Recreate tables with CHECK ---------------------------------
        _recreate_table(
            con,
            table="episodes",
            create_new_sql=_episodes_create_sql(),
            column_list_sql=(
                "episode_id, project, timestamp, session_id, task_id, agent, "
                "type, title, prompt, enriched_prompt, wf_prompt, "
                "clarifications, keywords, tags, commands_executed, "
                "context_metrics, relevance_score, outcome, duration_seconds, "
                "exit_code, plan_status, output_length, output_tokens_approx"
            ),
        )
        _recreate_table(
            con,
            table="briefs",
            create_new_sql=_briefs_create_sql(),
            column_list_sql=(
                "id, project, name, status, surface_type, title, objective, "
                "context, approach, out_of_scope, topic_key, created_at, updated_at"
            ),
        )
        _recreate_table(
            con,
            table="plans",
            create_new_sql=_plans_create_sql(),
            column_list_sql="id, brief_id, status, created_at",
        )
        _recreate_table(
            con,
            table="tasks",
            create_new_sql=_tasks_create_sql(),
            column_list_sql="id, plan_id, order_num, goal, status, evidence_path",
        )

        # ---- Re-apply indexes + triggers --------------------------------
        con.executescript(_REAPPLY_INDEXES_TRIGGERS)

        # ---- Rebuild FTS5 mirrors (rowids changed on table recreate) ----
        # episodes_fts and briefs_fts are external content tables -- rebuild
        # so their internal indexes match the new base-table rowids.
        con.execute("INSERT INTO episodes_fts(episodes_fts) VALUES ('rebuild');")
        con.execute("INSERT INTO briefs_fts(briefs_fts) VALUES ('rebuild');")

        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.execute("PRAGMA foreign_keys = ON;")

    post = _audit(con)
    return {"status": "applied", "pre_audit": pre, "post_audit": post}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB,
                        help=f"Path to gaia.db (default: {DEFAULT_DB})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Audit only; do not modify the DB")
    parser.add_argument("--allow-already-migrated", action="store_true",
                        help="Exit 0 if the migration is already applied")
    args = parser.parse_args()

    if not args.db.exists():
        print(f"DB not found: {args.db}", file=sys.stderr)
        return 1

    con = sqlite3.connect(str(args.db))
    try:
        if _already_migrated(con):
            msg = "Migration already applied (CHECK constraints present)."
            if args.allow_already_migrated:
                print(msg)
                return 0
            print(msg, file=sys.stderr)
            return 1
    finally:
        con.close()

    if args.dry_run:
        con = sqlite3.connect(str(args.db))
        try:
            result = _migrate(con, dry_run=True)
        finally:
            con.close()
        print("[dry-run]")
        for tbl, counts in result["pre_audit"].items():
            print(f"  {tbl}: {counts}")
        return 0

    # Backup before any mutation.
    backup = _backup_db(args.db)
    print(f"Backup written: {backup}")

    con = sqlite3.connect(str(args.db))
    try:
        result = _migrate(con, dry_run=False)
    except Exception as exc:
        print(f"Migration failed: {exc}", file=sys.stderr)
        print(f"Restore manually from backup: {backup}", file=sys.stderr)
        return 2
    finally:
        con.close()

    print("Migration applied.")
    print("Pre-audit (episodes.plan_status):")
    for k, v in result["pre_audit"]["episodes"].items():
        print(f"  {k!r}: {v}")
    print("Post-audit (episodes.plan_status):")
    for k, v in result["post_audit"]["episodes"].items():
        print(f"  {k!r}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
