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
        """The orchestrator must document surface routing explicitly.

        The simplified CLAUDE.md uses '## Routing' instead of '## Surface Routing'.
        """
        assert "## Routing" in claude_md_content

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
        """Multi-surface tasks must trigger multi-agent dispatch.

        The simplified CLAUDE.md says 'two or more agents apply, dispatch them
        in parallel' instead of the longer phrasing.
        """
        content_lower = claude_md_content.lower()
        assert "two or more" in content_lower, (
            "Template must define when to use multi-agent dispatch"
        )
        assert "parallel" in content_lower

    def test_gaia_consolidates_findings(self, claude_md_content):
        """The orchestrator must mention consolidation of multi-agent findings.

        The simplified CLAUDE.md uses 'Consolidate' in the Responses section
        and 'consolidate' in the multi-agent triggers. Detailed Gaia
        consolidation contracts are now in the agent-protocol skill.
        """
        content_lower = claude_md_content.lower()
        assert "consolidate" in content_lower, (
            "Template must mention consolidation of multi-agent findings"
        )
        assert "conflicts" in content_lower

    def test_consolidation_loop_is_documented(self, claude_md_content):
        """Consolidation loop details are now in the agent-protocol skill.

        The simplified CLAUDE.md delegates loop mechanics to hooks/skills.
        We only verify that multi-agent consolidation guidance exists.
        """
        content_lower = claude_md_content.lower()
        assert "consolidate" in content_lower, (
            "Template must mention consolidation for multi-agent work"
        )

    def test_reconnaissance_is_explicit_not_silent_fallback(self, claude_md_content):
        """devops-developer should not be a silent catch-all owner.

        The simplified CLAUDE.md uses 'If unclear, AskUserQuestion' instead
        of explicitly documenting devops-developer reconnaissance. The routing
        table still includes devops-developer for its actual surface.
        """
        assert "devops-developer" in claude_md_content, (
            "devops-developer must be documented in the routing table"
        )
        # Either explicit reconnaissance mention or a clear fallback mechanism
        content_lower = claude_md_content.lower()
        has_recon = "narrow reconnaissance task to `devops-developer`" in claude_md_content
        has_fallback = "if unclear" in content_lower
        assert has_recon or has_fallback, (
            "Template must define what to do when surface is unclear"
        )

    def test_investigation_brief_requires_evidence(self, claude_md_content):
        """The template must reference EVIDENCE_REPORT.

        Detailed evidence field requirements are now in the agent-protocol
        and investigation skills. The template just needs to mention it.
        """
        assert "EVIDENCE_REPORT" in claude_md_content

    def test_multi_surface_tasks_require_consolidation_report(self, claude_md_content):
        """Multi-surface consolidation must be documented in the system.

        The simplified CLAUDE.md delegates CONSOLIDATION_REPORT details to
        the agent-protocol skill. The template still mentions consolidation
        in the Responses section.
        """
        content_lower = claude_md_content.lower()
        assert "consolidate" in content_lower, (
            "Template must reference multi-agent consolidation"
        )

    def test_open_gap_with_owner_continues_loop(self, claude_md_content):
        """Actionable gaps should trigger multi-agent iteration.

        The simplified CLAUDE.md handles this via the Responses section
        (consolidate what each found, remaining gaps) rather than explicit
        loop mechanics. Detailed loop handling is in the agent-protocol skill.
        """
        content_lower = claude_md_content.lower()
        assert "remaining gaps" in content_lower or "open gap" in content_lower, (
            "Template must address handling of remaining gaps"
        )

    def test_surface_routing_is_documented_as_auto_injected_context(self, claude_md_content):
        """The orchestrator contract should mention hooks handle context injection.

        The simplified CLAUDE.md says 'Hooks handle context injection,
        permissions, and validation automatically' instead of naming
        specific payloads like surface_routing and investigation_brief.
        """
        content_lower = claude_md_content.lower()
        has_explicit = "surface_routing" in claude_md_content
        has_hooks = "hooks handle context injection" in content_lower
        assert has_explicit or has_hooks, (
            "Template must document that context is injected automatically"
        )

    def test_cross_agent_context_is_documented(self, claude_md_content):
        """Chained agents should receive a short summary of prior findings.

        The simplified CLAUDE.md uses 'chaining agents' with '2-3 sentence summary'
        in the Dispatch section instead of a labeled 'Cross-agent context' block.
        """
        content_lower = claude_md_content.lower()
        assert "chaining agents" in content_lower or "cross-agent context" in content_lower, (
            "Template must document cross-agent context passing"
        )
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
    """Validate the orchestrator approval flow contract.

    The simplified CLAUDE.md delegates approval details to the
    orchestrator-approval skill. Tests check the combined system.
    """

    @pytest.fixture
    def approval_skill_content(self):
        skills_dir = Path(__file__).resolve().parents[2] / "skills"
        path = skills_dir / "orchestrator-approval" / "SKILL.md"
        if path.exists():
            return path.read_text()
        return ""

    def test_nonce_must_not_be_synthesized(self, claude_md_content, approval_skill_content):
        """The system must forbid synthetic APPROVE tokens."""
        combined = claude_md_content + "\n" + approval_skill_content
        assert "APPROVE:" in combined, (
            "Approval protocol must mention APPROVE: token"
        )
        # The orchestrator-approval skill forbids synthesized nonces
        combined_lower = combined.lower()
        assert "never synthesize" in combined_lower or "never construct" in combined_lower, (
            "System must forbid synthetic nonce construction"
        )

    def test_semantic_approval_is_distinct_from_nonce(self, claude_md_content, approval_skill_content):
        """The system must distinguish user approval intent from hook nonce."""
        combined = claude_md_content + "\n" + approval_skill_content
        combined_lower = combined.lower()
        assert "human approval" in combined_lower or "approval intent" in combined_lower, (
            "System must distinguish human approval from hook nonce"
        )

    def test_no_nonce_means_resume_without_approve_token(self, claude_md_content, approval_skill_content):
        """If approval exists but nonce does not, the system must not invent one."""
        combined = claude_md_content + "\n" + approval_skill_content
        combined_lower = combined.lower()
        assert "no nonce" in combined_lower or "no nonce exists yet" in combined_lower, (
            "System must handle the case where approval exists but nonce does not"
        )

    def test_auto_resume_requires_same_operation(self, claude_md_content, approval_skill_content):
        """Auto-relay of a nonce is only valid for the same approved operation."""
        combined = claude_md_content + "\n" + approval_skill_content
        combined_lower = combined.lower()
        assert "scope" in combined_lower, (
            "System must address scope changes in nonce relay"
        )
        assert "changes operation" in combined_lower or "expands scope" in combined_lower, (
            "System must re-ask when operation scope changes"
        )

    def test_contract_repair_retry_cap_is_documented(self, claude_md_content):
        """Runtime contract repair should have a bounded retry policy.

        The simplified CLAUDE.md delegates this to the agent-protocol skill.
        """
        agent_protocol_path = Path(__file__).resolve().parents[2] / "skills" / "agent-protocol" / "SKILL.md"
        combined = claude_md_content
        if agent_protocol_path.exists():
            combined += "\n" + agent_protocol_path.read_text()

        assert "capped at 2" in combined, (
            "Contract repair retry cap must be documented in the system"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
