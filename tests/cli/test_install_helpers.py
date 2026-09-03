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

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BIN_DIR = _REPO_ROOT / "bin"
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
            self.assertEqual(data["permissions"]["defaultMode"], "acceptEdits")
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

    def test_preserves_user_default_mode(self):
        """A permission mode the user already chose is theirs -- never overwritten."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / ".claude").mkdir()
            local = workspace / ".claude" / "settings.local.json"
            local.write_text(json.dumps({
                "permissions": {"allow": [], "deny": [], "ask": [], "defaultMode": "plan"},
            }))
            helpers.merge_local_permissions(workspace)
            data = json.loads(local.read_text())
            self.assertEqual(data["permissions"]["defaultMode"], "plan")

    def test_adds_default_mode_to_already_installed_workspace(self):
        """`gaia update` must pin the mode on installs that predate the key.

        Such a workspace already carries Gaia's full allow/deny, so the merge
        finds nothing else to change. The key only reaches disk if adding it
        counts as a changed field -- otherwise the helper short-circuits to
        `noop` and never writes.
        """
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / ".claude").mkdir()
            local = workspace / ".claude" / "settings.local.json"
            helpers.merge_local_permissions(workspace)
            pre_change = json.loads(local.read_text())
            del pre_change["permissions"]["defaultMode"]
            local.write_text(json.dumps(pre_change))

            res = helpers.merge_local_permissions(workspace)

            self.assertEqual(res["action"], "updated")
            data = json.loads(local.read_text())
            self.assertEqual(data["permissions"]["defaultMode"], "acceptEdits")

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

    def test_bakes_stable_symlink_path_not_resolved_target(self):
        """Regression: dev+resume hook breakage.

        When `.claude/hooks` is a symlink (the normal installed state), the
        baked hook command must reference the STABLE `.claude/hooks/...` path,
        NOT the symlink's resolved target. Following the symlink baked the
        pnpm content-addressed virtual-store path into settings.local.json;
        that path's content-hash segment changes on every `gaia dev` content
        change and the old store dir is pruned, so the harness -- which pins
        hook commands at session start -- kept a dangling path on resume.
        """
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "ws"
            (workspace / ".claude").mkdir(parents=True)
            pkg = workspace / ".claude" / ".pkg"
            (pkg / "hooks").mkdir(parents=True)
            (pkg / "hooks" / "hooks.json").write_text(json.dumps({
                "hooks": {
                    "PreToolUse": [{
                        "matcher": "Bash",
                        "hooks": [{"type": "command",
                                   "command": "${CLAUDE_PLUGIN_ROOT}/hooks/pre.py"}],
                    }]
                }
            }))
            # A VOLATILE target standing in for the pnpm content-addressed
            # store dir: the .claude/hooks symlink points here today.
            volatile = Path(tmp) / "store-97c4c22d" / "hooks"
            volatile.mkdir(parents=True)
            (workspace / ".claude" / "hooks").symlink_to(volatile)

            res = helpers.merge_local_hooks(workspace, plugin_root=pkg)
            self.assertEqual(res["action"], "updated")
            data = json.loads((workspace / ".claude" / "settings.local.json").read_text())
            cmd = data["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
            # Baked path must go through the stable .claude/hooks symlink...
            self.assertIn("/.claude/hooks/pre.py", cmd)
            # ...and must NOT bake the volatile resolved store target.
            self.assertNotIn("store-97c4c22d", cmd)

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
# merge_local_hooks -- prune of events hooks.json no longer ships
# ---------------------------------------------------------------------------

class TestMergeLocalHooksPrunesRetiredEvents(unittest.TestCase):
    """The merge used to be additive only.

    Retiring an event from hooks.json therefore left its registration alive in
    every already-installed workspace, pointing at an entry-point file the
    release had deleted. Each test here fails if the prune is reverted.

    RETIRED_EVENT is deliberately a name no host defines and no release ever
    shipped: _is_gaia_hook_command keys ownership on the command TARGET, never
    on the event name, so binding the fixture to whichever event was retired
    last would assert an identity the prune does not read.
    """

    RETIRED_EVENT = "RetiredGaiaEvent"

    def _stage(self, tmp, local_hooks: dict):
        """Build a workspace whose settings.local.json holds *local_hooks*.

        Returns the workspace, the package root shipping one PreToolUse event,
        and the absolute hooks dir the merge bakes into commands.
        """
        workspace = Path(tmp) / "ws"
        (workspace / ".claude").mkdir(parents=True)
        pkg = Path(tmp) / "pkg"
        (pkg / "hooks").mkdir(parents=True)
        (pkg / "hooks" / "hooks.json").write_text(json.dumps({
            "hooks": {
                "PreToolUse": [{
                    "matcher": "Bash",
                    "hooks": [{"type": "command",
                               "command": "${CLAUDE_PLUGIN_ROOT}/hooks/pre_tool_use.py"}],
                }]
            }
        }))
        hooks_abs = ((workspace / ".claude").resolve() / "hooks").as_posix()
        (workspace / ".claude" / "settings.local.json").write_text(
            json.dumps({"hooks": local_hooks})
        )
        return workspace, pkg, hooks_abs

    def _local_hooks(self, workspace) -> dict:
        path = workspace / ".claude" / "settings.local.json"
        return json.loads(path.read_text())["hooks"]

    def test_retired_gaia_event_is_pruned(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace, pkg, hooks_abs = self._stage(tmp, {})
            (workspace / ".claude" / "settings.local.json").write_text(json.dumps({
                "hooks": {
                    self.RETIRED_EVENT: [{
                        "hooks": [{
                            "type": "command",
                            "command": f"python3 {hooks_abs}/retired_handler.py",
                        }],
                    }],
                }
            }))
            before = sorted(self._local_hooks(workspace))
            res = helpers.merge_local_hooks(workspace, plugin_root=pkg)
            after = sorted(self._local_hooks(workspace))
            print(f"BEFORE hooks keys: {before}")
            print(f"AFTER  hooks keys: {after}")
            self.assertEqual(res["action"], "updated")
            self.assertIn(self.RETIRED_EVENT, before)
            self.assertNotIn(self.RETIRED_EVENT, after)
            self.assertIn("PreToolUse", after)

    def test_third_party_entry_on_retired_event_survives(self):
        """The prune removes Gaia-owned entries only."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace, pkg, hooks_abs = self._stage(tmp, {})
            third_party = {
                "hooks": [{"type": "command", "command": "/opt/vendor/bin/audit.sh"}],
            }
            (workspace / ".claude" / "settings.local.json").write_text(json.dumps({
                "hooks": {
                    self.RETIRED_EVENT: [
                        {"hooks": [{
                            "type": "command",
                            "command": f"python3 {hooks_abs}/retired_handler.py",
                        }]},
                        third_party,
                    ],
                }
            }))
            helpers.merge_local_hooks(workspace, plugin_root=pkg)
            after = self._local_hooks(workspace)
            print(f"AFTER retired-event entries: {after.get(self.RETIRED_EVENT)}")
            self.assertEqual(after[self.RETIRED_EVENT], [third_party])

    def test_mixed_entry_keeps_only_the_third_party_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace, pkg, hooks_abs = self._stage(tmp, {})
            (workspace / ".claude" / "settings.local.json").write_text(json.dumps({
                "hooks": {
                    self.RETIRED_EVENT: [{
                        "hooks": [
                            {"type": "command",
                             "command": f"python3 {hooks_abs}/retired_handler.py"},
                            {"type": "command", "command": "/opt/vendor/bin/audit.sh"},
                        ],
                    }],
                }
            }))
            helpers.merge_local_hooks(workspace, plugin_root=pkg)
            after = self._local_hooks(workspace)
            commands = [h["command"] for h in after[self.RETIRED_EVENT][0]["hooks"]]
            self.assertEqual(commands, ["/opt/vendor/bin/audit.sh"])

    def test_event_gaia_never_owned_is_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace, pkg, _ = self._stage(tmp, {})
            vendor = {"hooks": [{"type": "command", "command": "/opt/vendor/bin/x.sh"}]}
            (workspace / ".claude" / "settings.local.json").write_text(
                json.dumps({"hooks": {"VendorOnlyEvent": [vendor]}})
            )
            helpers.merge_local_hooks(workspace, plugin_root=pkg)
            after = self._local_hooks(workspace)
            self.assertEqual(after["VendorOnlyEvent"], [vendor])

    def test_third_party_survives_the_prune_against_the_shipped_hooks_json(self):
        """The same two outcomes, driven by the real hooks.json this repo ships.

        The sibling tests merge against a one-event stand-in, which cannot show
        that the prune reads the shipped event set correctly. This one points
        plugin_root at the repository itself, so shipped_events comes from the
        real manifest-generated file and the retired event is absent from it for
        the same reason it is absent in a release.
        """
        with tempfile.TemporaryDirectory() as tmp:
            workspace, _, hooks_abs = self._stage(tmp, {})
            third_party = {
                "hooks": [{"type": "command", "command": "/opt/vendor/bin/audit.sh"}],
            }
            (workspace / ".claude" / "settings.local.json").write_text(json.dumps({
                "permissions": {"allow": ["Bash(ls:*)"]},
                "hooks": {
                    self.RETIRED_EVENT: [
                        {"hooks": [{
                            "type": "command",
                            "command": f"python3 {hooks_abs}/retired_handler.py",
                        }]},
                        third_party,
                    ],
                },
            }))
            before = sorted(self._local_hooks(workspace))
            helpers.merge_local_hooks(workspace, plugin_root=_REPO_ROOT)
            after_all = json.loads(
                (workspace / ".claude" / "settings.local.json").read_text()
            )
            after = sorted(after_all["hooks"])
            print(f"SHIPPED-MERGE BEFORE hooks keys: {before}")
            print(f"SHIPPED-MERGE AFTER  hooks keys: {after}")
            print(
                "SHIPPED-MERGE retired-event entries AFTER: "
                f"{after_all['hooks'].get(self.RETIRED_EVENT)}"
            )
            self.assertEqual(
                after_all["hooks"][self.RETIRED_EVENT], [third_party],
                "the third-party entry must survive and the Gaia-owned one must go",
            )
            self.assertIn("PreToolUse", after)
            self.assertEqual(after_all["permissions"], {"allow": ["Bash(ls:*)"]})


