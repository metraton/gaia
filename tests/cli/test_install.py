"""
Tests for bin/cli/install.py -- gaia install subcommand.

Smoke tests + orchestration tests only -- never invoke
bootstrap_database.sh against a real DB; the helper modules are mocked or
exercised against tmp dirs.

Parity coverage (cmd_install vs gaia-update.js fresh-install path):
  - bootstrap_database.sh         -- mocked
  - configure_settings_json       -- exercised + verified call order
  - merge_local_permissions       -- exercised + verified call order
  - merge_local_hooks             -- exercised + verified call order
  - manage_symlinks               -- exercised + verified call order
  - register_plugin               -- exercised + verified call order
  - gaia scan --fresh (postinstall)-- mocked, verified gated by --postinstall
"""

import argparse
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch, MagicMock

_BIN_DIR = Path(__file__).resolve().parents[2] / "bin"
if str(_BIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BIN_DIR))

from cli.install import (  # noqa: E402
    register,
    cmd_install,
    _install_path_launcher,
    _create_path_symlink,  # legacy alias retained
    _LAUNCHER_SCRIPT,
    _write_install_error_marker,
    _clear_install_error_marker,
)
import cli.install as install_mod  # noqa: E402  # for monkeypatching the marker path


class TestRegisterSubcommand(unittest.TestCase):
    def test_register_creates_install_parser(self):
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="subcommand")
        register(subparsers)

        # Subcommand parses without error
        args = parser.parse_args(["install"])
        self.assertEqual(args.subcommand, "install")

    def test_postinstall_flag(self):
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="subcommand")
        register(subparsers)
        args = parser.parse_args(["install", "--postinstall"])
        self.assertTrue(args.postinstall)

    def test_quiet_flag(self):
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="subcommand")
        register(subparsers)
        args = parser.parse_args(["install", "--quiet"])
        self.assertTrue(args.quiet)

    def test_verbose_flag(self):
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="subcommand")
        register(subparsers)
        args = parser.parse_args(["install", "--verbose"])
        self.assertTrue(args.verbose)

    def test_db_path_flag(self):
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="subcommand")
        register(subparsers)
        args = parser.parse_args(["install", "--db-path", "/tmp/test.db"])
        self.assertEqual(args.db_path, "/tmp/test.db")


class TestHelpOutput(unittest.TestCase):
    def test_help_lists_install_subcommand(self):
        """`gaia --help` (top-level parser with install registered) lists install."""
        parser = argparse.ArgumentParser(prog="gaia")
        subparsers = parser.add_subparsers(dest="subcommand")
        register(subparsers)

        buf = io.StringIO()
        with redirect_stdout(buf):
            parser.print_help()
        output = buf.getvalue()

        self.assertIn("install", output)

    def test_install_help_does_not_run_bootstrap(self):
        """`gaia install --help` exits via SystemExit without invoking bootstrap."""
        parser = argparse.ArgumentParser(prog="gaia")
        subparsers = parser.add_subparsers(dest="subcommand")
        register(subparsers)

        with patch("cli.install._run_bootstrap") as mock_bootstrap:
            with self.assertRaises(SystemExit) as cm:
                parser.parse_args(["install", "--help"])
            self.assertEqual(cm.exception.code, 0)
            mock_bootstrap.assert_not_called()


class TestCmdInstallDispatch(unittest.TestCase):
    """Verify cmd_install delegates to bootstrap and respects flags.

    Bootstrap is mocked out -- these tests never touch the real DB.
    """

    def _make_args(self, **overrides) -> argparse.Namespace:
        ns = argparse.Namespace()
        ns.postinstall = overrides.get("postinstall", False)
        ns.quiet = overrides.get("quiet", False)
        ns.verbose = overrides.get("verbose", False)
        ns.db_path = overrides.get("db_path", None)
        ns.workspace = overrides.get("workspace", None)
        ns.skip_workspace = overrides.get("skip_workspace", True)  # default tests skip workspace
        return ns

    def test_returns_bootstrap_exit_code_on_success(self):
        with patch("cli.install._run_bootstrap", return_value=0) as mock_bs:
            with redirect_stdout(io.StringIO()):
                rc = cmd_install(self._make_args(quiet=True))
        self.assertEqual(rc, 0)
        mock_bs.assert_called_once()

    def test_postinstall_swallows_failure(self):
        """Postinstall mode never returns non-zero -- npm install must not abort."""
        with patch("cli.install._run_bootstrap", return_value=1):
            with redirect_stdout(io.StringIO()):
                rc = cmd_install(self._make_args(postinstall=True, quiet=True))
        self.assertEqual(rc, 0)

    def test_manual_mode_propagates_failure(self):
        with patch("cli.install._run_bootstrap", return_value=1):
            with redirect_stdout(io.StringIO()):
                rc = cmd_install(self._make_args(quiet=True))
        self.assertEqual(rc, 1)

    def test_db_path_forwarded(self):
        captured = {}

        def fake_bootstrap(db_path, verbose, quiet):
            captured["db_path"] = db_path
            return 0

        with patch("cli.install._run_bootstrap", side_effect=fake_bootstrap):
            with redirect_stdout(io.StringIO()):
                cmd_install(self._make_args(quiet=True, db_path="/tmp/x.db"))
        self.assertEqual(captured["db_path"], "/tmp/x.db")


