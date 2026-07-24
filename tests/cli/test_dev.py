"""
Tests for bin/cli/dev.py -- `gaia dev` subcommand.

Smoke tests + orchestration tests (mocked), plus one isolated real
end-to-end pack -> install -> wire run.

Hygiene (per prior-session incident): every test that touches install/pack
state uses a `tempfile.TemporaryDirectory()` workspace and, for anything
that could bootstrap a DB, monkeypatches `GAIA_DATA_DIR`/`GAIA_DB` to a tmp
path. Nothing here ever writes to the real `~/.gaia` or the repo's own
`.claude/`.
"""

import argparse
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

_BIN_DIR = Path(__file__).resolve().parents[2] / "bin"
if str(_BIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BIN_DIR))

from cli import dev as dev_mod  # noqa: E402
from cli.dev import (  # noqa: E402
    register,
    cmd_dev,
    default_pack_dest,
    detect_package_manager,
    install_tarball,
    wire_workspace_via_installed_gaia,
    link_source_into_workspace,
    content_address_tarball,
    prune_sibling_tarballs,
    rewrite_workspace_dep_spec,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _npm_available() -> bool:
    import shutil
    return shutil.which("npm") is not None


# ---------------------------------------------------------------------------
# register() / argparse
# ---------------------------------------------------------------------------

class TestRegisterSubcommand(unittest.TestCase):
    def test_register_creates_dev_parser(self):
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="subcommand")
        register(subparsers)
        args = parser.parse_args(["dev"])
        self.assertEqual(args.subcommand, "dev")
        self.assertEqual(args.mode, "pack")  # default

    def test_mode_link_flag(self):
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="subcommand")
        register(subparsers)
        args = parser.parse_args(["dev", "--mode", "link"])
        self.assertEqual(args.mode, "link")

    def test_invalid_mode_rejected(self):
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="subcommand")
        register(subparsers)
        with self.assertRaises(SystemExit):
            parser.parse_args(["dev", "--mode", "bogus"])

    def test_workspace_flag(self):
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="subcommand")
        register(subparsers)
        args = parser.parse_args(["dev", "--workspace", "/tmp/ws"])
        self.assertEqual(args.workspace, "/tmp/ws")

    def test_keep_tarball_and_pack_dest_flags(self):
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="subcommand")
        register(subparsers)
        args = parser.parse_args(["dev", "--keep-tarball", "--pack-dest", "/tmp/dest"])
        self.assertTrue(args.keep_tarball)
        self.assertEqual(args.pack_dest, "/tmp/dest")


class TestHelpOutput(unittest.TestCase):
    def test_help_lists_dev_subcommand(self):
        parser = argparse.ArgumentParser(prog="gaia")
        subparsers = parser.add_subparsers(dest="subcommand")
        register(subparsers)
        buf = io.StringIO()
        with redirect_stdout(buf):
            parser.print_help()
        self.assertIn("dev", buf.getvalue())

    def test_dev_help_exits_zero_without_side_effects(self):
        parser = argparse.ArgumentParser(prog="gaia")
        subparsers = parser.add_subparsers(dest="subcommand")
        register(subparsers)
        with patch("cli.dev._pack_helpers.pack_tarball") as mock_pack:
            with self.assertRaises(SystemExit) as cm:
                parser.parse_args(["dev", "--help"])
            self.assertEqual(cm.exception.code, 0)
            mock_pack.assert_not_called()


# ---------------------------------------------------------------------------
# detect_package_manager
# ---------------------------------------------------------------------------

class TestDetectPackageManager(unittest.TestCase):
    def test_defaults_to_npm(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(detect_package_manager(Path(tmp)), "npm")

    def test_pnpm_lockfile_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "pnpm-lock.yaml").write_text("")
            self.assertEqual(detect_package_manager(Path(tmp)), "pnpm")

    def test_pnpm_workspace_yaml_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "pnpm-workspace.yaml").write_text("")
            self.assertEqual(detect_package_manager(Path(tmp)), "pnpm")


# ---------------------------------------------------------------------------
# default_pack_dest -- stable, persistent per-workspace pack destination
# ---------------------------------------------------------------------------

class TestDefaultPackDest(unittest.TestCase):
    """Regression coverage for the tempfile.TemporaryDirectory() incident:

    the old default deleted the pack destination before `gaia dev` even
    returned, but that same path is recorded as a `file:` dependency in the
    consumer workspace's package.json/pnpm-lock.yaml -- so the next
    `pnpm install` failed with ENOENT. `default_pack_dest` must be a pure
    function of (workspace, GAIA_DATA_DIR) so the same workspace always
    resolves to the same stable path.
    """

    def test_pure_function_same_input_same_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            gaia_data_dir = Path(tmp) / "gaia-data"
            workspace = Path(tmp) / "ws"
            workspace.mkdir()

            with patch.dict(os.environ, {"GAIA_DATA_DIR": str(gaia_data_dir)}):
                first = default_pack_dest(workspace)
                second = default_pack_dest(workspace)

            self.assertEqual(first, second)

    def test_result_is_under_cache_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            gaia_data_dir = Path(tmp) / "gaia-data"
            workspace = Path(tmp) / "ws"
            workspace.mkdir()

            with patch.dict(os.environ, {"GAIA_DATA_DIR": str(gaia_data_dir)}):
                result = default_pack_dest(workspace)

            self.assertTrue(
                str(result).startswith(str(gaia_data_dir / "cache")),
                f"{result} is not under {gaia_data_dir / 'cache'}",
            )

    def test_different_gaia_data_dir_changes_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "ws"
            workspace.mkdir()
            dir_a = Path(tmp) / "data-a"
            dir_b = Path(tmp) / "data-b"

            with patch.dict(os.environ, {"GAIA_DATA_DIR": str(dir_a)}):
                result_a = default_pack_dest(workspace)
            with patch.dict(os.environ, {"GAIA_DATA_DIR": str(dir_b)}):
                result_b = default_pack_dest(workspace)

            self.assertNotEqual(result_a, result_b)


