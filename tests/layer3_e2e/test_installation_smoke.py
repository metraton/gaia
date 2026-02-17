"""
Smoke tests for gaia-ops installation structure.

Validates that a simulated installation creates the correct
project structure with valid files.

Run: python3 -m pytest tests/layer3_e2e/test_installation_smoke.py -v -m e2e
"""

import json
import py_compile
import sys
import pytest
from pathlib import Path

# Add this directory to path for helpers import
sys.path.insert(0, str(Path(__file__).resolve().parent))

from helpers.claude_headless import setup_test_project


pytestmark = pytest.mark.e2e


@pytest.fixture
def package_root():
    """Root of the gaia-ops package."""
    root = Path(__file__).resolve().parents[2]
    return root.resolve() if root.is_symlink() else root


@pytest.fixture
def test_project(tmp_path, package_root):
    """Set up a temporary test project."""
    project_dir = tmp_path / "test-project"
    project_dir.mkdir()
    claude_dir = setup_test_project(project_dir, package_root)
    return project_dir, claude_dir


class TestProjectStructure:
    """Validate the created project structure."""

    def test_claude_dir_created(self, test_project):
        """The .claude/ directory must be created."""
        project_dir, claude_dir = test_project
        assert claude_dir.exists()
        assert claude_dir.is_dir()

    def test_agents_copied(self, test_project):
        """Agent definitions must be copied."""
        _, claude_dir = test_project
        agents_dir = claude_dir / "agents"
        assert agents_dir.exists()
        agent_files = list(agents_dir.glob("*.md"))
        assert len(agent_files) >= 4, \
            f"Expected at least 4 agent files, found {len(agent_files)}"

    def test_skills_copied(self, test_project):
        """Skill directories must be copied."""
        _, claude_dir = test_project
        skills_dir = claude_dir / "skills"
        assert skills_dir.exists()
        skill_dirs = [d for d in skills_dir.iterdir() if d.is_dir()]
        assert len(skill_dirs) >= 5, \
            f"Expected at least 5 skill dirs, found {len(skill_dirs)}"

    def test_hooks_copied(self, test_project):
        """Hook scripts must be copied."""
        _, claude_dir = test_project
        hooks_dir = claude_dir / "hooks"
        assert hooks_dir.exists()
        assert (hooks_dir / "pre_tool_use.py").exists()


class TestFileValidity:
    """Validate that created files are valid."""

    def test_hooks_are_valid_python(self, test_project):
        """All .py files in hooks/ must be valid Python."""
        _, claude_dir = test_project
        hooks_dir = claude_dir / "hooks"
        for py_file in hooks_dir.rglob("*.py"):
            try:
                py_compile.compile(str(py_file), doraise=True)
            except py_compile.PyCompileError as e:
                pytest.fail(f"{py_file.name} is not valid Python: {e}")

    def test_settings_json_valid(self, test_project):
        """settings.json must be valid JSON with hooks configured."""
        _, claude_dir = test_project
        settings_file = claude_dir / "settings.json"
        assert settings_file.exists(), "settings.json not found"
        data = json.loads(settings_file.read_text())
        assert "hooks" in data, "settings.json must have 'hooks' key"
        assert "PreToolUse" in data["hooks"], \
            "settings.json must configure PreToolUse hook"

    def test_project_context_valid(self, test_project):
        """project-context.json must be valid JSON with metadata."""
        _, claude_dir = test_project
        pc_file = claude_dir / "project-context" / "project-context.json"
        assert pc_file.exists(), "project-context.json not found"
        data = json.loads(pc_file.read_text())
        assert "metadata" in data, "project-context.json must have 'metadata'"

    def test_claude_md_exists(self, test_project):
        """CLAUDE.md must exist in project root."""
        project_dir, _ = test_project
        claude_md = project_dir / "CLAUDE.md"
        assert claude_md.exists(), "CLAUDE.md not found in project root"
        content = claude_md.read_text()
        assert len(content) > 100, "CLAUDE.md too short"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
