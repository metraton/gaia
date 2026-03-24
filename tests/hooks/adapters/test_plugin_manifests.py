#!/usr/bin/env python3
"""
Tests for Plugin Manifest Files.

Validates:
1. plugin.json exists and is valid JSON
2. plugin.json version matches package.json version
3. hooks.json exists and is valid JSON
4. hooks.json has PreToolUse, PostToolUse, SubagentStop events
5. hooks.json uses ${CLAUDE_PLUGIN_ROOT} in all command paths
6. marketplace.json exists and is valid JSON (flat format: name, owner, plugins)
7. marketplace.json has 2 plugins (gaia-security, gaia-ops) with source in dist/
8. Sub-plugin plugin.json files exist in dist/ and are valid
9. All version fields match across all manifest files
"""

import json
from pathlib import Path

import pytest

# Resolve project root (tests/hooks/adapters/ -> project root)
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent


class TestPluginJson:
    """Test .claude-plugin/plugin.json manifest."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.plugin_path = PROJECT_ROOT / ".claude-plugin" / "plugin.json"
        self.package_path = PROJECT_ROOT / "package.json"

    def test_plugin_json_exists(self):
        """plugin.json must exist in .claude-plugin/."""
        assert self.plugin_path.exists(), f"Missing: {self.plugin_path}"

    def test_plugin_json_valid(self):
        """plugin.json must be valid JSON."""
        data = json.loads(self.plugin_path.read_text())
        assert isinstance(data, dict)

    def test_plugin_json_required_fields(self):
        """plugin.json must have name, version, description."""
        data = json.loads(self.plugin_path.read_text())
        assert "name" in data, "Missing 'name' field"
        assert "version" in data, "Missing 'version' field"
        assert "description" in data, "Missing 'description' field"

    def test_plugin_json_name(self):
        """plugin.json name must be 'gaia-ops'."""
        data = json.loads(self.plugin_path.read_text())
        assert data["name"] == "gaia-ops"

    def test_plugin_json_description_length(self):
        """plugin.json description must be max 200 characters."""
        data = json.loads(self.plugin_path.read_text())
        assert len(data["description"]) <= 200, (
            f"Description too long: {len(data['description'])} chars (max 200)"
        )

    def test_plugin_json_version_matches_package(self):
        """plugin.json version must match package.json version."""
        plugin_data = json.loads(self.plugin_path.read_text())
        package_data = json.loads(self.package_path.read_text())
        assert plugin_data["version"] == package_data["version"], (
            f"Version mismatch: plugin.json={plugin_data['version']} "
            f"package.json={package_data['version']}"
        )

    def test_plugin_json_no_hooks_field(self):
        """plugin.json must NOT include a 'hooks' field (auto-loaded by convention)."""
        data = json.loads(self.plugin_path.read_text())
        assert "hooks" not in data, (
            "plugin.json should not have a 'hooks' field -- "
            "Claude Code v2.1+ auto-loads hooks/hooks.json by convention"
        )

    def test_plugin_json_has_engines(self):
        """plugin.json must have engines.claude-code field with >=2.1.0."""
        data = json.loads(self.plugin_path.read_text())
        assert "engines" in data, "Missing 'engines' field"
        assert "claude-code" in data["engines"], "Missing 'engines.claude-code' field"
        assert data["engines"]["claude-code"] == ">=2.1.0", (
            f"Expected engines.claude-code '>=2.1.0', got '{data['engines']['claude-code']}'"
        )

    def test_plugin_json_has_categories(self):
        """plugin.json must have categories array with devops, security, orchestration."""
        data = json.loads(self.plugin_path.read_text())
        assert "categories" in data, "Missing 'categories' field"
        assert isinstance(data["categories"], list), "categories must be a list"
        assert data["categories"] == ["devops", "security", "orchestration"], (
            f"Expected categories ['devops', 'security', 'orchestration'], "
            f"got {data['categories']}"
        )


class TestHooksJson:
    """Test hooks/hooks.json manifest."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.hooks_path = PROJECT_ROOT / "hooks" / "hooks.json"

    def test_hooks_json_exists(self):
        """hooks.json must exist in hooks/."""
        assert self.hooks_path.exists(), f"Missing: {self.hooks_path}"

    def test_hooks_json_valid(self):
        """hooks.json must be valid JSON."""
        data = json.loads(self.hooks_path.read_text())
        assert isinstance(data, dict)

    def test_hooks_json_has_hooks_key(self):
        """hooks.json must have a top-level 'hooks' key."""
        data = json.loads(self.hooks_path.read_text())
        assert "hooks" in data

    def test_hooks_json_has_pre_tool_use(self):
        """hooks.json must have PreToolUse event."""
        data = json.loads(self.hooks_path.read_text())
        assert "PreToolUse" in data["hooks"]

    def test_hooks_json_has_post_tool_use(self):
        """hooks.json must have PostToolUse event."""
        data = json.loads(self.hooks_path.read_text())
        assert "PostToolUse" in data["hooks"]

    def test_hooks_json_has_subagent_stop(self):
        """hooks.json must have SubagentStop event."""
        data = json.loads(self.hooks_path.read_text())
        assert "SubagentStop" in data["hooks"]

    def test_pre_tool_use_matchers(self):
        """PreToolUse must have Bash, Task, Agent, SendMessage, and file-tool matchers."""
        data = json.loads(self.hooks_path.read_text())
        matchers = {entry["matcher"] for entry in data["hooks"]["PreToolUse"]}
        expected = {
            "Bash", "Task", "Agent", "SendMessage",
            "Read|Edit|Write|Glob|Grep|WebSearch|WebFetch|NotebookEdit",
        }
        assert matchers == expected, (
            f"Expected matchers {expected}, got {matchers}"
        )

    def test_post_tool_use_matchers(self):
        """PostToolUse must have Bash matcher."""
        data = json.loads(self.hooks_path.read_text())
        matchers = {entry["matcher"] for entry in data["hooks"]["PostToolUse"]}
        assert "Bash" in matchers

    def test_subagent_stop_matchers(self):
        """SubagentStop must have wildcard matcher."""
        data = json.loads(self.hooks_path.read_text())
        matchers = {entry["matcher"] for entry in data["hooks"]["SubagentStop"]}
        assert "*" in matchers

    def test_all_commands_use_plugin_root(self):
        """All hook commands must use ${CLAUDE_PLUGIN_ROOT} prefix."""
        data = json.loads(self.hooks_path.read_text())
        for event_name, entries in data["hooks"].items():
            for entry in entries:
                for hook in entry["hooks"]:
                    command = hook["command"]
                    assert command.startswith("${CLAUDE_PLUGIN_ROOT}/"), (
                        f"Hook command in {event_name}/{entry['matcher']} "
                        f"does not use ${{CLAUDE_PLUGIN_ROOT}}: {command}"
                    )

    def test_hooks_json_matches_settings_template_events(self):
        """hooks.json must cover the same events as settings.template.json.

        All events in settings.template.json should have a corresponding
        entry in hooks.json (plugin version).
        """
        settings_path = PROJECT_ROOT / "templates" / "settings.template.json"
        settings_data = json.loads(settings_path.read_text())
        hooks_data = json.loads(self.hooks_path.read_text())

        settings_events = set(settings_data["hooks"].keys())
        hooks_events = set(hooks_data["hooks"].keys())
        assert hooks_events == settings_events, (
            f"Event mismatch: hooks.json has {hooks_events}, "
            f"settings.template.json has {settings_events}"
        )


