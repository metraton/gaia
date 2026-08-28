"""The versioned permission event is adapter-edge compatibility, nothing more.

``permission.v2.replied`` is an OpenCode spelling. It is allowed to exist at the
plugin edge, where OpenCode's own events arrive, and it is not allowed anywhere
Gaia's neutral layers can read it -- those receive a lane token instead.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT / "hooks") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "hooks"))

from adapters.consent_events import (
    COMPATIBILITY_DECISION_LANE,
    ConsentDecisionLedger,
    build_decision,
)
from adapters.types import ConsentBinding

_PLUGIN = _REPO_ROOT / "opencode" / "plugin.ts"
_ADAPTER_REGISTRY = _REPO_ROOT / "hooks" / "adapters" / "registry.py"
_COMPAT_EVENT = "permission.v2.replied"
_PREFERRED_EVENT = "permission.replied"

# The surfaces that must stay harness-agnostic: Gaia's runtime, its CLI and its
# store. tests/ is excluded because a test naming the event is the check itself.
_NEUTRAL_TREES = ("hooks", "bin", "gaia", "scripts", "agents", "skills")


def _neutral_sources():
    for tree in _NEUTRAL_TREES:
        root = _REPO_ROOT / tree
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if path == _ADAPTER_REGISTRY:
                continue
            if path.suffix in {".py", ".md", ".json", ".sql"} and path.is_file():
                yield path


def test_the_compat_event_name_is_declared_only_as_a_compatibility_lane():
    source = _PLUGIN.read_text()
    occurrences = [line.strip() for line in source.splitlines() if _COMPAT_EVENT in line]

    assert len(occurrences) == 1
    assert occurrences[0].startswith("export const COMPATIBILITY_PERMISSION_EVENTS")
    assert f'export const PREFERRED_PERMISSION_EVENT = "{_PREFERRED_EVENT}"' in source


def test_the_edge_routes_by_lane_instead_of_accepting_both_event_names():
    source = _PLUGIN.read_text()

    assert "permissionDecisionLane(event.type)" in source
    assert f'event.type !== "{_PREFERRED_EVENT}"' not in source
    assert "repliedPermissionIDs" not in source
    assert '"--decision-lane", lane' in source


def test_the_compat_event_name_never_reaches_a_neutral_surface():
    leaks = [
        str(path.relative_to(_REPO_ROOT))
        for path in _neutral_sources()
        if _COMPAT_EVENT in path.read_text(errors="ignore")
    ]

    assert leaks == []


def test_the_adapter_registry_names_compatibility_only_on_the_opencode_edge():
    source = _ADAPTER_REGISTRY.read_text()

    assert source.count(_COMPAT_EVENT) == 1
    opencode_registration = source.split('register_adapter(\n    "opencode",', 1)[1]
    assert _COMPAT_EVENT in opencode_registration


def test_the_neutral_consent_module_knows_no_host_event_name():
    neutral = (_REPO_ROOT / "hooks" / "adapters" / "consent_events.py").read_text()

    assert _COMPAT_EVENT not in neutral
    assert _PREFERRED_EVENT not in neutral
    assert "permission." not in neutral
    assert COMPATIBILITY_DECISION_LANE == "compatibility"


def test_the_compatibility_lane_still_carries_a_real_decision():
    binding = ConsentBinding(agent_id="agent-1", session_id="ses-1", call_id="call-1")
    ledger = ConsentDecisionLedger()

    admission = ledger.admit(COMPATIBILITY_DECISION_LANE, build_decision("P-1", binding, "once"))

    assert admission.accepted is True
    assert admission.lane == COMPATIBILITY_DECISION_LANE
    assert admission.decision.binding == binding


@pytest.mark.skipif(shutil.which("bun") is None, reason="bun is required to run the plugin edge")
def test_the_edge_maps_each_event_name_to_its_lane():
    script = (
        "import { permissionDecisionLane, PREFERRED_PERMISSION_EVENT,"
        " COMPATIBILITY_PERMISSION_EVENTS }"
        f" from {json.dumps(str(_PLUGIN))};"
        "console.log(JSON.stringify({"
        " preferred: permissionDecisionLane(PREFERRED_PERMISSION_EVENT),"
        " compatibility: permissionDecisionLane(COMPATIBILITY_PERMISSION_EVENTS[0]),"
        ' unknown: permissionDecisionLane("permission.v3.replied") ?? null,'
        " compatEvents: COMPATIBILITY_PERMISSION_EVENTS}));"
    )
    result = subprocess.run(["bun", "-e", script], text=True, capture_output=True, check=True)
    observed = json.loads(result.stdout)

    assert observed["preferred"] == "preferred"
    assert observed["compatibility"] == "compatibility"
    assert observed["unknown"] is None
    assert observed["compatEvents"] == [_COMPAT_EVENT]
