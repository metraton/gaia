"""
Test routing table consistency between CLAUDE.md and code.

Validates that the orchestrator's agent routing table matches
the available agents in code and the agent definition files on disk.
"""

import re
import pytest
from pathlib import Path
import sys

# Add hooks to path (same pattern as existing tests)
HOOKS_DIR = Path(__file__).resolve().parents[2] / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.tools.task_validator import AVAILABLE_AGENTS, META_AGENTS


class TestRoutingTableAgents:
    """Validate CLAUDE.md routing table matches actual agents."""

    @pytest.fixture
    def routing_agents(self, claude_md_content):
        """Extract agent names from the routing table in CLAUDE.md."""
        # Pattern: | **agent-name** | ...
        pattern = r'\|\s*\*\*([a-z][\w-]*)\*\*\s*\|'
        matches = re.findall(pattern, claude_md_content)
        return set(matches)

    def test_routing_agents_exist_on_disk(self, routing_agents, agents_dir):
        """All agents in routing table must exist as .md files."""
        for agent in routing_agents:
            agent_file = agents_dir / f"{agent}.md"
            assert agent_file.exists(), \
                f"Routing table references '{agent}' but {agent_file} not found"

    def test_disk_agents_in_routing_table(self, routing_agents, all_agent_files):
        """All agent .md files should appear in routing table."""
        disk_agents = {f.stem for f in all_agent_files}
        for agent in disk_agents:
            assert agent in routing_agents, \
                f"Agent '{agent}' exists on disk but not in CLAUDE.md routing table"

    def test_routing_agents_in_available_agents(self, routing_agents):
        """All routing table agents must be in AVAILABLE_AGENTS."""
        for agent in routing_agents:
            assert agent in AVAILABLE_AGENTS, \
                f"Routing table agent '{agent}' not in AVAILABLE_AGENTS"

    def test_available_project_agents_in_routing(self, routing_agents):
        """All project agents (non-meta) from AVAILABLE_AGENTS should be in routing table."""
        project_agents = [a for a in AVAILABLE_AGENTS if a not in META_AGENTS]
        for agent in project_agents:
            assert agent in routing_agents, \
                f"AVAILABLE_AGENT '{agent}' not in CLAUDE.md routing table"


class TestPlanStatusDocumentation:
    """Validate agent-protocol skill documents all valid PLAN_STATUS values.

    The full state machine lives in agent-protocol/SKILL.md.
    CLAUDE.md only handles orchestrator-visible terminal states.
    """

    VALID_STATUSES = [
        "INVESTIGATING",
        "PLANNING",
        "PENDING_APPROVAL",
        "APPROVED_EXECUTING",
        "FIXING",
        "COMPLETE",
        "BLOCKED",
        "NEEDS_INPUT",
    ]

    @pytest.fixture
    def agent_protocol_content(self):
        skills_dir = Path(__file__).resolve().parents[2] / "skills"
        return (skills_dir / "agent-protocol" / "SKILL.md").read_text()

    @pytest.mark.parametrize("status", VALID_STATUSES)
    def test_plan_status_documented(self, status, agent_protocol_content):
        """Each valid PLAN_STATUS must appear in agent-protocol skill."""
        assert status in agent_protocol_content, \
            f"PLAN_STATUS '{status}' not documented in agent-protocol/SKILL.md"


class TestSystemPaths:
    """Validate system paths documented in CLAUDE.md are valid."""

    def test_project_context_path_documented(self, claude_md_content):
        """project-context.json path must be documented."""
        assert "project-context.json" in claude_md_content

    def test_agents_path_documented(self, claude_md_content):
        """agents/ path must be documented."""
        assert "agents/" in claude_md_content

    def test_skills_path_documented(self, claude_md_content):
        """skills/ path should be referenced."""
        assert "skills/" in claude_md_content


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
