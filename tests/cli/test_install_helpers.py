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
