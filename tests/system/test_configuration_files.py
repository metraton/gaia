"""
Test suite for configuration files
Validates settings.json, surface-routing.json, and other configs
"""

import pytest
import json
from pathlib import Path


class TestSettingsTemplateRemoved:
    """Verify settings.template.json has been removed (hooks in hooks.json, env in settings.local.json)."""

    def test_settings_template_does_not_exist(self):
        """settings.template.json should not exist -- it was removed."""
        path = Path(__file__).resolve().parents[2] / "templates" / "settings.template.json"
        assert not path.exists(), f"settings.template.json should have been deleted: {path}"


class TestGitStandardsInlined:
    """Git commit standards are now inlined as constants in commit_validator.py.

    config/git_standards.json was removed: commit_validator.py was the single
    runtime consumer of the format/subject/body rules, so they now live as
    module-level constants. Footer detection lives in bash_validator. These
    tests assert the inlined constants exist and carry the expected shape.
    """

    @pytest.fixture
    def validator_module(self):
        import importlib.util

        mod_path = (
            Path(__file__).resolve().parents[2]
            / "hooks" / "modules" / "validation" / "commit_validator.py"
        )
        spec = importlib.util.spec_from_file_location("commit_validator", mod_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_git_standards_json_removed(self):
        """config/git_standards.json must no longer exist."""
        path = Path(__file__).resolve().parents[2] / "config" / "git_standards.json"
        assert not path.exists(), "git_standards.json should have been removed"

    def test_inlined_commit_types(self, validator_module):
        """commit_validator.py defines the allowed commit types inline."""
        assert hasattr(validator_module, "TYPE_ALLOWED")
        assert "feat" in validator_module.TYPE_ALLOWED
        assert "fix" in validator_module.TYPE_ALLOWED
        assert len(validator_module.TYPE_ALLOWED) == 10

    def test_inlined_subject_rules(self, validator_module):
        """commit_validator.py defines subject rules and max length inline."""
        assert validator_module.SUBJECT_MAX_LENGTH == 72
        assert validator_module.SUBJECT_RULES["no_period_at_end"] is True
        assert validator_module.SUBJECT_RULES["no_emoji"] is True


class TestConfigConsistency:
    """Test consistency across configuration files"""

    @pytest.fixture
    def config_dir(self):
        """Get config directory path"""
        return Path(__file__).resolve().parents[2] / "config"

    def test_all_json_files_valid(self, config_dir):
        """All JSON files in config/ should be valid"""
        if not config_dir.exists():
            pytest.skip("config/ directory not found")
            
        for json_file in config_dir.glob("*.json"):
            try:
                with open(json_file, 'r') as f:
                    json.load(f)
            except json.JSONDecodeError as e:
                pytest.fail(f"Invalid JSON in {json_file.name}: {e}")

    def test_no_empty_config_files(self, config_dir):
        """Config files should not be empty"""
        if not config_dir.exists():
            pytest.skip("config/ directory not found")
            
        for config_file in config_dir.glob("*.json"):
            size = config_file.stat().st_size
            assert size > 10, f"{config_file.name} is too small or empty"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
