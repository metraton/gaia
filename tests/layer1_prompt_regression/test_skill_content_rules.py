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
        """Must document json:contract block format."""
        assert "json:contract" in content, \
            "agent-protocol must document json:contract block format"

    def test_has_plan_status(self, content):
        """Must document plan_status field."""
        assert "plan_status" in content, \
            "agent-protocol must document plan_status"

    def test_has_pending_steps(self, content):
        """Must document pending_steps field."""
        assert "pending_steps" in content, \
            "agent-protocol must document pending_steps"

    def test_has_evidence_report_section(self, content):
        """Must document evidence_report object with all required fields."""
        assert '"evidence_report"' in content or "evidence_report" in content, \
            "agent-protocol must document evidence_report object"
        for field in [
            "patterns_checked",
            "files_checked",
            "commands_run",
            "key_outputs",
            "verbatim_outputs",
            "cross_layer_impacts",
            "open_gaps",
        ]:
            assert field in content, \
                f"agent-protocol should document evidence field '{field}'"

    def test_has_consolidation_report_section(self, content):
        """Must document consolidation_report object with required fields."""
        assert '"consolidation_report"' in content or "consolidation_report" in content, \
            "agent-protocol must document consolidation_report object"
        for field in [
            "ownership_assessment",
            "confirmed_findings",
            "suspected_findings",
            "conflicts",
            "next_best_agent",
        ]:
            assert field in content, \
                f"agent-protocol should document consolidation field '{field}'"

    def test_has_approval_request_section(self, content):
        """Must document approval_request object with required fields."""
        assert '"approval_request"' in content or "approval_request" in content, \
            "agent-protocol must document approval_request object"
        for field in [
            "operation",
            "exact_content",
            "scope",
            "risk_level",
            "rollback",
            "verification",
        ]:
            assert field in content, \
                f"agent-protocol should document approval_request field '{field}'"

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
    """context-updater SKILL.md specific rules."""

    @pytest.fixture
    def content(self, skills_dir):
        return (skills_dir / "context-updater" / "SKILL.md").read_text()

    def test_has_context_update_format(self, content):
        """Must document CONTEXT_UPDATE format and write_permissions field."""
        assert "CONTEXT_UPDATE" in content, \
            "context-updater must document CONTEXT_UPDATE format"
        assert "write_permissions" in content, \
            "context-updater should reference injected write_permissions as SSOT"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