class TestMarketplaceJson:
    """Test .claude-plugin/marketplace.json manifest.

    The marketplace.json is a flat structure with top-level name, owner,
    and plugins array. Plugins are built to dist/ (no plugins/ directory).
    """

    @pytest.fixture(autouse=True)
    def setup(self):
        self.marketplace_path = PROJECT_ROOT / ".claude-plugin" / "marketplace.json"

    def test_marketplace_json_exists(self):
        """marketplace.json must exist in .claude-plugin/."""
        assert self.marketplace_path.exists(), f"Missing: {self.marketplace_path}"

    def test_marketplace_json_valid(self):
        """marketplace.json must be valid JSON."""
        data = json.loads(self.marketplace_path.read_text())
        assert isinstance(data, dict)

    def test_marketplace_has_name(self):
        """marketplace.json must have a top-level 'name' field."""
        data = json.loads(self.marketplace_path.read_text())
        assert "name" in data, "Missing top-level 'name' field"

    def test_marketplace_has_plugins(self):
        """marketplace.json must have a top-level 'plugins' array."""
        data = json.loads(self.marketplace_path.read_text())
        assert "plugins" in data, "Missing 'plugins' field"
        assert isinstance(data["plugins"], list)

    def test_marketplace_has_at_least_one_plugin(self):
        """marketplace.json must have at least one plugin."""
        data = json.loads(self.marketplace_path.read_text())
        plugins = data["plugins"]
        assert len(plugins) >= 1, f"Expected at least 1 plugin, got {len(plugins)}"

    def test_marketplace_has_gaia_security(self):
        """marketplace.json must include gaia-security."""
        data = json.loads(self.marketplace_path.read_text())
        names = {p["name"] for p in data["plugins"]}
        assert "gaia-security" in names, f"gaia-security not found in {names}"

    def test_marketplace_plugins_have_required_fields(self):
        """Each marketplace plugin must have name, description, version, source."""
        data = json.loads(self.marketplace_path.read_text())
        for plugin in data["plugins"]:
            assert "name" in plugin, f"Plugin missing 'name': {plugin}"
            assert "description" in plugin, f"Plugin missing 'description': {plugin}"
            assert "version" in plugin, f"Plugin missing 'version': {plugin}"
            assert "source" in plugin, f"Plugin missing 'source': {plugin}"

    def test_marketplace_plugin_sources_point_to_dist(self):
        """Each marketplace plugin source must point to dist/."""
        data = json.loads(self.marketplace_path.read_text())
        for plugin in data["plugins"]:
            assert plugin["source"].startswith("./dist/"), (
                f"Plugin '{plugin['name']}' source must start with './dist/', "
                f"got '{plugin['source']}'"
            )


