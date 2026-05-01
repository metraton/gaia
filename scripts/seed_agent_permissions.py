#!/usr/bin/env python3
"""
seed_agent_permissions.py -- Populate agent_permissions with the full B3 mapping.

Inserts the complete mapping for the 5 domain agents into `agent_permissions`
in `~/.gaia/gaia.db`. Uses INSERT OR IGNORE (idempotent). Exits non-zero if
the table does not exist (B1 not applied).

Mapping (B3 M2):
  developer         -> apps, libraries, services, features
  terraform-architect -> tf_modules, tf_live, clusters
  gitops-operator   -> releases, workloads, clusters_defined
  gaia-operator     -> integrations, gaia_installations
  cloud-troubleshooter -> clusters

Usage:
    python3 scripts/seed_agent_permissions.py [--dry-run] [--db-path PATH]
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Canonical mapping: B3 M1 source of truth
# ---------------------------------------------------------------------------
AGENT_TABLE_MAPPING: list[tuple[str, str]] = [
    # developer: application layer (apps, libraries, services, features)
    ("developer", "apps"),
    ("developer", "libraries"),
    ("developer", "services"),
    ("developer", "features"),
    # terraform-architect: IaC layer (tf_modules, tf_live, clusters declarative)
    # NOTE: clusters write is declarative (IaC) only, not runtime state.
    ("terraform-architect", "tf_modules"),
    ("terraform-architect", "tf_live"),
    ("terraform-architect", "clusters"),
    # gitops-operator: desired state (releases, workloads, clusters_defined)
    ("gitops-operator", "releases"),
    ("gitops-operator", "workloads"),
    ("gitops-operator", "clusters_defined"),
    # gaia-operator: integrations and installation registry
    ("gaia-operator", "integrations"),
    ("gaia-operator", "gaia_installations"),
    # cloud-troubleshooter: observed cluster state (read-heavy, write declarative)
    ("cloud-troubleshooter", "clusters"),
]


def _default_db_path() -> Path:
    """Resolve default DB path via gaia.paths if available; fallback to ~/.gaia/gaia.db."""
    try:
        from gaia.paths import db_path
        return db_path()
    except ImportError:
        return Path.home() / ".gaia" / "gaia.db"


def run(db_path: Path, dry_run: bool) -> int:
    """Insert missing rows into agent_permissions. Returns exit code (0 = success)."""
    if not db_path.exists():
        print(f"ERROR: DB not found at {db_path}. Is B1 applied?", file=sys.stderr)
        return 1

    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        # Verify table exists
        table_exists = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='agent_permissions'"
        ).fetchone()
        if table_exists is None:
            print(
                "ERROR: agent_permissions table not found. Is B1 schema applied?",
                file=sys.stderr,
            )
            return 1

        # Load current rows
        existing = {
            (row["table_name"], row["agent_name"])
            for row in con.execute(
                "SELECT table_name, agent_name FROM agent_permissions"
            ).fetchall()
        }

        to_insert = []
        already_present = []
        for agent, table in AGENT_TABLE_MAPPING:
            key = (table, agent)
            if key in existing:
                already_present.append((agent, table))
            else:
                to_insert.append((agent, table))

        # Report already-present rows
        if already_present:
            print(f"Already present ({len(already_present)} rows):")
            for agent, table in sorted(already_present):
                print(f"  {agent} -> {table}")

        # Report rows to insert
        if to_insert:
            print(f"{'[DRY-RUN] Would insert' if dry_run else 'Inserting'} ({len(to_insert)} rows):")
            for agent, table in sorted(to_insert):
                print(f"  {agent} -> {table}")
        else:
            print("Nothing to insert -- all rows already present.")

        if dry_run:
            print("[DRY-RUN] No changes written.")
            return 0

        # Execute inserts
        for agent, table in to_insert:
            con.execute(
                "INSERT OR IGNORE INTO agent_permissions (table_name, agent_name, allow_write) "
                "VALUES (?, ?, 1)",
                (table, agent),
            )
        con.commit()

        total = len(AGENT_TABLE_MAPPING)
        inserted = len(to_insert)
        skipped = len(already_present)
        print(f"Done: {inserted} inserted, {skipped} already present, {total} total expected.")
        return 0

    except sqlite3.Error as exc:
        print(f"ERROR: SQLite error: {exc}", file=sys.stderr)
        return 1
    finally:
        con.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Show what would be inserted without writing.",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=None,
        help="Explicit path to gaia.db (default: ~/.gaia/gaia.db).",
    )
    args = parser.parse_args()

    db_path = args.db_path if args.db_path is not None else _default_db_path()
    print(f"DB path: {db_path}")

    sys.exit(run(db_path, args.dry_run))


if __name__ == "__main__":
    main()
