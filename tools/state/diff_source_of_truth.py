#!/usr/bin/env python3
"""
diff_source_of_truth.py -- Compare Python SSOT against DB CHECK constraints.

For each (table, column) registered in ``gaia.state.STATE_MACHINE_REGISTRY``,
this tool extracts the CHECK clause text from ``sqlite_master.sql`` and
compares the canonical value set declared in Python with the values listed
in the SQL clause. Any difference is emitted as a unified-style diff.

Output behaviour matches AC-5 of the gaia-state-machines brief:

  * When Python and DB agree, the artifact path is empty (zero bytes).
  * When they disagree, the artifact contains a human-readable diff
    pointing at the offending column and listing the divergent values.

Usage::

    python3 tools/state/diff_source_of_truth.py
    python3 tools/state/diff_source_of_truth.py --db /path/to/gaia.db
    python3 tools/state/diff_source_of_truth.py --out /tmp/source_of_truth_check.diff

Exit codes:
    0 -- Python and DB agree (or empty diff written).
    1 -- divergence detected; non-empty diff written.
    2 -- preflight failed (DB missing, table missing, regex failed).
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

# Ensure the gaia package is importable
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from gaia.state import STATE_MACHINE_REGISTRY  # noqa: E402


DEFAULT_DB = Path.home() / ".gaia" / "gaia.db"
DEFAULT_OUT = Path("/tmp/source_of_truth_check.diff")


def _extract_check_values(table_sql: str, column: str) -> list[str] | None:
    """Pull the IN (...) literal values out of a CHECK clause for ``column``.

    Returns None when no CHECK clause is found. Returns ``[]`` (empty list)
    when a CHECK exists but no IN list could be parsed -- an edge case we
    surface upstream instead of silently treating it as a match.
    """
    if "CHECK" not in (table_sql or "").upper():
        return None
    # Match: column IN ('a', 'b', 'c'). Allow IS NULL OR prefix.
    pattern = re.compile(
        rf"\b{re.escape(column)}\s+IN\s*\(\s*(.*?)\s*\)",
        re.IGNORECASE | re.DOTALL,
    )
    match = pattern.search(table_sql)
    if not match:
        return []
    inner = match.group(1)
    values = re.findall(r"'((?:[^']|'')*)'", inner)
    # Un-escape doubled single-quotes.
    return [v.replace("''", "'") for v in values]


def _diff_one(table: str, column: str, db_values: list[str], py_values: tuple[str, ...]) -> list[str]:
    """Return a list of diff lines, or [] when the sets agree."""
    py_set = set(py_values)
    if db_values is None:
        return [f"--- {table}.{column} (Python SSOT)",
                f"+++ {table}.{column} (DB CHECK)",
                f"  Python: {sorted(py_set)}",
                "  DB    : (no CHECK constraint found)"]
    db_set = set(db_values)
    if db_set == py_set:
        return []
    only_py = sorted(py_set - db_set)
    only_db = sorted(db_set - py_set)
    out = [
        f"--- {table}.{column} (Python SSOT)",
        f"+++ {table}.{column} (DB CHECK)",
        f"  Python: {sorted(py_set)}",
        f"  DB    : {sorted(db_set)}",
    ]
    if only_py:
        out.append(f"  + only in Python: {only_py}")
    if only_db:
        out.append(f"  - only in DB    : {only_db}")
    return out


def _build_diff(con: sqlite3.Connection) -> str:
    rows = con.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='table' "
        "AND name IN ('episodes','briefs','plans','tasks')"
    ).fetchall()
    sqls = {r[0]: (r[1] or "") for r in rows}

    diff_chunks: list[str] = []
    for (table, column), tup in STATE_MACHINE_REGISTRY.items():
        table_sql = sqls.get(table, "")
        db_values = _extract_check_values(table_sql, column)
        chunk = _diff_one(table, column, db_values, tup)
        if chunk:
            diff_chunks.extend(chunk)
            diff_chunks.append("")
    return "\n".join(diff_chunks).rstrip() + ("\n" if diff_chunks else "")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB,
                        help=f"Path to gaia.db (default: {DEFAULT_DB})")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT,
                        help=f"Diff artifact path (default: {DEFAULT_OUT})")
    parser.add_argument("--quiet", action="store_true",
                        help="Suppress stdout summary; only writes the artifact")
    args = parser.parse_args()

    if not args.db.exists():
        print(f"DB not found: {args.db}", file=sys.stderr)
        return 2

    con = sqlite3.connect(str(args.db))
    try:
        diff_text = _build_diff(con)
    finally:
        con.close()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(diff_text)

    if not args.quiet:
        if diff_text:
            print(f"Drift detected; diff written to {args.out}")
            print(diff_text)
        else:
            print(f"No drift; empty artifact written to {args.out}")

    return 1 if diff_text else 0


if __name__ == "__main__":
    sys.exit(main())
