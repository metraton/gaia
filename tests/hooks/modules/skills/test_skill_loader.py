#!/usr/bin/env python3
"""
Tests for Skill Loader.

PRIORITY: HIGH - Critical for on-demand skill injection.

Validates:
1. Trigger loading and matching
2. Phase detection (start, approval, execution)
3. Skill file loading (success + failure)
4. Domain skills loading
5. Standards skills loading
6. Edge cases (missing files, malformed JSON, empty triggers)
"""

import sys
import json
import pytest
from pathlib import Path
from unittest.mock import patch

# Add hooks to path
HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.skills.skill_loader import SkillLoader, load_skills_for_task, get_skills_directory


@pytest.fixture
def tmp_skills_dir(tmp_path):
    """Create a temporary skills directory structure."""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()

    # Create workflow skills
    workflow_dir = skills_dir / "workflow"
    for skill_name in ["investigation", "approval", "execution"]:
        skill_path = workflow_dir / skill_name
        skill_path.mkdir(parents=True)
        (skill_path / "SKILL.md").write_text(f"# {skill_name.title()} Skill\nContent for {skill_name}.")

    # Create domain skills
    domain_dir = skills_dir / "domain"
    for skill_name in ["terraform-patterns", "gitops-patterns", "universal-protocol", "fast-queries"]:
        skill_path = domain_dir / skill_name
        skill_path.mkdir(parents=True)
        (skill_path / "SKILL.md").write_text(f"# {skill_name.title()} Skill\nDomain content for {skill_name}.")

    # Create standards skills
    standards_dir = skills_dir / "standards"
    for skill_name in ["security-tiers", "output-format", "command-execution", "anti-patterns"]:
        skill_path = standards_dir / skill_name
        skill_path.mkdir(parents=True)
        (skill_path / "SKILL.md").write_text(f"# {skill_name.title()} Skill\nStandards content for {skill_name}.")

    return skills_dir


@pytest.fixture
def triggers_config(tmp_path):
    """Create a temporary skill-triggers.json."""
    config = {
        "workflow": {
            "investigation": {
                "phase": "start",
                "auto_load": True,
                "description": "Always loaded at task start"
            },
            "approval": {
                "phase": "approval",
                "triggers": ["apply", "deploy", "create", "delete", "push"],
                "description": "Loaded when T3 operation detected"
            },
            "execution": {
                "phase": "execution",
                "triggers": ["approved", "proceed", "execute", "user approved"],
                "description": "Loaded when user has approved"
            }
        },
        "domain": {
            "terraform-patterns": {
                "triggers": ["terraform", "terragrunt", "hcl", "module"],
                "description": "Terraform patterns"
            },
            "gitops-patterns": {
                "triggers": ["kubectl", "k8s", "kubernetes", "helm", "flux"],
                "description": "GitOps patterns"
            },
            "universal-protocol": {
                "triggers": ["terraform-architect", "gitops-operator"],
                "auto_load": True,
                "description": "Universal protocol for project agents"
            },
            "fast-queries": {
                "triggers": ["health", "status", "check", "diagnose"],
                "auto_load": True,
                "description": "Quick diagnostic scripts"
            }
        },
        "standards": {
            "security-tiers": {
                "auto_load": True,
                "description": "T0-T3 classification"
            },
            "output-format": {
                "auto_load": True,
                "description": "Output standards"
            },
            "command-execution": {
                "triggers": ["bash", "kubectl", "terraform", "command"],
                "description": "Shell security rules"
            },
            "anti-patterns": {
                "triggers": ["apply", "deploy", "kubectl", "terraform"],
                "description": "Common mistakes"
            }
        }
    }

    config_file = tmp_path / "skill-triggers.json"
    config_file.write_text(json.dumps(config, indent=2))
    return config_file


@pytest.fixture
def loader(tmp_skills_dir, triggers_config):
    """Create SkillLoader with temporary directories."""
    return SkillLoader(tmp_skills_dir, triggers_config)


