"""
Test schema compatibility between CLAUDE.template.md and system components.

Ensures the orchestrator template contains the essential sections that
the hook system and agent routing depend on.
"""

import json
import pytest
from pathlib import Path


class TestSchemaCompatibility:
    """Verify CLAUDE.template.md contains required orchestrator sections."""

    @pytest.fixture
    def package_root(self):
        return Path(__file__).resolve().parents[2]

    @pytest.fixture
    def template_content(self, package_root):
        template_path = package_root / "templates" / "CLAUDE.template.md"
        assert template_path.exists(), "CLAUDE.template.md not found"
        return template_path.read_text()

    @pytest.fixture
    def gaia_init_content(self, package_root):
        init_path = package_root / "bin" / "gaia-init.js"
        assert init_path.exists(), "gaia-init.js not found"
        return init_path.read_text()

    @pytest.fixture
    def fixture_contexts(self, package_root):
        """Load all test fixture project-context files."""
        fixtures_dir = package_root / "tests" / "fixtures"
        contexts = {}
        for f in fixtures_dir.glob("project-context.*.json"):
            contexts[f.stem] = json.loads(f.read_text())
        return contexts

    def test_template_defines_orchestrator_identity(self, template_content):
        """Template must define the orchestrator role."""
        assert "orchestrator" in template_content.lower(), (
            "Template must define the orchestrator identity"
        )

    def test_template_enforces_delegation(self, template_content):
        """Template must enforce delegation to specialist agents."""
        assert "delegate" in template_content.lower(), (
            "Template must instruct delegation to specialist agents"
        )
        assert "Task" in template_content, (
            "Template must reference the Task tool for agent invocation"
        )

    def test_template_has_routing_table(self, template_content):
        """Template must contain the agent routing table."""
        required_agents = [
            "terraform-architect",
            "gitops-operator",
            "cloud-troubleshooter",
            "devops-developer",
        ]
        for agent in required_agents:
            assert agent in template_content, (
                f"Template routing table must include {agent}"
            )

    def test_template_documents_plan_status(self, template_content):
        """Template must document PLAN_STATUS values for orchestrator parsing."""
        required_statuses = [
            "INVESTIGATING",
            "PENDING_APPROVAL",
            "APPROVED_EXECUTING",
            "COMPLETE",
            "BLOCKED",
            "NEEDS_INPUT",
        ]
        for status in required_statuses:
            assert status in template_content, (
                f"Template must document PLAN_STATUS: {status}"
            )

    def test_template_references_agent_status(self, template_content):
        """Template must reference AGENT_STATUS and AGENT_ID for resume support."""
        assert "AGENT_STATUS" in template_content, (
            "Template must reference AGENT_STATUS block"
        )
        assert "AGENT_ID" in template_content, (
            "Template must reference AGENT_ID for resume operations"
        )

    def test_fixture_contexts_have_expected_structure(self, fixture_contexts):
        """Test fixture project-context files must have the sections gaia-init generates."""
        if not fixture_contexts:
            pytest.skip("No fixture project-context files found")

        for name, ctx in fixture_contexts.items():
            assert "metadata" in ctx, f"{name}: missing metadata"
            assert "sections" in ctx, f"{name}: missing sections"

    def test_gaia_init_writes_project_context(self, gaia_init_content):
        """gaia-init must write to project-context.json."""
        assert "project-context" in gaia_init_content, (
            "gaia-init must reference project-context"
        )