# ---------------------------------------------------------------------------
# install_tarball (mocked subprocess)
# ---------------------------------------------------------------------------

class TestInstallTarball(unittest.TestCase):
    def test_npm_install_invoked_with_expected_args(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / "package.json").write_text("{}")
            tarball = Path(tmp) / "pkg.tgz"
            tarball.write_bytes(b"x")

            captured = {}

            def fake_run(cmd, **kwargs):
                captured["cmd"] = cmd
                captured["cwd"] = kwargs.get("cwd")
                return subprocess.CompletedProcess(cmd, 0, "added 1 package", "")

            with patch("cli.dev.subprocess.run", side_effect=fake_run):
                res = install_tarball(workspace, tarball, package_manager="npm")

            self.assertEqual(res["action"], "created")
            self.assertEqual(res["package_manager"], "npm")
            self.assertEqual(captured["cwd"], str(workspace))
            self.assertIn("install", captured["cmd"])
            self.assertIn(str(tarball), captured["cmd"])

    def test_pnpm_add_invoked_when_pnpm(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / "package.json").write_text("{}")
            tarball = Path(tmp) / "pkg.tgz"
            tarball.write_bytes(b"x")

            captured = {}

            def fake_run(cmd, **kwargs):
                captured["cmd"] = cmd
                return subprocess.CompletedProcess(cmd, 0, "", "")

            with patch("cli.dev.subprocess.run", side_effect=fake_run):
                res = install_tarball(workspace, tarball, package_manager="pnpm")

            self.assertEqual(res["package_manager"], "pnpm")
            self.assertEqual(captured["cmd"][0], "pnpm")
            self.assertEqual(captured["cmd"][1], "add")

    def test_creates_anchor_package_json_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            tarball = Path(tmp) / "pkg.tgz"
            tarball.write_bytes(b"x")
            self.assertFalse((workspace / "package.json").exists())

            calls = []

            def fake_run(cmd, **kwargs):
                calls.append(cmd)
                if cmd[:2] == ["npm", "init"]:
                    (workspace / "package.json").write_text("{}")
                return subprocess.CompletedProcess(cmd, 0, "", "")

            with patch("cli.dev.subprocess.run", side_effect=fake_run):
                install_tarball(workspace, tarball, package_manager="npm")

            self.assertEqual(calls[0][:2], ["npm", "init"])

    def test_nonzero_exit_returns_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / "package.json").write_text("{}")
            tarball = Path(tmp) / "pkg.tgz"
            tarball.write_bytes(b"x")

            with patch(
                "cli.dev.subprocess.run",
                return_value=subprocess.CompletedProcess([], 1, "", "npm ERR! failure"),
            ):
                res = install_tarball(workspace, tarball, package_manager="npm")

            self.assertEqual(res["action"], "error")
            self.assertIn("npm ERR! failure", res["details"])


# ---------------------------------------------------------------------------
# wire_workspace_via_installed_gaia (mocked subprocess)
# ---------------------------------------------------------------------------

class TestWireWorkspaceViaInstalledGaia(unittest.TestCase):
    def test_errors_when_installed_entrypoint_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            res = wire_workspace_via_installed_gaia(Path(tmp))
            self.assertEqual(res["action"], "error")
            self.assertIn("not found", res["details"])

    def test_invokes_installed_gaia_install_with_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            entrypoint = workspace / "node_modules" / "@jaguilar87" / "gaia" / "bin" / "gaia"
            entrypoint.parent.mkdir(parents=True)
            entrypoint.write_text("#!/usr/bin/env python3\n")

            captured = {}

            def fake_run(cmd, **kwargs):
                captured["cmd"] = cmd
                return subprocess.CompletedProcess(cmd, 0, "Gaia ready.", "")

            with patch("cli.dev.subprocess.run", side_effect=fake_run):
                res = wire_workspace_via_installed_gaia(workspace, quiet=True)

            self.assertEqual(res["action"], "created")
            self.assertIn(str(entrypoint), captured["cmd"])
            self.assertIn("install", captured["cmd"])
            self.assertIn("--workspace", captured["cmd"])
            self.assertIn(str(workspace), captured["cmd"])
            self.assertIn("--quiet", captured["cmd"])

    def test_nonzero_exit_returns_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            entrypoint = workspace / "node_modules" / "@jaguilar87" / "gaia" / "bin" / "gaia"
            entrypoint.parent.mkdir(parents=True)
            entrypoint.write_text("#!/usr/bin/env python3\n")

            with patch(
                "cli.dev.subprocess.run",
                return_value=subprocess.CompletedProcess([], 1, "", "boom"),
            ):
                res = wire_workspace_via_installed_gaia(workspace)

            self.assertEqual(res["action"], "error")
            self.assertIn("boom", res["details"])


