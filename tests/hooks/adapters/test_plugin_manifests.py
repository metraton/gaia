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
7. marketplace.json has the single unified 'gaia' plugin with source '.' and no entry version
   (release:prepare leaves the entry alone; the validator still catches plugin.json drift)
8. The manifest's declared bin/agents/commands entries exist in the source tree
9. All version fields match across all manifest files
10. release:prepare's CHANGELOG bump: a stable folds [Unreleased] and its pre-releases into
    one section, a pre-release keeps [Unreleased], a re-run changes nothing
"""

import importlib.util
import json
import re
import shutil
import subprocess
import sys
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
    live there (scripts/build-plugin.py only regenerates package-root artifacts
    in place via --manifests-only). So "the built plugin" for test
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

    def test_plugin_json_exists(self):
        """plugin.json must exist in .claude-plugin/."""
        assert self.plugin_path.exists(), f"Missing: {self.plugin_path}"

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
        """PreToolUse must have Bash, Task, Agent, SendMessage, AskUserQuestion, and file-tool matchers."""
        data = json.loads(self.hooks_path.read_text())
        matchers = {entry["matcher"] for entry in data["hooks"]["PreToolUse"]}
        expected = {
            "Bash", "Task", "Agent", "SendMessage", "AskUserQuestion",
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

    def test_session_start_matcher_includes_resume(self, gaia_manifest):
        """SessionStart must fire on a fresh start, a resume, AND a compact.

        A SessionStart hook wired with matcher "startup" ONLY never runs on
        `claude --resume`/`--continue` -- Claude Code dispatches those with
        `source: "resume"`, a distinct matcher. Without "resume" in the
        matcher, the pinned_build marker (`gaia doctor`'s "Hooks active &
        fresh" check) never refreshes on the user's real exit -> --resume
        workflow. "compact" is required too: it is the only source/event
        combination whose hookSpecificOutput can carry additionalContext
        around compaction -- PreCompact and PostCompact's hookSpecificOutput
        is not part of Claude Code's validated schema and is never consumed
        by the runtime (see hooks/pre_compact.py and hooks/post_compact.py).
        "clear" and "fork" start a new session id, which must be attested and
        given the birth block like any other new session.
        Checked in both the generated hooks/hooks.json (the file Claude Code
        actually reads) and the source manifest it is derived from
        (build/gaia.manifest.json), so drift between the two is caught here
        rather than only at pack time.
        """
        data = json.loads(self.hooks_path.read_text())
        installed_matchers = {
            entry["matcher"] for entry in data["hooks"]["SessionStart"]
        }
        assert installed_matchers == {"startup|resume|clear|compact|fork"}, (
            f"hooks/hooks.json SessionStart matcher must be "
            f"'startup|resume|clear|compact|fork', got {installed_matchers}"
        )

        manifest_matchers = {
            entry["matcher"]
            for entry in gaia_manifest["hooks"]["matchers"]["SessionStart"]
        }
        assert manifest_matchers == {"startup|resume|clear|compact|fork"}, (
            f"build/gaia.manifest.json SessionStart matcher must be "
            f"'startup|resume|clear|compact|fork', got {manifest_matchers}"
        )

    def test_every_registered_command_starts_its_hook_from_a_root_with_spaces(self, tmp_path):
        """Each command of hooks.json, run as the host runs it, reaches its hook module.

        The host substitutes ${CLAUDE_PLUGIN_ROOT} and hands the string to a
        shell; a plugin cache path may contain spaces. A launcher that finds no
        Python, a module path that no longer exists or an unquoted root all
        surface here as the command failing to start its entrypoint.
        """
        root = tmp_path / "plugin root"
        root.symlink_to(PROJECT_ROOT, target_is_directory=True)
        data = json.loads(self.hooks_path.read_text())
        commands = {
            hook["command"]: event_name
            for event_name, entries in data["hooks"].items()
            for entry in entries
            for hook in entry["hooks"]
        }
        failures = []
        for command, event_name in commands.items():
            assert "${CLAUDE_PLUGIN_ROOT}" in command, command
            payload = {"hook_event_name": event_name, "session_id": "manifest-launch",
                       "tool_name": "Bash", "tool_input": {"command": "true"}}
            result = subprocess.run(
                ["sh", "-c", command.replace("${CLAUDE_PLUGIN_ROOT}", str(root))],
                input=json.dumps(payload), capture_output=True, text=True,
                cwd=tmp_path, timeout=60,
            )
            not_started = ("can't open file", "No such file", "Traceback", "no Python 3 found")
            if any(marker in result.stderr for marker in not_started):
                failures.append(f"{event_name}: {command}\n  rc={result.returncode} {result.stderr[-400:]}")
        assert not failures, "hook commands that did not start their hook:\n" + "\n".join(failures)

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
            "PreToolUse", "PostToolUse", "PostToolUseFailure", "SubagentStop",
            "SessionStart", "SessionEnd", "UserPromptSubmit", "Stop",
            "TaskCompleted", "SubagentStart", "PostCompact",
            "PreCompact",
        }
        assert hooks_events == required_events, (
            f"Event mismatch: hooks.json has {hooks_events}, "
            f"expected {required_events}"
        )


class TestHooksJsonManifestSync:
    """hooks/hooks.json must equal generate_hooks_json(build/gaia.manifest.json).

    hooks.json is a GENERATED artifact; build/gaia.manifest.json is its source of
    truth. A hand edit to the generated file that is not mirrored in the manifest
    is a divergence the generator now refuses to overwrite without --force
    (TestGeneratorRefusesProtectedOverwrite), so it would surface as a failed
    `npm pack` rather than a silent revert. These tests turn the drift itself
    into a loud failure in the suite instead of a discovery hours later.

    scripts/check_hooks_drift.py enforces the same invariant at publish time
    (bin/pre-publish-validate.js); this exercises it against the working tree.
    """

    def _load_drift_guard(self):
        spec = importlib.util.spec_from_file_location(
            "_gaia_check_hooks_drift", PROJECT_ROOT / "scripts" / "check_hooks_drift.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_hooks_json_matches_generator_output(self, gaia_manifest):
        """The committed hooks.json is exactly what the manifest generates."""
        build_plugin = _load_build_plugin_module()
        expected = build_plugin.generate_hooks_json(gaia_manifest)
        actual = json.loads((PROJECT_ROOT / "hooks" / "hooks.json").read_text())
        assert actual == expected, (
            "hooks/hooks.json has drifted from build/gaia.manifest.json. Edit the "
            "manifest (never the generated file) and run "
            "`npm run generate:plugin-root`."
        )

    def test_opencode_agent_inventory_matches_generator_output(self, gaia_manifest):
        """The packaged fallback is exactly the manifest-derived inventory."""
        build_plugin = _load_build_plugin_module()
        expected = build_plugin.generate_opencode_agent_inventory(gaia_manifest)
        actual = json.loads(
            (PROJECT_ROOT / "opencode" / "agent-inventory.json").read_text()
        )

        assert actual == expected
        assert set(actual["agents"]) == set(gaia_manifest["agents"])

    def test_opencode_agent_inventory_is_in_build_and_npm_distribution(self, gaia_manifest):
        """Both published inventories include the generated fallback artifact."""
        build_plugin = _load_build_plugin_module()
        artifact = PROJECT_ROOT / "opencode" / "agent-inventory.json"
        assert artifact in build_plugin.resolve_file_list(gaia_manifest)
        result = subprocess.run(
            ["npm", "pack", "--dry-run", "--ignore-scripts", "--json"],
            cwd=PROJECT_ROOT, capture_output=True, text=True, check=True, timeout=60,
        )
        paths = {entry["path"] for entry in json.loads(result.stdout)[0]["files"]}
        assert "opencode/agent-inventory.json" in paths

    def test_drift_guard_passes_on_the_working_tree(self, capsys):
        """The publish-time guard agrees with the suite on the real files."""
        guard = self._load_drift_guard()
        assert guard.main() == 0, capsys.readouterr().err

    def test_drift_guard_fails_on_a_hand_edited_hooks_json(self, tmp_path, monkeypatch, capsys):
        """A generated-file-only edit must be detected, not silently reverted.

        Reproduces the real failure on a fixture pair: the manifest keeps its
        matcher, hooks.json is edited by hand. The guard must exit 1.
        """
        guard = self._load_drift_guard()
        build_plugin = _load_build_plugin_module()

        manifest = json.loads((PROJECT_ROOT / "build" / "gaia.manifest.json").read_text())
        hand_edited = build_plugin.generate_hooks_json(manifest)
        hand_edited["hooks"]["PreToolUse"][0]["matcher"] = "Bash|SomethingNobodyDeclared"

        fixture_manifest = tmp_path / "gaia.manifest.json"
        fixture_hooks = tmp_path / "hooks.json"
        fixture_plugin = tmp_path / "plugin.json"
        fixture_manifest.write_text(json.dumps(manifest))
        fixture_hooks.write_text(json.dumps(hand_edited))
        fixture_plugin.write_text(json.dumps({"name": "gaia"}))

        monkeypatch.setattr(guard, "MANIFEST", fixture_manifest)
        monkeypatch.setattr(guard, "HOOKS_JSON", fixture_hooks)
        monkeypatch.setattr(guard, "PLUGIN_JSON", fixture_plugin)

        assert guard.main() == 1, "drift guard failed to detect a hand-edited hooks.json"
        assert "matcher mismatch: PreToolUse" in capsys.readouterr().err

    def test_drift_guard_passes_on_an_in_sync_fixture(self, tmp_path, monkeypatch):
        """Control: the same fixture wiring reports 0 when nothing diverges."""
        guard = self._load_drift_guard()
        build_plugin = _load_build_plugin_module()

        manifest = json.loads((PROJECT_ROOT / "build" / "gaia.manifest.json").read_text())
        fixture_manifest = tmp_path / "gaia.manifest.json"
        fixture_hooks = tmp_path / "hooks.json"
        fixture_plugin = tmp_path / "plugin.json"
        fixture_manifest.write_text(json.dumps(manifest))
        fixture_hooks.write_text(json.dumps(build_plugin.generate_hooks_json(manifest)))
        fixture_plugin.write_text(json.dumps({"name": "gaia"}))

        monkeypatch.setattr(guard, "MANIFEST", fixture_manifest)
        monkeypatch.setattr(guard, "HOOKS_JSON", fixture_hooks)
        monkeypatch.setattr(guard, "PLUGIN_JSON", fixture_plugin)

        assert guard.main() == 0


class TestGeneratorRefusesProtectedOverwrite:
    """The manifest generator must fail closed on a protected overwrite.

    Measured incident: an Edit of hooks/hooks.json was DENIED by the
    protected-path gate, and the identical mutation then landed anyway through
    pytest -> `npm pack` (tests/cli/test_pack_helpers.py::TestPackTarballReal)
    -> the `prepack` lifecycle -> `npm run generate:plugin-root` ->
    write_root_manifests, a subprocess chain neither guarded surface sees.
    The fix is at the sink: overwriting an existing hooks/hooks.json with
    DIFFERENT content refuses without --force, while an in-sync tree
    regenerates as a no-op so pack/publish flows are untouched.

    These tests drive the ACTUAL invocation `prepack` runs -- the script
    itself, as a subprocess, differing only in --output-dir -- against a tree
    that carries the Gaia root marker (build/gaia.manifest.json).

    This refusal no longer depends on
    hooks/modules/security/protected_paths.py::is_protected_hook_path: that
    predicate stopped covering the source checkout (decision
    decision_gaia_proteccion_sigue_a_la_instalacion_no_al_repo), so a refusal
    keyed on it would silently stop firing for the real repo's own
    hooks/hooks.json. The generator's refusal is unconditional on divergence
    instead (see build-plugin.py::_write_generated_manifest) -- the property
    these tests protect never needed the narrower, install-only question.
    """

    SCRIPT = PROJECT_ROOT / "scripts" / "build-plugin.py"

    def _gaia_marked_tree(self, tmp_path: Path) -> Path:
        (tmp_path / "build").mkdir()
        (tmp_path / "build" / "gaia.manifest.json").write_text("{}")
        (tmp_path / "hooks").mkdir()
        return tmp_path

    def _run_generator(self, output_dir: Path, *extra: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.SCRIPT), "gaia", "--manifests-only",
             "--output-dir", str(output_dir), *extra],
            capture_output=True, text=True, timeout=60,
        )

    def _expected_hooks_text(self) -> str:
        build_plugin = _load_build_plugin_module()
        generated = build_plugin.generate_hooks_json(build_plugin.load_manifest("gaia"))
        return json.dumps(generated, indent=2) + "\n"

    def test_refuses_divergent_overwrite_without_force(self, tmp_path):
        tree = self._gaia_marked_tree(tmp_path)
        divergent = json.dumps({"hooks": {"PreToolUse": []}}, indent=2) + "\n"
        (tree / "hooks" / "hooks.json").write_text(divergent)

        result = self._run_generator(tree)

        assert result.returncode == 1, result.stderr
        assert "refusing to overwrite protected generated file" in result.stderr
        assert (tree / "hooks" / "hooks.json").read_text() == divergent, (
            "the refused write must leave the file byte-identical"
        )

    def test_in_sync_tree_is_a_noop(self, tmp_path):
        tree = self._gaia_marked_tree(tmp_path)
        target = tree / "hooks" / "hooks.json"
        target.write_text(self._expected_hooks_text())
        mtime_before = target.stat().st_mtime_ns

        result = self._run_generator(tree)

        assert result.returncode == 0, result.stderr
        assert "hooks/hooks.json: unchanged" in result.stderr
        assert target.stat().st_mtime_ns == mtime_before, (
            "an identical regeneration must not rewrite the file"
        )

    def test_force_applies_the_divergent_overwrite(self, tmp_path):
        tree = self._gaia_marked_tree(tmp_path)
        target = tree / "hooks" / "hooks.json"
        target.write_text(json.dumps({"hooks": {}}) + "\n")

        result = self._run_generator(tree, "--force")

        assert result.returncode == 0, result.stderr
        assert target.read_text() == self._expected_hooks_text()

    def test_missing_file_is_created(self, tmp_path):
        tree = self._gaia_marked_tree(tmp_path)

        result = self._run_generator(tree)

        assert result.returncode == 0, result.stderr
        assert (tree / "hooks" / "hooks.json").read_text() == self._expected_hooks_text()

    def test_refusal_no_longer_depends_on_the_protected_path_predicate(self):
        """The decoupling is structural, not incidental.

        Before decision decision_gaia_proteccion_sigue_a_la_instalacion_no_al_repo,
        this refusal was gated by protected_paths.is_protected_hook_path -- which
        now returns False for the real checkout's own hooks/hooks.json (it is a
        checkout path, not a live install). Asserting the refusal still fires on
        that exact real path (as the OLD test here did) would therefore prove
        nothing: on THIS machine it would pass only if some OTHER lane happened
        to still classify it as protected, which is precisely the coincidence
        that made the checkout's protection a function of deployment layout in
        the first place. So this checks the actual decoupling: the generator no
        longer imports or calls is_protected_hook_path at all, and its refusal
        function takes no `is_protected` parameter -- the behavioral proof that
        it still refuses on divergence, unconditionally, is
        test_refuses_divergent_overwrite_without_force above.
        """
        import ast
        import inspect

        module = _load_build_plugin_module()
        assert not hasattr(module, "_load_protected_predicate"), (
            "the predicate loader should have been removed, not left unused"
        )
        params = inspect.signature(module._write_generated_manifest).parameters
        assert "is_protected" not in params, (
            "_write_generated_manifest still takes an is_protected callable -- "
            "the refusal is still coupled to the narrower, install-only question"
        )

        # AST, not a text/docstring search: the prose above is allowed to name
        # is_protected_hook_path when explaining the decoupling; the code must
        # not import or call it.
        tree = ast.parse(
            (PROJECT_ROOT / "scripts" / "build-plugin.py").read_text(encoding="utf-8")
        )
        calls_and_imports = [
            node
            for node in ast.walk(tree)
            if (isinstance(node, ast.Name) and node.id == "is_protected_hook_path")
            or (
                isinstance(node, ast.ImportFrom)
                and any(alias.name == "is_protected_hook_path" for alias in node.names)
            )
        ]
        assert not calls_and_imports, (
            "build-plugin.py must not import or call is_protected_hook_path in code"
        )


class TestMarketplaceJson:
    """Test .claude-plugin/marketplace.json manifest.

    The marketplace.json is a flat structure with top-level name, owner,
    and plugins array. The gaia entry's `source` is "." -- the marketplace
    root is the plugin root, so `/plugin marketplace add metraton/gaia#<ref>`
    installs the code of that branch or tag; there is no dist/ bundle.
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
        """Each marketplace plugin must have name, description, source."""
        data = json.loads(self.marketplace_path.read_text())
        for plugin in data["plugins"]:
            assert "name" in plugin, f"Plugin missing 'name': {plugin}"
            assert "description" in plugin, f"Plugin missing 'description': {plugin}"
            assert "source" in plugin, f"Plugin missing 'source': {plugin}"

    def test_marketplace_plugin_source_is_the_repo_root(self):
        """The gaia entry's source is "." and it carries no version or ref.

        A relative source resolves inside the marketplace checkout, so the ref
        the user pins with `marketplace add metraton/gaia#<ref>` is the code
        that installs. A github source with its own `ref` would install that
        ref instead, whatever branch the marketplace was added from.
        """
        data = json.loads(self.marketplace_path.read_text())
        (gaia,) = [p for p in data["plugins"] if p["name"] == "gaia"]
        assert gaia["source"] == ".", f"source must be '.', got {gaia['source']!r}"
        assert "version" not in gaia, "the entry must not declare a version"
        assert "ref" not in gaia, "the entry must not declare a ref"


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

    def test_marketplace_owner_ships_no_email(self):
        """marketplace.json owner carries no email; the marketplace schema only requires 'name'."""
        assert "email" not in self.marketplace["owner"], "Owner 'email' must not ship in the package"


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

    def test_marketplace_entries_declare_no_version(self):
        """The plugin version lives once, in plugin.json.

        Claude Code lets plugin.json win over an entry version and only warns,
        so a second copy in the entry could drift without failing anything.
        """
        marketplace_data = json.loads(self.marketplace_path.read_text())
        declared = {p["name"]: p["version"] for p in marketplace_data["plugins"] if "version" in p}
        assert not declared, f"marketplace entries declare a version: {declared}"