class TestCmdInstallOrchestration(unittest.TestCase):
    """Verify cmd_install invokes every helper in the documented order.

    These tests exercise the parity contract: install must invoke each of
    the 5 workspace helpers (configure_settings_json, merge_local_permissions,
    merge_local_hooks, manage_symlinks, register_plugin) in the documented
    order. Bootstrap is mocked.
    """

    def _make_args(self, workspace, **overrides) -> argparse.Namespace:
        ns = argparse.Namespace()
        ns.postinstall = overrides.get("postinstall", False)
        ns.quiet = overrides.get("quiet", True)  # quiet by default for tests
        ns.verbose = overrides.get("verbose", False)
        ns.db_path = overrides.get("db_path", None)
        ns.workspace = str(workspace) if workspace else None
        ns.skip_workspace = overrides.get("skip_workspace", False)
        return ns

    def test_invokes_all_five_helpers_in_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / ".claude").mkdir()

            call_order = []

            def make_tracker(name):
                def fn(*args, **kwargs):
                    call_order.append(name)
                    return {"action": "noop", "path": str(workspace), "details": "mock"}
                return fn

            with patch("cli.install._run_bootstrap", return_value=0):
                with patch(
                    "cli.install._install_helpers.configure_settings_json",
                    side_effect=make_tracker("settings_json"),
                ), patch(
                    "cli.install._install_helpers.merge_local_permissions",
                    side_effect=make_tracker("permissions"),
                ), patch(
                    "cli.install._install_helpers.merge_local_hooks",
                    side_effect=make_tracker("hooks"),
                ), patch(
                    "cli.install._install_helpers.manage_symlinks",
                    side_effect=make_tracker("symlinks"),
                ), patch(
                    "cli.install._install_helpers.register_plugin",
                    side_effect=make_tracker("registry"),
                ):
                    with redirect_stdout(io.StringIO()):
                        rc = cmd_install(self._make_args(workspace))

            self.assertEqual(rc, 0)
            self.assertEqual(
                call_order,
                ["settings_json", "permissions", "hooks", "symlinks", "registry"],
            )

    def test_postinstall_triggers_fresh_scan_when_no_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / ".claude").mkdir()
            scan_called = {"hit": False}

            def fake_scan(workspace, verbose, quiet):
                scan_called["hit"] = True
                return {"action": "created", "details": "scan ran"}

            with patch("cli.install._run_bootstrap", return_value=0):
                with patch("cli.install._maybe_run_fresh_scan", side_effect=fake_scan):
                    with redirect_stdout(io.StringIO()):
                        cmd_install(self._make_args(workspace, postinstall=True))

            self.assertTrue(scan_called["hit"])

    def test_skip_workspace_only_runs_bootstrap(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / ".claude").mkdir()
            calls = []

            def tracker(*args, **kwargs):
                calls.append("called")
                return {"action": "noop", "path": "x", "details": ""}

            with patch("cli.install._run_bootstrap", return_value=0):
                with patch(
                    "cli.install._install_helpers.configure_settings_json",
                    side_effect=tracker,
                ):
                    with redirect_stdout(io.StringIO()):
                        rc = cmd_install(self._make_args(workspace, skip_workspace=True))

            self.assertEqual(rc, 0)
            self.assertEqual(calls, [])  # helpers never called

    def test_workspace_default_falls_back_to_init_cwd(self):
        """If --workspace not given, defaults to INIT_CWD or cwd."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / ".claude").mkdir()
            captured = {}

            def fake_settings(ws, **kwargs):
                captured["ws"] = ws
                return {"action": "noop", "path": "x", "details": ""}

            ns = argparse.Namespace(
                postinstall=False, quiet=True, verbose=False,
                db_path=None, workspace=None, skip_workspace=False,
            )
            with patch("cli.install._run_bootstrap", return_value=0):
                with patch.dict("os.environ", {"INIT_CWD": str(workspace)}):
                    with patch(
                        "cli.install._install_helpers.configure_settings_json",
                        side_effect=fake_settings,
                    ), patch(
                        "cli.install._install_helpers.merge_local_permissions",
                        return_value={"action": "noop", "path": "x", "details": ""},
                    ), patch(
                        "cli.install._install_helpers.merge_local_hooks",
                        return_value={"action": "noop", "path": "x", "details": ""},
                    ), patch(
                        "cli.install._install_helpers.manage_symlinks",
                        return_value={"action": "noop", "path": "x", "details": ""},
                    ), patch(
                        "cli.install._install_helpers.register_plugin",
                        return_value={"action": "noop", "path": "x", "details": ""},
                    ):
                        with redirect_stdout(io.StringIO()):
                            cmd_install(ns)

            self.assertEqual(captured.get("ws"), workspace)


class TestCmdInstallCreatesClaudeDir(unittest.TestCase):
    """Fix 2 regression coverage.

    Before this fix, cmd_install invoked the four early-helpers
    (configure_settings_json, merge_local_permissions, merge_local_hooks,
    manage_symlinks) before any code path created `.claude/`. Each helper
    early-returned "skipped" on a fresh workspace; only register_plugin
    (the fifth helper) mkdir'd .claude/ -- too late. cmd_install must now
    create .claude/ between bootstrap and the helpers so each helper sees a
    real directory to write into.
    """

    def _make_args(self, workspace, **overrides) -> argparse.Namespace:
        ns = argparse.Namespace()
        ns.postinstall = overrides.get("postinstall", False)
        ns.quiet = overrides.get("quiet", True)
        ns.verbose = overrides.get("verbose", False)
        ns.db_path = overrides.get("db_path", None)
        ns.workspace = str(workspace) if workspace else None
        ns.skip_workspace = overrides.get("skip_workspace", False)
        ns.no_path = overrides.get("no_path", True)  # default: don't write PATH
        return ns

    def test_creates_claude_dir_before_helpers_run(self):
        """When workspace has no .claude/, cmd_install creates it BEFORE any
        helper is called -- so each helper sees the directory it needs."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "fresh-ws"
            workspace.mkdir()
            # NOTE: no `.claude` directory created. This is the precondition.
            self.assertFalse((workspace / ".claude").exists())

            claude_seen = []

            def make_tracker(name):
                def fn(ws, *args, **kwargs):
                    # Record whether .claude/ exists at the moment this
                    # helper is invoked.
                    claude_seen.append((name, (ws / ".claude").exists()))
                    return {"action": "noop", "path": str(ws), "details": ""}
                return fn

            with patch("cli.install._run_bootstrap", return_value=0):
                with patch(
                    "cli.install._install_helpers.configure_settings_json",
                    side_effect=make_tracker("settings_json"),
                ), patch(
                    "cli.install._install_helpers.merge_local_permissions",
                    side_effect=make_tracker("permissions"),
                ), patch(
                    "cli.install._install_helpers.merge_local_hooks",
                    side_effect=make_tracker("hooks"),
                ), patch(
                    "cli.install._install_helpers.manage_symlinks",
                    side_effect=make_tracker("symlinks"),
                ), patch(
                    "cli.install._install_helpers.register_plugin",
                    side_effect=make_tracker("registry"),
                ):
                    with redirect_stdout(io.StringIO()):
                        rc = cmd_install(self._make_args(workspace))

            self.assertEqual(rc, 0)
            # All five helpers must have seen .claude/ already present.
            self.assertEqual(len(claude_seen), 5)
            for name, present in claude_seen:
                self.assertTrue(
                    present,
                    msg=f"helper {name} ran with .claude/ missing -- regression of fix 2",
                )
            # And the directory must persist after cmd_install returns.
            self.assertTrue((workspace / ".claude").exists())
            self.assertTrue((workspace / ".claude").is_dir())

    def test_claude_dir_creation_is_idempotent(self):
        """Pre-existing .claude/ must not be disturbed."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "existing-ws"
            workspace.mkdir()
            (workspace / ".claude").mkdir()
            sentinel = workspace / ".claude" / "sentinel.txt"
            sentinel.write_text("pre-existing user content\n")

            noop = {"action": "noop", "path": "x", "details": ""}
            patches = [
                patch("cli.install._run_bootstrap", return_value=0),
                patch("cli.install._install_helpers.configure_settings_json",
                      return_value=noop),
                patch("cli.install._install_helpers.merge_local_permissions",
                      return_value=noop),
                patch("cli.install._install_helpers.merge_local_hooks",
                      return_value=noop),
                patch("cli.install._install_helpers.manage_symlinks",
                      return_value=noop),
                patch("cli.install._install_helpers.register_plugin",
                      return_value=noop),
            ]
            for p in patches:
                p.start()
            try:
                with redirect_stdout(io.StringIO()):
                    rc = cmd_install(self._make_args(workspace))
            finally:
                for p in patches:
                    p.stop()

            self.assertEqual(rc, 0)
            # Pre-existing content untouched.
            self.assertTrue(sentinel.exists())
            self.assertEqual(sentinel.read_text(), "pre-existing user content\n")

    def test_claude_dir_creation_runs_after_bootstrap(self):
        """If bootstrap fails (non-zero), .claude/ must not be created --
        partial wire-up is a worse outcome than no wire-up."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "fresh-ws"
            workspace.mkdir()

            # Bootstrap returns failure; manual mode (no --postinstall) so
            # cmd_install propagates the failure.
            with patch("cli.install._run_bootstrap", return_value=1):
                with redirect_stdout(io.StringIO()):
                    rc = cmd_install(self._make_args(workspace))

            self.assertEqual(rc, 1)
            # .claude/ must not have been created when bootstrap failed.
            self.assertFalse((workspace / ".claude").exists())


