#!/usr/bin/env python3
"""
migrate_05_briefs.py

Convierte directorios de briefs (cada uno con brief.md / plan.md / handoff.md /
announcement.md) -> archivo SQL con INSERT OR IGNORE.

Reglas:
  - Solo I/O sobre filesystem.
  - NO importa sqlite3.
  - Idempotente: UNIQUE (project, name) -> INSERT OR IGNORE.
  - Cada subdirectorio del briefs/ source es UN brief.
  - Convención dir: '<status_prefix>_<bare-name>' donde status_prefix ∈ {open, closed}.
      * 'closed_*' -> status='closed'
      * 'open_*'   -> status='draft' (la mayoría de los open_* en DB son 'draft')
  - Frontmatter status (si existe en brief.md) override:
      * 'complete' o 'closed' -> 'closed'
      * 'draft'   -> 'draft'
      * 'open'    -> 'open'

Schema columnas (briefs):
  project, name, status, surface_type, title, objective, context,
  approach, out_of_scope, topic_key, created_at, updated_at
  (id es AUTOINCREMENT, NO se inserta. created_at/updated_at tienen DEFAULT.)

CLI args:
  --project   workspace name (default: 'me')
  --src       directorio de briefs (cada subdir es un brief)
  --out       path al SQL de salida (default: /tmp/migrate_05_briefs.sql)
  --fragment  emite solo INSERTs (sin BEGIN/COMMIT)
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

DEFAULT_PROJECT = "me"
DEFAULT_SRC_DIR = Path("/home/jorge/ws/me/briefs")
DEFAULT_OUT = Path("/tmp/migrate_05_briefs.sql")

# `created_at` y `updated_at` tienen DEFAULT en el schema; los pasamos
# explicitamente desde el mtime del directorio para preservar señal histórica.
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
    """Devuelve (front: dict, body: str). YAML mínimo key:value."""
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
        # Solo capturamos pares simples key: value en la raíz; ignoramos listas
        # anidadas (acceptance_criteria, etc.) que no se mapean al schema.
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
    """
    Extrae secciones markdown estándar -> dict.
    Buscamos H1 (# Title) y H2 (## Section).
    """
    title = None
    sections = {
        "objective": None,
        "context": None,
        "approach": None,
        "out_of_scope": None,
    }

    # Headers H2 -> nombre canónico de columna
    h2_map = {
        "objective": "objective",
        "objetivo": "objective",
        "context": "context",
        "contexto": "context",
        "approach": "approach",
        "scope": "approach",  # algunos briefs usan 'Scope' como approach
        "out of scope": "out_of_scope",
        "out-of-scope": "out_of_scope",
        "fuera de alcance": "out_of_scope",
    }

    # Particionar por headers
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
        # H1
        m1 = re.match(r"^#\s+(.+?)\s*$", line)
        if m1 and title is None:
            title = m1.group(1).strip()
            flush()
            continue
        # H2
        m2 = re.match(r"^##\s+(.+?)\s*$", line)
        if m2:
            flush()
            heading = m2.group(1).strip().lower()
            # quita números/prefijos como "1. " "2. "
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
    """
    Status mapping:
      - frontmatter status='complete'|'closed' -> 'closed'
      - frontmatter status='draft' -> 'draft'
      - frontmatter status='open'  -> 'open'
      - else por prefijo dir:
          'closed_*' -> 'closed'
          'open_*'   -> 'draft'  (consistente con DB existente)
          fallback   -> 'draft'
    """
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
    """Quita prefijo 'open_' / 'closed_' del directorio."""
    for prefix in ("closed_", "open_"):
        if dir_name.startswith(prefix):
            return dir_name[len(prefix):]
    return dir_name


def extract_brief(brief_dir: Path, project: str):
    """
    Lee un directorio de brief. Source canónico: brief.md.
    Si no existe brief.md, retorna None y se skipea.
    """
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

    # timestamps from dir mtime (best-effort)
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
    parser.add_argument("--src", default=str(DEFAULT_SRC_DIR), help="briefs/ dir (each subdir is a brief)")
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
