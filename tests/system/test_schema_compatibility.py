"""
Test schema compatibility between CLAUDE.template.md and gaia-init.js output.

Ensures the orchestrator's bootstrap instruction references the correct
project-context.json path and that fixture schemas match expected structure.
"""

import json
import pytest
from pathlib import Path


class TestSchemaCompatibility:
    """Verify CLAUDE.template.md references match gaia-init.js schema."""

    @pytest.fixture
    def package_root(self):
        return Path(__file__).resolve().parents[2]

    @pytest.fixture
    def template_content(self, package_root):
        template_path = package_root / "templates" / "CLAUDE.template.md"
        assert template_path.exists(), "CLAUDE.template.md not found"
        return template_path.read_text()

    @pytest.fixture
    def gaia_init_content(self, package_root):
        init_path = package_root / "bin" / "gaia-init.js"
        assert init_path.exists(), "gaia-init.js not found"
        return init_path.read_text()

    @pytest.fixture
    def fixture_contexts(self, package_root):
        """Load all test fixture project-context files."""
        fixtures_dir = package_root / "tests" / "fixtures"
        contexts = {}
        for f in fixtures_dir.glob("project-context.*.json"):
            contexts[f.stem] = json.loads(f.read_text())
        return contexts

    def test_template_instructs_read_project_context(self, template_content):
        """Template must instruct the orchestrator to read project-context.json."""
        assert "project-context.json" in template_content, (
            "Template must reference project-context.json for Quick Context"
        )

    def test_template_references_key_fields(self, template_content):
        """Template Quick Context must mention the key fields to extract."""
        expected_fields = [
            "project name",
            "cloud provider",
            "region",
            "cluster name",
        ]
        content_lower = template_content.lower()
        for field in expected_fields:
            assert field in content_lower, (
                f"Template Quick Context should mention '{field}'"
            )

    def test_fixture_contexts_have_expected_structure(self, fixture_contexts):
        """Test fixture project-context files must have the sections gaia-init generates."""
        if not fixture_contexts:
            pytest.skip("No fixture project-context files found")

        for name, ctx in fixture_contexts.items():
            assert "metadata" in ctx, f"{name}: missing metadata"
            assert "sections" in ctx, f"{name}: missing sections"

    def test_template_path_matches_gaia_init_output(self, template_content, gaia_init_content):
        """Template must reference the same file path that gaia-init writes to."""
        assert ".claude/project-context/project-context.json" in template_content, (
            "Template must reference .claude/project-context/project-context.json"
        )
        assert "'.claude', 'project-context', 'project-context.json'" in gaia_init_content or \
               "'project-context', 'project-context.json'" in gaia_init_content, (
            "gaia-init must write to .claude/project-context/project-context.json"
        )

    def test_template_enforces_no_bash(self, template_content):
        """Template must explicitly state the orchestrator has no Bash."""
        assert "do NOT have Bash" in template_content or \
               "do not have Bash" in template_content, (
            "Template must explicitly state orchestrator has no Bash"
        )
