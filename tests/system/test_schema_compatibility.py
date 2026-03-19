"""
Test schema compatibility between CLAUDE.template.md and system components.

Ensures the orchestrator template contains the essential structural elements
that the hook system and surface routing depend on.
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
    def gaia_scan_content(self, package_root):
        scan_path = package_root / "bin" / "gaia-scan.py"
        assert scan_path.exists(), "gaia-scan.py not found"
        return scan_path.read_text()

    @pytest.fixture
    def fixture_contexts(self, package_root):
        """Load all test fixture project-context files."""
        fixtures_dir = package_root / "tests" / "fixtures"
        contexts = {}
        for f in fixtures_dir.glob("project-context.*.json"):
            contexts[f.stem] = json.loads(f.read_text())
        return contexts

    def test_template_has_surface_routing_contract(self, template_content):
        """Template must contain a routing section with surfaces and agents.

        The simplified template uses ## Routing instead of ## Surface Routing.
        Consolidation details, investigation briefs, and loop caps are now
        handled by hooks and the agent-protocol skill, not the orchestrator template.
        """
        has_routing = ("## Routing" in template_content
                       or "## Your agents" in template_content)
        assert has_routing, "Template must have a routing/agents section"
        for surface in [
            "live_runtime",
            "gitops_desired_state",
            "terraform_iac",
            "app_ci_tooling",
            "planning_specs",
            "gaia_system",
        ]:
            assert surface in template_content, (
                f"Template routing must include {surface}"
            )

        for agent in [
            "terraform-architect",
            "gitops-operator",
            "cloud-troubleshooter",
            "devops-developer",
            "speckit-planner",
            "gaia-system",
        ]:
            assert agent in template_content, (
                f"Template routing must include {agent}"
            )

    def test_template_documents_plan_status(self, template_content, package_root):
        """PLAN_STATUS values must be documented in the template or agent-protocol skill."""
        agent_protocol_path = package_root / "skills" / "agent-protocol" / "SKILL.md"
        combined = template_content
        if agent_protocol_path.exists():
            combined += "\n" + agent_protocol_path.read_text()

        required_statuses = [
            "IN_PROGRESS",
            "REVIEW",
            "AWAITING_APPROVAL",
            "COMPLETE",
            "BLOCKED",
            "NEEDS_INPUT",
        ]
        for status in required_statuses:
            assert status in combined, (
                f"PLAN_STATUS '{status}' not found in template or agent-protocol skill"
            )

    def test_template_references_agent_status(self, template_content, package_root):
        """Agent status and ID must be documented in the template or agent-protocol skill."""
        agent_protocol_path = package_root / "skills" / "agent-protocol" / "SKILL.md"
        combined = template_content
        if agent_protocol_path.exists():
            combined += "\n" + agent_protocol_path.read_text()

        has_status = "AGENT_STATUS" in combined or "plan_status" in combined
        assert has_status, (
            "Agent status must be documented in template or agent-protocol skill "
            "(as AGENT_STATUS or plan_status)"
        )
        has_id = "AGENT_ID" in combined or "agent_id" in combined
        assert has_id, (
            "Agent ID must be documented in template or agent-protocol skill "
            "(as AGENT_ID or agent_id)"
        )

    def test_fixture_contexts_have_expected_structure(self, fixture_contexts):
        """Test fixture project-context files must have the sections gaia-init generates."""
        if not fixture_contexts:
            pytest.skip("No fixture project-context files found")

        for name, ctx in fixture_contexts.items():
            assert "metadata" in ctx, f"{name}: missing metadata"
            assert "sections" in ctx, f"{name}: missing sections"

    def test_gaia_scan_writes_project_context(self, gaia_scan_content):
        """gaia-scan must write to project-context.json."""
        assert "project-context" in gaia_scan_content, (
            "gaia-scan must reference project-context"
        )
