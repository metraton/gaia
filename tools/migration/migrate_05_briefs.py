#!/usr/bin/env python3
"""Convert a directory of briefs into a SQL file of INSERT OR IGNORE statements.

It never opens the database. Each subdirectory of --src is one brief and only
its brief.md is read; a subdirectory without one is skipped and listed in the
SQL header. The name is the directory name without its open_/closed_ prefix.

Status comes from the brief.md frontmatter when it says complete, completed or
closed (closed), draft or open; otherwise from the prefix: closed_ is closed,
open_ and any other name are draft. surface_type and topic_key also come from
the frontmatter. The H1 is the title; the objective, context, approach and
out-of-scope columns come from the matching H2 sections.

The `project` column name predates the workspace rename: schema.sql now keys
briefs by `workspace` (NOT NULL, UNIQUE with name), which this INSERT does not
set, so against that schema OR IGNORE skips every row.

Options: --project (default 'me'), --src (required), --out (default
/tmp/migrate_05_briefs.sql) and --fragment (INSERTs only, no BEGIN/COMMIT).
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

DEFAULT_PROJECT = "me"
DEFAULT_OUT = Path("/tmp/migrate_05_briefs.sql")

# Both timestamps are set from the brief directory's mtime instead of the
# table's DEFAULT, to keep the original date.
COLUMNS = [
    "project",
    "name",
    "status",
    "surface_type",
    "title",
    "objective",
    "context",
    "approach",
    "out_of_scope",
    "topic_key",
    "created_at",
    "updated_at",
]


def sql_quote(value) -> str:
    if value is None:
        return "NULL"
    s = str(value)
    return "'" + s.replace("'", "''") + "'"


def parse_frontmatter(text: str):
    """Return (frontmatter, body), reading only top-level `key: value` lines."""
    if not text.startswith("---"):
        return {}, text

    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text

    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break
    if end_idx is None:
        return {}, text

    front = {}
    for raw in lines[1:end_idx]:
        if not raw or raw.startswith(" ") or raw.startswith("\t"):
            continue
        if ":" not in raw:
            continue
        key, _, val = raw.partition(":")
        key = key.strip()
        val = val.strip()
        if not val:
            continue
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        front[key] = val

    body = "\n".join(lines[end_idx + 1 :])
    if body.startswith("\n"):
        body = body[1:]
    return front, body


def file_iso_mtime(p: Path) -> str:
    import datetime as _dt

    ts = p.stat().st_mtime
    return _dt.datetime.utcfromtimestamp(ts).strftime("%Y-%m-%dT%H:%M:%SZ")


def split_brief_sections(body: str):
    """Return (title, sections): the first H1 and the text of the recognized H2 sections."""
    title = None
    sections = {
        "objective": None,
        "context": None,
        "approach": None,
        "out_of_scope": None,
    }

    h2_map = {
        "objective": "objective",
        "objetivo": "objective",
        "context": "context",
        "contexto": "context",
        "approach": "approach",
        "scope": "approach",
        "out of scope": "out_of_scope",
        "out-of-scope": "out_of_scope",
        "fuera de alcance": "out_of_scope",
    }

    lines = body.splitlines()
    current_key = None
    current_buf = []

    def flush():
        nonlocal current_key, current_buf
        if current_key and current_buf:
            text = "\n".join(current_buf).strip()
            if text:
                sections[current_key] = text
        current_key = None
        current_buf = []

    for line in lines:
        m1 = re.match(r"^#\s+(.+?)\s*$", line)
        if m1 and title is None:
            title = m1.group(1).strip()
            flush()
            continue
        m2 = re.match(r"^##\s+(.+?)\s*$", line)
        if m2:
            flush()
            heading = m2.group(1).strip().lower()
            heading_clean = re.sub(r"^\d+[\.\)]\s*", "", heading)
            mapped = h2_map.get(heading_clean)
            if mapped:
                current_key = mapped
                current_buf = []
            else:
                current_key = None
            continue
        if current_key:
            current_buf.append(line)

    flush()
    return title, sections


def derive_status(dir_name: str, front: dict) -> str:
    """Frontmatter status wins (complete, completed, closed, draft, open); else the directory prefix; else draft."""
    fm = (front.get("status") or "").strip().lower()
    if fm in ("complete", "completed", "closed"):
        return "closed"
    if fm in ("draft", "open"):
        return fm if fm == "draft" else "open"
    if dir_name.startswith("closed_"):
        return "closed"
    if dir_name.startswith("open_"):
        return "draft"
    return "draft"


def derive_bare_name(dir_name: str) -> str:
    """Strip a leading 'closed_' or 'open_' from the directory name."""
    for prefix in ("closed_", "open_"):
        if dir_name.startswith(prefix):
            return dir_name[len(prefix):]
    return dir_name


def extract_brief(brief_dir: Path, project: str):
    """Build the row for one brief directory, or None when it has no brief.md."""
    brief_md = brief_dir / "brief.md"
    if not brief_md.exists():
        return None

    text = brief_md.read_text(encoding="utf-8")
    front, body = parse_frontmatter(text)
    title, sections = split_brief_sections(body)

    name = derive_bare_name(brief_dir.name)
    status = derive_status(brief_dir.name, front)
    surface_type = front.get("surface_type")
    topic_key = front.get("topic_key")

    dir_mtime = file_iso_mtime(brief_dir)

    return {
        "project": project,
        "name": name,
        "status": status,
        "surface_type": surface_type,
        "title": title,
        "objective": sections.get("objective"),
        "context": sections.get("context"),
        "approach": sections.get("approach"),
        "out_of_scope": sections.get("out_of_scope"),
        "topic_key": topic_key,
        "created_at": dir_mtime,
        "updated_at": dir_mtime,
    }


def row_values_sql(row: dict) -> str:
    return "(" + ",".join(sql_quote(row.get(col)) for col in COLUMNS) + ")"


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate INSERT SQL for briefs table.")
    parser.add_argument("--project", default=DEFAULT_PROJECT)
    parser.add_argument("--src", required=True, help="briefs/ dir (each subdir is a brief)")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--fragment", action="store_true")
    args = parser.parse_args()

    project = args.project
    src_dir = Path(args.src)
    out = Path(args.out)
    fragment = args.fragment

    if not src_dir.exists():
        print(f"[migrate_05:{project}] ERROR: source dir not found: {src_dir}", file=sys.stderr)
        return 1

    rows = []
    skipped = []
    dirs = sorted(p for p in src_dir.iterdir() if p.is_dir())

    for d in dirs:
        row = extract_brief(d, project)
        if row is None:
            skipped.append(d.name)
            continue
        rows.append(row)

    cols_csv = ",".join(COLUMNS)
    insert_prefix = f"INSERT OR IGNORE INTO briefs ({cols_csv}) VALUES\n"

    with out.open("w", encoding="utf-8") as fh:
        fh.write(f"-- Generated by migrate_05_briefs.py (idempotent)\n")
        fh.write(f"-- Project: {project}\n")
        fh.write(f"-- Source dir: {src_dir}\n")
        fh.write(f"-- Brief dirs considered: {len(dirs)}\n")
        fh.write(f"-- Records to insert: {len(rows)}\n")
        fh.write(f"-- Skipped (no brief.md): {len(skipped)} -> {skipped}\n")
        if not fragment:
            fh.write("BEGIN TRANSACTION;\n")
        if rows:
            fh.write(insert_prefix)
            fh.write(",\n".join(row_values_sql(r) for r in rows))
            fh.write(";\n")
        if not fragment:
            fh.write("COMMIT;\n")

    print(f"[migrate_05:{project}] wrote {out} ({len(rows)} rows, {len(skipped)} skipped)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
