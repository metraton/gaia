"""
Tests for bin/cli/_install_helpers.py.

Each helper must be:
  1. Idempotent -- re-running over a populated state does not mutate.
  2. Dry-run honest -- dry_run=True never writes; reported action matches reality.
  3. Result-shape compliant -- returns {"action", "path", "details"} at minimum.

Parity with bin/gaia-update.js:
  configure_settings_json   <- updateSettingsJson
  merge_local_permissions   <- updateLocalPermissions
  merge_local_hooks         <- updateLocalHooks
  manage_symlinks           <- updateSymlinks
  register_plugin           <- plugin-registry.json write
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

_BIN_DIR = Path(__file__).resolve().parents[2] / "bin"
if str(_BIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BIN_DIR))

from cli import _install_helpers as helpers  # noqa: E402


# ---------------------------------------------------------------------------
# configure_settings_json
# ---------------------------------------------------------------------------

class TestConfigureSettingsJson(unittest.TestCase):
    def test_skipped_when_no_claude_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            res = helpers.configure_settings_json(Path(tmp))
        self.assertEqual(res["action"], "skipped")

    def test_creates_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / ".claude").mkdir()
            res = helpers.configure_settings_json(workspace)
            self.assertEqual(res["action"], "created")
            self.assertTrue((workspace / ".claude" / "settings.json").exists())

    def test_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / ".claude").mkdir()
            helpers.configure_settings_json(workspace)
            res2 = helpers.configure_settings_json(workspace)
            self.assertEqual(res2["action"], "noop")

    def test_dry_run_does_not_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / ".claude").mkdir()
            res = helpers.configure_settings_json(workspace, dry_run=True)
            self.assertEqual(res["action"], "created")
            self.assertFalse((workspace / ".claude" / "settings.json").exists())

    def test_preserves_existing_content(self):
        """Existing settings.json must not be overwritten -- non-invasive contract."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / ".claude").mkdir()
            settings = workspace / ".claude" / "settings.json"
            user_content = '{"customField": "user-value"}\n'
            settings.write_text(user_content)
            res = helpers.configure_settings_json(workspace)
            self.assertEqual(res["action"], "noop")
            self.assertEqual(settings.read_text(), user_content)


# ---------------------------------------------------------------------------
# merge_local_permissions
# ---------------------------------------------------------------------------

