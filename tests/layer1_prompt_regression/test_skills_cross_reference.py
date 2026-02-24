"""
Test skills cross-references between agents and skill directories.

Validates bidirectional consistency: agents reference skills that exist,
and all skills are referenced by at least one agent.
"""

import pytest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import parse_frontmatter


class TestSkillDirectoryStructure:
    """Validate skill directory structure."""

    def test_every_skill_dir_has_skill_md(self, skills_dir):
        """Every skill directory must contain a SKILL.md file."""
        for skill_dir in skills_dir.iterdir():
            if not skill_dir.is_dir():
                continue
            if skill_dir.name.startswith(".") or skill_dir.name == "__pycache__":
                continue
            skill_md = skill_dir / "SKILL.md"
            assert skill_md.exists(), \
                f"Skill directory '{skill_dir.name}' missing SKILL.md"

    def test_skill_md_has_frontmatter(self, all_skill_dirs):
        """Every SKILL.md must have valid frontmatter."""
        for skill_dir in all_skill_dirs:
            content = (skill_dir / "SKILL.md").read_text()
            assert content.startswith("---"), \
                f"{skill_dir.name}/SKILL.md must start with frontmatter ---"
            fm = parse_frontmatter(content)
            assert fm, f"{skill_dir.name}/SKILL.md frontmatter is empty"

    def test_skill_name_matches_directory(self, all_skill_dirs):
        """SKILL.md frontmatter 'name' must match directory name."""
        for skill_dir in all_skill_dirs:
            content = (skill_dir / "SKILL.md").read_text()
            fm = parse_frontmatter(content)
            assert fm.get("name") == skill_dir.name, \
                f"{skill_dir.name}/SKILL.md: name '{fm.get('name')}' != dir '{skill_dir.name}'"

    def test_skill_has_description(self, all_skill_dirs):
        """SKILL.md frontmatter must have a description."""
        for skill_dir in all_skill_dirs:
            content = (skill_dir / "SKILL.md").read_text()
            fm = parse_frontmatter(content)
            assert "description" in fm, \
                f"{skill_dir.name}/SKILL.md missing 'description' field"
            assert len(fm["description"]) > 10, \
                f"{skill_dir.name}/SKILL.md description too short"


class TestAgentSkillReferences:
    """Validate that agents reference existing skills."""

    @pytest.fixture
    def agent_skill_map(self, all_agent_files):
        """Map of agent name -> list of skills."""
        result = {}
        for agent_file in all_agent_files:
            fm = parse_frontmatter(agent_file.read_text())
            skills = fm.get("skills", [])
            if isinstance(skills, list):
                result[agent_file.stem] = skills
        return result

    def test_all_referenced_skills_exist(self, agent_skill_map, skills_dir):
        """Every skill referenced by an agent must exist."""
        for agent_name, skills in agent_skill_map.items():
            for skill_name in skills:
                skill_path = skills_dir / skill_name / "SKILL.md"
                assert skill_path.exists(), \
                    f"Agent '{agent_name}' references skill '{skill_name}' but SKILL.md not found"

    def test_no_orphan_skills(self, agent_skill_map, all_skill_dirs, all_agent_files, skills_dir):
        """Skills not referenced by any agent or other skill should be flagged."""
        all_referenced = set()
        for skills in agent_skill_map.values():
            all_referenced.update(skills)

        # Also check agent body text references (e.g., "skills/approval/SKILL.md")
        for agent_file in all_agent_files:
            content = agent_file.read_text()
            for skill_dir in all_skill_dirs:
                if f"skills/{skill_dir.name}" in content:
                    all_referenced.add(skill_dir.name)

        # Also check skill-to-skill cross-references
        for skill_dir in all_skill_dirs:
            content = (skill_dir / "SKILL.md").read_text()
            for other_skill in all_skill_dirs:
                if other_skill.name != skill_dir.name:
                    if f"skills/{other_skill.name}" in content or f"/{other_skill.name}/" in content:
                        all_referenced.add(other_skill.name)

        # Check skills README for references
        readme = skills_dir / "README.md"
        if readme.exists():
            readme_content = readme.read_text()
            for skill_dir in all_skill_dirs:
                if skill_dir.name in readme_content:
                    all_referenced.add(skill_dir.name)

        for skill_dir in all_skill_dirs:
            assert skill_dir.name in all_referenced, \
                f"Skill '{skill_dir.name}' is not referenced by any agent or skill (orphan)"


class TestSkillContentLoading:
    """Validate content volume for frontmatter-referenced skills."""

    def test_skill_content_is_substantial(self, all_agent_files, skills_dir):
        """Simulated skill loading must produce >100 chars per skill."""
        for agent_file in all_agent_files:
            fm = parse_frontmatter(agent_file.read_text())
            skills = fm.get("skills", [])
            if not isinstance(skills, list) or not skills:
                continue

            for skill_name in skills:
                skill_md = skills_dir / skill_name / "SKILL.md"
                if not skill_md.exists():
                    continue
                content = skill_md.read_text().strip()
                # Strip frontmatter to approximate runtime skill body payload
                if content.startswith("---"):
                    try:
                        end_idx = content.index("---", 3)
                        content = content[end_idx + 3:].strip()
                    except ValueError:
                        pass
                assert len(content) > 100, \
                    f"Skill '{skill_name}' content too short ({len(content)} chars) for agent '{agent_file.stem}'"

    def test_simulated_load_produces_content(self, all_agent_files, skills_dir):
        """Full simulated load (all skills concatenated) should be substantial."""
        for agent_file in all_agent_files:
            fm = parse_frontmatter(agent_file.read_text())
            skills = fm.get("skills", [])
            if not isinstance(skills, list) or not skills:
                continue

            parts = []
            for skill_name in skills:
                skill_md = skills_dir / skill_name / "SKILL.md"
                if not skill_md.exists():
                    continue
                content = skill_md.read_text().strip()
                if content.startswith("---"):
                    try:
                        end_idx = content.index("---", 3)
                        content = content[end_idx + 3:].strip()
                    except ValueError:
                        pass
                parts.append(content)

            combined = "\n\n---\n\n".join(parts)
            assert len(combined) > 100, \
                f"Agent '{agent_file.stem}' combined skills content too short ({len(combined)} chars)"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