class TestInstallPathLauncher(unittest.TestCase):
    """Unit tests for _install_path_launcher.

    The launcher is workspace-aware: it walks up from cwd looking for a
    local node_modules/@jaguilar87/gaia install, falling back to GAIA_HOME
    and ~/.gaia/global. These tests cover file-level behavior (write,
    idempotency, migration from legacy symlink, fallbacks behavior end to
    end via the embedded shell script).
    """

    def test_creates_launcher_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            link = tmp_p / "bin" / "gaia"

            res = _install_path_launcher(link_path=link)

            self.assertEqual(res["action"], "created")
            self.assertTrue(link.is_file())
            self.assertFalse(link.is_symlink())
            content = link.read_text()
            self.assertEqual(content, _LAUNCHER_SCRIPT)
            # 0755 -- executable bit set
            self.assertTrue(link.stat().st_mode & 0o100)

    def test_creates_parent_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            link = Path(tmp) / "deep" / "nested" / "bin" / "gaia"
            res = _install_path_launcher(link_path=link)
            self.assertEqual(res["action"], "created")
            self.assertTrue(link.parent.is_dir())

    def test_idempotent_when_content_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            link = Path(tmp) / "bin" / "gaia"
            _install_path_launcher(link_path=link)
            res2 = _install_path_launcher(link_path=link)
            self.assertEqual(res2["action"], "noop")
            self.assertTrue(link.is_file())

    def test_migrates_legacy_symlink_to_launcher(self):
        """Legacy installs left a symlink; install must replace it."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            target = tmp_p / "old-gaia"
            target.write_text("#!/bin/sh\n")
            link = tmp_p / "bin" / "gaia"
            link.parent.mkdir(parents=True)
            link.symlink_to(target)

            self.assertTrue(link.is_symlink())  # precondition

            res = _install_path_launcher(link_path=link)

            self.assertEqual(res["action"], "migrated")
            self.assertTrue(link.is_file())
            self.assertFalse(link.is_symlink())
            self.assertEqual(link.read_text(), _LAUNCHER_SCRIPT)

    def test_skips_when_regular_file_with_different_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            link = Path(tmp) / "bin" / "gaia"
            link.parent.mkdir(parents=True)
            link.write_text("custom user content")

            res = _install_path_launcher(link_path=link)

            self.assertEqual(res["action"], "skipped")
            self.assertEqual(link.read_text(), "custom user content")

    def test_overwrite_replaces_drifted_launcher(self):
        with tempfile.TemporaryDirectory() as tmp:
            link = Path(tmp) / "bin" / "gaia"
            link.parent.mkdir(parents=True)
            link.write_text("old launcher version")

            res = _install_path_launcher(link_path=link, overwrite=True)

            self.assertEqual(res["action"], "replaced")
            self.assertEqual(link.read_text(), _LAUNCHER_SCRIPT)
            self.assertTrue(link.stat().st_mode & 0o100)

    def test_skips_when_directory_in_the_way(self):
        with tempfile.TemporaryDirectory() as tmp:
            link = Path(tmp) / "bin" / "gaia"
            link.parent.mkdir(parents=True)
            link.mkdir()
            res = _install_path_launcher(link_path=link)
            self.assertEqual(res["action"], "skipped")

    def test_legacy_alias_still_callable(self):
        """_create_path_symlink must remain importable for back-compat."""
        with tempfile.TemporaryDirectory() as tmp:
            link = Path(tmp) / "bin" / "gaia"
            res = _create_path_symlink(link_path=link)
            self.assertEqual(res["action"], "created")
            self.assertTrue(link.is_file())


class TestLauncherShellBehavior(unittest.TestCase):
    """Run the launcher script under bash and verify resolution order.

    These tests prove the embedded shell logic actually resolves the right
    Gaia install (walk-up local first, then GAIA_HOME, then ~/.gaia/global,
    then fail-fast).

    Note: the harness runs on `/tmp` mounted with `noexec`, which makes
    `[ -x file ]` return false for files there even when chmod 0o755 was
    applied (the kernel rejects the exec bit semantics under noexec).
    The launcher's resolution chain depends on `[ -x ]` succeeding for
    the candidate, so tests stage their fixtures under `$HOME` (which is
    exec-mounted on this harness) instead of /tmp.
    """

    def setUp(self):
        # Stage fixtures under $HOME so they land on an exec-mounted FS.
        # Without this, `[ -x ]` in the launcher script returns false for
        # files under /tmp (noexec) and resolution falls through to
        # fail-fast incorrectly.
        self._tmp = tempfile.mkdtemp(prefix="gaia-launcher-test-", dir=str(Path.home()))

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _write_launcher(self, link: Path) -> None:
        link.parent.mkdir(parents=True, exist_ok=True)
        link.write_text(_LAUNCHER_SCRIPT)
        link.chmod(0o755)

    def _make_fake_gaia(self, dst: Path, label: str) -> None:
        """Create an executable that prints `label` when run via python3."""
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(
            "import sys\n"
            f"print('{label}')\n"
            "sys.exit(0)\n"
        )
        dst.chmod(0o755)

    def _run_launcher(self, launcher: Path, *, cwd: Path, env: dict, args=None):
        """Invoke launcher via `bash <path>` to bypass any noexec parents."""
        cmd = ["bash", str(launcher)]
        if args:
            cmd.extend(args)
        return subprocess.run(
            cmd,
            cwd=str(cwd),
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_walk_up_resolves_workspace_local(self):
        """Launcher prefers ./node_modules/@jaguilar87/gaia/bin/gaia."""
        tmp_p = Path(self._tmp)
        launcher = tmp_p / "bin" / "gaia"
        self._write_launcher(launcher)

        workspace = tmp_p / "ws" / "deep" / "subdir"
        workspace.mkdir(parents=True)
        local_gaia = (
            tmp_p / "ws" / "node_modules" / "@jaguilar87" / "gaia" / "bin" / "gaia"
        )
        self._make_fake_gaia(local_gaia, "LOCAL")

        # Run the launcher from the deep subdir; walk-up should find local_gaia
        result = self._run_launcher(
            launcher,
            cwd=workspace,
            env={**os.environ, "HOME": str(tmp_p), "GAIA_HOME": ""},
            args=["arg1"],
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("LOCAL", result.stdout)

    def test_falls_back_to_gaia_home(self):
        """No node_modules walk-up hit -- falls back to $GAIA_HOME/bin/gaia."""
        tmp_p = Path(self._tmp)
        launcher = tmp_p / "bin" / "gaia"
        self._write_launcher(launcher)

        gaia_home = tmp_p / "gaia-home"
        self._make_fake_gaia(gaia_home / "bin" / "gaia", "GAIA_HOME")

        workspace = tmp_p / "no-modules"
        workspace.mkdir()

        result = self._run_launcher(
            launcher,
            cwd=workspace,
            env={**os.environ, "HOME": str(tmp_p), "GAIA_HOME": str(gaia_home)},
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("GAIA_HOME", result.stdout)

    def test_falls_back_to_user_global(self):
        """No walk-up + no GAIA_HOME -- falls back to ~/.gaia/global/bin/gaia."""
        tmp_p = Path(self._tmp)
        launcher = tmp_p / "bin" / "gaia"
        self._write_launcher(launcher)

        home = tmp_p / "home"
        self._make_fake_gaia(home / ".gaia" / "global" / "bin" / "gaia", "GLOBAL")

        workspace = tmp_p / "no-modules"
        workspace.mkdir()

        result = self._run_launcher(
            launcher,
            cwd=workspace,
            env={**os.environ, "HOME": str(home), "GAIA_HOME": ""},
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("GLOBAL", result.stdout)

    def test_fail_fast_with_message(self):
        """All resolution paths empty -- exit 127 + helpful stderr."""
        tmp_p = Path(self._tmp)
        launcher = tmp_p / "bin" / "gaia"
        self._write_launcher(launcher)

        home = tmp_p / "home"
        home.mkdir()
        workspace = tmp_p / "no-modules"
        workspace.mkdir()

        result = self._run_launcher(
            launcher,
            cwd=workspace,
            env={**os.environ, "HOME": str(home), "GAIA_HOME": ""},
        )
        self.assertEqual(result.returncode, 127)
        self.assertIn("no Gaia installation found", result.stderr)
        self.assertIn("node_modules/@jaguilar87/gaia", result.stderr)
        self.assertIn("GAIA_HOME", result.stderr)
        self.assertIn(".gaia/global", result.stderr)

    def test_propagates_exit_code(self):
        """Launcher must propagate the underlying process's exit code."""
        tmp_p = Path(self._tmp)
        launcher = tmp_p / "bin" / "gaia"
        self._write_launcher(launcher)

        gaia_home = tmp_p / "gaia-home"
        target = gaia_home / "bin" / "gaia"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "import sys\n"
            "sys.exit(42)\n"
        )
        target.chmod(0o755)

        workspace = tmp_p / "no-modules"
        workspace.mkdir()

        result = self._run_launcher(
            launcher,
            cwd=workspace,
            env={**os.environ, "HOME": str(tmp_p), "GAIA_HOME": str(gaia_home)},
        )
        self.assertEqual(result.returncode, 42)


