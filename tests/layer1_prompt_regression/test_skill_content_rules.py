"""
Test skill content rules - validate that SKILL.md files
have correct structure and document required schema fields.
"""

import pytest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import parse_frontmatter


class TestAllSkillsCommon:
    """Common requirements for all SKILL.md files."""

    def test_all_skills_have_heading_after_frontmatter(self, all_skill_dirs):
        """All SKILL.md files must have a heading after frontmatter."""
        for skill_dir in all_skill_dirs:
            content = (skill_dir / "SKILL.md").read_text()
            if content.startswith("---"):
                try:
                    end = content.index("---", 3)
                    body = content[end + 3:].strip()
                except ValueError:
                    pytest.fail(f"{skill_dir.name}/SKILL.md malformed frontmatter")
                    continue
            else:
                body = content.strip()

            assert body.startswith("#"), \
                f"{skill_dir.name}/SKILL.md should have a heading after frontmatter"

    def test_all_skills_have_substantial_content(self, all_skill_dirs):
        """All SKILL.md files must have substantial content (>200 chars body)."""
        for skill_dir in all_skill_dirs:
            content = (skill_dir / "SKILL.md").read_text()
            if content.startswith("---"):
                try:
                    end = content.index("---", 3)
                    body = content[end + 3:].strip()
                except ValueError:
                    body = content
            else:
                body = content.strip()

            assert len(body) > 200, \
                f"{skill_dir.name}/SKILL.md body too short ({len(body)} chars)"


class TestSecurityTiersSkill:
    """security-tiers SKILL.md specific rules."""

    @pytest.fixture
    def content(self, skills_dir):
        return (skills_dir / "security-tiers" / "SKILL.md").read_text()

    def test_documents_all_tier_levels(self, content):
        """Must document T0, T1, T2, T3."""
        for tier in ["T0", "T1", "T2", "T3"]:
            assert tier in content, f"security-tiers must document {tier}"


class TestAgentProtocolSkill:
    """agent-protocol SKILL.md specific rules."""

    @pytest.fixture
    def content(self, skills_dir):
        return (skills_dir / "agent-protocol" / "SKILL.md").read_text()

    def test_has_agent_status_section(self, content):
        """Must document agent_contract_handoff block format."""
        assert "agent_contract_handoff" in content, \
            "agent-protocol must document agent_contract_handoff block format"

    def test_has_plan_status(self, content):
        """Must document plan_status field."""
        assert "plan_status" in content, \
            "agent-protocol must document plan_status"

    def test_has_pending_steps(self, content, skills_dir):
        """pending_steps field schema is documented in the owning skill.

        The field-level schema (required status, presence-only trigger)
        migrated out of agent-protocol (produce-side judgment only) into
        agent-contract-handoff, which now OWNS the agent_status sub-field
        table. Assert the token where it actually lives -- mirrors the same
        migration already applied to consolidation_report / approval_request
        below. agent-protocol itself still references the unchanged
        `agent_status` shape generically, without re-deriving the field list.
        """
        handoff = (skills_dir / "agent-contract-handoff" / "SKILL.md").read_text()
        assert "pending_steps" in handoff, \
            "agent-contract-handoff must document pending_steps"
        assert "agent_status" in content, \
            "agent-protocol must still reference the agent_status container"

    def test_has_evidence_report_section(self, content, skills_dir):
        """evidence_report object + all required fields are documented in the owning skill.

        The full field schema migrated out of agent-protocol (produce-side
        judgment only) into agent-contract-handoff, which now OWNS the
        evidence_report sub-field table. Assert the tokens where they
        actually live -- mirrors the same migration already applied to
        consolidation_report / approval_request below. agent-protocol itself
        still references the unchanged `evidence_report` shape generically
        (e.g. in its CLI usage example), without re-deriving the full field
        list.
        """
        assert "evidence_report" in content, \
            "agent-protocol must still reference the evidence_report object"
        handoff = (skills_dir / "agent-contract-handoff" / "SKILL.md").read_text()
        assert "evidence_report" in handoff, \
            "agent-contract-handoff must document evidence_report object"
        for field in [
            "patterns_checked",
            "files_checked",
            "commands_run",
            "key_outputs",
            "verbatim_outputs",
            "cross_layer_impacts",
            "open_gaps",
        ]:
            assert field in handoff, \
                f"agent-contract-handoff should document evidence field '{field}'"

    def test_has_consolidation_report_section(self, skills_dir):
        """consolidation_report object + fields are documented in the owning skill.

        The full field schema migrated out of agent-protocol (produce-side
        judgment only) into agent-contract-handoff, which now OWNS the
        consolidation_report sub-field table. Assert the tokens where they
        actually live.
        """
        handoff = (skills_dir / "agent-contract-handoff" / "SKILL.md").read_text()
        assert "consolidation_report" in handoff, \
            "agent-contract-handoff must document consolidation_report object"
        for field in [
            "ownership_assessment",
            "confirmed_findings",
            "suspected_findings",
            "conflicts",
            "next_best_agent",
        ]:
            assert field in handoff, \
                f"agent-contract-handoff should document consolidation field '{field}'"

    def test_has_approval_request_section(self, skills_dir):
        """approval_request object + fields are documented in the owning skill.

        The approval payload schema migrated out of agent-protocol into
        agent-approval-protocol, which now OWNS the sealed_payload /
        approval_request field set. Assert the tokens where they actually live.
        """
        approval = (skills_dir / "agent-approval-protocol" / "SKILL.md").read_text()
        assert "approval_request" in approval, \
            "agent-approval-protocol must document approval_request object"
        for field in [
            "operation",
            "exact_content",
            "scope",
            "risk_level",
            "rollback",
            "verification",
        ]:
            assert field in approval, \
                f"agent-approval-protocol should document approval_request field '{field}'"

    def test_documents_all_valid_statuses(self, content):
        """Must document all active PLAN_STATUS values.

        The skill documents the 5 active statuses.
        """
        statuses = ["COMPLETE", "NEEDS_INPUT", "APPROVAL_REQUEST",
                    "BLOCKED", "IN_PROGRESS"]
        for status in statuses:
            assert status in content, \
                f"agent-protocol should document PLAN_STATUS '{status}'"


class TestContextUpdaterSkill:
    """agent-contract-handoff SKILL.md context-enrichment rules.

    The context-updater skill was retired; the update_contracts envelope clause
    is now documented in agent-contract-handoff, which is the live source of
    truth for how an agent enriches project-context.
    """

    @pytest.fixture
    def content(self, skills_dir):
        return (skills_dir / "agent-contract-handoff" / "SKILL.md").read_text()

    def test_has_context_update_format(self, content):
        """Must document the update_contracts clause and writable contracts."""
        assert "update_contracts" in content, \
            "agent-contract-handoff must document the update_contracts clause"
        assert ("write_permissions" in content or "writable" in content), \
            "agent-contract-handoff should reference writable contracts as SSOT"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
