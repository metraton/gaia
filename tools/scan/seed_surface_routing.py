"""
seed_surface_routing.py -- Populate surface_routing from agent frontmatters.

Reads the `routing` block declared in each agent's YAML frontmatter and
inserts one row per surface into the `surface_routing` table. This is the
mirror of seed_contract_permissions.py: same discovery (agents/*.md), same
frontmatter parse, same install-time invocation, same idempotency contract.

Together with seed_contract_permissions.py this replaces config/surface-routing.json:
the frontmatter is the single source of truth, the DB is the runtime SSOT, and
tools/context/surface_router.py reads the DB (never the retired JSON).

Called during `gaia install` after bootstrap_database.sh has applied migrations
and created the table. Idempotent: each surface row is DELETEd then re-inserted
so a re-seed converges (surface is the PRIMARY KEY, but an agent could rename
its surface, so a full clear-and-reinsert keyed by primary_agent is the safe
convergence).

Contract schema
---------------
agent frontmatter:
  name: developer
  project_context_contracts:
    read: [project_identity, stack, ...]     # becomes contract_sections
  routing:
    surface: app_ci_tooling
    adjacent_surfaces: [iac, gitops_desired_state]
    commands: [npm, ...]
    artifacts: [package.json, ...]
    required_checks: ["..."]
    sub_surfaces:                            # optional; only where a surface
      - name: brief                          # splits by sub-surface owner
        owner: gaia-orchestrator
        owner_skill: brief-spec

`keywords` is retired as a routing signal: tools/context/surface_router.py
scores surfaces from `commands` and `artifacts` only. No agent frontmatter
declares `keywords` anymore; a block that omits it seeds cleanly (the
`keywords_json` column keeps its schema default `'[]'`). The column itself
stays in the schema, deprecated rather than dropped, so an un-migrated
install or a stray legacy field does not crash the seeder or the loader --
it is simply never read by the matcher.

Resulting row:
  surface | primary_agent | adjacent_surfaces_json | contract_sections_json |
  required_checks_json | keywords_json (deprecated) | commands_json |
  artifacts_json | sub_surfaces_json

Usage:
    python3 seed_surface_routing.py [--db-path PATH] [--agents-dir PATH] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

try:
    # Reuse the exact frontmatter parser the permissions seeder uses so both
    # seeders read the frontmatter identically.
    from tools.scan.seed_contract_permissions import _parse_frontmatter
except ImportError:  # pragma: no cover - direct-invocation fallback
    from seed_contract_permissions import _parse_frontmatter  # type: ignore

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_AGENTS_DIR = _REPO_ROOT / "agents"


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def _as_list(value: Any) -> list[str]:
    """Coerce a frontmatter value into a clean list of strings."""
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if str(v).strip()]


def _extract_routing_row(frontmatter: dict[str, Any]) -> dict[str, Any] | None:
    """Return a surface_routing row dict from a frontmatter dict, or None.

    Agents without a `routing` block (e.g. gaia-orchestrator, which IS the
    router) are skipped.
    """
    routing = frontmatter.get("routing")
    if not routing or not isinstance(routing, dict):
        return None

    surface = str(routing.get("surface", "")).strip()
    if not surface:
        return None

    primary_agent = str(frontmatter.get("name", "")).strip()
    if not primary_agent:
        return None

    # contract_sections mirrors the agent's project_context_contracts.read --
    # a single source of truth for both the permissions seeder and the surface
    # context filter (see get_relevant_sections in context_provider.py).
    pcc = frontmatter.get("project_context_contracts") or {}
    read_contracts = _as_list(pcc.get("read")) if isinstance(pcc, dict) else []

    sub_surfaces = routing.get("sub_surfaces")
    sub_surfaces_json = (
        json.dumps(sub_surfaces) if isinstance(sub_surfaces, list) and sub_surfaces else None
    )

    return {
        "surface": surface,
        "primary_agent": primary_agent,
        "adjacent_surfaces_json": json.dumps(_as_list(routing.get("adjacent_surfaces"))),
        "contract_sections_json": json.dumps(read_contracts),
        "required_checks_json": json.dumps(_as_list(routing.get("required_checks"))),
        # Deprecated: no agent frontmatter declares `keywords` anymore (the
        # matcher in surface_router.py scores commands/artifacts only). Kept
        # so the column's NOT NULL DEFAULT '[]' is honored explicitly and an
        # absent field never crashes the insert.
        "keywords_json": json.dumps(_as_list(routing.get("keywords"))),
        "commands_json": json.dumps(_as_list(routing.get("commands"))),
        "artifacts_json": json.dumps(_as_list(routing.get("artifacts"))),
        "sub_surfaces_json": sub_surfaces_json,
    }


# ---------------------------------------------------------------------------
# Core seeder
# ---------------------------------------------------------------------------

def seed_surface_routing(db_path: Path, agents_dir: Path | None = None) -> dict[str, int]:
    """Seed surface_routing from agents/*.md frontmatters.

    Returns a summary dict: ``agents_processed``, ``surfaces_seeded``,
    ``agents_skipped`` (agents with no routing block).

    Idempotent: the table is cleared once, then each discovered surface is
    inserted, so a re-seed fully replaces prior rows without accumulating
    duplicates or leaving stale surfaces behind.
    """
    if agents_dir is None:
        agents_dir = _DEFAULT_AGENTS_DIR

    agent_files = sorted(agents_dir.glob("*.md"))
    if not agent_files:
        raise FileNotFoundError(f"No agent .md files found in {agents_dir}")

    rows: list[dict[str, Any]] = []
    agents_skipped = 0
    for agent_file in agent_files:
        if agent_file.name == "README.md":
            continue
        frontmatter = _parse_frontmatter(agent_file.read_text(encoding="utf-8"))
        row = _extract_routing_row(frontmatter)
        if row is None:
            agents_skipped += 1
            continue
        rows.append(row)

    con = sqlite3.connect(str(db_path))
    try:
        con.execute("BEGIN")
        # Full replace: a surface rename in frontmatter must not leave the old
        # surface row behind. surface is the PRIMARY KEY so a partial re-seed
        # would orphan renamed surfaces; clearing first is the safe converge.
        con.execute("DELETE FROM surface_routing")
        for row in rows:
            con.execute(
                "INSERT INTO surface_routing "
                "(surface, primary_agent, adjacent_surfaces_json, contract_sections_json, "
                " required_checks_json, keywords_json, commands_json, artifacts_json, "
                " sub_surfaces_json) "
                "VALUES (:surface, :primary_agent, :adjacent_surfaces_json, "
                ":contract_sections_json, :required_checks_json, :keywords_json, "
                ":commands_json, :artifacts_json, :sub_surfaces_json)",
                row,
            )
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()

    return {
        "agents_processed": len(agent_files),
        "surfaces_seeded": len(rows),
        "agents_skipped": agents_skipped,
    }


# ---------------------------------------------------------------------------
# CLI entry point (for `gaia install` invocation and manual use)
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Seed surface_routing from agent frontmatters."
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
        for agent_file in sorted(agents_dir.glob("*.md")):
            if agent_file.name == "README.md":
                continue
            fm = _parse_frontmatter(agent_file.read_text(encoding="utf-8"))
            row = _extract_routing_row(fm)
            if row:
                print(f"[dry-run] {row['primary_agent']} -> surface '{row['surface']}'")
                print(f"  commands={json.loads(row['commands_json'])}")
                print(f"  artifacts={json.loads(row['artifacts_json'])}")
                print(f"  contract_sections={json.loads(row['contract_sections_json'])}")
        return 0

    try:
        summary = seed_surface_routing(db_path=db_path, agents_dir=agents_dir)
        print(
            f"[seed_surface_routing] done: "
            f"{summary['agents_processed']} agents, "
            f"{summary['surfaces_seeded']} surfaces seeded, "
            f"{summary['agents_skipped']} agents skipped (no routing block)"
        )
        return 0
    except Exception as exc:
        print(f"[seed_surface_routing] ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
