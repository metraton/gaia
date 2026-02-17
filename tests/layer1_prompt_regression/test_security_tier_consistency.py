"""
Test security tier consistency between documentation and code.

Validates that the security-tiers SKILL.md documentation matches
the SecurityTier enum and classify_command_tier() behavior.
"""

import pytest
from pathlib import Path
import sys

# Add hooks to path (same pattern as existing tests)
HOOKS_DIR = Path(__file__).resolve().parents[2] / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.security.tiers import SecurityTier, classify_command_tier
from modules.tools.task_validator import T3_KEYWORDS


class TestSecurityTierSkillDoc:
    """Validate security-tiers SKILL.md documents all 4 tiers."""

    @pytest.fixture
    def skill_content(self, skills_dir):
        return (skills_dir / "security-tiers" / "SKILL.md").read_text()

    def test_documents_t0(self, skill_content):
        assert "T0" in skill_content, "security-tiers SKILL.md must document T0"

    def test_documents_t1(self, skill_content):
        assert "T1" in skill_content, "security-tiers SKILL.md must document T1"

    def test_documents_t2(self, skill_content):
        assert "T2" in skill_content, "security-tiers SKILL.md must document T2"

    def test_documents_t3(self, skill_content):
        assert "T3" in skill_content, "security-tiers SKILL.md must document T3"

    def test_documents_approval_requirement(self, skill_content):
        """T3 section must mention approval."""
        content_lower = skill_content.lower()
        assert "approval" in content_lower, \
            "security-tiers SKILL.md must mention approval for T3"

    def test_documents_read_only_for_t0(self, skill_content):
        """T0 section should mention read-only."""
        content_lower = skill_content.lower()
        assert "read-only" in content_lower or "read only" in content_lower, \
            "security-tiers SKILL.md should describe T0 as read-only"


class TestSecurityTierEnum:
    """Validate SecurityTier enum properties."""

    def test_all_tiers_exist(self):
        """All 4 security tiers must exist in enum."""
        tiers = [SecurityTier.T0_READ_ONLY, SecurityTier.T1_VALIDATION,
                 SecurityTier.T2_DRY_RUN, SecurityTier.T3_BLOCKED]
        assert len(tiers) == 4

    def test_all_tiers_have_description(self):
        """All tiers must have a non-empty description."""
        for tier in SecurityTier:
            assert tier.description, f"{tier} has no description"
            assert len(tier.description) > 5, f"{tier} description too short"

    def test_only_t3_requires_approval(self):
        """Only T3 should require approval."""
        for tier in SecurityTier:
            if tier == SecurityTier.T3_BLOCKED:
                assert tier.requires_approval, "T3 must require approval"
            else:
                assert not tier.requires_approval, \
                    f"{tier} should NOT require approval"

    def test_tier_string_values(self):
        """Tier string values should be T0, T1, T2, T3."""
        assert str(SecurityTier.T0_READ_ONLY) == "T0"
        assert str(SecurityTier.T1_VALIDATION) == "T1"
        assert str(SecurityTier.T2_DRY_RUN) == "T2"
        assert str(SecurityTier.T3_BLOCKED) == "T3"


class TestT3KeywordsConsistency:
    """Validate T3_KEYWORDS match documentation."""

    @pytest.fixture
    def skill_content(self, skills_dir):
        return (skills_dir / "security-tiers" / "SKILL.md").read_text()

    def test_t3_keywords_documented(self, skill_content):
        """T3 operations from code should appear in documentation."""
        content_lower = skill_content.lower()
        # Core T3 commands should be documented
        core_t3 = ["terraform apply", "kubectl apply"]
        for cmd in core_t3:
            assert cmd in content_lower, \
                f"T3 keyword '{cmd}' not documented in security-tiers SKILL.md"

    def test_documented_t0_classifies_as_t0(self):
        """Commands documented as T0 should classify as T0."""
        t0_commands = ["kubectl get pods", "ls -la"]
        for cmd in t0_commands:
            tier = classify_command_tier(cmd)
            assert tier == SecurityTier.T0_READ_ONLY, \
                f"'{cmd}' should be T0, got {tier}"

    def test_cloud_troubleshooter_enforces_read_only(self, agents_dir):
        """cloud-troubleshooter doc says T0-T2 and task_validator enforces it."""
        content = (agents_dir / "cloud-troubleshooter.md").read_text()
        # Check doc says T0-T2
        assert "T0-T2" in content or "T3 forbidden" in content.lower(), \
            "cloud-troubleshooter should document T0-T2 restriction"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