# ---------------------------------------------------------------------------
# link_source_into_workspace (real filesystem, no subprocess)
# ---------------------------------------------------------------------------

class TestLinkSourceIntoWorkspace(unittest.TestCase):
    def test_creates_symlink_to_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "ws"
            workspace.mkdir()
            source = Path(tmp) / "source"
            source.mkdir()

            res = link_source_into_workspace(workspace, source)

            target = workspace / "node_modules" / "@jaguilar87" / "gaia"
            self.assertEqual(res["action"], "created")
            self.assertTrue(target.is_symlink())
            self.assertEqual(target.resolve(), source.resolve())

    def test_idempotent_when_already_linked(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "ws"
            workspace.mkdir()
            source = Path(tmp) / "source"
            source.mkdir()

            link_source_into_workspace(workspace, source)
            res2 = link_source_into_workspace(workspace, source)

            self.assertEqual(res2["action"], "noop")

    def test_relinks_when_pointing_elsewhere(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "ws"
            workspace.mkdir()
            old_source = Path(tmp) / "old-source"
            old_source.mkdir()
            new_source = Path(tmp) / "new-source"
            new_source.mkdir()

            link_source_into_workspace(workspace, old_source)
            res2 = link_source_into_workspace(workspace, new_source)

            target = workspace / "node_modules" / "@jaguilar87" / "gaia"
            self.assertEqual(res2["action"], "created")
            self.assertEqual(target.resolve(), new_source.resolve())

    def test_refuses_to_clobber_real_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "ws"
            source = Path(tmp) / "source"
            source.mkdir()
            target = workspace / "node_modules" / "@jaguilar87" / "gaia"
            target.mkdir(parents=True)
            (target / "package.json").write_text("{}")

            res = link_source_into_workspace(workspace, source)

            self.assertEqual(res["action"], "skipped")
            self.assertTrue((target / "package.json").exists())


# ---------------------------------------------------------------------------
# cmd_dev fail-loud guard: refuse to run from a non-source-checkout copy
# ---------------------------------------------------------------------------

class TestCmdDevRefusesNonSourceCheckout(unittest.TestCase):
    def _make_args(self, workspace) -> argparse.Namespace:
        ns = argparse.Namespace()
        ns.workspace = str(workspace)
        ns.mode = "pack"
        ns.quiet = True
        ns.verbose = False
        ns.keep_tarball = False
        ns.pack_dest = None
        return ns

    def test_refuses_when_package_root_is_not_a_source_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            slim = Path(tmp) / "slim-install"
            slim.mkdir()
            (slim / "package.json").write_text("{}")  # no build/gaia.manifest.json, no tests/
            workspace = Path(tmp) / "ws"
            workspace.mkdir()

            with patch("cli.dev._PACKAGE_ROOT", slim), \
                 patch("cli.dev._pack_helpers.pack_tarball") as mock_pack:
                stderr = io.StringIO()
                with redirect_stdout(io.StringIO()), redirect_stderr(stderr):
                    rc = cmd_dev(self._make_args(workspace))

            self.assertEqual(rc, 1)
            mock_pack.assert_not_called()
            self.assertIn("not a Gaia SOURCE checkout", stderr.getvalue())
            self.assertIn("python3 <checkout>/bin/gaia dev", stderr.getvalue())

    def test_proceeds_when_package_root_is_a_real_source_checkout(self):
        # The real _PACKAGE_ROOT (this repo's checkout) IS a source checkout,
        # so the guard must not block the mocked orchestration below it.
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            fake_tarball = workspace / "pkg.tgz"
            fake_tarball.write_bytes(b"x")

            with patch("cli.dev._pack_helpers.pack_tarball", return_value={
                "action": "created", "path": str(fake_tarball), "details": "ok",
                "tarball": fake_tarball, "name": "@jaguilar87/gaia", "version": "9.9.9",
            }), patch("cli.dev.install_tarball", return_value={
                "action": "created", "path": str(workspace), "details": "ok", "package_manager": "npm",
            }), patch("cli.dev.wire_workspace_via_installed_gaia", return_value={
                "action": "created", "path": str(workspace), "details": "ok",
            }):
                with redirect_stdout(io.StringIO()):
                    rc = cmd_dev(self._make_args(workspace))

            self.assertEqual(rc, 0)


# ---------------------------------------------------------------------------
# cmd_dev orchestration (mocked pack/install/wire steps)
# ---------------------------------------------------------------------------

class TestCmdDevOrchestrationPackMode(unittest.TestCase):
    """Every test here pins GAIA_DATA_DIR to an isolated tmp dir in setUp:
    the default (pack_dest=None) path now resolves through
    `default_pack_dest` -> `gaia.paths.cache_dir()`, which falls back to the
    real `~/.gaia` when GAIA_DATA_DIR is unset. Pinning keeps every test in
    this class from ever touching the real `~/.gaia`.
    """

    def setUp(self):
        self._data_dir_ctx = tempfile.TemporaryDirectory()
        self._gaia_data_dir = Path(self._data_dir_ctx.name) / "gaia-data"
        self._gaia_data_dir.mkdir()
        self._env_patcher = patch.dict(os.environ, {"GAIA_DATA_DIR": str(self._gaia_data_dir)})
        self._env_patcher.start()

    def tearDown(self):
        self._env_patcher.stop()
        self._data_dir_ctx.cleanup()

    def _make_args(self, workspace, **overrides) -> argparse.Namespace:
        ns = argparse.Namespace()
        ns.workspace = str(workspace)
        ns.mode = overrides.get("mode", "pack")
        ns.quiet = overrides.get("quiet", True)
        ns.verbose = overrides.get("verbose", False)
        ns.keep_tarball = overrides.get("keep_tarball", False)
        ns.pack_dest = overrides.get("pack_dest", None)
        # Default OFF in these orchestration tests so they never invoke a real
        # `npm link` against the machine's global store. The reconcile-ON default
        # path is covered by TestGlobalNpmLinkReconcile below (runner mocked).
        ns.no_global_link = overrides.get("no_global_link", True)
        return ns

    def test_invokes_pack_install_wire_in_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            fake_tarball = Path(tmp) / "pkg.tgz"
            fake_tarball.write_bytes(b"x")

            call_order = []

            def fake_pack(source_root, dest_dir=None, **kwargs):
                call_order.append("pack")
                return {
                    "action": "created", "path": str(fake_tarball), "details": "ok",
                    "tarball": fake_tarball, "name": "@jaguilar87/gaia", "version": "9.9.9",
                }

            def fake_install(ws, tb, **kwargs):
                call_order.append("install")
                return {"action": "created", "path": str(ws), "details": "ok", "package_manager": "npm"}

            def fake_wire(ws, **kwargs):
                call_order.append("wire")
                return {"action": "created", "path": str(ws), "details": "ok"}

            with patch("cli.dev._pack_helpers.pack_tarball", side_effect=fake_pack), \
                 patch("cli.dev.install_tarball", side_effect=fake_install), \
                 patch("cli.dev.wire_workspace_via_installed_gaia", side_effect=fake_wire):
                with redirect_stdout(io.StringIO()):
                    rc = cmd_dev(self._make_args(workspace))

            self.assertEqual(rc, 0)
            self.assertEqual(call_order, ["pack", "install", "wire"])

    def test_pack_failure_short_circuits(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            calls = []

            with patch(
                "cli.dev._pack_helpers.pack_tarball",
                return_value={"action": "error", "path": "", "details": "npm pack failed"},
            ), patch("cli.dev.install_tarball", side_effect=lambda *a, **k: calls.append("install")), \
               patch("cli.dev.wire_workspace_via_installed_gaia", side_effect=lambda *a, **k: calls.append("wire")):
                with redirect_stdout(io.StringIO()):
                    rc = cmd_dev(self._make_args(workspace))

            self.assertEqual(rc, 1)
            self.assertEqual(calls, [])

    def test_install_failure_short_circuits_before_wire(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            fake_tarball = Path(tmp) / "pkg.tgz"
            fake_tarball.write_bytes(b"x")
            wire_calls = []

            with patch(
                "cli.dev._pack_helpers.pack_tarball",
                return_value={
                    "action": "created", "path": str(fake_tarball), "details": "ok",
                    "tarball": fake_tarball, "name": "@jaguilar87/gaia", "version": "9.9.9",
                },
            ), patch(
                "cli.dev.install_tarball",
                return_value={"action": "error", "path": "", "details": "npm install failed", "package_manager": "npm"},
            ), patch(
                "cli.dev.wire_workspace_via_installed_gaia",
                side_effect=lambda *a, **k: wire_calls.append("wire"),
            ):
                with redirect_stdout(io.StringIO()):
                    rc = cmd_dev(self._make_args(workspace))

            self.assertEqual(rc, 1)
            self.assertEqual(wire_calls, [])

    def test_workspace_missing_returns_error_without_packing(self):
        pack_calls = []
        with patch("cli.dev._pack_helpers.pack_tarball", side_effect=lambda *a, **k: pack_calls.append(1)):
            with redirect_stdout(io.StringIO()):
                rc = cmd_dev(self._make_args(Path("/nonexistent/gaia-dev-test-path")))
        self.assertEqual(rc, 1)
        self.assertEqual(pack_calls, [])

    def test_default_pack_dest_persists_and_does_not_pollute_workspace(self):
        # Prior to the fix, the default pack destination was a
        # tempfile.TemporaryDirectory() that cmd_dev deleted after a
        # successful run. Now the default is the stable, persistent
        # `default_pack_dest(workspace)` path, which is never deleted --
        # this test asserts both halves: the tarball survives (regression
        # guard) AND it never lands inside the target workspace itself.
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            captured = {}

            # pack_tarball creates dest_dir itself (mirroring the real
            # implementation's `dest.mkdir(parents=True, exist_ok=True)`)
            # since default_pack_dest's directory does not exist yet.
            def fake_pack(source_root, dest_dir=None, **kwargs):
                Path(dest_dir).mkdir(parents=True, exist_ok=True)
                tb = Path(dest_dir) / "pkg.tgz"
                tb.write_bytes(b"x")
                captured["dest_dir"] = Path(dest_dir)
                captured["tarball"] = tb
                return {
                    "action": "created", "path": str(tb), "details": "ok",
                    "tarball": tb, "name": "@jaguilar87/gaia", "version": "9.9.9",
                }

            with patch("cli.dev._pack_helpers.pack_tarball", side_effect=fake_pack), \
                 patch("cli.dev.install_tarball", return_value={"action": "created", "path": "x", "details": "ok", "package_manager": "npm"}), \
                 patch("cli.dev.wire_workspace_via_installed_gaia", return_value={"action": "created", "path": "x", "details": "ok"}):
                with redirect_stdout(io.StringIO()):
                    rc = cmd_dev(self._make_args(workspace))

            self.assertEqual(rc, 0)
            # Never leaks into the workspace itself.
            self.assertFalse(any(workspace.glob("*.tgz")))
            # Persists -- this is the regression guard: the old default
            # deleted this directory before cmd_dev returned. The packed
            # tarball now survives under its content-addressed name (gaia dev
            # renames it in place before install), so assert the dest dir
            # still holds a tarball rather than the pre-rename filename.
            self.assertTrue(any(captured["dest_dir"].glob("*.tgz")))
            self.assertEqual(captured["dest_dir"], default_pack_dest(workspace))

    def test_second_consecutive_pack_run_succeeds_no_enoent(self):
        # Regression test for the incident: a fresh tempfile.TemporaryDirectory()
        # per run meant the tarball backing the workspace's `file:` dependency
        # was deleted before the run even returned, so a *second* `gaia dev`
        # (or any pnpm install touching the lockfile) failed with ENOENT
        # because the previous pack destination no longer existed. With a
        # stable, persistent default_pack_dest, two consecutive runs against
        # the same workspace must both succeed and resolve to the same dest.
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            captured_dests = []

            def fake_pack(source_root, dest_dir=None, **kwargs):
                dest = Path(dest_dir)
                dest.mkdir(parents=True, exist_ok=True)
                captured_dests.append(dest)
                tb = dest / "pkg.tgz"
                tb.write_bytes(b"x")
                return {
                    "action": "created", "path": str(tb), "details": "ok",
                    "tarball": tb, "name": "@jaguilar87/gaia", "version": "9.9.9",
                }

            with patch("cli.dev._pack_helpers.pack_tarball", side_effect=fake_pack), \
                 patch("cli.dev.install_tarball", return_value={"action": "created", "path": "x", "details": "ok", "package_manager": "npm"}), \
                 patch("cli.dev.wire_workspace_via_installed_gaia", return_value={"action": "created", "path": "x", "details": "ok"}):
                with redirect_stdout(io.StringIO()):
                    rc1 = cmd_dev(self._make_args(workspace))
                # The destination from run 1 must still be on disk before run
                # 2 starts -- exactly the state a real `pnpm install` (or a
                # second `gaia dev`) depends on.
                self.assertTrue(captured_dests[0].exists())

                with redirect_stdout(io.StringIO()):
                    rc2 = cmd_dev(self._make_args(workspace))

            self.assertEqual(rc1, 0)
            self.assertEqual(rc2, 0)
            self.assertEqual(len(captured_dests), 2)
            self.assertEqual(captured_dests[0], captured_dests[1])

    def test_keep_tarball_preserves_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "ws"
            workspace.mkdir()
            pack_dest = Path(tmp) / "pack-dest"

            def fake_pack(source_root, dest_dir=None, **kwargs):
                tb = Path(dest_dir) / "pkg.tgz"
                Path(dest_dir).mkdir(parents=True, exist_ok=True)
                tb.write_bytes(b"x")
                return {
                    "action": "created", "path": str(tb), "details": "ok",
                    "tarball": tb, "name": "@jaguilar87/gaia", "version": "9.9.9",
                }

            with patch("cli.dev._pack_helpers.pack_tarball", side_effect=fake_pack), \
                 patch("cli.dev.install_tarball", return_value={"action": "created", "path": "x", "details": "ok", "package_manager": "npm"}), \
                 patch("cli.dev.wire_workspace_via_installed_gaia", return_value={"action": "created", "path": "x", "details": "ok"}):
                with redirect_stdout(io.StringIO()):
                    rc = cmd_dev(self._make_args(
                        workspace, keep_tarball=True, pack_dest=str(pack_dest),
                    ))

            self.assertEqual(rc, 0)
            # Content-addressed in place: the pre-rename `pkg.tgz` becomes
            # `pkg+<sha8>.tgz`, so assert a tarball survives in pack_dest.
            self.assertTrue(any(pack_dest.glob("*.tgz")))

    def _mock_pack_steps(self, tarball: Path):
        """Context-manager stack of the pack/install/wire mocks shared below."""
        return (
            patch("cli.dev._pack_helpers.pack_tarball", side_effect=lambda *a, **k: {
                "action": "created", "path": str(tarball), "details": "ok",
                "tarball": tarball, "name": "@jaguilar87/gaia", "version": "9.9.9",
            }),
            patch("cli.dev.install_tarball", side_effect=lambda ws, tb, **k: {
                "action": "created", "path": str(ws), "details": "ok", "package_manager": "npm",
            }),
            patch("cli.dev.wire_workspace_via_installed_gaia", side_effect=lambda ws, **k: {
                "action": "created", "path": str(ws), "details": "ok",
            }),
        )

    def test_pack_mode_emits_restart_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            fake_tarball = Path(tmp) / "pkg.tgz"; fake_tarball.write_bytes(b"x")
            buf = io.StringIO()
            p_pack, p_install, p_wire = self._mock_pack_steps(fake_tarball)
            with p_pack, p_install, p_wire:
                with redirect_stdout(buf):
                    rc = cmd_dev(self._make_args(workspace, quiet=False))

            self.assertEqual(rc, 0)
            out = buf.getvalue()
            self.assertIn("Restart your Claude Code session", out)
            self.assertIn("hooks", out)
            self.assertIn("⚠", out)  # the warning glyph

    def test_global_link_reconcile_invoked_by_default(self):
        """By default (no --no-global-link) pack mode reconciles surface 4 by
        `npm link`-ing the SOURCE tree globally. The reconcile function is
        mocked so no real global mutation happens; we assert it was called with
        the source root (_PACKAGE_ROOT), and the shadow check ran after it."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            fake_tarball = Path(tmp) / "pkg.tgz"; fake_tarball.write_bytes(b"x")
            p_pack, p_install, p_wire = self._mock_pack_steps(fake_tarball)
            with p_pack, p_install, p_wire, \
                 patch("cli.install.reconcile_global_via_npm_link",
                       return_value={"action": "created", "path": "src", "details": "linked"}) as spy_link, \
                 patch("cli.install._warn_launcher_shadowed", return_value=None) as spy_warn:
                with redirect_stdout(io.StringIO()):
                    rc = cmd_dev(self._make_args(workspace, no_global_link=False))

            self.assertEqual(rc, 0)
            spy_link.assert_called_once()
            # First positional arg is the SOURCE tree (the command's origin).
            called_root = Path(spy_link.call_args[0][0])
            self.assertEqual(called_root, dev_mod._PACKAGE_ROOT)
            spy_warn.assert_called_once()

    def test_no_global_link_skips_reconcile(self):
        """--no-global-link leaves the global install untouched: neither the
        reconcile nor the shadow check runs."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            fake_tarball = Path(tmp) / "pkg.tgz"; fake_tarball.write_bytes(b"x")
            p_pack, p_install, p_wire = self._mock_pack_steps(fake_tarball)
            with p_pack, p_install, p_wire, \
                 patch("cli.install.reconcile_global_via_npm_link") as spy_link, \
                 patch("cli.install._warn_launcher_shadowed") as spy_warn:
                with redirect_stdout(io.StringIO()):
                    rc = cmd_dev(self._make_args(workspace, no_global_link=True))

            self.assertEqual(rc, 0)
            spy_link.assert_not_called()
            spy_warn.assert_not_called()


class TestCmdDevOrchestrationLinkMode(unittest.TestCase):
    def _make_args(self, workspace, **overrides) -> argparse.Namespace:
        ns = argparse.Namespace()
        ns.workspace = str(workspace)
        ns.mode = "link"
        ns.quiet = overrides.get("quiet", True)
        ns.verbose = overrides.get("verbose", False)
        ns.keep_tarball = False
        ns.pack_dest = None
        return ns

    def test_link_mode_calls_cmd_install_with_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            captured = {}

            def fake_cmd_install(ns):
                captured["workspace"] = ns.workspace
                captured["skip_workspace"] = ns.skip_workspace
                return 0

            with patch("cli.dev.link_source_into_workspace",
                       return_value={"action": "created", "path": "x", "details": "ok"}), \
                 patch("cli.dev.install_mod.cmd_install", side_effect=fake_cmd_install):
                with redirect_stdout(io.StringIO()):
                    rc = cmd_dev(self._make_args(workspace))

            self.assertEqual(rc, 0)
            self.assertEqual(captured["workspace"], str(workspace))
            self.assertFalse(captured["skip_workspace"])

    def test_link_mode_never_packs_or_installs_tarball(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            pack_calls = []
            install_calls = []

            with patch("cli.dev.link_source_into_workspace",
                       return_value={"action": "created", "path": "x", "details": "ok"}), \
                 patch("cli.dev.install_mod.cmd_install", return_value=0), \
                 patch("cli.dev._pack_helpers.pack_tarball", side_effect=lambda *a, **k: pack_calls.append(1)), \
                 patch("cli.dev.install_tarball", side_effect=lambda *a, **k: install_calls.append(1)):
                with redirect_stdout(io.StringIO()):
                    cmd_dev(self._make_args(workspace))

            self.assertEqual(pack_calls, [])
            self.assertEqual(install_calls, [])

    def test_link_mode_short_circuits_on_link_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            install_calls = []

            with patch("cli.dev.link_source_into_workspace",
                       return_value={"action": "error", "path": "x", "details": "boom"}), \
                 patch("cli.dev.install_mod.cmd_install", side_effect=lambda *a, **k: install_calls.append(1)):
                with redirect_stdout(io.StringIO()):
                    rc = cmd_dev(self._make_args(workspace))

            self.assertEqual(rc, 1)
            self.assertEqual(install_calls, [])

    def test_link_mode_emits_restart_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            buf = io.StringIO()
            with patch("cli.dev.link_source_into_workspace",
                       return_value={"action": "created", "path": "x", "details": "ok"}), \
                 patch("cli.dev.install_mod.cmd_install", return_value=0):
                with redirect_stdout(buf):
                    rc = cmd_dev(self._make_args(workspace, quiet=False))

            self.assertEqual(rc, 0)
            out = buf.getvalue()
            self.assertIn("Restart your Claude Code session", out)
            self.assertIn("⚠", out)

    def test_link_mode_no_warning_when_install_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            buf = io.StringIO()
            with patch("cli.dev.link_source_into_workspace",
                       return_value={"action": "created", "path": "x", "details": "ok"}), \
                 patch("cli.dev.install_mod.cmd_install", return_value=1):
                with redirect_stdout(buf):
                    rc = cmd_dev(self._make_args(workspace, quiet=False))

            self.assertEqual(rc, 1)
            self.assertNotIn("Restart your Claude Code session", buf.getvalue())


# ---------------------------------------------------------------------------
# Real end-to-end: pack -> install -> wire against an isolated tmp workspace.
#
# GAIA_DATA_DIR / GAIA_DB are pinned to a tmp path for the entire test so the
# DB bootstrap triggered by the freshly-installed copy's own `gaia install`
# never touches ~/.gaia. The tmp workspace is unrelated to the repo's own
# .claude/ -- nothing here can leak into either.
# ---------------------------------------------------------------------------

@unittest.skipUnless(_npm_available(), "npm not available in this environment")
class TestDevPackModeRealEndToEnd(unittest.TestCase):
    def test_pack_install_wire_produces_healthy_workspace(self):
        with tempfile.TemporaryDirectory(prefix="gaia-dev-e2e-") as tmp:
            tmp_path = Path(tmp)
            workspace = tmp_path / "consumer-ws"
            workspace.mkdir()
            data_dir = tmp_path / ".gaia-data"
            data_dir.mkdir()

            env_patch = {
                "GAIA_DATA_DIR": str(data_dir),
                "GAIA_DB": str(data_dir / "gaia.db"),
                "INIT_CWD": str(workspace),
            }

            args = argparse.Namespace(
                workspace=str(workspace),
                mode="pack",
                quiet=True,
                verbose=False,
                keep_tarball=False,
                pack_dest=None,
                # Opt out of the real global `npm link` -- this e2e asserts the
                # workspace wiring, not the machine's global npm store.
                no_global_link=True,
            )

            with patch.dict(os.environ, env_patch):
                with redirect_stdout(io.StringIO()) as out:
                    rc = cmd_dev(args)

            self.assertEqual(rc, 0, out.getvalue())

            # The workspace must now be a healthy, wired Gaia install.
            claude_dir = workspace / ".claude"
            self.assertTrue(claude_dir.is_dir())
            for name in ("agents", "hooks", "config", "skills", "tools"):
                link = claude_dir / name
                self.assertTrue(link.is_symlink(), f".claude/{name} is not a symlink")
                # Must resolve into the INSTALLED tarball copy, not this dev
                # source tree -- the safeguard the module docstring documents.
                resolved = link.resolve()
                installed_root = (
                    workspace / "node_modules" / "@jaguilar87" / "gaia"
                ).resolve()
                self.assertTrue(
                    str(resolved).startswith(str(installed_root)),
                    f".claude/{name} -> {resolved} does not resolve under {installed_root}",
                )

            registry = json.loads((claude_dir / "plugin-registry.json").read_text())
            self.assertEqual(registry["installed"][0]["name"], "gaia")

            # DB must exist ONLY at the isolated tmp path.
            self.assertTrue((data_dir / "gaia.db").exists())


# ---------------------------------------------------------------------------
# content_address_tarball -- content-hashed filename for pnpm store freshness
# ---------------------------------------------------------------------------

class TestContentAddressTarball(unittest.TestCase):
    def test_renames_with_content_hash_suffix(self):
        with tempfile.TemporaryDirectory() as tmp:
            tb = Path(tmp) / "jaguilar87-gaia-5.1.1.tgz"
            tb.write_bytes(b"payload-A")
            new_path = content_address_tarball(tb)
            # Original name is gone; new name carries +<sha8> before .tgz.
            self.assertFalse(tb.exists())
            self.assertTrue(new_path.exists())
            self.assertTrue(new_path.name.startswith("jaguilar87-gaia-5.1.1+"))
            self.assertTrue(new_path.name.endswith(".tgz"))
            suffix = new_path.name[len("jaguilar87-gaia-5.1.1+"):-len(".tgz")]
            self.assertEqual(len(suffix), 8)
            int(suffix, 16)  # must be hex

    def test_same_content_yields_same_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            t1 = Path(tmp) / "jaguilar87-gaia-5.1.1.tgz"
            t1.write_bytes(b"identical-bytes")
            n1 = content_address_tarball(t1)
            t2 = Path(tmp) / "again" / "jaguilar87-gaia-5.1.1.tgz"
            t2.parent.mkdir()
            t2.write_bytes(b"identical-bytes")
            n2 = content_address_tarball(t2)
            self.assertEqual(n1.name, n2.name)

    def test_changed_content_yields_different_name(self):
        # The core cache-bust property: a same-version repack with CHANGED
        # content must produce a different filename (-> new pnpm store key).
        with tempfile.TemporaryDirectory() as tmp:
            a = Path(tmp) / "a" / "jaguilar87-gaia-5.1.1.tgz"
            b = Path(tmp) / "b" / "jaguilar87-gaia-5.1.1.tgz"
            a.parent.mkdir()
            b.parent.mkdir()
            a.write_bytes(b"content-one")
            b.write_bytes(b"content-two")
            na = content_address_tarball(a)
            nb = content_address_tarball(b)
            self.assertNotEqual(na.name, nb.name)

    def test_no_double_suffix_when_rehashed(self):
        # Feeding an already-hashed name back in re-hashes the base version,
        # never stacks a second +<sha8>.
        with tempfile.TemporaryDirectory() as tmp:
            tb = Path(tmp) / "jaguilar87-gaia-5.1.1+deadbeef.tgz"
            tb.write_bytes(b"x")
            new_path = content_address_tarball(tb)
            self.assertEqual(new_path.name.count("+"), 1)
            self.assertTrue(new_path.name.startswith("jaguilar87-gaia-5.1.1+"))


# ---------------------------------------------------------------------------
# prune_sibling_tarballs -- reclaim stale content-addressed siblings
# ---------------------------------------------------------------------------

class TestPruneSiblingTarballs(unittest.TestCase):
    def test_removes_siblings_except_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            keep = d / "jaguilar87-gaia-5.1.1+aaaaaaaa.tgz"
            old1 = d / "jaguilar87-gaia-5.1.1+bbbbbbbb.tgz"
            old2 = d / "jaguilar87-gaia-5.1.0.tgz"  # legacy un-suffixed
            for p in (keep, old1, old2):
                p.write_bytes(b"x")

            removed = prune_sibling_tarballs(keep)

            self.assertTrue(keep.exists())
            self.assertFalse(old1.exists())
            self.assertFalse(old2.exists())
            self.assertEqual(set(removed), {old1.name, old2.name})

    def test_noop_when_only_kept_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            keep = Path(tmp) / "jaguilar87-gaia-5.1.1+aaaaaaaa.tgz"
            keep.write_bytes(b"x")
            removed = prune_sibling_tarballs(keep)
            self.assertEqual(removed, [])
            self.assertTrue(keep.exists())


# ---------------------------------------------------------------------------
# rewrite_workspace_dep_spec -- keep package.json in lockstep with install
# ---------------------------------------------------------------------------

class TestRewriteWorkspaceDepSpec(unittest.TestCase):
    def test_rewrites_stale_spec(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "ws"
            ws.mkdir()
            (ws / "package.json").write_text(json.dumps({
                "name": "consumer",
                "dependencies": {"@jaguilar87/gaia": "file:gaia/jaguilar87-gaia-5.1.0.tgz"},
            }) + "\n")
            tarball = Path(tmp) / "cache" / "jaguilar87-gaia-5.1.1+abcd1234.tgz"
            tarball.parent.mkdir()
            tarball.write_bytes(b"x")

            res = rewrite_workspace_dep_spec(ws, tarball)

            self.assertEqual(res["action"], "updated")
            data = json.loads((ws / "package.json").read_text())
            spec = data["dependencies"]["@jaguilar87/gaia"]
            self.assertTrue(spec.startswith("file:"))
            self.assertIn("jaguilar87-gaia-5.1.1+abcd1234.tgz", spec)

    def test_noop_when_spec_already_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "ws"
            ws.mkdir()
            tarball = Path(tmp) / "jaguilar87-gaia-5.1.1+abcd1234.tgz"
            tarball.write_bytes(b"x")
            rel = os.path.relpath(tarball, ws)
            (ws / "package.json").write_text(json.dumps({
                "dependencies": {"@jaguilar87/gaia": f"file:{rel}"},
            }) + "\n")

            res = rewrite_workspace_dep_spec(ws, tarball)
            self.assertEqual(res["action"], "noop")

    def test_skipped_when_no_package_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "ws"
            ws.mkdir()
            tarball = Path(tmp) / "x.tgz"
            tarball.write_bytes(b"x")
            res = rewrite_workspace_dep_spec(ws, tarball)
            self.assertEqual(res["action"], "skipped")


# ---------------------------------------------------------------------------
# The dev-pack workspace marker was REMOVED alongside `gaia release sync-local`
# (its only reader). Guard against reintroduction.
# ---------------------------------------------------------------------------

class TestWorkspaceMarkerRemoved(unittest.TestCase):
    def test_marker_symbols_are_gone(self):
        self.assertFalse(hasattr(dev_mod, "write_workspace_marker"))
        self.assertFalse(hasattr(dev_mod, "read_workspace_marker"))
        self.assertFalse(hasattr(dev_mod, "_WORKSPACE_MARKER"))


if __name__ == "__main__":
    unittest.main()
