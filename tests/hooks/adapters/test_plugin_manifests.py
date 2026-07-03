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
7. marketplace.json has the single unified 'gaia' plugin with a `source: github` source
8. The manifest's declared bin/agents/commands entries exist in the source tree
9. All version fields match across all manifest files
"""

import importlib.util
import json
from pathlib import Path

import pytest

# Resolve project root (tests/hooks/adapters/ -> project root)
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent


def _load_build_plugin_module():
    """Import scripts/build-plugin.py (hyphenated filename, not import-able directly)."""
    spec = importlib.util.spec_from_file_location(
        "_gaia_build_plugin", PROJECT_ROOT / "scripts" / "build-plugin.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def gaia_manifest() -> dict:
    """Load the `gaia` build manifest (build/gaia.manifest.json).

    Under the `source: npm` delivery model there is no dist/ build step to
    exercise -- the package root IS the plugin, and component files already
    live there (scripts/build-plugin.py only regenerates the two generated
    manifests in place via --manifests-only). So "the built plugin" for test
    purposes is the source tree itself: load the manifest directly and check
    its declared entries resolve under PROJECT_ROOT.
    """
    build_plugin = _load_build_plugin_module()
    return build_plugin.load_manifest("gaia")


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
        """plugin.json name must be 'gaia' (single unified plugin)."""
        data = json.loads(self.plugin_path.read_text())
        assert data["name"] == "gaia"

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

    def test_plugin_json_has_no_inline_hooks(self):
        """plugin.json must NOT embed an inline 'hooks' block.

        Hooks are declared in exactly ONE place -- hooks/hooks.json (the
        standard plugin convention Claude Code reads). An earlier design also
        embedded them inline here as a ${CLAUDE_PLUGIN_ROOT} workaround, but CC
        reads BOTH sources, so every hook registered twice (17 -> 34) and every
        event (SessionStart, SessionEnd, ...) fired twice. Dropping the inline
        block is the fix; this test guards against the regression. See
        generate_plugin_json() in scripts/build-plugin.py.
        """
        data = json.loads(self.plugin_path.read_text())
        assert "hooks" not in data, (
            "plugin.json must NOT embed an inline 'hooks' block -- hooks belong "
            "only in hooks/hooks.json. An inline block double-registers every "
            "hook. Run `npm run generate:plugin-root` to regenerate it."
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
        """All hook commands must use ${CLAUDE_PLUGIN_ROOT} prefix.

        Hook commands are now invoked via `python3 ${CLAUDE_PLUGIN_ROOT}/...`
        so the kernel never needs +x on the .py file (tarball installs do not
        always preserve 0755). The ${CLAUDE_PLUGIN_ROOT} token must still
        appear so CC resolves it to the plugin cache directory.
        """
        data = json.loads(self.hooks_path.read_text())
        for event_name, entries in data["hooks"].items():
            for entry in entries:
                for hook in entry["hooks"]:
                    command = hook["command"]
                    assert "${CLAUDE_PLUGIN_ROOT}/" in command, (
                        f"Hook command in {event_name}/{entry.get('matcher', '')} "
                        f"does not reference ${{CLAUDE_PLUGIN_ROOT}}: {command}"
                    )
                    assert command.startswith("python3 ${CLAUDE_PLUGIN_ROOT}/"), (
                        f"Hook command in {event_name}/{entry.get('matcher', '')} "
                        f"must use `python3 ${{CLAUDE_PLUGIN_ROOT}}/...` invoker: {command}"
                    )

    def test_hooks_json_has_all_required_events(self):
        """hooks.json must have all 12 required hook event types.

        hooks.json is the single source of truth for GAIA hooks
        (auto-discovered via the .claude/hooks symlink). SessionEnd
        was added in Phase 1 of the context-injection redesign so
        heartbeat-based liveness gets a deterministic teardown signal.
        """
        hooks_data = json.loads(self.hooks_path.read_text())
        hooks_events = set(hooks_data["hooks"].keys())

        required_events = {
            "PreToolUse", "PostToolUse", "SubagentStop",
            "SessionStart", "SessionEnd", "UserPromptSubmit", "Stop",
            "TaskCompleted", "SubagentStart", "PostCompact",
            "PreCompact", "ElicitationResult",
        }
        assert hooks_events == required_events, (
            f"Event mismatch: hooks.json has {hooks_events}, "
            f"expected {required_events}"
        )


class TestMarketplaceJson:
    """Test .claude-plugin/marketplace.json manifest.

    The marketplace.json is a flat structure with top-level name, owner,
    and plugins array. Each plugin's `source` is the github object form
    ({"source": "github", "repo": "metraton/gaia"}) -- `/plugin install`
    clones the repo so the full plugin tree ships; there is no dist/ bundle.
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

    def test_marketplace_has_gaia(self):
        """marketplace.json must include the single unified 'gaia' plugin."""
        data = json.loads(self.marketplace_path.read_text())
        names = {p["name"] for p in data["plugins"]}
        assert "gaia" in names, f"gaia not found in {names}"

    def test_marketplace_has_single_plugin(self):
        """Exactly one plugin ships: the single unified 'gaia'."""
        data = json.loads(self.marketplace_path.read_text())
        names = {p["name"] for p in data["plugins"]}
        assert names == {"gaia"}, f"expected only {{'gaia'}}, got {names}"

    def test_marketplace_plugins_have_required_fields(self):
        """Each marketplace plugin must have name, description, version, source."""
        data = json.loads(self.marketplace_path.read_text())
        for plugin in data["plugins"]:
            assert "name" in plugin, f"Plugin missing 'name': {plugin}"
            assert "description" in plugin, f"Plugin missing 'description': {plugin}"
            assert "version" in plugin, f"Plugin missing 'version': {plugin}"
            assert "source" in plugin, f"Plugin missing 'source': {plugin}"

    def test_marketplace_plugin_sources_are_github(self):
        """Each marketplace plugin source must be the github object form.

        The plugin surface is distributed via a git/github source
        ({"source": "github", "repo": "metraton/gaia"}) so `/plugin install`
        clones the repo and the full plugin tree (agents, skills, hooks) ships
        intact -- the npm-source model dropped the skills/ tree, so the plugin
        surface uses github while npm remains the CLI-only surface.
        `source.repo` is the `owner/name` slug.

        `source.ref` is OPTIONAL and pins the install to a release tag
        (`v<version>`) so installs are reproducible instead of tracking moving
        default-branch HEAD. release:prepare (bumpMarketplace) writes it
        atomically with the version at every release cut, so it never goes
        stale. This test is tolerant when it is absent (refless resolves the
        repo's current default ref -- a valid pre-pin state) and strict when
        present: a pinned ref MUST equal `v<plugin version>`.
        """
        data = json.loads(self.marketplace_path.read_text())
        for plugin in data["plugins"]:
            source = plugin["source"]
            assert isinstance(source, dict), (
                f"Plugin '{plugin['name']}' source must be an object, got {type(source)}"
            )
            assert source.get("source") == "github", (
                f"Plugin '{plugin['name']}' source.source must be 'github', got {source.get('source')!r}"
            )
            assert source.get("repo") == "metraton/gaia", (
                f"Plugin '{plugin['name']}' source.repo must be 'metraton/gaia', "
                f"got {source.get('repo')!r}"
            )
            ref = source.get("ref")
            if ref is not None:
                assert ref == f"v{plugin['version']}", (
                    f"Plugin '{plugin['name']}' source.ref must be "
                    f"'v{plugin['version']}' (the release tag) when pinned, got {ref!r}"
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


class TestBuiltPluginManifest:
    """Test the manifest-declared bundle content resolves in the source tree.

    Under `source: npm` there is no dist/ build: the root .claude-plugin/plugin.json
    (already covered by TestPluginJson) IS the generated artifact, and the
    "bundle" is the repo root itself -- component files ship via package.json
    `files[]` with no copy step. These tests read the `gaia` manifest directly
    and assert the paths it declares actually exist, catching drift between
    the manifest and the source tree without emitting a dist/ artifact.
    """

    @pytest.fixture(autouse=True)
    def setup(self, gaia_manifest):
        self.manifest = gaia_manifest
        self.plugin_path = PROJECT_ROOT / ".claude-plugin" / "plugin.json"

    def test_gaia_plugin_json_exists(self):
        """Root .claude-plugin/plugin.json must exist (generated by --manifests-only)."""
        assert self.plugin_path.exists(), f"Missing: {self.plugin_path}"

    def test_gaia_plugin_json_valid(self):
        """Root plugin.json must be valid JSON."""
        data = json.loads(self.plugin_path.read_text())
        assert isinstance(data, dict)

    def test_gaia_name(self):
        """Plugin name declared in the manifest must be 'gaia'."""
        assert self.manifest["plugin_name"] == "gaia"

    def test_built_plugin_ships_bin_cli(self):
        """The `gaia` CLI must exist in the source tree so /plugin install exposes it."""
        assert (PROJECT_ROOT / "bin" / "gaia").exists(), "missing bin/gaia"
        assert (PROJECT_ROOT / "bin" / "cli" / "install.py").exists(), "missing bin/cli/"
        # Lazy DB bootstrap needs the schema + bootstrap script.
        assert (PROJECT_ROOT / "gaia" / "store" / "schema.sql").exists(), "missing gaia/store/schema.sql"
        assert (PROJECT_ROOT / "scripts" / "bootstrap_database.sh").exists(), "missing scripts/bootstrap_database.sh"

    def test_built_plugin_has_required_fields(self):
        """Root plugin.json must have name, version, description."""
        data = json.loads(self.plugin_path.read_text())
        assert "name" in data, "missing 'name'"
        assert "version" in data, "missing 'version'"
        assert "description" in data, "missing 'description'"


class TestVersionSync:
    """Test version synchronization across all manifest files."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.package_path = PROJECT_ROOT / "package.json"
        self.plugin_path = PROJECT_ROOT / ".claude-plugin" / "plugin.json"
        self.marketplace_path = PROJECT_ROOT / ".claude-plugin" / "marketplace.json"

    def _get_version(self, path: Path) -> str:
        """Extract version from a JSON file."""
        return json.loads(path.read_text())["version"]

    def test_all_versions_match_package_json(self):
        """All manifest versions must match package.json version."""
        expected = self._get_version(self.package_path)

        manifest_files = {
            "plugin.json": self.plugin_path,
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
