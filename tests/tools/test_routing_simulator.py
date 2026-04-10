"""
Tests for the routing simulator and skills mapper modules.

Tests cover:
1. RoutingSimulator: surface classification and routing prediction
2. SkillsMapper: agent/skill/surface/contract mapping
3. Frontmatter parsing from agent .md files
4. Integration with real config files

Run: python3 -m pytest tests/tools/test_routing_simulator.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Add the tools directory to sys.path so gaia_simulator package is importable
TOOLS_DIR = Path(__file__).resolve().parents[2] / "tools"
if TOOLS_DIR.is_symlink():
    TOOLS_DIR = TOOLS_DIR.resolve()
sys.path.insert(0, str(TOOLS_DIR))

# Module under test
from gaia_simulator.routing_simulator import (  # noqa: E402
    RoutingResult,
    RoutingSimulator,
    _parse_frontmatter,
    format_routing_result,
)
from gaia_simulator.skills_mapper import (  # noqa: E402
    AgentProfile,
    SkillMapping,
    SkillsMapper,
)


# ============================================================================
# Fixtures: real config paths
# ============================================================================

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PLUGIN_ROOT / "config"
AGENTS_DIR = PLUGIN_ROOT / "agents"
SKILLS_DIR = PLUGIN_ROOT / "skills"


@pytest.fixture
def simulator() -> RoutingSimulator:
    """Create a RoutingSimulator with real config files."""
    return RoutingSimulator(config_dir=CONFIG_DIR, agents_dir=AGENTS_DIR)


@pytest.fixture
def mapper() -> SkillsMapper:
    """Create a SkillsMapper with real config files."""
    return SkillsMapper(
        agents_dir=AGENTS_DIR, skills_dir=SKILLS_DIR, config_dir=CONFIG_DIR
    )


# ============================================================================
# Test frontmatter parsing
# ============================================================================


class TestFrontmatterParsing:
    """Tests for _parse_frontmatter."""

    def test_parse_agent_frontmatter(self):
        content = """---
name: test-agent
description: A test agent
tools: Read, Bash
model: inherit
skills:
  - agent-protocol
  - security-tiers
  - investigation
---

## Identity
"""
        fm = _parse_frontmatter(content)
        assert fm["name"] == "test-agent"
        assert fm["skills"] == ["agent-protocol", "security-tiers", "investigation"]

    def test_no_frontmatter(self):
        assert _parse_frontmatter("# Just a heading") == {}

    def test_empty_skills(self):
        content = """---
name: minimal
---
"""
        fm = _parse_frontmatter(content)
        assert fm.get("name") == "minimal"
        assert "skills" not in fm

    def test_inline_skills(self):
        content = """---
