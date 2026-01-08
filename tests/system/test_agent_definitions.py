"""
Test suite for agent definition files
Validates agent prompts have required sections and structure
"""

import pytest
from pathlib import Path


# Meta-agents have different documentation structure than project agents
META_AGENTS = ["gaia.md"]


class TestAgentStructure:
    """Test that agent files have proper structure"""

    @pytest.fixture
    def agents_dir(self):
        """Get the agents directory path"""
        agents = Path(__file__).resolve().parents[2] / "agents"
        return agents.resolve() if agents.is_symlink() else agents

    @pytest.fixture
    def all_agents(self, agents_dir):
        """Get all agent markdown files (exclude READMEs and documentation)"""
        return [f for f in agents_dir.glob("*.md") if "README" not in f.name.upper()]

    def test_all_agents_have_title(self, all_agents):
        """All agents should have a title heading"""
        for agent_file in all_agents:
            content = agent_file.read_text()
            # Check for any heading (# or ##)
            has_heading = any(line.strip().startswith('#') for line in content.split('\n'))
            assert has_heading, \
                f"{agent_file.name} should have at least one heading"

    def test_all_agents_have_role_section(self, all_agents):
        """All agents should define their role"""
        for agent_file in all_agents:
            content = agent_file.read_text()
            # Check for role-related sections or keywords
            role_indicators = [
                "## Role", "## Primary Role", "## Core Identity",
                "role", "responsibility", "specialize"
            ]
            has_role = any(indicator.lower() in content.lower() for indicator in role_indicators)
            assert has_role, f"{agent_file.name} missing role definition"

    def test_all_agents_have_capabilities(self, all_agents):
        """All agents should list their capabilities"""
        for agent_file in all_agents:
            content = agent_file.read_text()
            # Extended indicators to include meta-agent patterns
            capability_indicators = [
                "## Capabilities",
                "## Core Capabilities", 
                "## What I Do",
                "## Responsibilities",
                "## Knowledge Domain",  # Used by gaia.md
                "## Your approach",     # Used by gaia.md
                "## Scope",             # Used by gaia.md
                "capabilities",
                "can do",
                "will handle"
            ]
            has_capabilities = any(ind.lower() in content.lower() for ind in capability_indicators)
            assert has_capabilities, \
                f"{agent_file.name} missing capabilities section"

    def test_all_agents_have_workflow(self, all_agents):
        """All agents should document their workflow"""
        for agent_file in all_agents:
            content = agent_file.read_text()
            workflow_indicators = [
                "## Workflow",
                "## Operating Protocol",
                "## Process",
                "## Execution Protocol",
                "workflow",
                "protocol",
                "procedure",
                "steps"
            ]
            has_workflow = any(ind.lower() in content.lower() for ind in workflow_indicators)
            assert has_workflow, f"{agent_file.name} missing workflow description"


class TestProjectAgents:
    """Test project agent specific requirements"""

    @pytest.fixture
    def agents_dir(self):
        """Get the agents directory path"""
        agents = Path(__file__).resolve().parents[2] / "agents"
        return agents.resolve() if agents.is_symlink() else agents

    def test_terraform_architect_exists(self, agents_dir):
        """terraform-architect.md must exist"""
        tf_agent = agents_dir / "terraform-architect.md"
        assert tf_agent.exists(), "terraform-architect.md not found"

    def test_gitops_operator_exists(self, agents_dir):
        """gitops-operator.md must exist"""
        gitops_agent = agents_dir / "gitops-operator.md"
        assert gitops_agent.exists(), "gitops-operator.md not found"

    def test_cloud_troubleshooter_exists(self, agents_dir):
        """cloud-troubleshooter.md must exist (unified GCP/AWS)"""
        cloud_agent = agents_dir / "cloud-troubleshooter.md"
        assert cloud_agent.exists(), "cloud-troubleshooter.md not found"

    def test_devops_developer_exists(self, agents_dir):
        """devops-developer.md must exist"""
        devops_agent = agents_dir / "devops-developer.md"
        assert devops_agent.exists(), "devops-developer.md not found"


class TestAgentSecurity:
    """Test that agents document security tiers"""

    @pytest.fixture
    def agents_dir(self):
        """Get the agents directory path"""
        agents = Path(__file__).resolve().parents[2] / "agents"
        return agents.resolve() if agents.is_symlink() else agents

    def test_agents_document_security_tiers(self, agents_dir):
        """Agents should document their security tier capabilities"""
        agent_files = [f for f in agents_dir.glob("*.md") if "README" not in f.name.upper()]
        for agent_file in agent_files:
            content = agent_file.read_text()

            # Extended indicators - meta-agents may use "Scope" or "read-only" instead of T0-T3
            tier_indicators = [
                "T0", "T1", "T2", "T3", 
                "tier", "security", "permission",
                "Scope",      # gaia.md uses "## Scope" section
                "read-only",  # Some agents mention read-only operations
                "You CAN",    # gaia.md uses this pattern
                "You CANNOT", # Alternative pattern
            ]
            mentions_tiers = any(indicator.lower() in content.lower() for indicator in tier_indicators)

            assert mentions_tiers, \
                f"{agent_file.name} should document security tier usage or permissions"


class TestAgentConsistency:
    """Test consistency across agent definitions"""

    @pytest.fixture
    def agents_dir(self):
        """Get the agents directory path"""
        agents = Path(__file__).resolve().parents[2] / "agents"
        return agents.resolve() if agents.is_symlink() else agents

    def test_no_duplicate_agent_names(self, agents_dir):
        """Agent names should be unique"""
        agent_files = [f for f in agents_dir.glob("*.md") if "README" not in f.name.upper()]
        agent_names = [f.stem for f in agent_files]

        assert len(agent_names) == len(set(agent_names)), \
            "Duplicate agent names detected"

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
