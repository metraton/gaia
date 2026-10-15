"""OpenCode offers an agent only the tools its permission does not deny, so the plugin's compaction tool must be allowed for the orchestrator alone."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bin"))

from cli import _install_helpers  # noqa: E402
from tests.cli.test_opencode_orchestrator_access import _permission_decision  # noqa: E402

TOOL = "gaia_compact_when_idle"


@pytest.fixture(scope="module")
def agents() -> dict:
    policy = json.loads((ROOT / "opencode" / "agent-policy.json").read_text())
    return _install_helpers._opencode_agents(ROOT, policy, None)


def test_the_plugin_still_registers_the_tool_under_that_name():
    assert f"{TOOL}: {{" in (ROOT / "opencode" / "plugin.ts").read_text()


def test_the_main_orchestrator_is_offered_the_compaction_tool(agents):
    assert _permission_decision(TOOL, "*", agents["gaia-orchestrator"]["permission"]) == "allow"


def test_no_dispatched_specialist_is_offered_it(agents):
    specialists = {name: agent for name, agent in agents.items() if name != "gaia-orchestrator"}
    assert specialists
    for name, agent in specialists.items():
        assert _permission_decision(TOOL, "*", agent["permission"]) == "deny", name


def test_other_plugin_tools_stay_withheld_from_the_orchestrator(agents):
    assert _permission_decision("some_other_plugin_tool", "*", agents["gaia-orchestrator"]["permission"]) == "deny"
