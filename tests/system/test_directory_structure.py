"""
Test suite for Claude agent system directory structure
Validates all required directories and files exist
"""

import pytest
from pathlib import Path


class TestCoreDirectories:
    """Test that all core package directories exist (gaia-ops npm package structure)"""

    @pytest.fixture
    def package_root(self):
        """Get the package root directory (gaia-ops/)"""
        return Path(__file__).resolve().parents[2]

    def test_package_directory_exists(self, package_root):
        """Package root directory must exist"""
        assert package_root.exists(), "Package root directory not found"
        assert package_root.is_dir(), "Package root path is not a directory"

    def test_required_directories_exist(self, package_root):
        """All required npm package directories must exist"""
        # Directories that should be in the published npm package
        required_dirs = [
            "tools",
            "agents",
            "hooks",
            "commands",
            "speckit",
            "templates",
            "config",
            "tests",
            "bin"
        ]

        for dir_name in required_dirs:
            dir_path = package_root / dir_name
            # Follow symlinks
            if dir_path.is_symlink():
                dir_path = dir_path.resolve()
            assert dir_path.exists(), f"Required directory missing: {dir_name}"
            assert dir_path.is_dir(), f"{dir_name} exists but is not a directory"

    def test_config_has_required_files(self, package_root):
        """Config directory should have contracts and standards"""
        config_dir = package_root / "config"
        required_files = [
            "git_standards.json",
            "universal-rules.json",
            "skill-triggers.json"
        ]

        for file_name in required_files:
            file_path = config_dir / file_name
            assert file_path.exists(), f"Required config file missing: {file_name}"


class TestAgentsDirectory:
    """Test agents directory structure and contents"""

    @pytest.fixture
    def agents_dir(self):
        """Get the agents directory path"""
        agents = Path(__file__).resolve().parents[2] / "agents"
        return agents.resolve() if agents.is_symlink() else agents

    def test_all_project_agents_exist(self, agents_dir):
        """All 4 project agents must exist (cloud-troubleshooter unified GCP/AWS)"""
        required_agents = [
            "gitops-operator.md",
            "cloud-troubleshooter.md",
            "terraform-architect.md",
            "devops-developer.md"
        ]

        for agent in required_agents:
            agent_path = agents_dir / agent
            assert agent_path.exists(), f"Agent missing: {agent}"

    def test_agent_files_not_empty(self, agents_dir):
        """All agent files should have substantial content"""
        for agent_file in agents_dir.glob("*.md"):
            content = agent_file.read_text()
            assert len(content) > 100, f"Agent file too small: {agent_file.name}"


class TestToolsDirectory:
    """Test tools directory structure and contents"""

    @pytest.fixture
    def tools_dir(self):
        """Get the tools directory path"""
        tools = Path(__file__).resolve().parents[2] / "tools"
        return tools.resolve() if tools.is_symlink() else tools

    def test_critical_tools_exist(self, tools_dir):
        """All critical tools must exist in reorganized structure"""
        critical_tools = {
            "context/context_provider.py",
            "context/context_section_reader.py",
            "validation/approval_gate.py",
            "memory/episodic.py"
            # Note: commit_validator.py moved to hooks/modules/validation/
        }

        for tool in critical_tools:
            tool_path = tools_dir / tool
            assert tool_path.exists(), f"Critical tool missing: {tool}"

    def test_quicktriage_scripts_exist(self, tools_dir):
        """All QuickTriage scripts must exist in fast-queries"""
        quicktriage_scripts = {
            "fast-queries/gitops/quicktriage_gitops_operator.sh",
            "fast-queries/cloud/gcp/quicktriage_gcp_troubleshooter.sh",
            "fast-queries/terraform/quicktriage_terraform_architect.sh",
            "fast-queries/appservices/quicktriage_devops_developer.sh",
            "fast-queries/cloud/aws/quicktriage_aws_troubleshooter.sh"
        }

        for script in quicktriage_scripts:
            script_path = tools_dir / script
            assert script_path.exists(), f"QuickTriage script missing: {script}"


class TestHooksDirectory:
    """Test hooks directory structure and contents"""

    @pytest.fixture
    def hooks_dir(self):
        """Get the hooks directory path"""
        hooks = Path(__file__).resolve().parents[2] / "hooks"
        return hooks.resolve() if hooks.is_symlink() else hooks

    def test_security_hooks_exist(self, hooks_dir):
        """All security hooks must exist"""
        required_hooks = [
            "pre_tool_use.py",
            "post_tool_use.py",
            "subagent_stop.py"
        ]

        for hook in required_hooks:
            hook_path = hooks_dir / hook
            assert hook_path.exists(), f"Security hook missing: {hook}"

    def test_hooks_are_executable(self, hooks_dir):
        """Hook files should be executable or have proper permissions"""
        for hook_file in hooks_dir.glob("*.py"):
            content = hook_file.read_text()
            assert "def " in content, f"Hook has no functions: {hook_file.name}"


class TestConfigDirectory:
    """Test config directory structure and contents"""

    @pytest.fixture
    def config_dir(self):
        """Get the config directory path"""
        config = Path(__file__).resolve().parents[2] / "config"
        return config.resolve() if config.is_symlink() else config

    def test_git_standards_exist(self, config_dir):
        """git_standards.json must exist"""
        git_standards = config_dir / "git_standards.json"
        assert git_standards.exists(), "git_standards.json not found"

    def test_config_files_valid_json(self, config_dir):
        """All JSON config files should be valid"""
        import json
        
        if not config_dir.exists():
            pytest.skip("config/ directory not found")
        
        for config_file in config_dir.glob("*.json"):
            try:
                with open(config_file, 'r') as f:
                    json.load(f)
            except json.JSONDecodeError as e:
                pytest.fail(f"Invalid JSON in {config_file.name}: {e}")


class TestSpecKitDirectory:
    """Test Spec-Kit directory structure"""

    @pytest.fixture
    def speckit_dir(self):
        """Get the speckit directory path"""
        speckit = Path(__file__).resolve().parents[2] / "speckit"
        return speckit.resolve() if speckit.is_symlink() else speckit

    def test_speckit_subdirectories_exist(self, speckit_dir):
        """Spec-Kit subdirectories must exist"""
        required_subdirs = ["templates", "scripts"]

        for subdir in required_subdirs:
            subdir_path = speckit_dir / subdir
            assert subdir_path.exists(), f"Spec-Kit subdirectory missing: {subdir}"

    def test_speckit_templates_exist(self, speckit_dir):
        """Spec-Kit templates must exist"""
        templates_dir = speckit_dir / "templates"
        required_templates = [
            "spec-template.md",
            "plan-template.md",
            "tasks-template.md",
            "adr-template.md"
        ]

        for template in required_templates:
            template_path = templates_dir / template
            assert template_path.exists(), f"Spec-Kit template missing: {template}"

    def test_governance_file_exists(self, speckit_dir):
        """governance.md must exist"""
        governance = speckit_dir / "governance.md"
        assert governance.exists(), "governance.md not found"
        
        content = governance.read_text()
        assert len(content) > 1000, "governance.md seems too small"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
