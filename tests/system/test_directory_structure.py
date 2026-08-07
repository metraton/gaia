"""
Test suite for Claude agent system directory structure
Validates all required directories and files exist
"""

import pytest
from pathlib import Path


class TestAgentsDirectory:
    """Test agents directory structure and contents"""

    @pytest.fixture
    def agents_dir(self):
        """Get the agents directory path"""
        agents = Path(__file__).resolve().parents[2] / "agents"
        return agents.resolve() if agents.is_symlink() else agents

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
            "context/surface_router.py",
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
        """Hook files should have functions or a __main__ entry point."""
        for hook_file in hooks_dir.glob("*.py"):
            content = hook_file.read_text()
            has_functions = "def " in content
            has_main = '__name__' in content and '__main__' in content
            assert has_functions or has_main, (
                f"Hook has no functions or __main__ block: {hook_file.name}"
            )


class TestConfigDirectory:
    """Test config directory structure and contents"""

    @pytest.fixture
    def config_dir(self):
        """Get the config directory path"""
        config = Path(__file__).resolve().parents[2] / "config"
        return config.resolve() if config.is_symlink() else config

    def test_surface_routing_json_retired(self, config_dir):
        """surface-routing.json must NOT exist -- routing is DB-backed now.

        The routing source of truth moved to each agent's `routing:` frontmatter
        block, seeded into the surface_routing table by
        tools/scan/seed_surface_routing.py. A lingering JSON file would be stale
        drift.
        """
        surface_routing = config_dir / "surface-routing.json"
        assert not surface_routing.exists(), (
            "config/surface-routing.json should be retired (routing is DB-backed)"
        )

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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