name: inline
skills: [alpha, beta, gamma]
---
"""
        fm = _parse_frontmatter(content)
        assert fm["skills"] == ["alpha", "beta", "gamma"]


# ============================================================================
# Test RoutingSimulator with real configs
# ============================================================================


class TestRoutingSimulator:
    """Tests for RoutingSimulator using real surface-routing.json."""

    def test_kubectl_routes_to_cloud_troubleshooter(self, simulator):
        result = simulator.simulate("kubectl get pods")
        assert result.primary_agent == "cloud-troubleshooter"
        assert "live_runtime" in result.surfaces_active

    def test_terraform_routes_to_terraform_architect(self, simulator):
        result = simulator.simulate("terraform plan")
        assert result.primary_agent == "terraform-architect"
        assert "terraform_iac" in result.surfaces_active

    def test_dockerfile_routes_to_devops_developer(self, simulator):
        result = simulator.simulate("fix the Dockerfile")
        assert result.primary_agent == "developer"
        assert "app_ci_tooling" in result.surfaces_active

    def test_flux_routes_to_gitops_operator(self, simulator):
        result = simulator.simulate("deploy via Flux")
        assert result.primary_agent == "gitops-operator"
        assert "gitops_desired_state" in result.surfaces_active

    def test_skills_loaded_match_agent_frontmatter(self, simulator):
        result = simulator.simulate("kubectl get pods")
        # cloud-troubleshooter should have agent-protocol in skills
        assert "agent-protocol" in result.skills_loaded
        assert "security-tiers" in result.skills_loaded

    def test_context_sections_match_surface_config(self, simulator):
        result = simulator.simulate("terraform plan")
        # terraform_iac surface should include terraform-related sections
        assert len(result.context_sections) > 0

    def test_contracts_populated(self, simulator):
        result = simulator.simulate("kubectl get pods")
        assert len(result.contracts["read"]) > 0

    def test_agent_type_override(self, simulator):
        result = simulator.simulate("some generic task", agent_type="gaia-system")
        assert result.primary_agent == "gaia-system"

    def test_multi_surface_detection(self, simulator):
        # A prompt that touches multiple surfaces
        result = simulator.simulate("kubectl get pods and terraform plan")
        assert result.multi_surface is True
        assert len(result.surfaces_active) > 1

    def test_routing_result_is_dataclass(self, simulator):
        result = simulator.simulate("ls /tmp")
        assert isinstance(result, RoutingResult)
        assert isinstance(result.prompt, str)
        assert isinstance(result.surfaces_active, list)

    def test_tokens_estimate_is_positive_for_matched_surface(self, simulator):
        result = simulator.simulate("kubectl get pods")
        assert result.tokens_estimate > 0

    def test_empty_prompt_returns_result(self, simulator):
        result = simulator.simulate("")
        assert isinstance(result, RoutingResult)
        assert result.confidence == 0.0

    def test_simulate_from_log(self, simulator):
        events = [
            {"prompt": "kubectl get pods"},
            {"prompt": "terraform plan"},
            {"prompt": ""},  # should be skipped
        ]
        results = simulator.simulate_from_log(events)
        assert len(results) == 2

    def test_compare_routing(self, simulator):
        events = [
            {"prompt": "kubectl get pods", "agent": "cloud-troubleshooter"},
            {"prompt": "terraform plan", "agent": "terraform-architect"},
            {"prompt": "fix Dockerfile", "agent": "developer"},
        ]
        comparison = simulator.compare_routing(events)
        assert comparison["total"] == 3
        assert comparison["matches"] + comparison["mismatches"] == 3


# ============================================================================
# Test format_routing_result
# ============================================================================


class TestFormatRoutingResult:
    """Tests for format_routing_result output."""

    def test_format_includes_key_fields(self, simulator):
        result = simulator.simulate("kubectl get pods")
        text = format_routing_result(result)
        assert "ROUTING SIMULATION" in text
        assert "Primary agent:" in text
        assert "cloud-troubleshooter" in text
        assert "Active surfaces:" in text
        assert "Skills loaded:" in text

    def test_format_empty_result(self, simulator):
        result = simulator.simulate("")
        text = format_routing_result(result)
        assert "ROUTING SIMULATION" in text


# ============================================================================
# Test SkillsMapper with real configs
# ============================================================================


class TestSkillsMapper:
    """Tests for SkillsMapper using real agent and skill directories."""

    def test_get_agent_profiles(self, mapper):
        profiles = mapper.get_agent_profiles()
        assert len(profiles) > 0
        names = [p.agent_name for p in profiles]
        assert "cloud-troubleshooter" in names
        assert "developer" in names
        assert "gitops-operator" in names
        assert "terraform-architect" in names
        assert "gaia-system" in names
        assert "speckit-planner" in names

    def test_agent_profiles_have_skills(self, mapper):
        """Specialist agents have skills via frontmatter; orchestrator uses on-demand Skill tool."""
        profiles = mapper.get_agent_profiles()
        for profile in profiles:
            assert isinstance(profile.skills, list)
            if profile.agent_name == "gaia-orchestrator":
                # v5: orchestrator has skills: [] -- loads skills on-demand via Skill tool
                assert profile.skills == [], \
                    "Orchestrator should have empty skills list (uses on-demand Skill tool)"
            else:
                # Specialist agents should have at least agent-protocol
                assert "agent-protocol" in profile.skills

    def test_agent_profiles_have_surfaces(self, mapper):
        profiles = mapper.get_agent_profiles()
        ct_profile = [p for p in profiles if p.agent_name == "cloud-troubleshooter"][0]
        assert "live_runtime" in ct_profile.surfaces

    def test_agent_profiles_have_contracts(self, mapper):
        profiles = mapper.get_agent_profiles()
        for profile in profiles:
            assert isinstance(profile.read_sections, list)
            assert isinstance(profile.write_sections, list)

    def test_get_skill_mappings(self, mapper):
        mappings = mapper.get_skill_mappings()
        assert len(mappings) > 0
        skill_names = [m.skill_name for m in mappings]
        assert "agent-protocol" in skill_names
        assert "security-tiers" in skill_names

    def test_agent_protocol_used_by_all_agents(self, mapper):
        mappings = mapper.get_skill_mappings()
        ap_mapping = [m for m in mappings if m.skill_name == "agent-protocol"][0]
        assert not ap_mapping.is_orphan
        assert len(ap_mapping.used_by_agents) >= 6

    def test_orphan_detection(self, mapper):
        mappings = mapper.get_skill_mappings()
        # There may or may not be orphans; just verify the field works
        for m in mappings:
            assert isinstance(m.is_orphan, bool)
            if m.is_orphan:
                assert len(m.used_by_agents) == 0

    def test_get_unused_skills(self, mapper):
        unused = mapper.get_unused_skills()
        assert isinstance(unused, list)

    def test_format_report(self, mapper):
        report = mapper.format_report()
        assert "SKILLS MAPPER REPORT" in report
        assert "AGENT -> SKILLS:" in report
        assert "SKILL -> AGENTS:" in report

    def test_enrich_from_logs_empty_dir(self, mapper, tmp_path):
        result = mapper.enrich_from_logs(tmp_path)
        assert result["agent_invocations"] == {}
        assert result["skill_loads"] == {}

    def test_enrich_from_logs_nonexistent_dir(self, mapper, tmp_path):
        result = mapper.enrich_from_logs(tmp_path / "nonexistent")
        assert result["agent_invocations"] == {}
