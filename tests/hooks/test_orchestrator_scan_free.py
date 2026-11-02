"""The orchestrator refreshes its workspace substrate with `gaia scan` and no signature."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "hooks"))

from modules.security import gaia_cli_only_guard
from modules.tools.bash_validator import BashValidator

ORCHESTRATOR = {"session_id": "s-scan", "agent_type": "gaia-orchestrator", "cwd": "/tmp"}

SCANS = [
    "gaia scan",
    "gaia scan --json",
    "gaia scan --workspace me",
    "gaia scan --workspace=me --json",
    "gaia scan --workspace me /home/user/ws",
    "gaia scan --workspace me --dry-run",
    "gaia context scan",
    "gaia context scan --json",
]


@pytest.fixture
def trusted_gaia(monkeypatch):
    monkeypatch.setattr(gaia_cli_only_guard, "is_trusted_gaia_binary", lambda _: True)


@pytest.mark.parametrize("command", SCANS)
def test_the_orchestrator_lane_admits_scan(trusted_gaia, command):
    allowed, reason = gaia_cli_only_guard.check(command, ORCHESTRATOR)
    assert allowed, reason


@pytest.mark.parametrize("command", SCANS)
def test_the_orchestrator_runs_scan_without_an_approval(trusted_gaia, command):
    result = BashValidator().validate(
        command, is_subagent=False, session_id="s-scan",
        agent_type="gaia-orchestrator", hook_payload=ORCHESTRATOR,
    )
    assert result.allowed, result.reason
    assert result.tier.value != "T3"
    assert result.approval_id is None