class TestMergeLocalPermissions(unittest.TestCase):
    def test_skipped_when_no_claude_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            res = helpers.merge_local_permissions(Path(tmp))
        self.assertEqual(res["action"], "skipped")

    def test_creates_settings_local_with_agent_and_perms(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / ".claude").mkdir()
            res = helpers.merge_local_permissions(workspace, mode="ops")
            self.assertEqual(res["action"], "updated")
            data = json.loads((workspace / ".claude" / "settings.local.json").read_text())
            self.assertEqual(data["agent"], "gaia-orchestrator")
            self.assertIn("Bash(*)", data["permissions"]["allow"])
            self.assertIn("CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS", data["env"])

    def test_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / ".claude").mkdir()
            helpers.merge_local_permissions(workspace, mode="ops")
            res2 = helpers.merge_local_permissions(workspace, mode="ops")
            self.assertEqual(res2["action"], "noop")

    def test_preserves_user_permissions_for_unmanaged_tools(self):
        """User-added entries for tools Gaia does NOT manage must survive."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / ".claude").mkdir()
            local = workspace / ".claude" / "settings.local.json"
            local.write_text(json.dumps({
                "permissions": {
                    "allow": ["MyCustomTool(*)"],
                    "deny": [],
                    "ask": [],
                },
            }))
            helpers.merge_local_permissions(workspace, mode="ops")
            data = json.loads(local.read_text())
            self.assertIn("MyCustomTool(*)", data["permissions"]["allow"])
            self.assertIn("Bash(*)", data["permissions"]["allow"])

    def test_dry_run_does_not_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / ".claude").mkdir()
            res = helpers.merge_local_permissions(workspace, mode="ops", dry_run=True)
            self.assertEqual(res["action"], "updated")
            self.assertFalse((workspace / ".claude" / "settings.local.json").exists())

    def test_preserves_existing_env_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / ".claude").mkdir()
            local = workspace / ".claude" / "settings.local.json"
            local.write_text(json.dumps({
                "env": {"CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "0", "CUSTOM_VAR": "x"},
            }))
            helpers.merge_local_permissions(workspace, mode="ops")
            data = json.loads(local.read_text())
            # Existing values preserved
            self.assertEqual(data["env"]["CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS"], "0")
            self.assertEqual(data["env"]["CUSTOM_VAR"], "x")


# ---------------------------------------------------------------------------
# merge_local_hooks
# ---------------------------------------------------------------------------

class TestMergeLocalHooks(unittest.TestCase):
    def test_skipped_when_no_claude_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            res = helpers.merge_local_hooks(Path(tmp))
        self.assertEqual(res["action"], "skipped")

    def test_skipped_when_hooks_json_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / ".claude").mkdir()
            fake_pkg = Path(tmp) / "fake-pkg"
            fake_pkg.mkdir()
            res = helpers.merge_local_hooks(workspace, plugin_root=fake_pkg)
            self.assertEqual(res["action"], "skipped")

    def test_merges_hooks_into_settings_local(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / ".claude").mkdir()
            pkg = Path(tmp) / "pkg"
            (pkg / "hooks").mkdir(parents=True)
            (pkg / "hooks" / "hooks.json").write_text(json.dumps({
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "Bash",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "${CLAUDE_PLUGIN_ROOT}/hooks/pre.py",
                                }
                            ],
                        }
                    ]
                }
            }))
            res = helpers.merge_local_hooks(workspace, plugin_root=pkg)
            self.assertEqual(res["action"], "updated")
            data = json.loads((workspace / ".claude" / "settings.local.json").read_text())
            cmd = data["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
            self.assertNotIn("${CLAUDE_PLUGIN_ROOT}", cmd)
            self.assertTrue(cmd.endswith("/pre.py"))

    def test_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / ".claude").mkdir()
            pkg = Path(tmp) / "pkg"
            (pkg / "hooks").mkdir(parents=True)
            (pkg / "hooks" / "hooks.json").write_text(json.dumps({
                "hooks": {
                    "PreToolUse": [{
                        "matcher": "Bash",
                        "hooks": [{"type": "command",
                                   "command": "${CLAUDE_PLUGIN_ROOT}/hooks/x.py"}],
                    }]
                }
            }))
            helpers.merge_local_hooks(workspace, plugin_root=pkg)
            res2 = helpers.merge_local_hooks(workspace, plugin_root=pkg)
            self.assertEqual(res2["action"], "noop")

    def test_dry_run_does_not_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / ".claude").mkdir()
            pkg = Path(tmp) / "pkg"
            (pkg / "hooks").mkdir(parents=True)
            (pkg / "hooks" / "hooks.json").write_text(json.dumps({
                "hooks": {"PreToolUse": [{"matcher": "Bash",
                                          "hooks": [{"type": "command",
                                                     "command": "x"}]}]}
            }))
            res = helpers.merge_local_hooks(workspace, plugin_root=pkg, dry_run=True)
            self.assertEqual(res["action"], "updated")
            self.assertFalse((workspace / ".claude" / "settings.local.json").exists())


# ---------------------------------------------------------------------------
# merge_hooks_to_settings_local thin wrapper (Fix 4)
# ---------------------------------------------------------------------------

class TestSetupMergeHooksWrapper(unittest.TestCase):
    """``tools/scan/setup.py::merge_hooks_to_settings_local`` is now a thin
    wrapper that delegates to the canonical
    ``cli._install_helpers.merge_local_hooks``.

    These tests verify:
      1. The wrapper produces an equivalent settings.local.json to a direct
         call against the canonical implementation -- no divergent output.
      2. The wrapper returns the legacy bool API ("True if file modified").
      3. The wrapper is idempotent (matching the canonical behavior).
      4. The output uses absolute paths (canonical), not the legacy
         relative ``.claude/hooks/...`` paths -- this is the contract that
         caused the original divergence.
    """

    def _make_pkg_with_hooks(self, pkg_root: Path) -> Path:
        (pkg_root / "hooks").mkdir(parents=True, exist_ok=True)
        (pkg_root / "hooks" / "hooks.json").write_text(json.dumps({
            "hooks": {
                "PreToolUse": [{
                    "matcher": "Bash",
                    "hooks": [{
                        "type": "command",
                        "command": "${CLAUDE_PLUGIN_ROOT}/hooks/pre.py",
                    }],
                }],
                "PostToolUse": [{
                    "matcher": "*",
                    "hooks": [{
                        "type": "command",
                        "command": "${CLAUDE_PLUGIN_ROOT}/hooks/post.py",
                    }],
                }],
            }
        }))
        return pkg_root / "hooks" / "hooks.json"

    def test_wrapper_matches_canonical_output(self):
        """The wrapper writes the same settings.local.json as a direct call
        to the canonical merge_local_hooks (idempotency / parity guarantee)."""
        with tempfile.TemporaryDirectory() as tmp:
            # Two parallel workspaces -- one driven via wrapper, one direct.
            ws_wrap = Path(tmp) / "wrap"
            ws_can = Path(tmp) / "canonical"
            (ws_wrap / ".claude").mkdir(parents=True)
            (ws_can / ".claude").mkdir(parents=True)

            # Simulate an installed npm package layout under each workspace
            # so the wrapper's _find_installed_package_root resolves it.
            pkg_wrap = ws_wrap / "node_modules" / "@jaguilar87" / "gaia"
            pkg_can = ws_can / "node_modules" / "@jaguilar87" / "gaia"
            self._make_pkg_with_hooks(pkg_wrap)
            self._make_pkg_with_hooks(pkg_can)

            # Wrapper path
            from tools.scan.setup import merge_hooks_to_settings_local
            wrap_changed = merge_hooks_to_settings_local(ws_wrap)

            # Canonical path
            can_res = helpers.merge_local_hooks(ws_can, plugin_root=pkg_can)

            # Both must report "modified" / "updated".
            self.assertTrue(wrap_changed)
            self.assertEqual(can_res["action"], "updated")

            wrap_data = json.loads(
                (ws_wrap / ".claude" / "settings.local.json").read_text()
            )
            can_data = json.loads(
                (ws_can / ".claude" / "settings.local.json").read_text()
            )

            # Commands must use absolute paths (canonical behavior) -- not
            # ${CLAUDE_PLUGIN_ROOT} placeholders, and not legacy .claude/ relative.
            event_to_script = {"PreToolUse": "pre.py", "PostToolUse": "post.py"}
            for event_name, script_name in event_to_script.items():
                wrap_cmd = wrap_data["hooks"][event_name][0]["hooks"][0]["command"]
                can_cmd = can_data["hooks"][event_name][0]["hooks"][0]["command"]
                self.assertNotIn("${CLAUDE_PLUGIN_ROOT}", wrap_cmd)
                self.assertNotIn("${CLAUDE_PLUGIN_ROOT}", can_cmd)
                self.assertFalse(
                    wrap_cmd.startswith(".claude/hooks/"),
                    msg=f"wrapper produced legacy relative path: {wrap_cmd}",
                )
                # Both must end in the same script name -- the absolute prefix
                # differs because the two .claude/hooks/ paths live in distinct
                # workspaces, but the script suffix matches the canonical
                # resolution.
                self.assertTrue(
                    wrap_cmd.endswith(f"/{script_name}"),
                    msg=f"wrapper cmd {wrap_cmd!r} does not end with /{script_name}",
                )
                self.assertTrue(
                    can_cmd.endswith(f"/{script_name}"),
                    msg=f"canonical cmd {can_cmd!r} does not end with /{script_name}",
                )

            # And both files have the same JSON keys / structure shape.
            self.assertEqual(
                set(wrap_data["hooks"].keys()),
                set(can_data["hooks"].keys()),
            )

    def test_wrapper_returns_false_when_already_up_to_date(self):
        """Idempotency contract preserved through the wrapper."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "ws"
            (workspace / ".claude").mkdir(parents=True)
            pkg = workspace / "node_modules" / "@jaguilar87" / "gaia"
            self._make_pkg_with_hooks(pkg)

            from tools.scan.setup import merge_hooks_to_settings_local
            first = merge_hooks_to_settings_local(workspace)
            second = merge_hooks_to_settings_local(workspace)

            self.assertTrue(first)
            self.assertFalse(second, msg="wrapper must be idempotent")

    def test_wrapper_uses_absolute_paths_not_legacy_relative(self):
        """The contract that caused the original divergence: the wrapper must
        emit absolute hook command paths, identical to the canonical helper.
        A regression here would re-introduce two divergent settings.local.json
        flavors between install (absolute) and scan --fresh (relative)."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "ws"
            (workspace / ".claude").mkdir(parents=True)
            pkg = workspace / "node_modules" / "@jaguilar87" / "gaia"
            self._make_pkg_with_hooks(pkg)

            from tools.scan.setup import merge_hooks_to_settings_local
            merge_hooks_to_settings_local(workspace)

            data = json.loads(
                (workspace / ".claude" / "settings.local.json").read_text()
            )
            cmd = data["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
            # Absolute -- starts with /
            self.assertTrue(
                cmd.startswith("/"),
                msg=f"expected absolute path, got: {cmd!r}",
            )
            # Not ${CLAUDE_PLUGIN_ROOT}
            self.assertNotIn("${CLAUDE_PLUGIN_ROOT}", cmd)
            # Not legacy .claude/hooks/
            self.assertFalse(cmd.startswith(".claude/"))

    def test_wrapper_returns_false_when_hooks_json_missing(self):
        """Wrapper must not raise when the package has no hooks.json."""
        # Need to patch _find_package_root so the fallback also misses.
        import unittest.mock as mock
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "ws"
            (workspace / ".claude").mkdir(parents=True)
            # No node_modules layout, no hooks.json anywhere.
            fake_root = Path(tmp) / "empty-pkg"
            fake_root.mkdir()

            from tools.scan import setup as scan_setup
            with mock.patch.object(scan_setup, "_find_package_root",
                                    return_value=fake_root):
                result = scan_setup.merge_hooks_to_settings_local(workspace)

            self.assertFalse(result)


# ---------------------------------------------------------------------------
# manage_symlinks
# ---------------------------------------------------------------------------

class TestManageSymlinks(unittest.TestCase):
    def test_skipped_when_no_claude_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            res = helpers.manage_symlinks(Path(tmp))
        self.assertEqual(res["action"], "skipped")

    def test_creates_missing_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / ".claude").mkdir()
            pkg = Path(tmp) / "pkg"
            (pkg / "agents").mkdir(parents=True)
            (pkg / "hooks").mkdir()
            res = helpers.manage_symlinks(workspace, plugin_root=pkg)
            self.assertEqual(res["action"], "updated")
            self.assertIn("agents", res["fixed"])
            self.assertIn("hooks", res["fixed"])
            self.assertTrue((workspace / ".claude" / "agents").is_symlink())

    def test_idempotent_when_links_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / ".claude").mkdir()
            pkg = Path(tmp) / "pkg"
            (pkg / "agents").mkdir(parents=True)
            helpers.manage_symlinks(workspace, plugin_root=pkg)
            res2 = helpers.manage_symlinks(workspace, plugin_root=pkg)
            self.assertEqual(res2["action"], "noop")

    def test_repairs_broken_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / ".claude").mkdir()
            pkg = Path(tmp) / "pkg"
            (pkg / "agents").mkdir(parents=True)
            # Create a broken symlink to a nonexistent target
            broken_target = Path(tmp) / "ghost"
            (workspace / ".claude" / "agents").symlink_to(broken_target)
            res = helpers.manage_symlinks(workspace, plugin_root=pkg)
            self.assertEqual(res["action"], "updated")
            # Should now resolve
            self.assertTrue((workspace / ".claude" / "agents").resolve().exists())

    def test_dry_run_does_not_create(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / ".claude").mkdir()
            pkg = Path(tmp) / "pkg"
            (pkg / "agents").mkdir(parents=True)
            res = helpers.manage_symlinks(workspace, plugin_root=pkg, dry_run=True)
            self.assertEqual(res["action"], "updated")
            self.assertFalse((workspace / ".claude" / "agents").exists())


# ---------------------------------------------------------------------------
# register_plugin
# ---------------------------------------------------------------------------

class TestRegisterPlugin(unittest.TestCase):
    def _make_pkg(self, root: Path, name="@jaguilar87/gaia", version="5.4.0"):
        root.mkdir(parents=True, exist_ok=True)
        (root / "package.json").write_text(json.dumps({"name": name, "version": version}))
        return root

    def test_creates_registry(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / ".claude").mkdir()
            pkg = self._make_pkg(Path(tmp) / "pkg")
            res = helpers.register_plugin(workspace, plugin_root=pkg, source="cli-install")
            self.assertEqual(res["action"], "created")
            data = json.loads((workspace / ".claude" / "plugin-registry.json").read_text())
            self.assertEqual(data["installed"][0]["name"], "gaia-ops")
            self.assertEqual(data["installed"][0]["version"], "5.4.0")
            self.assertEqual(data["source"], "cli-install")

    def test_idempotent_when_version_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / ".claude").mkdir()
            pkg = self._make_pkg(Path(tmp) / "pkg")
            helpers.register_plugin(workspace, plugin_root=pkg, source="cli-install")
            res2 = helpers.register_plugin(workspace, plugin_root=pkg, source="cli-install")
            self.assertEqual(res2["action"], "noop")

    def test_updates_when_version_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / ".claude").mkdir()
            pkg = self._make_pkg(Path(tmp) / "pkg", version="5.4.0")
            helpers.register_plugin(workspace, plugin_root=pkg, source="cli-install")
            # Bump version
            (pkg / "package.json").write_text(
                json.dumps({"name": "@jaguilar87/gaia", "version": "5.5.0"})
            )
            res2 = helpers.register_plugin(workspace, plugin_root=pkg, source="cli-update")
            self.assertEqual(res2["action"], "updated")
            data = json.loads((workspace / ".claude" / "plugin-registry.json").read_text())
            self.assertEqual(data["installed"][0]["version"], "5.5.0")

    def test_dry_run_does_not_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / ".claude").mkdir()
            pkg = self._make_pkg(Path(tmp) / "pkg")
            res = helpers.register_plugin(
                workspace, plugin_root=pkg, source="cli-install", dry_run=True,
            )
            self.assertEqual(res["action"], "created")
            self.assertFalse((workspace / ".claude" / "plugin-registry.json").exists())

    def test_handles_missing_package_json_gracefully(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / ".claude").mkdir()
            pkg = Path(tmp) / "pkg-no-json"
            pkg.mkdir()
            res = helpers.register_plugin(workspace, plugin_root=pkg, source="cli-install")
            # Still writes a registry, just with version="unknown"
            self.assertIn(res["action"], ("created", "updated"))
            data = json.loads((workspace / ".claude" / "plugin-registry.json").read_text())
            self.assertEqual(data["installed"][0]["version"], "unknown")


if __name__ == "__main__":
    unittest.main()
