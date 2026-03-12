"""
Test schema compatibility between CLAUDE.template.md and system components.

Ensures the orchestrator template contains the essential sections that
the hook system and surface routing depend on.
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
        # The simplified template uses "subagent" instead of "Task" tool
        assert "subagent" in template_content.lower() or "Task" in template_content, (
            "Template must reference delegation via subagent or Task tool"
        )

    def test_template_has_surface_routing_contract(self, template_content):
        """Template must contain a routing section with surfaces and agents.

        The simplified template uses ## Routing instead of ## Surface Routing.
        Consolidation details, investigation briefs, and loop caps are now
        handled by hooks and the agent-protocol skill, not the orchestrator template.
        """
        assert "## Routing" in template_content
        assert "## Agent Routing" not in template_content
        for surface in [
            "live_runtime",
            "gitops_desired_state",
            "terraform_iac",
            "app_ci_tooling",
            "planning_specs",
            "gaia_system",
        ]:
            assert f"`{surface}`" in template_content, (
                f"Template routing must include {surface}"
            )

        for agent in [
            "terraform-architect",
            "gitops-operator",
            "cloud-troubleshooter",
            "devops-developer",
            "speckit-planner",
            "gaia",
        ]:
            assert agent in template_content, (
                f"Template routing must include {agent}"
            )

        lowered = template_content.lower()
        # Multi-agent dispatch must still be documented
        assert "two or more agents" in lowered or "parallel" in lowered, (
            "Template must define when to use multi-agent dispatch"
        )

    def test_template_documents_plan_status(self, template_content, package_root):
        """PLAN_STATUS values must be documented in the template or agent-protocol skill."""
        # The orchestrator template documents the statuses it acts on directly.
        # The full set of valid states (including agent-internal ones like
        # INVESTIGATING, APPROVED_EXECUTING) is authoritative in agent-protocol.
        agent_protocol_path = package_root / "skills" / "agent-protocol" / "SKILL.md"
        combined = template_content
        if agent_protocol_path.exists():
            combined += "\n" + agent_protocol_path.read_text()

        required_statuses = [
            "INVESTIGATING",
            "PENDING_APPROVAL",
            "APPROVED_EXECUTING",
            "COMPLETE",
            "BLOCKED",
            "NEEDS_INPUT",
        ]
        for status in required_statuses:
            assert status in combined, (
                f"PLAN_STATUS '{status}' not found in template or agent-protocol skill"
            )

    def test_template_references_agent_status(self, template_content, package_root):
        """Agent status and ID must be documented in the template or agent-protocol skill.

        The simplified template delegates status block details to agent-protocol.
        The agent-protocol skill uses json:contract with plan_status and agent_id fields.
        """
        agent_protocol_path = package_root / "skills" / "agent-protocol" / "SKILL.md"
        combined = template_content
        if agent_protocol_path.exists():
            combined += "\n" + agent_protocol_path.read_text()

        # Accept either legacy AGENT_STATUS or json:contract plan_status
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

    def test_template_documents_cross_agent_context(self, template_content):
        """Template should document how agents receive prior findings."""
        lowered = template_content.lower()
        assert "chaining" in lowered or "cross-agent" in lowered or "prior" in lowered, (
            "Template must document cross-agent context passing"
        )

    def test_template_documents_contract_repair_retry_cap(self, template_content, package_root):
        """Contract repair retry cap must be documented in the template or agent-protocol skill."""
        agent_protocol_path = package_root / "skills" / "agent-protocol" / "SKILL.md"
        combined = template_content
        if agent_protocol_path.exists():
            combined += "\n" + agent_protocol_path.read_text()

        assert "## Contract Repair" in combined, (
            "Contract repair section must be documented in template or agent-protocol"
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
