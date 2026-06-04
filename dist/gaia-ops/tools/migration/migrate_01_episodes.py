#!/usr/bin/env python3
"""
migrate_01_episodes.py

Convierte episodes.jsonl -> archivo SQL con INSERT OR IGNORE batched.

Reglas:
  - Solo I/O sobre filesystem.
  - NO importa sqlite3.
  - Idempotente: usa INSERT OR IGNORE (PK = episode_id).
  - Campos JSON anidados (keywords, tags, clarifications, commands_executed,
    context.metrics) se serializan con json.dumps y se guardan como TEXT.

CLI args (parametrización cross-workspace):
  --project   workspace name (default: 'me')
  --src       path al episodes.jsonl (default: ws/me)
  --out       path al SQL de salida (default: /tmp/migrate_01_episodes.sql)
  --fragment  emite solo INSERTs (sin BEGIN/COMMIT) — para concatenar en master SQL
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# --- Defaults (compat invocaciones previas con 'me') ------------------------

DEFAULT_PROJECT = "me"
DEFAULT_SRC = Path("/home/jorge/ws/me/.claude/project-context/episodic-memory/episodes.jsonl")
DEFAULT_OUT = Path("/tmp/migrate_01_episodes.sql")
BATCH_SIZE = 80  # filas por sentencia VALUES (...)

# Orden y nombre de columnas en la tabla episodes (ver schema.sql).
COLUMNS = [
    "episode_id",
    "project",
    "timestamp",
    "session_id",
    "task_id",
    "agent",
    "type",
    "title",
    "prompt",
    "enriched_prompt",
    "wf_prompt",
    "clarifications",
    "keywords",
    "tags",
    "commands_executed",
    "context_metrics",
    "relevance_score",
    "outcome",
    "duration_seconds",
    "exit_code",
    "plan_status",
    "output_length",
    "output_tokens_approx",
]


def sql_quote(value) -> str:
    """Convierte un valor Python a literal SQL seguro para SQLite."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
            return "NULL"
        return str(value)
    s = str(value)
    return "'" + s.replace("'", "''") + "'"


def to_json_text(value):
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return value


def extract_row(record: dict, project: str) -> dict:
    context = record.get("context") or {}
    metrics = context.get("metrics") if isinstance(context, dict) else None

    return {
        "episode_id": record.get("episode_id"),
        "project": project,
        "timestamp": record.get("timestamp"),
        "session_id": record.get("session_id"),
        "task_id": record.get("task_id"),
        "agent": record.get("agent"),
        "type": record.get("type"),
        "title": record.get("title"),
        "prompt": record.get("prompt"),
        "enriched_prompt": record.get("enriched_prompt"),
        "wf_prompt": record.get("wf_prompt"),
        "clarifications": to_json_text(record.get("clarifications")),
        "keywords": to_json_text(record.get("keywords")),
        "tags": to_json_text(record.get("tags")),
        "commands_executed": to_json_text(record.get("commands_executed")),
        "context_metrics": to_json_text(metrics),
        "relevance_score": record.get("relevance_score"),
        "outcome": record.get("outcome"),
        "duration_seconds": record.get("duration_seconds"),
        "exit_code": record.get("exit_code"),
        "plan_status": record.get("plan_status"),
        "output_length": record.get("output_length"),
        "output_tokens_approx": record.get("output_tokens_approx"),
    }


def row_values_sql(row: dict) -> str:
    parts = [sql_quote(row.get(col)) for col in COLUMNS]
    return "(" + ",".join(parts) + ")"


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate INSERT SQL for episodes table.")
    parser.add_argument("--project", default=DEFAULT_PROJECT, help="workspace project name")
    parser.add_argument("--src", default=str(DEFAULT_SRC), help="path to episodes.jsonl")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="path to output .sql")
    parser.add_argument("--fragment", action="store_true",
                        help="emit only INSERT statements (no BEGIN/COMMIT) for master concatenation")
    args = parser.parse_args()

    project = args.project
    src = Path(args.src)
    out = Path(args.out)
    fragment = args.fragment

    if not src.exists():
        print(f"[migrate_01:{project}] ERROR: source not found: {src}", file=sys.stderr)
        return 1

    cols_csv = ",".join(COLUMNS)
    insert_prefix = f"INSERT OR IGNORE INTO episodes ({cols_csv}) VALUES\n"

    rows = []
    skipped = 0
    total_lines = 0

    with src.open("r", encoding="utf-8") as f:
        for line in f:
            total_lines += 1
            s = line.strip()
            if not s:
                continue
            try:
                rec = json.loads(s)
            except json.JSONDecodeError:
                skipped += 1
                continue
            if not rec.get("episode_id"):
                skipped += 1
                continue
            rows.append(extract_row(rec, project))

    with out.open("w", encoding="utf-8") as fh:
        fh.write(f"-- Generated by migrate_01_episodes.py (idempotent)\n")
        fh.write(f"-- Project: {project}\n")
        fh.write(f"-- Source:  {src}\n")
        fh.write(f"-- Total source lines: {total_lines}\n")
        fh.write(f"-- Records to insert:  {len(rows)}\n")
        fh.write(f"-- Skipped:            {skipped}\n")
        if not fragment:
            fh.write("BEGIN TRANSACTION;\n")

        for i in range(0, len(rows), BATCH_SIZE):
            batch = rows[i : i + BATCH_SIZE]
            fh.write(insert_prefix)
            values_sqls = [row_values_sql(r) for r in batch]
            fh.write(",\n".join(values_sqls))
            fh.write(";\n")

        if not fragment:
            fh.write("COMMIT;\n")

    print(f"[migrate_01:{project}] wrote {out} ({len(rows)} rows, {skipped} skipped)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
