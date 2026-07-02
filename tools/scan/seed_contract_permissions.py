"""
seed_contract_permissions.py -- Populate agent_contract_permissions from agent frontmatters.

Reads the `project_context_contracts` field declared in each agent's YAML
frontmatter and inserts rows into the `agent_contract_permissions` table.

Called during `gaia install` after bootstrap_database.sh has applied migrations
and created the table.  Idempotent: uses INSERT OR REPLACE so repeated runs
converge to the same state without duplicates.

Contract schema
---------------
agent frontmatter:
  project_context_contracts:
    read: [contract_a, contract_b]
    write: [contract_a]          # may overlap with read

Resulting rows (cloud_scope=NULL for this initial seed):
  agent_name     | contract_name | can_read | can_write
  ---------------+---------------+----------+-----------
  <agent>        | contract_a    |    1     |     1      <- in both read+write
  <agent>        | contract_b    |    1     |     0      <- in read only

Usage:
    python3 seed_contract_permissions.py [--db-path PATH] [--agents-dir PATH] [--dry-run]
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_AGENTS_DIR = _REPO_ROOT / "agents"


# ---------------------------------------------------------------------------
# Frontmatter parser
# ---------------------------------------------------------------------------

def _parse_frontmatter(text: str) -> dict[str, Any]:
    """Extract the YAML frontmatter block from a Markdown agent file.

    Supports the nested `project_context_contracts:` field (a mapping with
    `read` and `write` sub-keys containing lists).  Falls back to an empty
    dict when the file has no frontmatter or when parsing fails.

    Uses pyyaml when available; otherwise falls back to a minimal bespoke
    parser that covers the structures present in agents/*.md.
    """
    if not text.startswith("---"):
        return {}

    try:
        end_marker = text.index("---", 3)
    except ValueError:
        return {}

    fm_block = text[3:end_marker].strip()
    if not fm_block:
        return {}

    try:
        import yaml  # type: ignore
        return yaml.safe_load(fm_block) or {}
    except ImportError:
        pass

    # Minimal fallback parser -- handles the subset of YAML used in agents/*.md.
    # Specifically supports:
    #   key: scalar_value
    #   key:
    #     - item
    #   parent_key:
    #     child_key: [item1, item2]
    #     child_key:
    #       - item
    result: dict[str, Any] = {}
    current_top_key: str | None = None
    current_top_list: list[str] | None = None
    current_sub_key: str | None = None
    current_sub_list: list[str] | None = None
    current_sub_map: dict[str, Any] | None = None

    def _flush_sub() -> None:
        nonlocal current_sub_key, current_sub_list
        if current_top_key and current_sub_map is not None and current_sub_key is not None:
            if current_sub_list is not None:
                current_sub_map[current_sub_key] = current_sub_list
        current_sub_key = None
        current_sub_list = None

    def _flush_top() -> None:
        nonlocal current_top_key, current_top_list, current_sub_key, current_sub_list, current_sub_map
        _flush_sub()
        if current_top_key is not None:
            if current_sub_map is not None:
                result[current_top_key] = current_sub_map
            elif current_top_list is not None:
                result[current_top_key] = current_top_list
        current_top_key = None
        current_top_list = None
        current_sub_map = None

    for line in fm_block.splitlines():
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())

        if not stripped or stripped.startswith("#"):
            continue

        # Top-level list item (indent 0 or 2 under a top-level list key)
        if stripped.startswith("- ") and indent <= 2 and current_top_list is not None:
            current_top_list.append(stripped[2:].strip())
            continue

        # Sub-level list item (indent > 2, under a sub-key within a map)
        if stripped.startswith("- ") and indent > 2 and current_sub_list is not None:
            current_sub_list.append(stripped[2:].strip())
            continue

        if ":" in stripped:
            key, _, value = stripped.partition(":")
            key = key.strip()
            value = value.strip()

            if indent == 0:
                # New top-level key
                _flush_top()
                current_top_key = key
                if value:
                    result[key] = value
                    current_top_key = key
                    current_top_list = None
                    current_sub_map = None
                else:
                    current_top_list = None
                    current_sub_map = None  # will be set when we see sub-keys

            elif indent > 0 and current_top_key is not None:
                # Sub-key under current top-level mapping
                _flush_sub()
                if current_sub_map is None:
                    current_sub_map = {}
                    current_top_list = None  # switch top from list to map mode
                current_sub_key = key
                if value.startswith("[") and value.endswith("]"):
                    # Inline list: [a, b, c]
                    inner = value[1:-1].strip()
                    items = [s.strip() for s in inner.split(",") if s.strip()] if inner else []
                    current_sub_map[key] = items
                    current_sub_key = None
                    current_sub_list = None
                elif value:
                    current_sub_map[key] = value
                    current_sub_key = None
                    current_sub_list = None
                else:
                    current_sub_list = []

    _flush_top()
    return result


# ---------------------------------------------------------------------------
# Core seeder
# ---------------------------------------------------------------------------

def _extract_contract_grants(frontmatter: dict[str, Any]) -> list[tuple[str, int, int]]:
    """Return (contract_name, can_read, can_write) tuples from a frontmatter dict.

    Contracts listed in `read` get can_read=1.  Contracts listed in `write`
    get can_write=1 and can_read=1 (write implies read access).  Contracts
    appearing in both lists are merged into a single row with both flags set.
    """
    pcc = frontmatter.get("project_context_contracts")
    if not pcc or not isinstance(pcc, dict):
        return []

    read_contracts: list[str] = pcc.get("read") or []
    write_contracts: list[str] = pcc.get("write") or []

    # Build per-contract flags, normalising to lists.
    if isinstance(read_contracts, str):
        read_contracts = [read_contracts]
    if isinstance(write_contracts, str):
        write_contracts = [write_contracts]

    grants: dict[str, tuple[int, int]] = {}
    for c in read_contracts:
        c = c.strip()
        if c:
            r, w = grants.get(c, (0, 0))
            grants[c] = (1, w)

    for c in write_contracts:
        c = c.strip()
        if c:
            r, w = grants.get(c, (0, 0))
            grants[c] = (max(r, 1), 1)  # write implies read

    return [(contract, r, w) for contract, (r, w) in sorted(grants.items())]


def grant_contract_permissions(db_path: Path, agents_dir: Path | None = None) -> dict[str, int]:
    """Seed agent_contract_permissions from agents/*.md frontmatters.

    Returns a summary dict with keys: ``agents_processed``, ``rows_inserted``,
    ``rows_skipped`` (agents with no project_context_contracts field).
    """
    if agents_dir is None:
        agents_dir = _DEFAULT_AGENTS_DIR

    agent_files = sorted(agents_dir.glob("*.md"))
    if not agent_files:
        raise FileNotFoundError(f"No agent .md files found in {agents_dir}")

    rows_total = 0
    rows_skipped = 0

    con = sqlite3.connect(str(db_path))
    try:
        con.execute("BEGIN")

        for agent_file in agent_files:
            if agent_file.name == "README.md":
                continue

            text = agent_file.read_text(encoding="utf-8")
            frontmatter = _parse_frontmatter(text)

            agent_name = frontmatter.get("name") or agent_file.stem
            grants = _extract_contract_grants(frontmatter)

            if not grants:
                rows_skipped += 1
                continue

            # Idempotency guard: INSERT OR REPLACE does NOT dedupe NULL-scope
            # rows, because SQLite treats NULL as distinct in the composite
            # PRIMARY KEY (agent_name, contract_name, cloud_scope) -- NULL never
            # equals NULL, so no ON CONFLICT fires and every re-run APPENDS a
            # fresh duplicate. Left unchecked this accumulated ~1977 duplicate
            # rows per (agent, contract) in the field. Clear this agent's
            # NULL-scope rows first so a re-seed fully replaces them; any
            # provider-scoped overlays (cloud_scope IS NOT NULL) are untouched.
            con.execute(
                "DELETE FROM agent_contract_permissions "
                "WHERE agent_name = ? AND cloud_scope IS NULL",
                (agent_name,),
            )

            for contract_name, can_read, can_write in grants:
                con.execute(
                    "INSERT OR REPLACE INTO agent_contract_permissions "
                    "(agent_name, contract_name, can_read, can_write, cloud_scope) "
                    "VALUES (?, ?, ?, ?, NULL)",
                    (agent_name, contract_name, can_read, can_write),
                )
                rows_total += 1

        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()

    return {
        "agents_processed": len(agent_files),
        "rows_inserted": rows_total,
        "rows_skipped": rows_skipped,
    }


# ---------------------------------------------------------------------------
# CLI entry point (for `gaia install` invocation and manual use)
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Seed agent_contract_permissions from agent frontmatters."
    )
    p.add_argument(
        "--db-path",
        default=str(Path("~/.gaia/gaia.db").expanduser()),
        help="Path to gaia.db (default: ~/.gaia/gaia.db)",
    )
    p.add_argument(
        "--agents-dir",
        default=str(_DEFAULT_AGENTS_DIR),
        help=f"Directory containing agent .md files (default: {_DEFAULT_AGENTS_DIR})",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Parse agents and print what would be inserted without writing to DB",
    )
    return p


def main() -> int:
    args = _build_parser().parse_args()
    db_path = Path(args.db_path).expanduser().resolve()
    agents_dir = Path(args.agents_dir).expanduser().resolve()

    if args.dry_run:
        agent_files = sorted(agents_dir.glob("*.md"))
        for agent_file in agent_files:
            if agent_file.name == "README.md":
                continue
            text = agent_file.read_text(encoding="utf-8")
            fm = _parse_frontmatter(text)
            agent_name = fm.get("name") or agent_file.stem
            grants = _extract_contract_grants(fm)
            if grants:
                print(f"[dry-run] {agent_name}:")
                for contract, r, w in grants:
                    print(f"  {contract}: can_read={r} can_write={w}")
        return 0

    try:
        summary = grant_contract_permissions(db_path=db_path, agents_dir=agents_dir)
        print(
            f"[seed_contract_permissions] done: "
            f"{summary['agents_processed']} agents, "
            f"{summary['rows_inserted']} rows inserted, "
            f"{summary['rows_skipped']} agents skipped (no field)"
        )
        return 0
    except Exception as exc:
        print(f"[seed_contract_permissions] ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