class TestMarketplaceRegistrable:
    """Test marketplace.json has all required fields for /plugin marketplace add."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.marketplace_path = PROJECT_ROOT / ".claude-plugin" / "marketplace.json"
        self.marketplace = json.loads(self.marketplace_path.read_text())

    def test_marketplace_has_name(self):
        """marketplace.json must have a 'name' field for marketplace registration."""
        assert "name" in self.marketplace, "Missing 'name' field"

    def test_marketplace_has_owner(self):
        """marketplace.json must have an 'owner' field for marketplace registration."""
        assert "owner" in self.marketplace, "Missing 'owner' field"

    def test_marketplace_has_plugins_field(self):
        """marketplace.json must have a 'plugins' field for marketplace registration."""
        assert "plugins" in self.marketplace, "Missing 'plugins' field"

    def test_marketplace_owner_has_name(self):
        """marketplace.json owner must have a non-empty 'name'."""
        assert self.marketplace["owner"].get("name"), "Owner 'name' is missing or empty"

    def test_marketplace_owner_has_email(self):
        """marketplace.json owner must have a non-empty 'email'."""
        assert self.marketplace["owner"].get("email"), "Owner 'email' is missing or empty"


class TestSubPluginManifests:
    """Test sub-plugin plugin.json files in dist/."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.security_path = (
            PROJECT_ROOT / "dist" / "gaia-security" / ".claude-plugin" / "plugin.json"
        )
        self.ops_path = (
            PROJECT_ROOT / "dist" / "gaia-ops" / ".claude-plugin" / "plugin.json"
        )

    def test_gaia_security_plugin_json_exists(self):
        """gaia-security/plugin.json must exist in dist/."""
        assert self.security_path.exists(), f"Missing: {self.security_path}"

    def test_gaia_security_plugin_json_valid(self):
        """gaia-security/plugin.json must be valid JSON."""
        data = json.loads(self.security_path.read_text())
        assert isinstance(data, dict)

    def test_gaia_security_name(self):
        """gaia-security plugin name must be 'gaia-security'."""
        data = json.loads(self.security_path.read_text())
        assert data["name"] == "gaia-security"

    def test_gaia_ops_plugin_json_exists(self):
        """gaia-ops/plugin.json must exist in dist/."""
        assert self.ops_path.exists(), f"Missing: {self.ops_path}"

    def test_gaia_ops_plugin_json_valid(self):
        """gaia-ops/plugin.json must be valid JSON."""
        data = json.loads(self.ops_path.read_text())
        assert isinstance(data, dict)

    def test_gaia_ops_name(self):
        """gaia-ops plugin name must be 'gaia-ops'."""
        data = json.loads(self.ops_path.read_text())
        assert data["name"] == "gaia-ops"

    def test_sub_plugins_have_required_fields(self):
        """Sub-plugin plugin.json files must have name, version, description."""
        for path in [self.security_path, self.ops_path]:
            data = json.loads(path.read_text())
            assert "name" in data, f"{path}: missing 'name'"
            assert "version" in data, f"{path}: missing 'version'"
            assert "description" in data, f"{path}: missing 'description'"


class TestVersionSync:
    """Test version synchronization across all manifest files."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.package_path = PROJECT_ROOT / "package.json"
        self.plugin_path = PROJECT_ROOT / ".claude-plugin" / "plugin.json"
        self.marketplace_path = PROJECT_ROOT / ".claude-plugin" / "marketplace.json"
        self.security_path = (
            PROJECT_ROOT / "dist" / "gaia-security" / ".claude-plugin" / "plugin.json"
        )
        self.ops_path = (
            PROJECT_ROOT / "dist" / "gaia-ops" / ".claude-plugin" / "plugin.json"
        )

    def _get_version(self, path: Path) -> str:
        """Extract version from a JSON file."""
        return json.loads(path.read_text())["version"]

    def test_all_versions_match_package_json(self):
        """All manifest versions must match package.json version."""
        expected = self._get_version(self.package_path)

        manifest_files = {
            "plugin.json": self.plugin_path,
            "dist/gaia-security/plugin.json": self.security_path,
            "dist/gaia-ops/plugin.json": self.ops_path,
        }

        mismatches = []
        for label, path in manifest_files.items():
            actual = self._get_version(path)
            if actual != expected:
                mismatches.append(f"{label}: {actual}")

        assert not mismatches, (
            f"Version mismatch (expected {expected}): {', '.join(mismatches)}"
        )

    def test_marketplace_plugin_versions_match(self):
        """All marketplace sub-plugin versions must match package.json version."""
        expected = self._get_version(self.package_path)
        marketplace_data = json.loads(self.marketplace_path.read_text())

        mismatches = []
        for plugin in marketplace_data["plugins"]:
            if plugin["version"] != expected:
                mismatches.append(f"{plugin['name']}: {plugin['version']}")

        assert not mismatches, (
            f"Marketplace version mismatch (expected {expected}): "
            f"{', '.join(mismatches)}"
        )
