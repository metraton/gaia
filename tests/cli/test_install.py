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
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch, MagicMock

_BIN_DIR = Path(__file__).resolve().parents[2] / "bin"
if str(_BIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BIN_DIR))

from cli.install import register, cmd_install, _create_path_symlink  # noqa: E402


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


class TestCreatePathSymlink(unittest.TestCase):
    """Unit tests for _create_path_symlink."""

    def test_creates_symlink_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            target = tmp_p / "gaia"
            target.write_text("#!/bin/sh\n")
            link = tmp_p / "bin" / "gaia"

            res = _create_path_symlink(target, link)

            self.assertEqual(res["action"], "created")
            self.assertTrue(link.is_symlink())
            self.assertEqual(Path(os.readlink(link)).resolve(), target.resolve())

    def test_creates_parent_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            target = tmp_p / "gaia"
            target.write_text("#!/bin/sh\n")
            link = tmp_p / "deep" / "nested" / "bin" / "gaia"

            res = _create_path_symlink(target, link)

            self.assertEqual(res["action"], "created")
            self.assertTrue(link.parent.is_dir())

    def test_idempotent_when_target_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            target = tmp_p / "gaia"
            target.write_text("#!/bin/sh\n")
            link = tmp_p / "bin" / "gaia"

            _create_path_symlink(target, link)
            res2 = _create_path_symlink(target, link)

            self.assertEqual(res2["action"], "noop")
            self.assertTrue(link.is_symlink())

    def test_skips_when_pointing_at_other_target_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            target = tmp_p / "gaia"
            target.write_text("#!/bin/sh\n")
            other = tmp_p / "other-gaia"
            other.write_text("#!/bin/sh\n")
            link = tmp_p / "bin" / "gaia"
            link.parent.mkdir(parents=True)
            link.symlink_to(other)

            res = _create_path_symlink(target, link)

            self.assertEqual(res["action"], "skipped")
            # Original symlink preserved
            self.assertEqual(Path(os.readlink(link)).resolve(), other.resolve())

    def test_overwrite_replaces_existing_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            target = tmp_p / "gaia"
            target.write_text("#!/bin/sh\n")
            other = tmp_p / "other-gaia"
            other.write_text("#!/bin/sh\n")
            link = tmp_p / "bin" / "gaia"
            link.parent.mkdir(parents=True)
            link.symlink_to(other)

            res = _create_path_symlink(target, link, overwrite=True)

            self.assertEqual(res["action"], "replaced")
            self.assertEqual(Path(os.readlink(link)).resolve(), target.resolve())

    def test_skips_when_regular_file_in_the_way(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            target = tmp_p / "gaia"
            target.write_text("#!/bin/sh\n")
            link = tmp_p / "bin" / "gaia"
            link.parent.mkdir(parents=True)
            link.write_text("not a symlink")

            res = _create_path_symlink(target, link)

            self.assertEqual(res["action"], "skipped")
            self.assertEqual(link.read_text(), "not a symlink")


class TestCmdInstallPathSymlink(unittest.TestCase):
    """Verify cmd_install creates the PATH symlink unless --no-path is set."""

    def _make_args(self, workspace, link_path, **overrides) -> argparse.Namespace:
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

    def test_default_creates_path_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "ws"
            workspace.mkdir()
            (workspace / ".claude").mkdir()
            link = Path(tmp) / "local" / "bin" / "gaia"

            captured = {}

            real_fn = _create_path_symlink

            def fake_create(target, link_path="~/.local/bin/gaia", overwrite=False):
                captured["called"] = True
                captured["target"] = Path(target)
                # Actually exercise the implementation against tmp link
                return real_fn(target, link, overwrite=overwrite)

            patches = self._patch_helpers_noop()
            patches.append(patch("cli.install._run_bootstrap", return_value=0))
            patches.append(patch("cli.install._create_path_symlink",
                                 side_effect=fake_create))

            for p in patches:
                p.start()
            try:
                with redirect_stdout(io.StringIO()):
                    rc = cmd_install(self._make_args(workspace, link))
            finally:
                for p in patches:
                    p.stop()

            self.assertEqual(rc, 0)
            self.assertTrue(captured.get("called"))
            self.assertTrue(link.is_symlink())

    def test_no_path_flag_skips_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "ws"
            workspace.mkdir()
            (workspace / ".claude").mkdir()

            patches = self._patch_helpers_noop()
            patches.append(patch("cli.install._run_bootstrap", return_value=0))
            mock_sym = patch("cli.install._create_path_symlink")
            patches.append(mock_sym)

            started = [p.start() for p in patches]
            try:
                with redirect_stdout(io.StringIO()):
                    rc = cmd_install(
                        self._make_args(workspace, None, no_path=True)
                    )
            finally:
                for p in patches:
                    p.stop()

            self.assertEqual(rc, 0)
            # Last started corresponds to mock_sym
            mock_create = started[-1]
            mock_create.assert_not_called()

    def test_install_path_symlink_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "ws"
            workspace.mkdir()
            (workspace / ".claude").mkdir()
            link = Path(tmp) / "local" / "bin" / "gaia"

            results = []
            real_fn = _create_path_symlink

            def fake_create(target, link_path="~/.local/bin/gaia", overwrite=False):
                r = real_fn(target, link, overwrite=overwrite)
                results.append(r)
                return r

            patches = self._patch_helpers_noop()
            patches.append(patch("cli.install._run_bootstrap", return_value=0))
            patches.append(patch("cli.install._create_path_symlink",
                                 side_effect=fake_create))

            for p in patches:
                p.start()
            try:
                with redirect_stdout(io.StringIO()):
                    cmd_install(self._make_args(workspace, link))
                    cmd_install(self._make_args(workspace, link))
            finally:
                for p in patches:
                    p.stop()

            self.assertEqual(len(results), 2)
            self.assertEqual(results[0]["action"], "created")
            self.assertEqual(results[1]["action"], "noop")
            self.assertTrue(link.is_symlink())

    def test_install_skips_when_other_symlink_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "ws"
            workspace.mkdir()
            (workspace / ".claude").mkdir()
            other = Path(tmp) / "other-gaia"
            other.write_text("#!/bin/sh\n")
            link = Path(tmp) / "local" / "bin" / "gaia"
            link.parent.mkdir(parents=True)
            link.symlink_to(other)

            captured = {}
            real_fn = _create_path_symlink

            def fake_create(target, link_path="~/.local/bin/gaia", overwrite=False):
                r = real_fn(target, link, overwrite=overwrite)
                captured["result"] = r
                return r

            patches = self._patch_helpers_noop()
            patches.append(patch("cli.install._run_bootstrap", return_value=0))
            patches.append(patch("cli.install._create_path_symlink",
                                 side_effect=fake_create))

            for p in patches:
                p.start()
            try:
                with redirect_stdout(io.StringIO()):
                    cmd_install(self._make_args(workspace, link))
            finally:
                for p in patches:
                    p.stop()

            self.assertEqual(captured["result"]["action"], "skipped")
            # Existing target preserved
            self.assertEqual(Path(os.readlink(link)).resolve(), other.resolve())


if __name__ == "__main__":
    unittest.main()
