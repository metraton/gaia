"""
Test schema compatibility between orchestrator identity and system components.

Ensures the identity system (ops_identity.py + on-demand skills) contains
the essential structural elements that the hook system and surface routing depend on.
"""

import json
import pytest
from pathlib import Path


class TestSchemaCompatibility:
    """Verify orchestrator identity system contains required elements."""

    @pytest.fixture
    def package_root(self):
        return Path(__file__).resolve().parents[2]

    @pytest.fixture
    def identity_content(self, package_root):
        """Load ops_identity.py and extract the identity string."""
        identity_path = package_root / "hooks" / "modules" / "identity" / "ops_identity.py"
        assert identity_path.exists(), "ops_identity.py not found"
        content = identity_path.read_text()
        return content

    @pytest.fixture
    def dispatch_skill_content(self, package_root):
        skill_path = package_root / "skills" / "project-dispatch" / "SKILL.md"
        assert skill_path.exists(), "project-dispatch/SKILL.md not found"
        return skill_path.read_text()

    @pytest.fixture
    def response_skill_content(self, package_root):
        skill_path = package_root / "skills" / "agent-response" / "SKILL.md"
        assert skill_path.exists(), "agent-response/SKILL.md not found"
        return skill_path.read_text()

    @pytest.fixture
    def gaia_scan_content(self, package_root):
        scan_path = package_root / "bin" / "gaia-scan.py"
        assert scan_path.exists(), "gaia-scan.py not found"
        return scan_path.read_text()

    @pytest.fixture
    def fixture_contexts(self, package_root):
        fixtures_dir = package_root / "tests" / "fixtures"
        contexts = {}
        for f in fixtures_dir.glob("project-context.*.json"):
            contexts[f.stem] = json.loads(f.read_text())
        return contexts

    def test_dispatch_skill_has_surface_routing(self, dispatch_skill_content):
        """project-dispatch skill must contain surfaces and agents."""
        for surface in [
            "live_runtime",
            "gitops_desired_state",
            "terraform_iac",
            "app_ci_tooling",
            "planning_specs",
            "gaia_system",
        ]:
            assert surface in dispatch_skill_content, (
                f"Dispatch skill must include {surface}"
            )

        for agent in [
            "terraform-architect",
            "gitops-operator",
            "cloud-troubleshooter",
            "devops-developer",
            "speckit-planner",
            "gaia-system",
        ]:
            assert agent in dispatch_skill_content, (
                f"Dispatch skill must include {agent}"
            )

    def test_response_skill_documents_plan_status(self, response_skill_content, package_root):
        """Plan status values must be documented in agent-response or agent-protocol skill."""
        agent_protocol_path = package_root / "skills" / "agent-protocol" / "SKILL.md"
        combined = response_skill_content
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
                f"PLAN_STATUS '{status}' not found in agent-response or agent-protocol skill"
            )

    def test_identity_references_tools(self, identity_content):
        """Identity must reference the orchestrator's tools."""
        for tool in ["Agent", "SendMessage", "AskUserQuestion", "Skill"]:
            assert tool in identity_content, (
                f"Identity must reference {tool}"
            )

    def test_identity_references_skills(self, identity_content):
        """Identity must tell orchestrator to load on-demand skills."""
        assert "project-dispatch" in identity_content
        assert "agent-response" in identity_content

    def test_fixture_contexts_have_expected_structure(self, fixture_contexts):
        if not fixture_contexts:
            pytest.skip("No fixture project-context files found")
        for name, ctx in fixture_contexts.items():
            assert "metadata" in ctx, f"{name}: missing metadata"
            assert "sections" in ctx, f"{name}: missing sections"

    def test_gaia_scan_writes_project_context(self, gaia_scan_content):
        assert "project-context" in gaia_scan_content