# ---------------------------------------------------------------------------
# merge_worktree_settings
# ---------------------------------------------------------------------------

class TestMergeWorktreeSettings(unittest.TestCase):
    def test_skipped_when_no_claude_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            res = helpers.merge_worktree_settings(Path(tmp))
        self.assertEqual(res["action"], "skipped")

    def test_creates_settings_local_with_bg_isolation_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / ".claude").mkdir()
            res = helpers.merge_worktree_settings(workspace)
            self.assertEqual(res["action"], "updated")
            data = json.loads((workspace / ".claude" / "settings.local.json").read_text())
            self.assertEqual(data["worktree"]["bgIsolation"], "none")

    def test_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / ".claude").mkdir()
            helpers.merge_worktree_settings(workspace)
            before = (workspace / ".claude" / "settings.local.json").read_text()
            res2 = helpers.merge_worktree_settings(workspace)
            after = (workspace / ".claude" / "settings.local.json").read_text()
            self.assertEqual(res2["action"], "noop")
            self.assertEqual(before, after)

    def test_dry_run_does_not_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / ".claude").mkdir()
            res = helpers.merge_worktree_settings(workspace, dry_run=True)
            self.assertEqual(res["action"], "updated")
            self.assertFalse((workspace / ".claude" / "settings.local.json").exists())

    def test_preserves_unrelated_top_level_content(self):
        """Other settings.local.json keys (hooks, permissions, agent, env)
        set by prior helpers or the user must not be lost or reordered away."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / ".claude").mkdir()
            local = workspace / ".claude" / "settings.local.json"
            local.write_text(json.dumps({
                "agent": "gaia-orchestrator",
                "env": {"CUSTOM_VAR": "x"},
                "permissions": {"allow": ["MyCustomTool(*)"], "deny": [], "ask": []},
            }))
            helpers.merge_worktree_settings(workspace)
            data = json.loads(local.read_text())
            self.assertEqual(data["agent"], "gaia-orchestrator")
            self.assertEqual(data["env"]["CUSTOM_VAR"], "x")
            self.assertIn("MyCustomTool(*)", data["permissions"]["allow"])
            self.assertEqual(data["worktree"]["bgIsolation"], "none")

    def test_preserves_sibling_keys_under_worktree(self):
        """Only `bgIsolation` is Gaia-owned -- any other key nested under
        `worktree` (present or future harness options) is left untouched."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / ".claude").mkdir()
            local = workspace / ".claude" / "settings.local.json"
            local.write_text(json.dumps({
                "worktree": {"someOtherOption": "keep-me"},
            }))
            helpers.merge_worktree_settings(workspace)
            data = json.loads(local.read_text())
            self.assertEqual(data["worktree"]["someOtherOption"], "keep-me")
            self.assertEqual(data["worktree"]["bgIsolation"], "none")

    def test_normalizes_existing_divergent_value(self):
        """A pre-existing worktree.bgIsolation set to anything other than
        "none" (a harness default, or a value written before this fix
        existed) is normalized to "none" -- this key is authoritative, like
        the `agent` identity field, not a user preference to preserve."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / ".claude").mkdir()
            local = workspace / ".claude" / "settings.local.json"
            local.write_text(json.dumps({
                "worktree": {"bgIsolation": "worktree"},
            }))
            res = helpers.merge_worktree_settings(workspace)
            self.assertEqual(res["action"], "updated")
            data = json.loads(local.read_text())
            self.assertEqual(data["worktree"]["bgIsolation"], "none")

    def test_malformed_worktree_value_is_replaced(self):
        """A non-dict `worktree` value (corrupted file) is replaced with a
        fresh dict carrying only bgIsolation, rather than raising."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / ".claude").mkdir()
            local = workspace / ".claude" / "settings.local.json"
            local.write_text(json.dumps({"worktree": "not-a-dict"}))
            res = helpers.merge_worktree_settings(workspace)
            self.assertEqual(res["action"], "updated")
            data = json.loads(local.read_text())
            self.assertEqual(data["worktree"], {"bgIsolation": "none"})


