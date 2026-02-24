"""
Test skill content rules - validate that SKILL.md files
contain expected sections and patterns for their specific purpose.
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

    def test_has_approval_section(self, content):
        """Must have an approval section or mention approval protocol."""
        content_lower = content.lower()
        assert "approval" in content_lower, \
            "security-tiers should have approval section"

    def test_has_tier_definitions_table(self, content):
        """Should have a tier definitions table."""
        assert "Tier" in content and "|" in content, \
            "security-tiers should have a tier definitions table"

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
        """Must document AGENT_STATUS format."""
        assert "AGENT_STATUS" in content, \
            "agent-protocol must document AGENT_STATUS"

    def test_has_plan_status(self, content):
        """Must document PLAN_STATUS values."""
        assert "PLAN_STATUS" in content, \
            "agent-protocol must document PLAN_STATUS"

    def test_has_pending_steps(self, content):
        """Must document PENDING_STEPS."""
        assert "PENDING_STEPS" in content, \
            "agent-protocol must document PENDING_STEPS"

    def test_documents_all_valid_statuses(self, content):
        """Must document all valid PLAN_STATUS values."""
        statuses = ["INVESTIGATING", "PLANNING", "PENDING_APPROVAL",
                    "APPROVED_EXECUTING", "FIXING", "COMPLETE",
                    "BLOCKED", "NEEDS_INPUT"]
        for status in statuses:
            assert status in content, \
                f"agent-protocol should document PLAN_STATUS '{status}'"


class TestContextUpdaterSkill:
    """context-updater SKILL.md specific rules."""

    @pytest.fixture
    def content(self, skills_dir):
        return (skills_dir / "context-updater" / "SKILL.md").read_text()

    def test_has_context_update_format(self, content):
        """Must document CONTEXT_UPDATE format."""
        assert "CONTEXT_UPDATE" in content, \
            "context-updater must document CONTEXT_UPDATE format"

    def test_documents_merge_rules(self, content):
        """Should document merge rules."""
        content_lower = content.lower()
        assert "merge" in content_lower, \
            "context-updater should document merge rules"


class TestOutputFormatSkill:
    """output-format SKILL.md specific rules."""

    @pytest.fixture
    def content(self, skills_dir):
        return (skills_dir / "output-format" / "SKILL.md").read_text()

    def test_has_status_icons_table(self, content):
        """Must have a status icons table."""
        assert "Icon" in content or "icon" in content.lower(), \
            "output-format should have icon documentation"

    def test_has_standard_icons(self, content):
        """Must document standard status icons."""
        # These icons are used consistently across agents
        icons = ["✅", "❌", "⚠️"]
        for icon in icons:
            assert icon in content, \
                f"output-format should document icon '{icon}'"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
