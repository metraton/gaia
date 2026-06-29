#!/usr/bin/env python3
"""Scratch triage: map recheck-survivor job_ids to line/operator (read-only)."""
import json
import sqlite3
import sys

results = sys.argv[1] if len(sys.argv) > 1 else "tests/evals/results/blocked-survivors-recheck.json"
d = json.load(open(results))
surv = [j for j, o in d.items() if str(o).upper() == "SURVIVED"]
c = sqlite3.connect("file:blocked-commands.sqlite?mode=ro", uri=True)
rows = []
for j in surv:
    r = c.execute(
        "SELECT definition_name, operator_name, start_pos_row, start_pos_col, occurrence "
        "FROM mutation_specs WHERE job_id=?",
        (j,),
    ).fetchone()
    rows.append((r[2], r[3], r[0], r[1], r[4], j))
rows.sort()
print(f"{len(surv)} survived")
for row in rows:
    print(f"L{row[0]}:{row[1]}\t{row[2]}\t{row[3]}\tocc{row[4]}\t{row[5]}")