def _node_modules_dir() -> Path | None:
    """The node_modules release-prepare.mjs imports chalk from.

    An agent worktree has none of its own; the main checkout that owns it
    (git's common dir) does.
    """
    candidates = [PROJECT_ROOT / "node_modules"]
    common = subprocess.run(
        ["git", "-C", str(PROJECT_ROOT), "rev-parse", "--path-format=absolute", "--git-common-dir"],
        capture_output=True, text=True,
    )
    if common.returncode == 0:
        candidates.append(Path(common.stdout.strip()).parent / "node_modules")
    return next((c for c in candidates if (c / "chalk").is_dir()), None)


@pytest.fixture
def repo_copy(tmp_path: Path) -> Path:
    """A disposable copy of the source tree that release:prepare may rewrite."""
    from tests.conftest import require_tool

    require_tool("node")
    require_tool("npm")
    node_modules = _node_modules_dir()
    if node_modules is None:
        pytest.fail("release:prepare needs an installed node_modules: run `npm ci` in the checkout",
                    pytrace=False)
    copy = tmp_path / "repo"
    shutil.copytree(
        PROJECT_ROOT, copy,
        ignore=shutil.ignore_patterns(".git", "node_modules", "__pycache__", ".pytest_cache"),
    )
    (copy / "node_modules").symlink_to(node_modules, target_is_directory=True)
    subprocess.run(["git", "init", "-q", str(copy)], check=True)
    return copy