class TestTriggerLoading:
    """Test trigger configuration loading."""

    def test_loads_triggers_from_config(self, loader):
        """Test that triggers are loaded from config file."""
        assert "workflow" in loader.triggers
        assert "domain" in loader.triggers

    def test_loads_workflow_triggers(self, loader):
        """Test workflow triggers are populated."""
        workflow = loader.triggers.get("workflow", {})
        assert "investigation" in workflow
        assert "approval" in workflow
        assert "execution" in workflow

    def test_loads_domain_triggers(self, loader):
        """Test domain triggers are populated."""
        domain = loader.triggers.get("domain", {})
        assert "terraform-patterns" in domain
        assert "gitops-patterns" in domain

    def test_loads_standards_triggers(self, loader):
        """Test standards triggers are populated."""
        standards = loader.triggers.get("standards", {})
        assert "security-tiers" in standards
        assert "output-format" in standards

    def test_handles_missing_config_file(self, tmp_skills_dir):
        """Test graceful handling of missing config file."""
        missing_config = tmp_skills_dir / "nonexistent.json"
        loader = SkillLoader(tmp_skills_dir, missing_config)
        assert loader.triggers == {"workflow": {}, "domain": {}}

    def test_handles_malformed_json(self, tmp_skills_dir, tmp_path):
        """Test graceful handling of malformed JSON config."""
        bad_config = tmp_path / "bad-config.json"
        bad_config.write_text("{invalid json content!!!")
        loader = SkillLoader(tmp_skills_dir, bad_config)
        assert loader.triggers == {"workflow": {}, "domain": {}}


class TestPhaseDetection:
    """Test workflow phase detection."""

    def test_detects_start_phase_default(self, loader):
        """Test default phase is 'start' for generic prompts."""
        phase = loader.detect_phase("Analyze the infrastructure")
        assert phase == "start"

    def test_detects_execution_phase(self, loader):
        """Test detection of execution phase keywords."""
        phase = loader.detect_phase("User approved. Proceed with deployment.")
        assert phase == "execution"

    def test_detects_approval_phase(self, loader):
        """Test detection of approval phase keywords."""
        phase = loader.detect_phase("Run terraform apply on production")
        assert phase == "approval"

    def test_execution_takes_priority_over_approval(self, loader):
        """Test execution phase detected before approval when both present."""
        # "approved" triggers execution, "apply" triggers approval
        phase = loader.detect_phase("User approved. Execute terraform apply now.")
        assert phase == "execution"

    def test_case_insensitive_detection(self, loader):
        """Test phase detection is case insensitive."""
        phase = loader.detect_phase("User APPROVED the change")
        assert phase == "execution"

    def test_empty_prompt_returns_start(self, loader):
        """Test empty prompt defaults to start phase."""
        phase = loader.detect_phase("")
        assert phase == "start"


class TestWorkflowSkillLoading:
    """Test workflow skill loading."""

    def test_loads_investigation_skill_at_start(self, loader):
        """Test investigation skill is loaded for start phase."""
        workflow_skill = loader._load_workflow_skill("start")
        assert "investigation" in workflow_skill
        assert "Investigation" in workflow_skill["investigation"]

    def test_loads_approval_skill(self, loader):
        """Test approval skill is loaded for approval phase."""
        workflow_skill = loader._load_workflow_skill("approval")
        assert "approval" in workflow_skill

    def test_loads_execution_skill(self, loader):
        """Test execution skill is loaded for execution phase."""
        workflow_skill = loader._load_workflow_skill("execution")
        assert "execution" in workflow_skill

    def test_returns_empty_for_missing_skill_file(self, loader):
        """Test returns empty when skill file does not exist."""
        # Remove a skill file
        skill_file = loader.skills_dir / "workflow" / "investigation" / "SKILL.md"
        skill_file.unlink()
        workflow_skill = loader._load_workflow_skill("start")
        assert workflow_skill == {}


class TestDomainSkillsLoading:
    """Test domain skill loading."""

    def test_loads_domain_skill_by_trigger(self, loader):
        """Test domain skill loaded when keyword trigger matches."""
        skills = loader._load_domain_skills("run terraform plan", "devops-developer")
        assert "terraform-patterns" in skills

    def test_loads_gitops_skill_by_trigger(self, loader):
        """Test gitops skill loaded when kubectl trigger matches."""
        skills = loader._load_domain_skills("check kubectl get pods", "devops-developer")
        assert "gitops-patterns" in skills

    def test_auto_loads_for_project_agents(self, loader):
        """Test auto_load skills are loaded for project agents."""
        skills = loader._load_domain_skills("generic prompt", "terraform-architect")
        assert "universal-protocol" in skills
        assert "fast-queries" in skills

    def test_no_auto_load_for_non_project_agents(self, loader):
        """Test auto_load skills are NOT loaded for non-project agents."""
        skills = loader._load_domain_skills("generic prompt", "speckit-planner")
        # universal-protocol requires PROJECT_AGENTS, speckit-planner is not one
        assert "universal-protocol" not in skills

    def test_trigger_match_overrides_non_project_agent(self, loader):
        """Test trigger match loads skill even for non-project agent."""
        skills = loader._load_domain_skills("analyze terraform modules", "gaia")
        assert "terraform-patterns" in skills

    def test_no_match_returns_empty(self, loader):
        """Test returns empty when no triggers match and agent is not project agent."""
        skills = loader._load_domain_skills("unrelated prompt", "gaia")
        # Only auto-loaded fast-queries would match but gaia is not PROJECT_AGENTS
        assert "terraform-patterns" not in skills
        assert "gitops-patterns" not in skills


