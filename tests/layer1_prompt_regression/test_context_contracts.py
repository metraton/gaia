"""
Test context contract files for structure and consistency.

Validates that context-contracts JSON files are valid, consistent
with the agent definitions, and follow permission rules.
"""

import json
import pytest
from pathlib import Path
import sys

# Add hooks to path (same pattern as existing tests)
HOOKS_DIR = Path(__file__).resolve().parents[2] / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.tools.task_validator import AVAILABLE_AGENTS, META_AGENTS


class TestContractFileStructure:
    """Validate contract JSON structure."""

    @pytest.fixture
    def contract_files(self, config_dir):
        """Find all context-contracts*.json files."""
        return list(config_dir.glob("context-contracts*.json"))

    @pytest.fixture
    def contracts(self, contract_files):
        """Parse all contract files."""
        result = {}
        for f in contract_files:
            result[f.name] = json.loads(f.read_text())
        return result

    def test_contract_files_exist(self, contract_files):
        """At least one context-contracts file must exist."""
        assert len(contract_files) >= 1, "No context-contracts files found"

    def test_contracts_are_valid_json(self, contract_files):
        """All contract files must be valid JSON."""
        for f in contract_files:
            try:
                json.loads(f.read_text())
            except json.JSONDecodeError as e:
                pytest.fail(f"{f.name} is not valid JSON: {e}")

    def test_contracts_have_version(self, contracts):
        """All contracts must have a 'version' field."""
        for name, data in contracts.items():
            assert "version" in data, f"{name} missing 'version' field"

    def test_contracts_have_agents(self, contracts):
        """All contracts must have an 'agents' field."""
        for name, data in contracts.items():
            assert "agents" in data, f"{name} missing 'agents' field"
            assert isinstance(data["agents"], dict), \
                f"{name} 'agents' must be a dict"


class TestContractAgentConsistency:
    """Validate contract agents match actual agent definitions."""

    @pytest.fixture
    def contracts(self, config_dir):
        result = {}
        for f in config_dir.glob("context-contracts*.json"):
            result[f.name] = json.loads(f.read_text())
        return result

    def test_no_meta_agents_in_contracts(self, contracts):
        """Meta-agents (gaia, Explore, Plan) should NOT appear in contracts."""
        for name, data in contracts.items():
            contract_agents = set(data.get("agents", {}).keys())
            for meta in META_AGENTS:
                assert meta not in contract_agents, \
                    f"{name} should not contain meta-agent '{meta}'"

    def test_contract_agents_are_available(self, contracts):
        """All agents in contracts must be in AVAILABLE_AGENTS."""
        for name, data in contracts.items():
            for agent in data.get("agents", {}).keys():
                assert agent in AVAILABLE_AGENTS, \
                    f"{name} references unknown agent '{agent}'"

    def test_project_agents_in_at_least_one_contract(self, contracts):
        """All project agents should appear in at least one contract."""
        project_agents = [a for a in AVAILABLE_AGENTS if a not in META_AGENTS]
        # speckit-planner doesn't need context contracts (it has its own workflow)
        optional_agents = {"speckit-planner"}
        required_agents = set(project_agents) - optional_agents

        all_contract_agents = set()
        for data in contracts.values():
            all_contract_agents.update(data.get("agents", {}).keys())

        for agent in required_agents:
            assert agent in all_contract_agents, \
                f"Project agent '{agent}' not found in any contract"


class TestPermissionRules:
    """Validate permission rules in contracts."""

    @pytest.fixture
    def contracts(self, config_dir):
        result = {}
        for f in config_dir.glob("context-contracts*.json"):
            result[f.name] = json.loads(f.read_text())
        return result

    def test_write_is_subset_of_read(self, contracts):
        """Write permissions must be a subset of read permissions."""
        for name, data in contracts.items():
            for agent, perms in data.get("agents", {}).items():
                read = set(perms.get("read", []))
                write = set(perms.get("write", []))
                assert write.issubset(read), \
                    f"{name}/{agent}: write {write - read} not in read permissions"

    def test_agents_have_read_permissions(self, contracts):
        """All agents in contracts must have read permissions."""
        for name, data in contracts.items():
            for agent, perms in data.get("agents", {}).items():
                read = perms.get("read", [])
                assert len(read) > 0, \
                    f"{name}/{agent}: must have at least one read permission"

    def test_all_agents_can_read_project_details(self, contracts):
        """All agents should be able to read project_details."""
        for name, data in contracts.items():
            for agent, perms in data.get("agents", {}).items():
                read = perms.get("read", [])
                assert "project_details" in read, \
                    f"{name}/{agent}: should have 'project_details' in read permissions"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
