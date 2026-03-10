"""
Test surface routing consistency between CLAUDE.md and code.

Validates that the orchestrator documents the final surface-routing model:
- no legacy agent routing table
- all agents are still documented
- multi-surface dispatch and Gaia consolidation are explicit
"""

import pytest
from pathlib import Path
import sys

# Add hooks to path (same pattern as existing tests)
HOOKS_DIR = Path(__file__).resolve().parents[2] / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.tools.task_validator import AVAILABLE_AGENTS, META_AGENTS


class TestSurfaceRoutingContract:
    """Validate CLAUDE.md surface routing matches actual agents."""

    REQUIRED_SURFACES = {
        "live_runtime",
        "gitops_desired_state",
        "terraform_iac",
        "app_ci_tooling",
        "planning_specs",
        "gaia_system",
    }

    def test_surface_routing_section_present(self, claude_md_content):
        """The orchestrator must document surface routing explicitly."""
        assert "## Surface Routing" in claude_md_content

    def test_legacy_agent_routing_removed(self, claude_md_content):
        """The legacy sequential routing section must be gone."""
        assert "## Agent Routing" not in claude_md_content
        assert "routing priority (in order)" not in claude_md_content.lower()

    def test_surface_routing_mentions_all_disk_agents(self, claude_md_content, all_agent_files):
        """Every agent file on disk must still be referenced in the orchestrator contract."""
        disk_agents = {f.stem for f in all_agent_files}
        for agent in disk_agents:
            assert agent in claude_md_content, (
                f"Agent '{agent}' exists on disk but is not documented in CLAUDE.md"
            )

    def test_documented_agents_exist_in_available_agents(self, claude_md_content):
        """Every runtime-available agent must remain documented."""
        for agent in AVAILABLE_AGENTS:
            if not agent[:1].islower():
                continue
            assert agent in claude_md_content, (
                f"AVAILABLE_AGENT '{agent}' not documented in CLAUDE.md"
            )

    def test_available_project_agents_are_documented(self, claude_md_content):
        """All project agents (non-meta) must appear in the routing contract."""
        project_agents = [a for a in AVAILABLE_AGENTS if a not in META_AGENTS]
        for agent in project_agents:
            assert agent in claude_md_content, (
                f"Project agent '{agent}' not documented in CLAUDE.md"
            )

    @pytest.mark.parametrize("surface", sorted(REQUIRED_SURFACES))
    def test_required_surfaces_documented(self, surface, claude_md_content):
        """The final routing contract must enumerate the core surfaces."""
        assert f"`{surface}`" in claude_md_content

    def test_multi_surface_dispatch_documented(self, claude_md_content):
        """Multi-surface tasks must trigger multi-agent dispatch."""
        content_lower = claude_md_content.lower()
        assert "two or more surfaces are active" in content_lower
        assert "parallel" in content_lower
        assert "primary agent for each active surface" in content_lower

    def test_gaia_consolidates_findings(self, claude_md_content):
        """Gaia must explicitly own cross-surface consolidation."""
        assert "Gaia consolidates" in claude_md_content
        assert "conflicts" in claude_md_content
        assert "recommended_action" in claude_md_content

    def test_consolidation_loop_is_documented(self, claude_md_content):
        """Gaia should keep iterating only while gaps are actionable."""
        content_lower = claude_md_content.lower()
        assert "consolidation loop" in content_lower
        assert "clear owner" in content_lower
        assert "2 consolidation rounds after the initial pass" in claude_md_content
        assert "new agent output adds no meaningful evidence" in content_lower

    def test_reconnaissance_is_explicit_not_silent_fallback(self, claude_md_content):
        """devops-developer can do reconnaissance, but not act as a silent catch-all owner."""
        content_lower = claude_md_content.lower()
        assert "narrow reconnaissance task to `devops-developer`" in claude_md_content
        assert "silently treat `devops-developer` as the owner of all ambiguous work" in content_lower

    def test_investigation_brief_requires_evidence(self, claude_md_content):
        """Delegated investigations must ask for evidence, not just conclusions."""
        assert "EVIDENCE_REPORT" in claude_md_content
        for phrase in [
            "patterns checked",
            "files/paths checked",
            "exact commands run",
            "key outputs or evidence",
            "cross-layer impacts",
            "open gaps",
        ]:
            assert phrase in claude_md_content

    def test_multi_surface_tasks_require_consolidation_report(self, claude_md_content):
        """Multi-surface work should require a consolidation-friendly block."""
        assert "CONSOLIDATION_REPORT" in claude_md_content
        for phrase in [
            "ownership assessment",
            "confirmed findings",
            "suspected findings",
            "conflicts",
            "next best agent",
        ]:
            assert phrase in claude_md_content

    def test_open_gap_with_owner_continues_loop(self, claude_md_content):
        """Actionable gaps should trigger another agent round, not immediate closure."""
        assert "Open gap has a clear owner and no user input is needed" in claude_md_content
        assert "Continue the consolidation loop" in claude_md_content

    def test_surface_routing_is_documented_as_auto_injected_context(self, claude_md_content):
        """The orchestrator contract should mention the deterministic routing payload."""
        assert "surface_routing" in claude_md_content
        assert "investigation_brief" in claude_md_content

    def test_cross_agent_context_is_documented(self, claude_md_content):
        """Chained agents should receive a short summary of prior findings."""
        assert "Cross-agent context" in claude_md_content
        assert "2-3 sentence summary" in claude_md_content


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
        assert status in agent_protocol_content, (
            f"PLAN_STATUS '{status}' not documented in agent-protocol/SKILL.md"
        )


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


class TestApprovalFlowContract:
    """Validate the orchestrator approval flow contract in CLAUDE.md."""

    def test_nonce_must_not_be_synthesized(self, claude_md_content):
        """The orchestrator must forbid synthetic APPROVE tokens."""
        assert "Never synthesize `APPROVE:<...>`" in claude_md_content
        assert "APPROVE:commit" in claude_md_content

    def test_semantic_approval_is_distinct_from_nonce(self, claude_md_content):
        """The orchestrator must distinguish user approval intent from hook nonce."""
        assert "Human approval and hook nonce are different things" in claude_md_content
        assert "approval intent" in claude_md_content

    def test_no_nonce_means_resume_without_approve_token(self, claude_md_content):
        """If approval exists but nonce does not, the orchestrator must not invent one."""
        assert "If the user approved earlier but no nonce exists yet" in claude_md_content
        assert "Resume the agent with normal language" in claude_md_content

    def test_auto_resume_requires_same_operation(self, claude_md_content):
        """Auto-relay of a nonce is only valid for the same approved operation."""
        assert "what the user approved" in claude_md_content
        assert "changes operation" in claude_md_content

    def test_contract_repair_retry_cap_is_documented(self, claude_md_content):
        """Runtime contract repair should have a bounded retry policy."""
        assert "capped at 2" in claude_md_content


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