# ---------------------------------------------------------------------------
# merge_worktree_settings -- git-scoped forcing
#
# The measured regression this class pins: forcing bgIsolation to "none"
# unconditionally undid a user's own choice (or the harness default) inside
# a git working tree, where the original bug (the harness cannot create a
# worktree because there is no repo) does not apply, and every reinstall
# re-asserted "none" -- so the override survived no longer than the next
# `gaia install`/`gaia update`. These tests exercise the git-vs-no-git split
# `_workspace_is_inside_git_work_tree` decides, not the value-merging logic
# above (already covered by `TestMergeWorktreeSettings`, all of which run in
# a plain tmpdir with no `.git` -- i.e. already exercise the no-git branch).
# ---------------------------------------------------------------------------

def _git_init(path: Path) -> None:
    import subprocess

    subprocess.run(["git", "init", "--quiet", str(path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "test@example.com"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.name", "test"], check=True, capture_output=True
    )


class TestMergeWorktreeSettingsGitScope(unittest.TestCase):
    def test_repo_root_workspace_is_skipped_key_absent(self):
        """Inside a git working tree, an absent key is left absent -- Gaia
        does not manufacture a value even to spare the user remembering."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            _git_init(workspace)
            (workspace / ".claude").mkdir()
            res = helpers.merge_worktree_settings(workspace)
            self.assertEqual(res["action"], "skipped")
            local = workspace / ".claude" / "settings.local.json"
            self.assertFalse(local.exists())

    def test_repo_root_workspace_never_overwrites_worktree_value(self):
        """The user's own choice (`worktree`, matching the harness default
        the fix restores) survives untouched."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            _git_init(workspace)
            (workspace / ".claude").mkdir()
            local = workspace / ".claude" / "settings.local.json"
            local.write_text(json.dumps({"worktree": {"bgIsolation": "worktree"}}))
            res = helpers.merge_worktree_settings(workspace)
            self.assertEqual(res["action"], "skipped")
            data = json.loads(local.read_text())
            self.assertEqual(data["worktree"]["bgIsolation"], "worktree")

    def test_repo_root_workspace_never_overwrites_none_either(self):
        """A user who deliberately chose "none" inside a git repo keeps it --
        this is no longer Gaia's key to normalize there in either direction."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            _git_init(workspace)
            (workspace / ".claude").mkdir()
            local = workspace / ".claude" / "settings.local.json"
            local.write_text(json.dumps({"worktree": {"bgIsolation": "none"}}))
            res = helpers.merge_worktree_settings(workspace)
            self.assertEqual(res["action"], "skipped")
            data = json.loads(local.read_text())
            self.assertEqual(data["worktree"]["bgIsolation"], "none")

    def test_reinstall_over_a_git_workspace_does_not_revert_the_choice(self):
        """The concrete failure this task exists to fix: a user flips
        bgIsolation to "worktree" by hand, then reinstalls/updates -- the
        choice must survive, not be silently reasserted back to "none"."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            _git_init(workspace)
            (workspace / ".claude").mkdir()
            local = workspace / ".claude" / "settings.local.json"
            local.write_text(json.dumps({"worktree": {"bgIsolation": "worktree"}}))

            # Simulate a `gaia install` immediately followed by a `gaia
            # update` -- both real call sites delegate to this one helper.
            helpers.merge_worktree_settings(workspace)
            helpers.merge_worktree_settings(workspace, dry_run=False)

            data = json.loads(local.read_text())
            self.assertEqual(data["worktree"]["bgIsolation"], "worktree")

    def test_nested_subdirectory_of_a_repo_is_also_skipped(self):
        """`.claude/` installed a few levels into a larger repo (a monorepo
        package, for instance) still counts as "inside a git working tree"
        -- git discovers the ancestor repo upward, so the harness's own
        worktree mechanism has a real repo to target from here too."""
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            _git_init(repo_root)
            nested = repo_root / "packages" / "app"
            nested.mkdir(parents=True)
            (nested / ".claude").mkdir()
            res = helpers.merge_worktree_settings(nested)
            self.assertEqual(res["action"], "skipped")

    def test_umbrella_directory_over_unrelated_repos_is_not_skipped(self):
        """A workspace that merely CONTAINS independent git repos as
        children -- never tracked itself -- is NOT "inside" a working tree:
        git only searches upward from the workspace, never downward into
        its children. The original no-git behavior (force "none") applies
        here exactly as before, since the harness bug still reproduces at
        this level regardless of what its subdirectories happen to be."""
        with tempfile.TemporaryDirectory() as tmp:
            umbrella = Path(tmp)
            nested_repo = umbrella / "some-project"
            nested_repo.mkdir()
            _git_init(nested_repo)
            (umbrella / ".claude").mkdir()
            res = helpers.merge_worktree_settings(umbrella)
            self.assertEqual(res["action"], "updated")
            data = json.loads((umbrella / ".claude" / "settings.local.json").read_text())
            self.assertEqual(data["worktree"]["bgIsolation"], "none")

    def test_no_git_workspace_is_unaffected_by_the_new_check(self):
        """Regression pin: a plain non-git workspace (no `.git` anywhere in
        its ancestry) keeps forcing "none" exactly as before this change."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / ".claude").mkdir()
            res = helpers.merge_worktree_settings(workspace)
            self.assertEqual(res["action"], "updated")
            data = json.loads((workspace / ".claude" / "settings.local.json").read_text())
            self.assertEqual(data["worktree"]["bgIsolation"], "none")


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
