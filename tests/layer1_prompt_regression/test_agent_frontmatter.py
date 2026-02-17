"""
Test agent frontmatter structure and validity.

Validates that all agent .md files have correct YAML frontmatter
with required fields and valid values.
"""

import pytest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import parse_frontmatter


class TestFrontmatterPresence:
    """All agents must have valid YAML frontmatter."""

    def test_all_agents_have_frontmatter(self, all_agent_files):
        """Every agent file must start with --- frontmatter ---."""
        for agent_file in all_agent_files:
            content = agent_file.read_text()
            assert content.startswith("---"), \
                f"{agent_file.name} must start with --- frontmatter"

    def test_frontmatter_has_closing_delimiter(self, all_agent_files):
        """Frontmatter must have a closing --- delimiter."""
        for agent_file in all_agent_files:
            content = agent_file.read_text()
            assert content.count("---") >= 2, \
                f"{agent_file.name} frontmatter missing closing ---"


class TestRequiredFields:
    """Frontmatter must have all required fields."""

    REQUIRED_FIELDS = ["name", "description", "tools", "model"]

    @pytest.fixture
    def agent_frontmatters(self, all_agent_files):
        """Parse frontmatter from all agent files."""
        return {
            f.stem: parse_frontmatter(f.read_text())
            for f in all_agent_files
        }

    @pytest.mark.parametrize("field", REQUIRED_FIELDS)
    def test_required_field_present(self, field, all_agent_files):
        """Each required field must be present in all agents."""
        for agent_file in all_agent_files:
            fm = parse_frontmatter(agent_file.read_text())
            assert field in fm, \
                f"{agent_file.name} frontmatter missing required field: {field}"

    def test_name_matches_filename(self, all_agent_files):
        """Frontmatter 'name' must match the filename (without .md)."""
        for agent_file in all_agent_files:
            fm = parse_frontmatter(agent_file.read_text())
            assert fm.get("name") == agent_file.stem, \
                f"{agent_file.name}: name '{fm.get('name')}' != filename '{agent_file.stem}'"

    def test_model_is_inherit(self, all_agent_files):
        """All agents must use model: inherit."""
        for agent_file in all_agent_files:
            fm = parse_frontmatter(agent_file.read_text())
            assert fm.get("model") == "inherit", \
                f"{agent_file.name}: model must be 'inherit', got '{fm.get('model')}'"

    def test_description_not_empty(self, all_agent_files):
        """Description must be non-empty."""
        for agent_file in all_agent_files:
            fm = parse_frontmatter(agent_file.read_text())
            desc = fm.get("description", "")
            assert len(desc) > 10, \
                f"{agent_file.name}: description too short ({len(desc)} chars)"

    def test_tools_not_empty(self, all_agent_files):
        """Tools field must list at least one tool."""
        for agent_file in all_agent_files:
            fm = parse_frontmatter(agent_file.read_text())
            tools = fm.get("tools", "")
            assert tools, f"{agent_file.name}: tools field is empty"


class TestSkillsField:
    """Agents with skills field must reference valid skills."""

    def test_skills_references_are_valid(self, all_agent_files, skills_dir):
        """All skills referenced in frontmatter must exist as directories."""
        for agent_file in all_agent_files:
            fm = parse_frontmatter(agent_file.read_text())
            skills = fm.get("skills", [])
            if not isinstance(skills, list):
                continue
            for skill_name in skills:
                skill_path = skills_dir / skill_name
                assert skill_path.is_dir(), \
                    f"{agent_file.name} references skill '{skill_name}' but {skill_path} not found"

    def test_skills_have_skill_md(self, all_agent_files, skills_dir):
        """Each referenced skill directory must contain a SKILL.md."""
        for agent_file in all_agent_files:
            fm = parse_frontmatter(agent_file.read_text())
            skills = fm.get("skills", [])
            if not isinstance(skills, list):
                continue
            for skill_name in skills:
                skill_md = skills_dir / skill_name / "SKILL.md"
                assert skill_md.exists(), \
                    f"{agent_file.name} references skill '{skill_name}' but SKILL.md not found"

    def test_project_agents_have_skills(self, agents_dir):
        """Project agents (non-meta) should have at least one skill."""
        project_agents = ["terraform-architect", "gitops-operator",
                          "cloud-troubleshooter", "devops-developer"]
        for agent_name in project_agents:
            agent_file = agents_dir / f"{agent_name}.md"
            if not agent_file.exists():
                continue
            fm = parse_frontmatter(agent_file.read_text())
            skills = fm.get("skills", [])
            assert isinstance(skills, list) and len(skills) > 0, \
                f"{agent_name} should have at least one skill"


class TestFrontmatterTools:
    """Validate tools field format."""

    def test_tools_is_comma_separated(self, all_agent_files):
        """Tools field should be a comma-separated string."""
        for agent_file in all_agent_files:
            fm = parse_frontmatter(agent_file.read_text())
            tools = fm.get("tools", "")
            if isinstance(tools, str):
                tool_list = [t.strip() for t in tools.split(",")]
                assert len(tool_list) >= 1, \
                    f"{agent_file.name}: tools must list at least one tool"
                for tool in tool_list:
                    assert tool, f"{agent_file.name}: empty tool name in tools list"

    def test_all_agents_have_read_tool(self, all_agent_files):
        """All agents should have Read in their tools."""
        for agent_file in all_agent_files:
            fm = parse_frontmatter(agent_file.read_text())
            tools = fm.get("tools", "")
            tool_list = [t.strip() for t in tools.split(",")]
            assert "Read" in tool_list, \
                f"{agent_file.name} should have Read in tools"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
