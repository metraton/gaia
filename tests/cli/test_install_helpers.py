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
from unittest import mock

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
            res = helpers.merge_local_permissions(workspace)
            self.assertEqual(res["action"], "updated")
            data = json.loads((workspace / ".claude" / "settings.local.json").read_text())
            self.assertEqual(data["agent"], "gaia-orchestrator")
            self.assertIn("Bash(*)", data["permissions"]["allow"])
            self.assertNotIn("CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS", data.get("env", {}))

    def test_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / ".claude").mkdir()
            helpers.merge_local_permissions(workspace)
            res2 = helpers.merge_local_permissions(workspace)
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
            helpers.merge_local_permissions(workspace)
            data = json.loads(local.read_text())
            self.assertIn("MyCustomTool(*)", data["permissions"]["allow"])
            self.assertIn("Bash(*)", data["permissions"]["allow"])

    def test_dry_run_does_not_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / ".claude").mkdir()
            res = helpers.merge_local_permissions(workspace, dry_run=True)
            self.assertEqual(res["action"], "updated")
            self.assertFalse((workspace / ".claude" / "settings.local.json").exists())

    def test_preserves_existing_env_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / ".claude").mkdir()
            local = workspace / ".claude" / "settings.local.json"
            local.write_text(json.dumps({
                "env": {"CUSTOM_VAR": "x"},
            }))
            helpers.merge_local_permissions(workspace)
            data = json.loads(local.read_text())
            # AGENT_TEAMS is not injected regardless of prior state
            self.assertNotIn("CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS", data.get("env", {}))
            # Unrelated user env var preserved
            self.assertEqual(data["env"]["CUSTOM_VAR"], "x")

    def test_writes_canonical_deny_rules(self):
        """Regression (release-check gate 2): a fresh merge MUST write the
        canonical deny rules into settings.local.json.

        Root cause of the original failure: _install_helpers imported
        PERMISSIONS via the dotted `hooks.modules.core.plugin_setup` path, whose
        package __init__ transitively does `from adapters.host_session import ...`
        -- a top-level `adapters` import that only resolves with hooks/ on
        sys.path. During `gaia install` hooks/ was NOT on the path, the import
        raised, the `except` fallback fired, and PERMISSIONS became the
        EMPTY-deny fallback. A fresh install then wrote NO deny rules and
        `gaia doctor` errored (rc=2, "No deny rules"), failing gate 2. This
        asserts the user-visible outcome: deny rules are present and non-empty.
        """
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / ".claude").mkdir()
            helpers.merge_local_permissions(workspace)
            data = json.loads(
                (workspace / ".claude" / "settings.local.json").read_text()
            )
            deny = data["permissions"]["deny"]
            self.assertTrue(deny, "deny rules must not be empty (gate-2 regression)")
            # A canonical destructive rule must be present -- proves the real
            # _DENY_RULES set was merged, not the empty-deny fallback.
            self.assertIn("Bash(kubectl delete:*)", deny)

    def test_permissions_is_not_empty_deny_fallback(self):
        """Guard the ROOT cause directly: the module-level PERMISSIONS resolved
        by _install_helpers must be the canonical set, never the empty-deny
        fallback (allow==['Bash(*)'] and deny==[])."""
        deny = helpers.PERMISSIONS["permissions"].get("deny", [])
        allow = helpers.PERMISSIONS["permissions"].get("allow", [])
        self.assertTrue(
            deny,
            "PERMISSIONS.deny is empty -- the plugin_setup import fell back to "
            "the empty-deny fallback (hooks/ not on sys.path?)",
        )
        self.assertNotEqual(
            allow, ["Bash(*)"],
            "PERMISSIONS.allow is the 1-entry fallback, not the canonical set",
        )


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

    def test_repoints_stale_but_existing_symlink_to_new_package(self):
        # Freshness fix: a symlink pointing at an OLD package location that
        # still exists on disk must be re-pointed at the desired package --
        # previously it was classified "valid" and left stale, so a new
        # install never reached the runtime (the .claude/hooks pin bug).
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / ".claude").mkdir()
            old_pkg = Path(tmp) / "old-pkg"
            new_pkg = Path(tmp) / "new-pkg"
            (old_pkg / "hooks").mkdir(parents=True)
            (new_pkg / "hooks").mkdir(parents=True)
            # Wire .claude/hooks at the OLD (but still existing) package.
            (workspace / ".claude" / "hooks").symlink_to(old_pkg / "hooks")

            res = helpers.manage_symlinks(workspace, plugin_root=new_pkg)

            self.assertEqual(res["action"], "updated")
            self.assertIn("hooks", " ".join(res["fixed"]))
            self.assertEqual(
                (workspace / ".claude" / "hooks").resolve(),
                (new_pkg / "hooks").resolve(),
            )

    def test_symlink_is_stale_flags_divergent_existing_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            claude = Path(tmp) / ".claude"
            claude.mkdir()
            old_pkg = Path(tmp) / "old"
            new_pkg = Path(tmp) / "new"
            (old_pkg / "hooks").mkdir(parents=True)
            (new_pkg / "hooks").mkdir(parents=True)
            link = claude / "hooks"
            link.symlink_to(old_pkg / "hooks")

            stale, reason = helpers._symlink_is_stale(link, new_pkg)
            self.assertTrue(stale)
            self.assertIsNotNone(reason)

    def test_symlink_is_stale_false_when_target_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            claude = Path(tmp) / ".claude"
            claude.mkdir()
            pkg = Path(tmp) / "pkg"
            (pkg / "hooks").mkdir(parents=True)
            link = claude / "hooks"
            link.symlink_to(pkg / "hooks")

            stale, _ = helpers._symlink_is_stale(link, pkg)
            self.assertFalse(stale)


