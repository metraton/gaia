"""
B6: Remove Live State from Context -- surface-routing signal keyword guard.

Enforces that no route in config/surface-routing.json declares a
signals.keywords entry that references a retired live-state field.

Live-state fields retired per live-state-audit.json (B1 M1.a / B6):
  GCP: gcp_services, workload_identity, monitoring_observability, static_ips
  AWS: vpc_mapping, load_balancers, api_gateway, irsa_bindings

These fields produce stale data between scans and require cloud API calls
to populate. project-context is an index, not a snapshot; live state is
queried with cloud CLIs at the moment it is needed.

Note: the banned list covers signal *keywords* only. References to these
field names in contract_sections (context injection declarations) or in
explanatory text are out of scope for this guard.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SURFACE_ROUTING_PATH = Path(__file__).parents[2] / "config" / "surface-routing.json"

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


@pytest.fixture(scope="module")
def routing_config() -> dict:
    """Load surface-routing.json once per module."""
    assert SURFACE_ROUTING_PATH.exists(), (
        f"surface-routing.json not found at {SURFACE_ROUTING_PATH}. "
        "Check that the config path is correct."
    )
    return json.loads(SURFACE_ROUTING_PATH.read_text())


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_no_live_state_signals(routing_config: dict) -> None:
    """
    No route in surface-routing.json may declare a signals.keywords entry
    that matches a retired live-state field name.

    Principle: project-context is an index, not a snapshot. Live-state
    values are queried with cloud CLIs at the moment they are needed, not
    stored as context fields or used as routing signals.
    """
    surfaces = routing_config.get("surfaces", {})
    violations: list[str] = []

    for surface_name, surface_def in surfaces.items():
        signals = surface_def.get("signals", {})
        keywords: list[str] = signals.get("keywords", [])

        for keyword in keywords:
            # Normalise: lowercase, underscores replace spaces/hyphens
            normalised = keyword.lower().replace(" ", "_").replace("-", "_")
            if normalised in BANNED_SIGNAL_KEYWORDS:
                violations.append(
                    f"surface '{surface_name}' has banned signal keyword: '{keyword}'"
                )

    assert not violations, (
        "surface-routing.json contains live-state signal keywords that were "
        "retired in B6. Remove or replace them:\n"
        + "\n".join(f"  - {v}" for v in violations)
    )


def test_routing_config_is_valid_json(routing_config: dict) -> None:
    """surface-routing.json is valid JSON and has the expected top-level structure."""
    assert "version" in routing_config, "missing 'version' key"
    assert "surfaces" in routing_config, "missing 'surfaces' key"
    assert isinstance(routing_config["surfaces"], dict), "'surfaces' must be a dict"


def test_each_surface_has_signals(routing_config: dict) -> None:
    """Every surface definition includes a 'signals' block with a 'keywords' list."""
    surfaces = routing_config.get("surfaces", {})
    missing: list[str] = []

    for surface_name, surface_def in surfaces.items():
        signals = surface_def.get("signals")
        if signals is None:
            missing.append(f"surface '{surface_name}' has no 'signals' block")
        elif not isinstance(signals.get("keywords"), list):
            missing.append(
                f"surface '{surface_name}'.signals.keywords is not a list"
            )

    assert not missing, (
        "Some surfaces are missing required 'signals.keywords':\n"
        + "\n".join(f"  - {m}" for m in missing)
    )
