"""OpenCode's permission event names never reach Gaia's neutral layers.

``permission.replied`` and ``permission.v2.replied`` are OpenCode spellings.
Gaia's neutral layers receive a lane token instead, and the compatibility lane
still carries a real decision.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT / "hooks") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "hooks"))

from adapters.consent_events import (
    COMPATIBILITY_DECISION_LANE,
    ConsentDecisionLedger,
    build_decision,
)
from adapters.types import ConsentBinding

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
            if path.suffix in {".py", ".md", ".json", ".sql"} and path.is_file():
                yield path


def test_the_compat_event_name_never_reaches_a_neutral_surface():
    leaks = [
        str(path.relative_to(_REPO_ROOT))
        for path in _neutral_sources()
        if _COMPAT_EVENT in path.read_text(errors="ignore")
    ]

    assert leaks == []


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
