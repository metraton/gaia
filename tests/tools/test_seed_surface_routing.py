"""Tests for seed_surface_routing (mirror of test_seed_contract_permissions).

Verifies the generator reads a `routing:` frontmatter block and seeds one
surface_routing row per agent, that contract_sections mirrors
project_context_contracts.read (single source of truth), that agents without a
routing block are skipped, and that re-seeding is idempotent (full replace, no
stale surfaces).

Keywords are retired as a routing signal: no fixture below declares a
`keywords:` field, matching the real agents/*.md frontmatters, and
test_seeds_one_row_per_routing_block asserts a routing block that omits
`keywords` still seeds cleanly (the column keeps its schema default '[]').
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from tests.fixtures.db_helpers import bootstrap_gaia_schema
from tools.scan.seed_surface_routing import seed_surface_routing

_AGENT_WITH_ROUTING = """---
name: fixture-specialist
project_context_contracts:
  read: [project_identity, stack, application_services]
  write: [application_services]
routing:
  surface: fixture_surface
  adjacent_surfaces: [iac, gitops_desired_state]
  commands: [mytool]
  artifacts: [foo.json]
  required_checks:
    - "Do the check"
---
# Fixture Specialist
Body.
"""

_AGENT_NO_ROUTING = """---
name: fixture-router
project_context_contracts:
  read: [project_identity]
  write: []
---
# Fixture Router (no routing block -- like gaia-orchestrator)
Body.
"""

_AGENT_SUBSURFACE = """---
name: fixture-planner
project_context_contracts:
  read: [project_identity, stack]
  write: []
routing:
  surface: fixture_planning
  adjacent_surfaces: [fixture_surface]
  commands: []
  artifacts: [plan.md]
  required_checks:
    - "Keep aligned"
  sub_surfaces:
    - name: brief
      owner: gaia-orchestrator
      owner_skill: brief-spec
    - name: plan
      owner: fixture-planner
---
# Fixture Planner
Body.
"""


def _write_agents(agents_dir: Path) -> None:
    agents_dir.mkdir(parents=True, exist_ok=True)
    (agents_dir / "fixture-specialist.md").write_text(_AGENT_WITH_ROUTING, encoding="utf-8")
    (agents_dir / "fixture-router.md").write_text(_AGENT_NO_ROUTING, encoding="utf-8")
    (agents_dir / "fixture-planner.md").write_text(_AGENT_SUBSURFACE, encoding="utf-8")


def _rows(db_path: Path) -> dict:
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT * FROM surface_routing").fetchall()
    con.close()
    return {r["surface"]: dict(r) for r in rows}


def test_seeds_one_row_per_routing_block(tmp_path):
    db = tmp_path / "gaia.db"
    bootstrap_gaia_schema(db)
    agents_dir = tmp_path / "agents"
    _write_agents(agents_dir)

    summary = seed_surface_routing(db, agents_dir=agents_dir)
    assert summary["surfaces_seeded"] == 2
    assert summary["agents_skipped"] == 1  # fixture-router has no routing block

    rows = _rows(db)
    assert set(rows) == {"fixture_surface", "fixture_planning"}
    spec = rows["fixture_surface"]
    assert spec["primary_agent"] == "fixture-specialist"
    assert json.loads(spec["commands_json"]) == ["mytool"]
    assert json.loads(spec["artifacts_json"]) == ["foo.json"]


def test_routing_block_without_keywords_seeds_cleanly(tmp_path):
    """A routing block that omits `keywords` (the real-world shape now that
    keywords are retired as a signal) seeds without crashing; the column
    keeps its schema default '[]' rather than erroring on a missing key."""
    db = tmp_path / "gaia.db"
    bootstrap_gaia_schema(db)
    agents_dir = tmp_path / "agents"
    _write_agents(agents_dir)

    seed_surface_routing(db, agents_dir=agents_dir)
    rows = _rows(db)
    assert json.loads(rows["fixture_surface"]["keywords_json"]) == []
    assert json.loads(rows["fixture_planning"]["keywords_json"]) == []


def test_contract_sections_mirror_read_contracts(tmp_path):
    db = tmp_path / "gaia.db"
    bootstrap_gaia_schema(db)
    agents_dir = tmp_path / "agents"
    _write_agents(agents_dir)

    seed_surface_routing(db, agents_dir=agents_dir)
    rows = _rows(db)
    assert json.loads(rows["fixture_surface"]["contract_sections_json"]) == [
        "project_identity",
        "stack",
        "application_services",
    ]


def test_sub_surfaces_persisted(tmp_path):
    db = tmp_path / "gaia.db"
    bootstrap_gaia_schema(db)
    agents_dir = tmp_path / "agents"
    _write_agents(agents_dir)

    seed_surface_routing(db, agents_dir=agents_dir)
    rows = _rows(db)
    subs = json.loads(rows["fixture_planning"]["sub_surfaces_json"])
    names = {s["name"]: s for s in subs}
    assert names["brief"]["owner"] == "gaia-orchestrator"
    assert names["brief"]["owner_skill"] == "brief-spec"
    assert names["plan"]["owner"] == "fixture-planner"
    # An agent without sub_surfaces stores NULL.
    assert rows["fixture_surface"]["sub_surfaces_json"] is None


def test_reseed_is_idempotent_and_replaces_stale(tmp_path):
    db = tmp_path / "gaia.db"
    bootstrap_gaia_schema(db)
    agents_dir = tmp_path / "agents"
    _write_agents(agents_dir)

    for _ in range(3):
        seed_surface_routing(db, agents_dir=agents_dir)
    rows = _rows(db)
    assert set(rows) == {"fixture_surface", "fixture_planning"}

    # Rename a surface in frontmatter: the old surface row must NOT linger.
    (agents_dir / "fixture-specialist.md").write_text(
        _AGENT_WITH_ROUTING.replace("surface: fixture_surface", "surface: renamed_surface"),
        encoding="utf-8",
    )
    seed_surface_routing(db, agents_dir=agents_dir)
    rows = _rows(db)
    assert set(rows) == {"renamed_surface", "fixture_planning"}
    assert "fixture_surface" not in rows


def test_real_agents_seed_all_surfaces(tmp_path):
    """The real agents/ dir seeds the 7 production surfaces."""
    db = tmp_path / "gaia.db"
    bootstrap_gaia_schema(db)
    summary = seed_surface_routing(db)  # defaults to repo agents/
    assert summary["surfaces_seeded"] == 7
    rows = _rows(db)
    assert set(rows) == {
        "live_runtime",
        "iac",
        "gitops_desired_state",
        "app_ci_tooling",
        "planning_specs",
        "gaia_system",
        "workspace",
    }
    assert rows["iac"]["primary_agent"] == "platform-architect"
