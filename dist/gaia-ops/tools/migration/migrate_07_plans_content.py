#!/usr/bin/env python3
"""
migrate_07_plans_content.py -- One-shot migration that adds the
``content`` and ``updated_at`` columns to the ``plans`` table.

Pre-substrate, the ``plans`` table only carried ``(id, brief_id, status,
created_at)``. The CLI work to add headless CRUD for plans (``gaia plan
save / show / list / delete / set-status``) requires a body column to
hold the rendered plan markdown plus an ``updated_at`` timestamp so that
listings can be sorted by recency. This migration adds both columns to
existing DBs without disturbing data.

Behaviour:

1. Backs up ``~/.gaia/gaia.db`` to a timestamped sibling.
2. Detects whether ``content`` already exists; if so, exits 0 (idempotent).
3. Issues ``ALTER TABLE plans ADD COLUMN content TEXT`` and
   ``ALTER TABLE plans ADD COLUMN updated_at TEXT NOT NULL DEFAULT
   (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))``.
4. Backfills ``updated_at`` to the existing ``created_at`` for every
   pre-existing row so chronology is preserved.

Usage::

    python3 tools/migration/migrate_07_plans_content.py
    python3 tools/migration/migrate_07_plans_content.py --db /path/to/gaia.db
    python3 tools/migration/migrate_07_plans_content.py --dry-run

Exit codes:
    0  Migration applied (or already in place).
    1  Pre-flight check failed (DB missing).
    2  Mid-migration failure -- restore the backup file manually.
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path


# ---------------------------------------------------------------------------
# CLI plumbing
# ---------------------------------------------------------------------------

def _default_db_path() -> Path:
    return Path.home() / ".gaia" / "gaia.db"


def _has_column(con: sqlite3.Connection, table: str, column: str) -> bool:
    rows = con.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row[1] == column for row in rows)


def _backup(db: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    bak = db.with_name(f"{db.name}.bak_migrate_07_{stamp}")
    shutil.copy2(db, bak)
    return bak


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(_default_db_path()),
                        help="Path to gaia.db (default: ~/.gaia/gaia.db)")
    parser.add_argument("--dry-run", action="store_true", default=False,
                        help="Inspect schema; do not write")
    args = parser.parse_args(argv)

    db = Path(args.db)
    if not db.exists():
        print(f"Error: DB not found at {db}", file=sys.stderr)
        return 1

    con = sqlite3.connect(str(db))
    try:
        has_content = _has_column(con, "plans", "content")
        has_updated = _has_column(con, "plans", "updated_at")

        if has_content and has_updated:
            print(f"[migrate_07] plans.content + plans.updated_at already present at {db}; nothing to do")
            return 0

        if args.dry_run:
            print("[migrate_07] DRY-RUN -- would add:")
            if not has_content:
                print("  ALTER TABLE plans ADD COLUMN content TEXT")
            if not has_updated:
                print("  ALTER TABLE plans ADD COLUMN updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))")
                print("  UPDATE plans SET updated_at = created_at")
            return 0

        bak = _backup(db)
        print(f"[migrate_07] backup -> {bak}")

        try:
            if not has_content:
                con.execute("ALTER TABLE plans ADD COLUMN content TEXT")
            if not has_updated:
                # SQLite ALTER TABLE ... DEFAULT must be a literal/strftime
                con.execute(
                    "ALTER TABLE plans ADD COLUMN updated_at TEXT NOT NULL "
                    "DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))"
                )
                con.execute("UPDATE plans SET updated_at = created_at "
                            "WHERE updated_at IS NULL OR updated_at = ''")
            con.commit()
        except Exception as exc:
            con.rollback()
            print(f"Error during migration: {exc}", file=sys.stderr)
            print(f"Restore the backup manually: {bak}", file=sys.stderr)
            return 2

        print("[migrate_07] migration applied")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
