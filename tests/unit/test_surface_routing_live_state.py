"""
B6: Remove Live State from Context -- surface-routing signal guard.

Enforces that no route in the DB-backed surface_routing table declares a
commands/artifacts signal that references a retired live-state field.

Live-state fields retired per live-state-audit.json (B1 M1.a / B6):
  GCP: gcp_services, workload_identity, monitoring_observability, static_ips
  AWS: vpc_mapping, load_balancers, api_gateway, irsa_bindings

These fields produce stale data between scans and require cloud API calls
to populate. project-context is an index, not a snapshot; live state is
queried with cloud CLIs at the moment it is needed.

Keywords were retired as a routing signal (tools/context/surface_router.py
scores commands/artifacts only; no agent frontmatter declares `keywords`
anymore), so this guard now scans commands and artifacts -- the only signals
the matcher reads.

Note: the banned list covers routing signals only. References to these
field names in contract_sections (context injection declarations) or in
explanatory text are out of scope for this guard.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if str(_REPO_ROOT / "tools" / "context") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "tools" / "context"))

BANNED_SIGNAL_KEYWORDS = {
    # GCP live-state fields retired per live-state-audit.json
    "gcp_services",
    "workload_identity",
    "monitoring_observability",
    "static_ips",
    # AWS live-state fields retired per live-state-audit.json
    "vpc_mapping",
    "load_balancers",
    "api_gateway",
    "irsa_bindings",
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def routing_config(tmp_path, monkeypatch) -> dict:
    """Load the DB-backed routing config seeded from agent frontmatters.

    Routing moved from config/surface-routing.json to the surface_routing
    table (seeded from each agent's `routing:` frontmatter block). This fixture
    seeds a temp DB from the real agents and loads the same in-memory shape the
    JSON used to produce.
    """
    from tests.fixtures.db_helpers import (
        bootstrap_gaia_schema,
        seed_surface_routing_from_agents,
    )
    from surface_router import load_surface_routing_config

    db = tmp_path / "gaia.db"
    bootstrap_gaia_schema(db)
    seed_surface_routing_from_agents(db)
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    return load_surface_routing_config()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_no_live_state_signals(routing_config: dict) -> None:
    """
    No route in the DB-backed surface_routing table may declare a
    commands/artifacts signal that matches a retired live-state field name.

    Principle: project-context is an index, not a snapshot. Live-state
    values are queried with cloud CLIs at the moment they are needed, not
    stored as context fields or used as routing signals.
    """
    surfaces = routing_config.get("surfaces", {})
    violations: list[str] = []

    for surface_name, surface_def in surfaces.items():
        signals = surface_def.get("signals", {})
        candidates: list[str] = list(signals.get("commands", [])) + list(
            signals.get("artifacts", [])
        )

        for signal in candidates:
            # Normalise: lowercase, underscores replace spaces/hyphens
            normalised = signal.lower().replace(" ", "_").replace("-", "_")
            if normalised in BANNED_SIGNAL_KEYWORDS:
                violations.append(
                    f"surface '{surface_name}' has banned routing signal: '{signal}'"
                )

    assert not violations, (
        "surface_routing contains live-state routing signals that were "
        "retired in B6. Remove or replace them:\n"
        + "\n".join(f"  - {v}" for v in violations)
    )


def test_routing_config_is_valid_json(routing_config: dict) -> None:
    """The DB-backed routing config has the expected top-level structure."""
    assert "version" in routing_config, "missing 'version' key"
    assert "surfaces" in routing_config, "missing 'surfaces' key"
    assert isinstance(routing_config["surfaces"], dict), "'surfaces' must be a dict"


def test_each_surface_has_signals(routing_config: dict) -> None:
    """Every surface definition includes a 'signals' block with 'commands'
    and 'artifacts' lists -- the only signals the matcher reads now that
    keywords are retired as a signal source."""
    surfaces = routing_config.get("surfaces", {})
    missing: list[str] = []

    for surface_name, surface_def in surfaces.items():
        signals = surface_def.get("signals")
        if signals is None:
            missing.append(f"surface '{surface_name}' has no 'signals' block")
            continue
        for key in ("commands", "artifacts"):
            if not isinstance(signals.get(key), list):
                missing.append(
                    f"surface '{surface_name}'.signals.{key} is not a list"
                )

    assert not missing, (
        "Some surfaces are missing required 'signals.commands'/'signals.artifacts':\n"
        + "\n".join(f"  - {m}" for m in missing)
    )
