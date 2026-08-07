"""
Test suite for agent definition files
Validates agent prompts have required sections and structure
"""

import json
import pytest
from pathlib import Path


# Meta-agents have different documentation structure than project agents
META_AGENTS = ["gaia.md"]


def _load_manifest_agent_names():
    """Agent stem names published in build/gaia.manifest.json.

    The manifest is the single source of truth for what Gaia ships. Reading
    it here (instead of hardcoding a list) means this test cannot drift from
    what actually gets published -- it previously listed 6 agents while the
    manifest published 9.
    """
    manifest_path = Path(__file__).resolve().parents[2] / "build" / "gaia.manifest.json"
    manifest = json.loads(manifest_path.read_text())
    return sorted(Path(p).stem for p in manifest["agents"])


MANIFEST_AGENT_NAMES = _load_manifest_agent_names()


class TestProjectAgents:
    """Every agent published in build/gaia.manifest.json must exist as a file."""

    @pytest.fixture
    def agents_dir(self):
        """Get the agents directory path"""
        agents = Path(__file__).resolve().parents[2] / "agents"
        return agents.resolve() if agents.is_symlink() else agents

    @pytest.mark.parametrize("agent_name", MANIFEST_AGENT_NAMES)
    def test_manifest_agent_exists(self, agent_name, agents_dir):
        """<agent_name>.md must exist (published in build/gaia.manifest.json)."""
        agent_path = agents_dir / f"{agent_name}.md"
        assert agent_path.exists(), (
            f"Agent missing: {agent_name}.md (published in build/gaia.manifest.json)"
        )


class TestAgentConsistency:
    """Test consistency across agent definitions"""

    @pytest.fixture
    def agents_dir(self):
        """Get the agents directory path"""
        agents = Path(__file__).resolve().parents[2] / "agents"
        return agents.resolve() if agents.is_symlink() else agents

    def test_agent_naming_convention(self, agents_dir):
        """Agent files should follow naming convention (kebab-case)"""
        agent_files = [f for f in agents_dir.glob("*.md") if "README" not in f.name.upper()]
        for agent_file in agent_files:
            name = agent_file.stem
            # Should be lowercase with hyphens (or all lowercase)
            assert name.islower() or "-" in name, \
                f"{agent_file.name} should use kebab-case or lowercase naming"
            assert " " not in name, \
                f"{agent_file.name} should not contain spaces"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