class TestReleasePrepareLeavesTheMarketplaceEntryAlone:
    """release:prepare bumps plugin.json and never writes the marketplace entry."""

    VERSION = "9.8.7-rc.6"

    def _prepare(self, repo: Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["node", "scripts/release-prepare.mjs", self.VERSION],
            cwd=repo, capture_output=True, text=True, timeout=300,
        )

    def test_bump_writes_plugin_json_and_not_the_entry(self, repo_copy):
        entry_before = (repo_copy / ".claude-plugin" / "marketplace.json").read_text()

        result = self._prepare(repo_copy)

        assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
        plugin = json.loads((repo_copy / ".claude-plugin" / "plugin.json").read_text())
        assert plugin["version"] == self.VERSION
        assert (repo_copy / ".claude-plugin" / "marketplace.json").read_text() == entry_before

    def test_validator_still_fails_on_plugin_json_drift(self, repo_copy):
        assert self._prepare(repo_copy).returncode == 0
        plugin_path = repo_copy / ".claude-plugin" / "plugin.json"
        plugin = json.loads(plugin_path.read_text())
        plugin["version"] = "0.0.1"
        plugin_path.write_text(json.dumps(plugin, indent=2) + "\n")

        result = subprocess.run(
            ["node", "bin/pre-publish-validate.js", "--validate-only"],
            cwd=repo_copy, capture_output=True, text=True, timeout=300,
        )

        assert result.returncode != 0
        assert "plugin.json" in result.stdout + result.stderr


