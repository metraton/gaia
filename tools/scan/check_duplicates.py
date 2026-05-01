"""
check_duplicates.py -- AC-6 duplicate detection for B5.

Checks that no two rows in the `projects` table share the same `identity`
value (i.e., no duplicate workspace identities after rescan).

Exit codes:
    0  No duplicates found
    1  Duplicates found (prints details)

Note: `gaia project merge --dry-run --report-duplicates` does not exist in the
CLI (the merge subcommand takes explicit <from_id> <to_id> arguments). This
script implements the equivalent check directly against the DB.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

_GAIA_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_GAIA_ROOT) not in sys.path:
    sys.path.insert(0, str(_GAIA_ROOT))


def check_duplicates(db_path: Path) -> int:
    con = sqlite3.connect(str(db_path))
    try:
        rows = con.execute(
            """
            SELECT identity, COUNT(*) as cnt, GROUP_CONCAT(name, ', ') as names
            FROM projects
            WHERE identity IS NOT NULL
            GROUP BY identity
            HAVING cnt > 1
            """
        ).fetchall()

        all_projects = con.execute(
            "SELECT name, identity FROM projects ORDER BY name"
        ).fetchall()

        print(f"Projects in DB ({len(all_projects)}):")
        for name, identity in all_projects:
            print(f"  name={name!r}  identity={identity!r}")

        if not rows:
            print("0 duplicates")
            return 0

        print(f"\nDUPLICATES FOUND ({len(rows)}):")
        for identity, cnt, names in rows:
            print(f"  identity={identity!r}  count={cnt}  names=[{names}]")
        return 1
    finally:
        con.close()


def main() -> int:
    from gaia.paths import db_path
    return check_duplicates(db_path())


if __name__ == "__main__":
    sys.exit(main())
