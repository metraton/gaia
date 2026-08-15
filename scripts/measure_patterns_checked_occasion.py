#!/usr/bin/env python3
"""Did the turn that demonstrably searched record WHAT it searched?

Answers one question against real rows: among turns that provably ran a
search, what share left ``evidence_report.patterns_checked`` empty?

Why the population is narrowed this way
---------------------------------------
An earlier census ran over "all partially-filled rows" and read a step on
2026-07-31 as a behaviour change. It was not: an incremental-mirror commit
landed that night and the table began holding a row shape it had never held
before, so the series measured COMPOSITION, not conduct. The control that
survives that is to fix the denominator at turns that DEMONSTRABLY searched --
``COMPLETE``, not auto-captured, with a ``grep`` or an ``rg`` in
``commands_run`` -- and ask only of those whether the field is empty. Changing
this predicate makes a run incomparable to its own baseline.

Baselines this reproduces (measured 2026-08-14, restated here so a later run
can tell drift from a changed query): July 2026, 3 of 451 empty (0.7%);
August 2026 through the 14th, 84 of 178 empty (47.2%).

The metric is the percentage EMPTY, so it improves by going DOWN.

``patterns_checked`` values that are not arrays are counted apart rather than
folded into either side: three such rows exist (2026-07-31) and a SQL
``json_array_length`` aborts on them, which is why the parsing happens in
Python here.

Read-only by construction: the database is opened ``mode=ro`` and this script
writes nothing, anywhere.

Usage:
    python3 scripts/measure_patterns_checked_occasion.py
    python3 scripts/measure_patterns_checked_occasion.py --since 2026-08-15
    python3 scripts/measure_patterns_checked_occasion.py --by month --json

Exit codes:
    0  the scan ran (the metric itself is reported, never asserted here)
    2  internal error (no database, unreadable table)
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Word-bounded so a turn that merely mentions "regrep" or a path segment
# ending in "rg" does not enter the denominator of turns that searched.
_SEARCH_RE = re.compile(r"\b(?:grep|rg)\b")


def _err(msg: str) -> None:
    print(f"[patterns-checked] ERROR: {msg}", file=sys.stderr)
    sys.exit(2)


def _resolve_db(explicit):
    if explicit:
        return Path(explicit).expanduser()
    from gaia.paths import data_dir

    return Path(data_dir()) / "gaia.db"


def _searched(commands_run) -> bool:
    if not isinstance(commands_run, list):
        return False
    return any(
        _SEARCH_RE.search(entry) for entry in commands_run if isinstance(entry, str)
    )


def scan(db_path: Path, since=None, until=None, by="day") -> dict:
    if not db_path.exists():
        _err(f"no database at {db_path}")
    conn = None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, created_at, raw_handoff_json "
            "FROM agent_contract_handoffs "
            "WHERE agent_state = 'COMPLETE' ORDER BY id"
        ).fetchall()
    except sqlite3.Error as exc:
        _err(f"cannot read agent_contract_handoffs: {exc}")
    finally:
        if conn is not None:
            conn.close()

    width = 7 if by == "month" else 10
    buckets = {}
    totals = {"searched": 0, "empty": 0, "filled": 0, "malformed": 0}
    unparseable = 0

    for row in rows:
        # The window is always compared on the full date: a month bucket is
        # only 7 characters, and comparing that to a YYYY-MM-DD bound silently
        # admits the whole month.
        day = (row["created_at"] or "")[:10]
        if len(day) < 10:
            continue
        if since and day < since:
            continue
        if until and day > until:
            continue
        stamp = day[:width]
        try:
            envelope = json.loads(row["raw_handoff_json"] or "")
        except (json.JSONDecodeError, TypeError):
            unparseable += 1
            continue
        if not isinstance(envelope, dict) or envelope.get("auto_captured"):
            continue
        evidence = envelope.get("evidence_report")
        if not isinstance(evidence, dict):
            continue
        if not _searched(evidence.get("commands_run")):
            continue

        patterns = evidence.get("patterns_checked")
        if patterns is None or (isinstance(patterns, list) and not patterns):
            outcome = "empty"
        elif isinstance(patterns, list):
            outcome = "filled"
        else:
            outcome = "malformed"

        bucket = buckets.setdefault(
            stamp, {"searched": 0, "empty": 0, "filled": 0, "malformed": 0}
        )
        bucket["searched"] += 1
        bucket[outcome] += 1
        totals["searched"] += 1
        totals[outcome] += 1

    for bucket in buckets.values():
        bucket["pct_empty"] = round(100.0 * bucket["empty"] / bucket["searched"], 1)
    totals["pct_empty"] = (
        round(100.0 * totals["empty"] / totals["searched"], 1)
        if totals["searched"]
        else None
    )

    return {
        "db": str(db_path),
        "by": by,
        "since": since,
        "until": until,
        "unparseable_rows": unparseable,
        "totals": totals,
        "buckets": {k: buckets[k] for k in sorted(buckets)},
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Share of demonstrably-searching turns that left patterns_checked empty"
    )
    ap.add_argument("--db", default=None,
                    help="Path to gaia.db (default: gaia.paths.data_dir()/gaia.db)")
    ap.add_argument("--since", default=None, metavar="YYYY-MM-DD")
    ap.add_argument("--until", default=None, metavar="YYYY-MM-DD")
    ap.add_argument("--by", choices=("day", "month"), default="day")
    ap.add_argument("--json", action="store_true", help="Machine-readable summary")
    args = ap.parse_args()

    report = scan(_resolve_db(args.db), since=args.since, until=args.until, by=args.by)

    if args.json:
        print(json.dumps(report, indent=2))
        return 0

    print(f"[patterns-checked] db={report['db']}")
    print("  population: COMPLETE, not auto-captured, commands_run has grep|rg")
    print("  metric: percentage with patterns_checked EMPTY (lower is better)")
    print(f"  {'bucket':<12} {'searched':>9} {'empty':>7} {'filled':>7} {'malformed':>10} {'%empty':>8}")
    for stamp, counts in report["buckets"].items():
        print(
            f"  {stamp:<12} {counts['searched']:>9} {counts['empty']:>7} "
            f"{counts['filled']:>7} {counts['malformed']:>10} {counts['pct_empty']:>8}"
        )
    totals = report["totals"]
    print(
        f"  {'TOTAL':<12} {totals['searched']:>9} {totals['empty']:>7} "
        f"{totals['filled']:>7} {totals['malformed']:>10} {totals['pct_empty']:>8}"
    )
    print(f"  unparseable rows: {report['unparseable_rows']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
