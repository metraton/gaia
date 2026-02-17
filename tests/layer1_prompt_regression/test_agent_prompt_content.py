"""
Test agent prompt content for required patterns and references.

Validates that agent prompts contain expected keywords, sections,
and cross-references that ensure correct agent behavior.
"""

import pytest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import parse_frontmatter


class TestTerraformArchitect:
    """terraform-architect must enforce plan-before-apply."""

    @pytest.fixture
    def tf_content(self, agents_dir):
        return (agents_dir / "terraform-architect.md").read_text()

    def test_mentions_plan_before_apply(self, tf_content):
        """Must mention investigating/planning before applying."""
        content_lower = tf_content.lower()
        assert any(phrase in content_lower for phrase in [
            "plan before apply", "before acting", "investigate",
            "plan first", "before apply", "before executing",
        ]), "terraform-architect should mention plan-before-apply pattern"

    def test_mentions_t3_approval(self, tf_content):
        """Must mention T3 approval requirement."""
        assert "T3" in tf_content, \
            "terraform-architect should mention T3 tier"
        content_lower = tf_content.lower()
        assert "approval" in content_lower, \
            "terraform-architect should mention approval requirement"

    def test_mentions_terraform_commands(self, tf_content):
        """Should reference core terraform commands."""
        assert "terraform" in tf_content.lower()


class TestCloudTroubleshooter:
    """cloud-troubleshooter must be read-only."""

    @pytest.fixture
    def ct_content(self, agents_dir):
        return (agents_dir / "cloud-troubleshooter.md").read_text()

    def test_indicates_read_only(self, ct_content):
        """Must indicate T0-T2 only or read-only."""
        content_lower = ct_content.lower()
        has_read_only = any(phrase in content_lower for phrase in [
            "t0-t2", "read-only", "read only", "t3 forbidden",
            "strictly read", "diagnostic",
        ])
        assert has_read_only, \
            "cloud-troubleshooter should indicate read-only/T0-T2 operation"

    def test_does_not_encourage_apply(self, ct_content):
        """Should not tell the agent to run apply/delete commands."""
        content_lower = ct_content.lower()
        # Check that the agent doc doesn't instruct to run destructive commands
        dangerous_instructions = [
            "run kubectl apply",
            "run terraform apply",
            "execute kubectl delete",
        ]
        for instruction in dangerous_instructions:
            assert instruction not in content_lower, \
                f"cloud-troubleshooter should not instruct: {instruction}"


class TestAllAgentsCommon:
    """Common requirements for all agent prompts."""

    def test_all_agents_have_tldr(self, all_agent_files):
        """All project agents should have a TL;DR section."""
        # speckit-planner may not have TL;DR, so we check project agents
        project_agents = {"terraform-architect", "gitops-operator",
                          "cloud-troubleshooter", "devops-developer"}
        for agent_file in all_agent_files:
            if agent_file.stem not in project_agents:
                continue
            content = agent_file.read_text()
            assert "TL;DR" in content or "tl;dr" in content.lower(), \
                f"{agent_file.name} should have a TL;DR section"

    def test_all_agents_mention_tiers(self, all_agent_files):
        """All agents should reference security tiers."""
        for agent_file in all_agent_files:
            content = agent_file.read_text()
            content_lower = content.lower()
            has_tier_ref = any(t in content for t in ["T0", "T1", "T2", "T3"]) or \
                           any(t in content_lower for t in ["tier", "security", "scope", "read-only"])
            assert has_tier_ref, \
                f"{agent_file.name} should reference security tiers"

    def test_project_agents_reference_agent_protocol(self, all_agent_files):
        """Project agents should reference agent-protocol skill."""
        project_agents = {"terraform-architect", "gitops-operator",
                          "cloud-troubleshooter", "devops-developer"}
        for agent_file in all_agent_files:
            if agent_file.stem not in project_agents:
                continue
            fm = parse_frontmatter(agent_file.read_text())
            skills = fm.get("skills", [])
            assert "agent-protocol" in skills, \
                f"{agent_file.name} should reference agent-protocol skill"

    def test_project_agents_reference_context_updater(self, all_agent_files):
        """Project agents should reference context-updater skill."""
        project_agents = {"terraform-architect", "gitops-operator",
                          "cloud-troubleshooter", "devops-developer"}
        for agent_file in all_agent_files:
            if agent_file.stem not in project_agents:
                continue
            fm = parse_frontmatter(agent_file.read_text())
            skills = fm.get("skills", [])
            assert "context-updater" in skills, \
                f"{agent_file.name} should reference context-updater skill"

    def test_all_agents_have_heading_after_frontmatter(self, all_agent_files):
        """All agents should have content after frontmatter."""
        for agent_file in all_agent_files:
            content = agent_file.read_text()
            if content.startswith("---"):
                try:
                    end = content.index("---", 3)
                    body = content[end + 3:].strip()
                    assert len(body) > 50, \
                        f"{agent_file.name} body too short ({len(body)} chars)"
                except ValueError:
                    pytest.fail(f"{agent_file.name} malformed frontmatter")


class TestAgentDescriptionAlignment:
    """Agent descriptions should align with their actual purpose."""

    def test_terraform_description_mentions_iac(self, agents_dir):
        """terraform-architect description should mention IaC/Terraform."""
        fm = parse_frontmatter((agents_dir / "terraform-architect.md").read_text())
        desc = fm.get("description", "").lower()
        assert any(w in desc for w in ["terraform", "iac", "infrastructure"]), \
            "terraform-architect description should mention Terraform/IaC"

    def test_gitops_description_mentions_kubernetes(self, agents_dir):
        """gitops-operator description should mention Kubernetes/GitOps."""
        fm = parse_frontmatter((agents_dir / "gitops-operator.md").read_text())
        desc = fm.get("description", "").lower()
        assert any(w in desc for w in ["kubernetes", "gitops", "k8s"]), \
            "gitops-operator description should mention Kubernetes/GitOps"

    def test_cloud_troubleshooter_description_mentions_diagnostic(self, agents_dir):
        """cloud-troubleshooter description should mention diagnostics."""
        fm = parse_frontmatter((agents_dir / "cloud-troubleshooter.md").read_text())
        desc = fm.get("description", "").lower()
        assert any(w in desc for w in ["diagnostic", "troubleshoot", "cloud"]), \
            "cloud-troubleshooter description should mention diagnostics"

    def test_devops_description_mentions_code(self, agents_dir):
        """devops-developer description should mention code/application."""
        fm = parse_frontmatter((agents_dir / "devops-developer.md").read_text())
        desc = fm.get("description", "").lower()
        assert any(w in desc for w in ["code", "application", "developer", "devops"]), \
            "devops-developer description should mention code/applications"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