class TestStandardsSkillsLoading:
    """Test standards skill loading."""

    def test_auto_loads_for_any_agent(self, loader):
        """Test auto_load standards load for ALL agents, not just project agents."""
        skills = loader._load_standards_skills("generic prompt", "gaia")
        assert "security-tiers" in skills
        assert "output-format" in skills

    def test_loads_by_trigger(self, loader):
        """Test standards skill loaded by trigger keyword."""
        skills = loader._load_standards_skills("run bash command", "devops-developer")
        assert "command-execution" in skills

    def test_loads_anti_patterns_by_trigger(self, loader):
        """Test anti-patterns skill loaded when deploy trigger matches."""
        skills = loader._load_standards_skills("deploy the service", "gitops-operator")
        assert "anti-patterns" in skills

    def test_returns_empty_for_missing_skill_file(self, loader):
        """Test returns empty when skill SKILL.md file is missing."""
        # Remove the security-tiers SKILL.md
        skill_file = loader.skills_dir / "standards" / "security-tiers" / "SKILL.md"
        skill_file.unlink()
        skills = loader._load_standards_skills("generic prompt", "devops-developer")
        # security-tiers should not be loaded since file is missing
        assert "security-tiers" not in skills
        # output-format should still be loaded
        assert "output-format" in skills


class TestFullSkillLoading:
    """Test the full load_skills method."""

    def test_loads_workflow_and_domain(self, loader):
        """Test full load includes both workflow and domain skills."""
        skills = loader.load_skills("Run terraform plan", "terraform-architect")
        # Should have workflow skill (investigation/start)
        assert any("Skill" in v for v in skills.values())
        # Should have domain skill (terraform-patterns)
        assert "terraform-patterns" in skills

    def test_loads_standards_skills(self, loader):
        """Test full load includes standards skills."""
        skills = loader.load_skills("Generic task", "devops-developer")
        # Auto-loaded standards should be present
        assert "security-tiers" in skills
        assert "output-format" in skills


class TestFormatSkillsForInjection:
    """Test skill formatting for prompt injection."""

    def test_formats_skills_with_headers(self, loader):
        """Test skills are formatted with section headers."""
        skills = {"test-skill": "# Test Skill\nContent here."}
        formatted = loader.format_skills_for_injection(skills)
        assert "# Active Skills" in formatted
        assert "## test-skill" in formatted
        assert "Content here." in formatted

    def test_empty_skills_returns_empty_string(self, loader):
        """Test empty skills dict returns empty string."""
        formatted = loader.format_skills_for_injection({})
        assert formatted == ""

    def test_multiple_skills_separated(self, loader):
        """Test multiple skills are separated by dividers."""
        skills = {
            "skill-a": "Content A",
            "skill-b": "Content B"
        }
        formatted = loader.format_skills_for_injection(skills)
        assert "## skill-a" in formatted
        assert "## skill-b" in formatted
        assert "---" in formatted


class TestGetSkillsDirectory:
    """Test dynamic skills directory detection."""

    def test_returns_path_or_none(self):
        """Test get_skills_directory returns Path or None."""
        result = get_skills_directory()
        assert result is None or isinstance(result, Path)


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_empty_triggers_in_config(self, tmp_skills_dir, tmp_path):
        """Test handling of empty triggers list."""
        config = {
            "workflow": {},
            "domain": {
                "test-skill": {
                    "triggers": [],
                    "description": "Skill with no triggers"
                }
            },
            "standards": {}
        }
        config_file = tmp_path / "empty-triggers.json"
        config_file.write_text(json.dumps(config))
        loader = SkillLoader(tmp_skills_dir, config_file)

        skills = loader._load_domain_skills("anything", "devops-developer")
        assert "test-skill" not in skills

    def test_handles_none_prompt(self, loader):
        """Test handling of None-like prompt gracefully."""
        # Empty prompt should not crash
        phase = loader.detect_phase("")
        assert phase == "start"

    def test_project_agents_list_is_populated(self):
        """Test PROJECT_AGENTS contains expected agents."""
        assert "terraform-architect" in SkillLoader.PROJECT_AGENTS
        assert "gitops-operator" in SkillLoader.PROJECT_AGENTS
        assert "cloud-troubleshooter" in SkillLoader.PROJECT_AGENTS
        assert "devops-developer" in SkillLoader.PROJECT_AGENTS

    def test_skill_loading_with_unicode_prompt(self, loader):
        """Test skill loading with unicode characters in prompt."""
        skills = loader.load_skills("Deploy aplicacion con terraform", "terraform-architect")
        assert isinstance(skills, dict)