CHANGELOG_BEFORE = """# Changelog

## [Unreleased]

### Added

- Unreleased feature.

### Fixed

- Unreleased fix.

## [9.9.0-rc.2] - 2026-01-02

### Fixed

- Fix shipped in rc.2.

## [9.9.0-rc.1] - 2026-01-01

## [9.8.0] - 2025-12-01

### Added

- Older stable feature.
"""


def _sections(changelog: str) -> list[str]:
    """The `## ` sections of a changelog, header line included, after the title."""
    return re.split(r"(?m)^(?=## )", changelog)[1:]


class TestReleasePrepareChangelog:
    """release:prepare gives a stable release notes that carry every change since the last stable.

    A stable folds [Unreleased] and the bodies of its own pre-release sections
    into one dated section; a pre-release leaves [Unreleased] whole so the
    stable still finds it. After either bump the CHANGELOG's top version agrees
    with package.json, which pre-publish-validate requires.
    """

    def _prepare(self, repo: Path, version: str) -> str:
        result = subprocess.run(
            ["node", "scripts/release-prepare.mjs", version],
            cwd=repo, capture_output=True, text=True, timeout=300,
        )
        assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
        return (repo / "CHANGELOG.md").read_text()

    def _assert_validator_passes(self, repo: Path) -> None:
        result = subprocess.run(
            ["node", "bin/pre-publish-validate.js", "--validate-only"],
            cwd=repo, capture_output=True, text=True, timeout=300,
        )
        assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]

    def test_stable_folds_unreleased_and_its_prereleases(self, repo_copy):
        """The stable section holds every pending change, each subsection once, under an empty [Unreleased]."""
        (repo_copy / "CHANGELOG.md").write_text(CHANGELOG_BEFORE)

        sections = _sections(self._prepare(repo_copy, "9.9.0"))

        header, body = sections[1].split("\n", 1)
        assert sections[0] == "## [Unreleased]\n\n"
        assert re.fullmatch(r"## \[9\.9\.0\] - \d{4}-\d{2}-\d{2}", header)
        assert body == (
            "\n### Added\n\n- Unreleased feature.\n\n"
            "### Fixed\n\n- Unreleased fix.\n- Fix shipped in rc.2.\n\n"
        )
        assert sections[2:] == _sections(CHANGELOG_BEFORE)[3:]
        self._assert_validator_passes(repo_copy)

    def test_prerelease_keeps_unreleased_for_the_stable(self, repo_copy):
        """A pre-release adds an empty dated header below [Unreleased] and moves no change."""
        (repo_copy / "CHANGELOG.md").write_text(CHANGELOG_BEFORE)

        sections = _sections(self._prepare(repo_copy, "9.9.0-rc.3"))
        before = _sections(CHANGELOG_BEFORE)

        assert sections[0] == before[0]
        assert re.fullmatch(r"## \[9\.9\.0-rc\.3\] - \d{4}-\d{2}-\d{2}\n\n", sections[1])
        assert sections[2:] == before[1:]
        self._assert_validator_passes(repo_copy)

    @pytest.mark.parametrize("version", ["9.9.0", "9.9.0-rc.3"])
    def test_rerun_on_a_bumped_tree_changes_nothing(self, repo_copy, version):
        """Running release:prepare again for the same version leaves the CHANGELOG byte-identical."""
        (repo_copy / "CHANGELOG.md").write_text(CHANGELOG_BEFORE)
        first = self._prepare(repo_copy, version)

        assert self._prepare(repo_copy, version) == first
        self._assert_validator_passes(repo_copy)