# ---------------------------------------------------------------------------
# manage_symlinks -- Windows copy/junction fallback (WinError 1314)
# ---------------------------------------------------------------------------

class TestManageSymlinksFallbackCopy(unittest.TestCase):
    """When symlink_to raises OSError (Windows without the symlink privilege),
    manage_symlinks must (a) materialize a real copy, (b) stamp it so it is
    recognized as Gaia-managed, and (c) refresh it on a reinstall/update when
    the package version drifts -- never leaving it silently stale."""

    def _make_pkg(self, root: Path, version="5.4.0", content="v1"):
        (root / "agents").mkdir(parents=True, exist_ok=True)
        (root / "agents" / "a.md").write_text(content)
        (root / "hooks").mkdir(parents=True, exist_ok=True)
        (root / "hooks" / "pre.py").write_text(content)
        (root / "package.json").write_text(
            json.dumps({"name": "@jaguilar87/gaia", "version": version})
        )
        return root

    def test_creates_copy_when_symlink_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / ".claude").mkdir()
            pkg = self._make_pkg(Path(tmp) / "pkg")

            with mock.patch.object(
                helpers.Path, "symlink_to", side_effect=OSError("WinError 1314")
            ):
                res = helpers.manage_symlinks(workspace, plugin_root=pkg)

            link = workspace / ".claude" / "agents"
            # (a) a real copy exists -- NOT a symlink
            self.assertFalse(link.is_symlink())
            self.assertTrue(link.is_dir())
            self.assertEqual((link / "a.md").read_text(), "v1")
            self.assertEqual(res["action"], "updated")
            # stamp records the package version + kind
            stamps = json.loads(
                (workspace / ".claude" / helpers._FALLBACK_STAMP_FILE).read_text()
            )
            self.assertEqual(stamps["agents"]["version"], "5.4.0")
            self.assertEqual(stamps["agents"]["kind"], "copy")
            self.assertEqual(stamps["hooks"]["version"], "5.4.0")

    def test_copy_idempotent_same_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / ".claude").mkdir()
            pkg = self._make_pkg(Path(tmp) / "pkg")

            with mock.patch.object(
                helpers.Path, "symlink_to", side_effect=OSError("WinError 1314")
            ):
                helpers.manage_symlinks(workspace, plugin_root=pkg)
                # (b) second run, same version, symlink still unavailable
                res2 = helpers.manage_symlinks(workspace, plugin_root=pkg)

            self.assertEqual(res2["action"], "noop")
            self.assertIn("agents", res2["valid"])
            self.assertEqual(res2["fixed"], [])

    def test_reinstall_refreshes_stale_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / ".claude").mkdir()
            pkg = self._make_pkg(Path(tmp) / "pkg", version="5.4.0", content="v1")

            with mock.patch.object(
                helpers.Path, "symlink_to", side_effect=OSError("WinError 1314")
            ):
                helpers.manage_symlinks(workspace, plugin_root=pkg)
                # bump version + content, reinstall with symlink STILL unavailable
                self._make_pkg(pkg, version="5.5.0", content="v2")
                res2 = helpers.manage_symlinks(workspace, plugin_root=pkg)

            link = workspace / ".claude" / "agents"
            # (c) content refreshed to the new package
            self.assertEqual((link / "a.md").read_text(), "v2")
            self.assertTrue(any("agents" in f for f in res2["fixed"]))
            stamps = json.loads(
                (workspace / ".claude" / helpers._FALLBACK_STAMP_FILE).read_text()
            )
            self.assertEqual(stamps["agents"]["version"], "5.5.0")

    def test_user_managed_dir_without_stamp_untouched(self):
        """A regular dir with NO fallback stamp is genuinely user-managed and
        must NOT be refreshed or removed."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / ".claude").mkdir()
            pkg = self._make_pkg(Path(tmp) / "pkg")
            # Pre-create a real (user-managed) agents dir with distinct content
            user_dir = workspace / ".claude" / "agents"
            user_dir.mkdir()
            (user_dir / "mine.md").write_text("keep me")

            res = helpers.manage_symlinks(workspace, plugin_root=pkg)

            self.assertIn("agents", res["valid"])
            self.assertTrue((user_dir / "mine.md").exists())

    def test_symlink_success_clears_stale_stamp(self):
        """If a copy was stamped but a later run can create a symlink (privilege
        restored) at a drifted version, the entry becomes a symlink and the
        stale stamp is cleared."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / ".claude").mkdir()
            pkg = self._make_pkg(Path(tmp) / "pkg", version="5.4.0")

            with mock.patch.object(
                helpers.Path, "symlink_to", side_effect=OSError("WinError 1314")
            ):
                helpers.manage_symlinks(workspace, plugin_root=pkg)

            # Privilege restored + version bump -> refresh path re-tries symlink
            self._make_pkg(pkg, version="5.5.0", content="v2")
            helpers.manage_symlinks(workspace, plugin_root=pkg)

            link = workspace / ".claude" / "agents"
            self.assertTrue(link.is_symlink())
            stamps = helpers._read_stamps(workspace / ".claude")
            self.assertNotIn("agents", stamps)


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
            self.assertEqual(data["installed"][0]["name"], "gaia")
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