class TestCmdInstallPathLauncher(unittest.TestCase):
    """Verify cmd_install installs the launcher unless --no-path is set."""

    def _make_args(self, workspace, **overrides) -> argparse.Namespace:
        ns = argparse.Namespace()
        ns.postinstall = overrides.get("postinstall", False)
        ns.quiet = overrides.get("quiet", True)
        ns.verbose = overrides.get("verbose", False)
        ns.db_path = overrides.get("db_path", None)
        ns.workspace = str(workspace) if workspace else None
        ns.skip_workspace = overrides.get("skip_workspace", False)
        ns.no_path = overrides.get("no_path", False)
        return ns

    def _patch_helpers_noop(self):
        noop = {"action": "noop", "path": "x", "details": ""}
        return [
            patch("cli.install._install_helpers.configure_settings_json",
                  return_value=noop),
            patch("cli.install._install_helpers.merge_local_permissions",
                  return_value=noop),
            patch("cli.install._install_helpers.merge_local_hooks",
                  return_value=noop),
            patch("cli.install._install_helpers.manage_symlinks",
                  return_value=noop),
            patch("cli.install._install_helpers.register_plugin",
                  return_value=noop),
        ]

    def test_default_installs_launcher(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "ws"
            workspace.mkdir()
            (workspace / ".claude").mkdir()
            link = Path(tmp) / "local" / "bin" / "gaia"

            captured = {}

            def fake_install(link_path="~/.local/bin/gaia", **kwargs):
                captured["called"] = True
                # Actually exercise implementation against tmp link
                return _install_path_launcher(link_path=link, **kwargs)

            patches = self._patch_helpers_noop()
            patches.append(patch("cli.install._run_bootstrap", return_value=0))
            patches.append(patch("cli.install._install_path_launcher",
                                 side_effect=fake_install))

            for p in patches:
                p.start()
            try:
                with redirect_stdout(io.StringIO()):
                    rc = cmd_install(self._make_args(workspace))
            finally:
                for p in patches:
                    p.stop()

            self.assertEqual(rc, 0)
            self.assertTrue(captured.get("called"))
            self.assertTrue(link.is_file())
            self.assertFalse(link.is_symlink())
            self.assertEqual(link.read_text(), _LAUNCHER_SCRIPT)

    def test_no_path_flag_skips_launcher(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "ws"
            workspace.mkdir()
            (workspace / ".claude").mkdir()

            patches = self._patch_helpers_noop()
            patches.append(patch("cli.install._run_bootstrap", return_value=0))
            mock_inst = patch("cli.install._install_path_launcher")
            patches.append(mock_inst)

            started = [p.start() for p in patches]
            try:
                with redirect_stdout(io.StringIO()):
                    rc = cmd_install(self._make_args(workspace, no_path=True))
            finally:
                for p in patches:
                    p.stop()

            self.assertEqual(rc, 0)
            mock_install = started[-1]
            mock_install.assert_not_called()

    def test_install_launcher_idempotent(self):
        """Two consecutive installs -- second is noop."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "ws"
            workspace.mkdir()
            (workspace / ".claude").mkdir()
            link = Path(tmp) / "local" / "bin" / "gaia"

            results = []

            def fake_install(link_path="~/.local/bin/gaia", **kwargs):
                r = _install_path_launcher(link_path=link, **kwargs)
                results.append(r)
                return r

            patches = self._patch_helpers_noop()
            patches.append(patch("cli.install._run_bootstrap", return_value=0))
            patches.append(patch("cli.install._install_path_launcher",
                                 side_effect=fake_install))

            for p in patches:
                p.start()
            try:
                with redirect_stdout(io.StringIO()):
                    cmd_install(self._make_args(workspace))
                    cmd_install(self._make_args(workspace))
            finally:
                for p in patches:
                    p.stop()

            self.assertEqual(len(results), 2)
            self.assertEqual(results[0]["action"], "created")
            self.assertEqual(results[1]["action"], "noop")
            self.assertTrue(link.is_file())

    def test_install_migrates_legacy_symlink(self):
        """Existing legacy symlink at ~/.local/bin/gaia is migrated."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "ws"
            workspace.mkdir()
            (workspace / ".claude").mkdir()
            old_target = Path(tmp) / "old-gaia"
            old_target.write_text("#!/bin/sh\n")
            link = Path(tmp) / "local" / "bin" / "gaia"
            link.parent.mkdir(parents=True)
            link.symlink_to(old_target)
            self.assertTrue(link.is_symlink())

            captured = {}

            def fake_install(link_path="~/.local/bin/gaia", **kwargs):
                r = _install_path_launcher(link_path=link, **kwargs)
                captured["result"] = r
                return r

            patches = self._patch_helpers_noop()
            patches.append(patch("cli.install._run_bootstrap", return_value=0))
            patches.append(patch("cli.install._install_path_launcher",
                                 side_effect=fake_install))

            for p in patches:
                p.start()
            try:
                with redirect_stdout(io.StringIO()):
                    cmd_install(self._make_args(workspace))
            finally:
                for p in patches:
                    p.stop()

            self.assertEqual(captured["result"]["action"], "migrated")
            self.assertTrue(link.is_file())
            self.assertFalse(link.is_symlink())
            self.assertEqual(link.read_text(), _LAUNCHER_SCRIPT)


class TestInstallErrorMarker(unittest.TestCase):
    """Pass 4 Fix 2.1: postinstall scan failures must persist a marker file
    so `gaia doctor` can surface the degradation.

    Contract:
      - postinstall mode + scan error -> marker written, exit 0 (npm can't
        be aborted), stderr explains the marker location
      - postinstall mode + scan success -> marker cleared if stale
      - interactive mode -> marker cleared on every successful install
    """

    def _make_args(self, workspace, **overrides):
        ns = argparse.Namespace()
        ns.postinstall = overrides.get("postinstall", False)
        ns.quiet = overrides.get("quiet", True)
        ns.verbose = overrides.get("verbose", False)
        ns.db_path = None
        ns.workspace = str(workspace)
        ns.skip_workspace = False
        ns.no_path = True  # skip launcher install in marker tests
        return ns

    def _patch_helpers_noop(self):
        noop = {"action": "noop", "path": "x", "details": ""}
        return [
            patch("cli.install._install_helpers.configure_settings_json", return_value=noop),
            patch("cli.install._install_helpers.merge_local_permissions", return_value=noop),
            patch("cli.install._install_helpers.merge_local_hooks", return_value=noop),
            patch("cli.install._install_helpers.manage_symlinks", return_value=noop),
            patch("cli.install._install_helpers.register_plugin", return_value=noop),
        ]

    def test_write_marker_helper_persists_payload(self):
        """_write_install_error_marker writes a JSON file with the expected
        keys. Direct test of the helper, isolated from cmd_install."""
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "marker.json"
            with patch.object(install_mod, "_INSTALL_ERROR_MARKER", marker):
                _write_install_error_marker(
                    workspace=Path("/tmp/fakews"),
                    step="project scan",
                    detail="boom",
                )
            self.assertTrue(marker.is_file())
            data = json.loads(marker.read_text())
            self.assertEqual(data["step"], "project scan")
            self.assertEqual(data["detail"], "boom")
            self.assertEqual(data["workspace"], "/tmp/fakews")
            self.assertIn("timestamp", data)  # ISO8601 string

    def test_clear_marker_helper_removes_file(self):
        """_clear_install_error_marker removes the marker if present and
        is a no-op when the marker does not exist."""
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "marker.json"
            marker.write_text('{"existing": true}')
            with patch.object(install_mod, "_INSTALL_ERROR_MARKER", marker):
                _clear_install_error_marker()
                self.assertFalse(marker.exists())
                # Idempotent -- second call must not raise.
                _clear_install_error_marker()

    def test_postinstall_scan_error_writes_marker(self):
        """When `gaia scan` fails under --postinstall, cmd_install must
        return 0 (so npm install does not abort) and write a marker file
        that `gaia doctor` can pick up."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "ws"
            workspace.mkdir()
            (workspace / ".claude").mkdir()
            marker = Path(tmp) / "marker.json"

            def fake_scan(workspace, verbose, quiet):
                return {"action": "error", "details": "scan blew up"}

            patches = self._patch_helpers_noop()
            patches.append(patch("cli.install._run_bootstrap", return_value=0))
            patches.append(patch("cli.install._maybe_run_fresh_scan", side_effect=fake_scan))
            patches.append(patch.object(install_mod, "_INSTALL_ERROR_MARKER", marker))

            for p in patches:
                p.start()
            try:
                with redirect_stdout(io.StringIO()):
                    rc = cmd_install(self._make_args(workspace, postinstall=True))
            finally:
                for p in patches:
                    p.stop()

            self.assertEqual(rc, 0, "postinstall must never return non-zero")
            self.assertTrue(marker.is_file(), "scan error should have written a marker")
            payload = json.loads(marker.read_text())
            self.assertEqual(payload["step"], "project scan")
            self.assertIn("scan blew up", payload["detail"])

    def test_postinstall_scan_success_clears_stale_marker(self):
        """If a previous postinstall left a marker but the current run
        succeeds, the marker must be cleared."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "ws"
            workspace.mkdir()
            (workspace / ".claude").mkdir()
            marker = Path(tmp) / "marker.json"
            # Seed stale marker
            marker.write_text('{"step": "previous failure"}')

            def fake_scan(workspace, verbose, quiet):
                return {"action": "created", "details": "context seeded"}

            patches = self._patch_helpers_noop()
            patches.append(patch("cli.install._run_bootstrap", return_value=0))
            patches.append(patch("cli.install._maybe_run_fresh_scan", side_effect=fake_scan))
            patches.append(patch.object(install_mod, "_INSTALL_ERROR_MARKER", marker))

            for p in patches:
                p.start()
            try:
                with redirect_stdout(io.StringIO()):
                    rc = cmd_install(self._make_args(workspace, postinstall=True))
            finally:
                for p in patches:
                    p.stop()

            self.assertEqual(rc, 0)
            self.assertFalse(marker.exists(), "stale marker should have been cleared")

    def test_interactive_install_clears_stale_marker(self):
        """Interactive `gaia install` (no --postinstall) means the user is
        repairing things by hand; any prior postinstall marker is no longer
        authoritative and must be cleared."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "ws"
            workspace.mkdir()
            (workspace / ".claude").mkdir()
            marker = Path(tmp) / "marker.json"
            marker.write_text('{"step": "old failure"}')

            patches = self._patch_helpers_noop()
            patches.append(patch("cli.install._run_bootstrap", return_value=0))
            patches.append(patch.object(install_mod, "_INSTALL_ERROR_MARKER", marker))

            for p in patches:
                p.start()
            try:
                with redirect_stdout(io.StringIO()):
                    # postinstall=False is the interactive path
                    rc = cmd_install(self._make_args(workspace, postinstall=False))
            finally:
                for p in patches:
                    p.stop()

            self.assertEqual(rc, 0)
            self.assertFalse(marker.exists(), "interactive install must clear stale marker")


if __name__ == "__main__":
    unittest.main()
