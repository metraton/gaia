"""
Test suite for configuration files
Validates settings.json, git_standards.json, and other configs
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


class TestGitStandards:
    """Test git_standards.json configuration"""

    @pytest.fixture
    def git_standards_path(self):
        """Get git_standards.json path"""
        return Path(__file__).resolve().parents[2] / "config" / "git_standards.json"

    def test_git_standards_exists(self, git_standards_path):
        """git_standards.json must exist"""
        assert git_standards_path.exists(), "git_standards.json not found"

    def test_git_standards_is_valid_json(self, git_standards_path):
        """git_standards.json must be valid JSON"""
        try:
            with open(git_standards_path, 'r') as f:
                json.load(f)
        except json.JSONDecodeError as e:
            pytest.fail(f"git_standards.json is not valid JSON: {e}")

    def test_git_standards_has_commit_types(self, git_standards_path):
        """git_standards.json should define allowed commit types"""
        with open(git_standards_path, 'r') as f:
            data = json.load(f)
        
        # Structure is: data['commit_message']['type_allowed']
        has_types = (
            ('commit_message' in data and 'type_allowed' in data['commit_message']) or
            'commit_types' in data or 
            'allowed_types' in data
        )
        assert has_types, "git_standards.json missing commit types"

    def test_git_standards_has_forbidden_footers(self, git_standards_path):
        """git_standards.json should define forbidden footers"""
        with open(git_standards_path, 'r') as f:
            data = json.load(f)
        
        # Structure is: data['commit_message']['footer_forbidden']
        has_forbidden = (
            ('commit_message' in data and 'footer_forbidden' in data['commit_message']) or
            'forbidden_footers' in data or 
            'blocked_footers' in data or
            'prohibited_footers' in data
        )
        assert has_forbidden, \
            "git_standards.json missing forbidden footers config"


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
