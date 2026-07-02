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
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

_BIN_DIR = Path(__file__).resolve().parents[2] / "bin"
if str(_BIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BIN_DIR))

from cli import dev as dev_mod  # noqa: E402
from cli.dev import (  # noqa: E402
    register,
    cmd_dev,
    detect_package_manager,
    install_tarball,
    wire_workspace_via_installed_gaia,
    link_source_into_workspace,
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
# cmd_dev orchestration (mocked pack/install/wire steps)
# ---------------------------------------------------------------------------

class TestCmdDevOrchestrationPackMode(unittest.TestCase):
    def _make_args(self, workspace, **overrides) -> argparse.Namespace:
        ns = argparse.Namespace()
        ns.workspace = str(workspace)
        ns.mode = overrides.get("mode", "pack")
        ns.quiet = overrides.get("quiet", True)
        ns.verbose = overrides.get("verbose", False)
        ns.keep_tarball = overrides.get("keep_tarball", False)
        ns.pack_dest = overrides.get("pack_dest", None)
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

    def test_default_deletes_tarball_after_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)

            # pack_tarball writes the fake tarball itself so we can assert
            # cmd_dev deleted it afterward.
            def fake_pack(source_root, dest_dir=None, **kwargs):
                tb = Path(dest_dir) / "pkg.tgz"
                tb.write_bytes(b"x")
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
            # Tarball lived in a tmp dir created by cmd_dev -- verified indirectly:
            # a fresh TemporaryDirectory means nothing leaks into the workspace.
            self.assertFalse(any(workspace.glob("*.tgz")))

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
            self.assertTrue((pack_dest / "pkg.tgz").exists())


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


if __name__ == "__main__":
    unittest.main()
