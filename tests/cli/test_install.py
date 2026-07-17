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

Scanning is decoupled from install: cmd_install never triggers a scan (the
former Step 7 / _maybe_run_fresh_scan path is removed).
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
    _LAUNCHER_TEMPLATE,
    _render_launcher,
    _render_cmd_launcher,
    _render_ps1_launcher,
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
        with patch("cli.install._run_bootstrap", return_value={"rc": 0, "detail": ""}) as mock_bs:
            with redirect_stdout(io.StringIO()):
                rc = cmd_install(self._make_args(quiet=True))
        self.assertEqual(rc, 0)
        mock_bs.assert_called_once()

    def test_postinstall_swallows_failure(self):
        """Postinstall mode never returns non-zero -- npm install must not abort."""
        with patch("cli.install._run_bootstrap", return_value={"rc": 1, "detail": "bootstrap rc=1: simulated"}):
            with redirect_stdout(io.StringIO()):
                rc = cmd_install(self._make_args(postinstall=True, quiet=True))
        self.assertEqual(rc, 0)

    def test_manual_mode_propagates_failure(self):
        with patch("cli.install._run_bootstrap", return_value={"rc": 1, "detail": "bootstrap rc=1: simulated"}):
            with redirect_stdout(io.StringIO()):
                rc = cmd_install(self._make_args(quiet=True))
        self.assertEqual(rc, 1)

    def test_db_path_forwarded(self):
        captured = {}

        def fake_bootstrap(db_path, verbose, quiet):
            captured["db_path"] = db_path
            return {"rc": 0, "detail": ""}

        with patch("cli.install._run_bootstrap", side_effect=fake_bootstrap):
            with redirect_stdout(io.StringIO()):
                cmd_install(self._make_args(quiet=True, db_path="/tmp/x.db"))
        self.assertEqual(captured["db_path"], "/tmp/x.db")


class TestCmdInstallBootstrapMarker(unittest.TestCase):
    """Bootstrap failures under --postinstall MUST write the install-error marker.

    Before Pass 6 the marker was only written for gaia-scan failures (the last
    step). A bootstrap failure (e.g. sqlite3 parse error in the seed SQL) would
    return 0 silently, leaving `gaia doctor` without any signal of the real
    root cause -- it could only report vague "missing file" symptoms downstream.
    """

    def _make_args(self, workspace, **overrides) -> argparse.Namespace:
        ns = argparse.Namespace()
        ns.postinstall = overrides.get("postinstall", True)
        ns.quiet = overrides.get("quiet", True)
        ns.verbose = overrides.get("verbose", False)
        ns.db_path = overrides.get("db_path", None)
        ns.workspace = str(workspace) if workspace else None
        ns.skip_workspace = overrides.get("skip_workspace", False)
        ns.no_path = overrides.get("no_path", True)
        return ns

    def test_postinstall_bootstrap_failure_writes_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "ws"
            workspace.mkdir()
            marker_path = Path(tmp) / "last-install-error.json"

            bootstrap_detail = (
                "bootstrap rc=1: Parse error near line 1: "
                "table projects has no column named identity"
            )

            with patch.object(install_mod, "_INSTALL_ERROR_MARKER", marker_path):
                with patch(
                    "cli.install._run_bootstrap",
                    return_value={"rc": 1, "detail": bootstrap_detail},
                ):
                    with redirect_stdout(io.StringIO()):
                        rc = cmd_install(self._make_args(workspace, postinstall=True))

            # Postinstall must NOT propagate failure to npm.
            self.assertEqual(rc, 0)
            # The marker MUST exist with the bootstrap detail.
            self.assertTrue(
                marker_path.exists(),
                "bootstrap failure under --postinstall must write the install-error marker",
            )
            payload = json.loads(marker_path.read_text())
            self.assertEqual(payload["step"], "bootstrap")
            self.assertIn("Parse error", payload["detail"])
            self.assertEqual(payload["workspace"], str(workspace))

    def test_manual_bootstrap_failure_does_not_write_marker(self):
        """Interactive `gaia install` (no --postinstall) propagates rc -- no marker needed."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "ws"
            workspace.mkdir()
            marker_path = Path(tmp) / "last-install-error.json"

            with patch.object(install_mod, "_INSTALL_ERROR_MARKER", marker_path):
                with patch(
                    "cli.install._run_bootstrap",
                    return_value={"rc": 1, "detail": "bootstrap rc=1: synthetic"},
                ):
                    with redirect_stdout(io.StringIO()):
                        rc = cmd_install(self._make_args(workspace, postinstall=False))

            # Manual mode propagates failure -- the user sees the rc directly.
            self.assertEqual(rc, 1)
            self.assertFalse(
                marker_path.exists(),
                "manual install must NOT write the marker (user gets rc + stderr directly)",
            )


class TestBootstrapScriptIntegration(unittest.TestCase):
    """Run the real bootstrap_database.sh against a tmp sqlite DB.

    This is the test that would have caught the rc.4 regression: the seed SQL
    in Section 4 referenced `projects.identity` (a column dropped in the
    workspaces/projects rename, commit be9698f). Without an integration test
    that actually executes the bash script against the schema, drift between
    bootstrap seed and schema goes undetected until npm install.
    """

    _BOOTSTRAP_SH = (
        Path(__file__).resolve().parents[2] / "scripts" / "bootstrap_database.sh"
    )
    _SCHEMA_SQL = (
        Path(__file__).resolve().parents[2] / "gaia" / "store" / "schema.sql"
    )

    def setUp(self):
        if not self._BOOTSTRAP_SH.is_file():
            self.skipTest(f"bootstrap script not found at {self._BOOTSTRAP_SH}")
        if not self._SCHEMA_SQL.is_file():
            self.skipTest(f"schema.sql not found at {self._SCHEMA_SQL}")

    def _run_bootstrap_against_tmp_db(self, workspace: Path) -> subprocess.CompletedProcess:
        """Invoke bootstrap_database.sh with GAIA_DB pointed at a tmp file."""
        tmp_db = workspace / "tmp_gaia.db"
        env = os.environ.copy()
        env["GAIA_DB"] = str(tmp_db)
        env["WORKSPACE"] = str(workspace)
        return subprocess.run(
            ["bash", str(self._BOOTSTRAP_SH)],
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )

    def test_bootstrap_runs_cleanly_on_fresh_db(self):
        """A fresh bootstrap must exit 0 with no sqlite parse errors."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            res = self._run_bootstrap_against_tmp_db(workspace)
            self.assertEqual(
                res.returncode, 0,
                f"bootstrap exited rc={res.returncode}\n"
                f"stdout:\n{res.stdout}\nstderr:\n{res.stderr}",
            )
            # No sqlite3 "no such" / parse-error lines should appear.
            combined = (res.stdout + res.stderr).lower()
            self.assertNotIn(
                "parse error", combined,
                f"bootstrap produced a sqlite parse error:\n{res.stdout}\n{res.stderr}",
            )
            self.assertNotIn(
                "no column named", combined,
                f"bootstrap referenced a column that does not exist in schema:\n"
                f"{res.stdout}\n{res.stderr}",
            )

    def test_bootstrap_seeds_workspaces_table(self):
        """Section 4 must insert into `workspaces`, not the obsolete `projects.identity`."""
        import sqlite3
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            res = self._run_bootstrap_against_tmp_db(workspace)
            self.assertEqual(res.returncode, 0, res.stderr)

            con = sqlite3.connect(str(workspace / "tmp_gaia.db"))
            try:
                rows = con.execute(
                    "SELECT name, identity FROM workspaces"
                ).fetchall()
            finally:
                con.close()

            self.assertGreaterEqual(
                len(rows), 1,
                "bootstrap must seed at least one row in workspaces (the current workspace)",
            )
            # identity == name in the bootstrap fallback path.
            name, identity = rows[0]
            self.assertEqual(name, identity)

    def test_bootstrap_is_idempotent(self):
        """Running bootstrap twice on the same DB must not fail or duplicate rows."""
        import sqlite3
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            res1 = self._run_bootstrap_against_tmp_db(workspace)
            self.assertEqual(res1.returncode, 0, res1.stderr)
            res2 = self._run_bootstrap_against_tmp_db(workspace)
            self.assertEqual(res2.returncode, 0, res2.stderr)

            con = sqlite3.connect(str(workspace / "tmp_gaia.db"))
            try:
                ws_count = con.execute("SELECT COUNT(*) FROM workspaces").fetchone()[0]
                perm_count = con.execute(
                    "SELECT COUNT(*) FROM agent_permissions"
                ).fetchone()[0]
            finally:
                con.close()

            self.assertEqual(ws_count, 1, "second run must not duplicate workspaces row")
            self.assertEqual(
                perm_count, 13,
                "agent_permissions count must remain 13 after second run",
            )

    def test_bootstrap_passes_with_legacy_gaia_operator_row(self):
        """Regression: a pre-existing DB carrying the legacy 'gaia-operator' row
        from older Gaia versions must NOT block bootstrap.

        Pass 6 fixed the seed SQL so bootstrap reaches Section 6. Pass 7 fixed
        the strict-equality check in Section 6 that was incompatible with the
        idempotent INSERT OR IGNORE semantics. This test simulates the exact
        condition observed in qxo (~/.gaia/gaia.db had both 'gaia-operator'
        legacy + 'gaia-system' current), proving bootstrap now exits 0 and
        cleans up the legacy row.
        """
        import sqlite3
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            tmp_db = workspace / "tmp_gaia.db"

            # Pre-seed the DB with the schema + a legacy gaia-operator row,
            # mimicking an upgraded install where the old agent name persists.
            schema_path = self._SCHEMA_SQL
            con = sqlite3.connect(str(tmp_db))
            try:
                con.executescript(schema_path.read_text())
                con.execute(
                    "INSERT OR IGNORE INTO agent_permissions "
                    "(table_name, agent_name, allow_write) "
                    "VALUES ('clusters', 'gaia-operator', 1)"
                )
                con.commit()
            finally:
                con.close()

            # Sanity: the legacy row is present before bootstrap.
            con = sqlite3.connect(str(tmp_db))
            try:
                pre_legacy = con.execute(
                    "SELECT COUNT(*) FROM agent_permissions "
                    "WHERE agent_name = 'gaia-operator'"
                ).fetchone()[0]
            finally:
                con.close()
            self.assertEqual(
                pre_legacy, 1,
                "test setup did not seed the legacy gaia-operator row",
            )

            res = self._run_bootstrap_against_tmp_db(workspace)
            self.assertEqual(
                res.returncode, 0,
                f"bootstrap MUST tolerate a legacy gaia-operator row "
                f"(Pass 7 fix). Got rc={res.returncode}\n"
                f"stdout:\n{res.stdout}\nstderr:\n{res.stderr}",
            )

            # Section 3a cleanup: legacy row should be gone after bootstrap.
            con = sqlite3.connect(str(tmp_db))
            try:
                post_legacy = con.execute(
                    "SELECT COUNT(*) FROM agent_permissions "
                    "WHERE agent_name = 'gaia-operator'"
                ).fetchone()[0]
                distinct_agents = con.execute(
                    "SELECT COUNT(DISTINCT agent_name) FROM agent_permissions"
                ).fetchone()[0]
            finally:
                con.close()

            self.assertEqual(
                post_legacy, 0,
                "Section 3a must DELETE legacy gaia-operator rows -- the "
                "DELETE is the migration that fixes the distinct-agents drift",
            )
            self.assertGreaterEqual(
                distinct_agents, 5,
                "After cleanup, distinct agents must be >= 5 (the canonical set)",
            )

            # Section 6 Check 2 should report '>=' wording, not strict '=='.
            self.assertIn(
                "distinct agents >= 5", res.stdout,
                "Check 2 must use the lenient '>=' formulation (Pass 7 fix); "
                "strict equality regressed when DBs survived a Gaia upgrade",
            )


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

            with patch("cli.install._run_bootstrap", return_value={"rc": 0, "detail": ""}):
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

    def test_install_never_triggers_scan(self):
        """Scanning is decoupled from install. cmd_install must complete
        without ever invoking a scan, and the scan-trigger functions must no
        longer exist on the install module."""
        # The install-coupled scan helpers are retired.
        self.assertFalse(hasattr(install_mod, "_maybe_run_fresh_scan"))
        self.assertFalse(hasattr(install_mod, "_workspace_already_scanned"))
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / ".claude").mkdir()
            with patch("cli.install._run_bootstrap", return_value={"rc": 0, "detail": ""}):
                with redirect_stdout(io.StringIO()):
                    rc = cmd_install(self._make_args(workspace, postinstall=True))
            self.assertEqual(rc, 0)

    def test_skip_workspace_only_runs_bootstrap(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / ".claude").mkdir()
            calls = []

            def tracker(*args, **kwargs):
                calls.append("called")
                return {"action": "noop", "path": "x", "details": ""}

            with patch("cli.install._run_bootstrap", return_value={"rc": 0, "detail": ""}):
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
            with patch("cli.install._run_bootstrap", return_value={"rc": 0, "detail": ""}):
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

            with patch("cli.install._run_bootstrap", return_value={"rc": 0, "detail": ""}):
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
                patch("cli.install._run_bootstrap", return_value={"rc": 0, "detail": ""}),
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
            with patch("cli.install._run_bootstrap", return_value={"rc": 1, "detail": "bootstrap rc=1: simulated"}):
                with redirect_stdout(io.StringIO()):
                    rc = cmd_install(self._make_args(workspace))

            self.assertEqual(rc, 1)
            # .claude/ must not have been created when bootstrap failed.
            self.assertFalse((workspace / ".claude").exists())


class TestInstallPathLauncher(unittest.TestCase):
    """Unit tests for _install_path_launcher.

    The launcher is workspace-bound: the workspace path is hardcoded at
    install time and baked into the script verbatim. There is no discovery
    logic, no env-var override, no fallback chain. Re-running ``gaia install``
    from a different workspace is the only way to retarget the shim.

    These tests cover file-level behavior (write, idempotency, migration
    from legacy symlink, workspace embedding, and the absence of legacy
    fallback markers in the rendered output).
    """

    def test_creates_launcher_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            link = tmp_p / "bin" / "gaia"
            workspace = tmp_p / "ws"
            workspace.mkdir()

            res = _install_path_launcher(link_path=link, workspace=workspace)

            self.assertEqual(res["action"], "created")
            self.assertTrue(link.is_file())
            self.assertFalse(link.is_symlink())
            content = link.read_text()
            self.assertEqual(content, _render_launcher(workspace.resolve()))
            # 0755 -- executable bit set
            self.assertTrue(link.stat().st_mode & 0o100)

    def test_creates_parent_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            link = Path(tmp) / "deep" / "nested" / "bin" / "gaia"
            workspace = Path(tmp) / "ws"
            workspace.mkdir()
            res = _install_path_launcher(link_path=link, workspace=workspace)
            self.assertEqual(res["action"], "created")
            self.assertTrue(link.parent.is_dir())

    def test_idempotent_when_content_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            link = Path(tmp) / "bin" / "gaia"
            workspace = Path(tmp) / "ws"
            workspace.mkdir()
            _install_path_launcher(link_path=link, workspace=workspace)
            res2 = _install_path_launcher(link_path=link, workspace=workspace)
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
            workspace = tmp_p / "ws"
            workspace.mkdir()

            self.assertTrue(link.is_symlink())  # precondition

            res = _install_path_launcher(link_path=link, workspace=workspace)

            self.assertEqual(res["action"], "migrated")
            self.assertTrue(link.is_file())
            self.assertFalse(link.is_symlink())
            self.assertEqual(link.read_text(), _render_launcher(workspace.resolve()))

    def test_skips_when_regular_file_with_different_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            link = Path(tmp) / "bin" / "gaia"
            link.parent.mkdir(parents=True)
            link.write_text("custom user content")
            workspace = Path(tmp) / "ws"
            workspace.mkdir()

            res = _install_path_launcher(link_path=link, workspace=workspace)

            self.assertEqual(res["action"], "skipped")
            self.assertEqual(link.read_text(), "custom user content")

    def test_overwrite_replaces_drifted_launcher(self):
        with tempfile.TemporaryDirectory() as tmp:
            link = Path(tmp) / "bin" / "gaia"
            link.parent.mkdir(parents=True)
            link.write_text("old launcher version")
            workspace = Path(tmp) / "ws"
            workspace.mkdir()

            res = _install_path_launcher(
                link_path=link, overwrite=True, workspace=workspace
            )

            self.assertEqual(res["action"], "replaced")
            self.assertEqual(link.read_text(), _render_launcher(workspace.resolve()))
            self.assertTrue(link.stat().st_mode & 0o100)

    def test_skips_when_directory_in_the_way(self):
        with tempfile.TemporaryDirectory() as tmp:
            link = Path(tmp) / "bin" / "gaia"
            link.parent.mkdir(parents=True)
            link.mkdir()
            workspace = Path(tmp) / "ws"
            workspace.mkdir()
            res = _install_path_launcher(link_path=link, workspace=workspace)
            self.assertEqual(res["action"], "skipped")

    def test_legacy_alias_still_callable(self):
        """_create_path_symlink must remain importable for back-compat."""
        with tempfile.TemporaryDirectory() as tmp:
            link = Path(tmp) / "bin" / "gaia"
            workspace = Path(tmp) / "ws"
            workspace.mkdir()
            res = _create_path_symlink(link_path=link, workspace=workspace)
            self.assertEqual(res["action"], "created")
            self.assertTrue(link.is_file())

    def test_rendered_shim_embeds_workspace_path(self):
        """The rendered shim contains the exact workspace path passed in."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "ws"
            workspace.mkdir()
            link = Path(tmp) / "bin" / "gaia"

            _install_path_launcher(link_path=link, workspace=workspace)
            content = link.read_text()

            expected_path = f'"{workspace.resolve()}/node_modules/@jaguilar87/gaia/bin/gaia"'
            self.assertIn(expected_path, content)
            self.assertIn('exec python3', content)
            self.assertIn('"$@"', content)

    def test_rendered_shim_has_no_discovery_logic(self):
        """The shim has no fallback chain -- no walk-up, no env vars, no global."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "ws"
            workspace.mkdir()
            link = Path(tmp) / "bin" / "gaia"

            _install_path_launcher(link_path=link, workspace=workspace)
            content = link.read_text()

            # Legacy fallback markers must not appear in the new shim.
            self.assertNotIn("GAIA_HOME", content)
            self.assertNotIn("GAIA_WORKSPACE", content)
            self.assertNotIn(".gaia/global", content)
            self.assertNotIn("while", content)  # no walk-up loop
            self.assertNotIn("dirname", content)  # no parent traversal

    def test_rendered_shim_is_three_lines(self):
        """The shim is intentionally minimal -- shebang + comment + exec."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "ws"
            workspace.mkdir()
            link = Path(tmp) / "bin" / "gaia"

            _install_path_launcher(link_path=link, workspace=workspace)
            content = link.read_text()
            non_empty_lines = [ln for ln in content.splitlines() if ln.strip()]

            self.assertEqual(len(non_empty_lines), 4)  # shebang + 2 comments + exec
            self.assertTrue(non_empty_lines[0].startswith("#!"))
            self.assertTrue(non_empty_lines[-1].startswith("exec python3 "))

    def test_workspace_defaults_to_cwd(self):
        """When workspace=None, the shim is rendered against Path.cwd()."""
        with tempfile.TemporaryDirectory() as tmp:
            link = Path(tmp) / "bin" / "gaia"
            cwd_before = Path.cwd()

            _install_path_launcher(link_path=link, workspace=None)
            content = link.read_text()

            self.assertIn(str(cwd_before.resolve()), content)

    def test_different_workspaces_produce_different_shims(self):
        """Re-running install from a new workspace retargets the shim."""
        with tempfile.TemporaryDirectory() as tmp:
            ws_a = Path(tmp) / "ws_a"
            ws_b = Path(tmp) / "ws_b"
            ws_a.mkdir()
            ws_b.mkdir()
            link = Path(tmp) / "bin" / "gaia"

            _install_path_launcher(link_path=link, workspace=ws_a)
            content_a = link.read_text()
            self.assertIn(str(ws_a.resolve()), content_a)

            # Re-render against ws_b with overwrite -- shim now targets ws_b
            res = _install_path_launcher(
                link_path=link, workspace=ws_b, overwrite=True
            )
            self.assertEqual(res["action"], "replaced")
            content_b = link.read_text()
            self.assertIn(str(ws_b.resolve()), content_b)
            self.assertNotIn(str(ws_a.resolve()), content_b)


class TestInstallPathLauncherWindows(unittest.TestCase):
    """Windows branch of _install_path_launcher (Step 6.5 platform guard).

    Exercised on Linux by forcing the platform guard via install_mod.sys.platform
    == "win32". Verifies that the Windows branch (a) writes gaia.cmd + gaia.ps1
    instead of a bash shim, (b) bakes the resolved workspace path, (c) exports
    GAIA_WORKSPACE_PATH, and (d) dispatches to the actual bin/gaia (global- and
    local-install safe -- NOT the workspace-relative node_modules path the POSIX
    shim uses). Real Windows execution is validated by the windows-compat CI.
    """

    def _win(self):
        return patch.object(install_mod.sys, "platform", "win32")

    def test_writes_cmd_and_ps1_not_bash(self):
        with tempfile.TemporaryDirectory() as tmp:
            link = Path(tmp) / "bin" / "gaia"
            workspace = Path(tmp) / "ws"
            workspace.mkdir()
            gaia_bin = Path(tmp) / "pkg" / "bin" / "gaia"

            with self._win():
                res = _install_path_launcher(
                    link_path=link, workspace=workspace, gaia_bin=gaia_bin
                )

            self.assertEqual(res["action"], "created")
            cmd = link.with_suffix(".cmd")
            ps1 = link.with_suffix(".ps1")
            self.assertTrue(cmd.is_file())
            self.assertTrue(ps1.is_file())
            # No extensionless bash shim was written.
            self.assertFalse(link.exists())

    def test_cmd_bakes_workspace_env_and_bin(self):
        with tempfile.TemporaryDirectory() as tmp:
            link = Path(tmp) / "bin" / "gaia"
            workspace = Path(tmp) / "ws"
            workspace.mkdir()
            gaia_bin = Path(tmp) / "pkg" / "bin" / "gaia"

            with self._win():
                _install_path_launcher(
                    link_path=link, workspace=workspace, gaia_bin=gaia_bin
                )

            content = link.with_suffix(".cmd").read_text()
            # (b) resolved workspace baked in
            self.assertIn(str(workspace.resolve()), content)
            # (c) GAIA_WORKSPACE_PATH exported to the resolved workspace
            self.assertIn(
                f'set "GAIA_WORKSPACE_PATH={workspace.resolve()}"', content
            )
            # (d) dispatches to the actual bin/gaia, not node_modules-relative
            self.assertIn(str(gaia_bin), content)
            self.assertNotIn("node_modules/@jaguilar87/gaia", content)

    def test_ps1_bakes_workspace_env_and_bin(self):
        with tempfile.TemporaryDirectory() as tmp:
            link = Path(tmp) / "bin" / "gaia"
            workspace = Path(tmp) / "ws"
            workspace.mkdir()
            gaia_bin = Path(tmp) / "pkg" / "bin" / "gaia"

            with self._win():
                _install_path_launcher(
                    link_path=link, workspace=workspace, gaia_bin=gaia_bin
                )

            content = link.with_suffix(".ps1").read_text()
            self.assertIn(str(workspace.resolve()), content)
            self.assertIn(
                f'$env:GAIA_WORKSPACE_PATH = "{workspace.resolve()}"', content
            )
            self.assertIn(str(gaia_bin), content)
            self.assertIn("$LASTEXITCODE", content)

    def test_default_gaia_bin_is_package_entrypoint(self):
        """When gaia_bin is omitted, the launchers dispatch to the installed
        package's bin/gaia (_gaia_entrypoint), which is valid under `npm i -g`
        where the workspace-relative node_modules path does not exist."""
        with tempfile.TemporaryDirectory() as tmp:
            link = Path(tmp) / "bin" / "gaia"
            workspace = Path(tmp) / "ws"
            workspace.mkdir()

            with self._win():
                _install_path_launcher(link_path=link, workspace=workspace)

            content = link.with_suffix(".cmd").read_text()
            self.assertIn(str(install_mod._gaia_entrypoint()), content)

    def test_idempotent_when_content_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            link = Path(tmp) / "bin" / "gaia"
            workspace = Path(tmp) / "ws"
            workspace.mkdir()
            gaia_bin = Path(tmp) / "pkg" / "bin" / "gaia"

            with self._win():
                _install_path_launcher(
                    link_path=link, workspace=workspace, gaia_bin=gaia_bin
                )
                res2 = _install_path_launcher(
                    link_path=link, workspace=workspace, gaia_bin=gaia_bin
                )

            self.assertEqual(res2["action"], "noop")

    def test_drift_skipped_without_overwrite_replaced_with(self):
        with tempfile.TemporaryDirectory() as tmp:
            link = Path(tmp) / "bin" / "gaia"
            ws_a = Path(tmp) / "ws_a"
            ws_b = Path(tmp) / "ws_b"
            ws_a.mkdir()
            ws_b.mkdir()
            gaia_bin = Path(tmp) / "pkg" / "bin" / "gaia"

            with self._win():
                _install_path_launcher(
                    link_path=link, workspace=ws_a, gaia_bin=gaia_bin
                )
                # Different workspace -> drifted content; no overwrite -> skipped.
                res_skip = _install_path_launcher(
                    link_path=link, workspace=ws_b, gaia_bin=gaia_bin
                )
                self.assertEqual(res_skip["action"], "skipped")
                # overwrite=True -> replaced, now targets ws_b.
                res_repl = _install_path_launcher(
                    link_path=link, workspace=ws_b, gaia_bin=gaia_bin,
                    overwrite=True,
                )

            self.assertEqual(res_repl["action"], "replaced")
            content = link.with_suffix(".cmd").read_text()
            self.assertIn(str(ws_b.resolve()), content)
            self.assertNotIn(str(ws_a.resolve()), content)

    def test_render_helpers_direct(self):
        """The render helpers bake both the workspace and the bin path."""
        ws = Path("/home/user/app")
        gaia_bin = Path("/opt/gaia/bin/gaia")
        cmd = _render_cmd_launcher(ws, gaia_bin)
        ps1 = _render_ps1_launcher(ws, gaia_bin)
        for content in (cmd, ps1):
            self.assertIn("/home/user/app", content)
            self.assertIn("/opt/gaia/bin/gaia", content)
            self.assertIn("GAIA_WORKSPACE_PATH", content)


class TestLauncherShellBehavior(unittest.TestCase):
    """Run the launcher script under bash and verify hardcoded-path semantics.

    The launcher is intentionally trivial -- a single ``exec`` to a hardcoded
    absolute path. There are no fallbacks to test; the only behaviors that
    matter are (1) it execs the embedded path, (2) it propagates the target's
    exit code, and (3) it does NOT walk up from cwd. The third assertion is
    the regression guard for the rc.5 cwd-walk bug.

    Note: the harness runs on `/tmp` mounted with `noexec`, which makes
    direct exec via ``[ -x file ]`` unreliable for files there. Fixtures
    are staged under `$HOME` (exec-mounted on this harness) so the embedded
    `exec python3` call resolves correctly.
    """

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="gaia-launcher-test-", dir=str(Path.home()))

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _make_fake_gaia(self, dst: Path, label: str, exit_code: int = 0) -> None:
        """Create a python script at *dst* that prints `label` and exits."""
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(
            "import sys\n"
            f"print('{label}')\n"
            f"sys.exit({exit_code})\n"
        )
        dst.chmod(0o755)

    def _write_launcher(self, link: Path, workspace: Path) -> None:
        link.parent.mkdir(parents=True, exist_ok=True)
        link.write_text(_render_launcher(workspace))
        link.chmod(0o755)

    def _run_launcher(self, launcher: Path, *, cwd: Path, args=None):
        cmd = ["bash", str(launcher)]
        if args:
            cmd.extend(args)
        return subprocess.run(
            cmd,
            cwd=str(cwd),
            env={**os.environ},
            capture_output=True,
            text=True,
            check=False,
        )

    def test_execs_hardcoded_path(self):
        """The shim execs the workspace path baked in at render time."""
        tmp_p = Path(self._tmp)
        workspace = tmp_p / "ws"
        target = workspace / "node_modules" / "@jaguilar87" / "gaia" / "bin" / "gaia"
        self._make_fake_gaia(target, "HARDCODED")

        launcher = tmp_p / "bin" / "gaia"
        self._write_launcher(launcher, workspace)

        # Run from a deep unrelated cwd -- the hardcoded path must still resolve.
        unrelated_cwd = tmp_p / "unrelated"
        unrelated_cwd.mkdir()
        result = self._run_launcher(launcher, cwd=unrelated_cwd, args=["arg1"])

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("HARDCODED", result.stdout)

    def test_does_not_walk_up_from_cwd(self):
        """Regression guard: the shim must NOT find a cwd-local Gaia install.

        Stage a Gaia install in the cwd's node_modules tree but render the
        shim against a different (empty) workspace. The shim must NOT exec
        the cwd-local install -- it must try the hardcoded path and fail.
        """
        tmp_p = Path(self._tmp)

        # Render shim against an empty workspace (no node_modules tree)
        empty_workspace = tmp_p / "empty_ws"
        empty_workspace.mkdir()
        launcher = tmp_p / "bin" / "gaia"
        self._write_launcher(launcher, empty_workspace)

        # Build a "cwd workspace" with a working Gaia install -- the OLD walk-up
        # shim would have picked this up; the new shim must NOT.
        cwd_workspace = tmp_p / "cwd_ws"
        cwd_local = (
            cwd_workspace / "node_modules" / "@jaguilar87" / "gaia" / "bin" / "gaia"
        )
        self._make_fake_gaia(cwd_local, "CWD_LOCAL")

        result = self._run_launcher(launcher, cwd=cwd_workspace)

        # Shim execs empty_workspace's path which does not exist -- bash exits
        # non-zero (127 or similar) and CWD_LOCAL was never run.
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("CWD_LOCAL", result.stdout)

    def test_propagates_exit_code(self):
        """Launcher must propagate the underlying process's exit code."""
        tmp_p = Path(self._tmp)
        workspace = tmp_p / "ws"
        target = workspace / "node_modules" / "@jaguilar87" / "gaia" / "bin" / "gaia"
        self._make_fake_gaia(target, "EXIT42", exit_code=42)

        launcher = tmp_p / "bin" / "gaia"
        self._write_launcher(launcher, workspace)

        unrelated_cwd = tmp_p / "unrelated"
        unrelated_cwd.mkdir()
        result = self._run_launcher(launcher, cwd=unrelated_cwd)

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
            patches.append(patch("cli.install._run_bootstrap", return_value={"rc": 0, "detail": ""}))
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
            self.assertEqual(link.read_text(), _render_launcher(workspace.resolve()))

    def test_no_path_flag_skips_launcher(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "ws"
            workspace.mkdir()
            (workspace / ".claude").mkdir()

            patches = self._patch_helpers_noop()
            patches.append(patch("cli.install._run_bootstrap", return_value={"rc": 0, "detail": ""}))
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
            patches.append(patch("cli.install._run_bootstrap", return_value={"rc": 0, "detail": ""}))
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
            patches.append(patch("cli.install._run_bootstrap", return_value={"rc": 0, "detail": ""}))
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
            self.assertEqual(link.read_text(), _render_launcher(workspace.resolve()))


class TestInstallErrorMarker(unittest.TestCase):
    """Install-error marker semantics.

    Scanning is decoupled from install, so the marker now tracks bootstrap
    failures only (see cmd_install). A clean install -- postinstall or
    interactive -- clears any stale marker from a prior failed bootstrap.

    Contract:
      - clean install (any mode) -> stale marker cleared
      - the marker helper persists/clears a JSON payload for `gaia doctor`
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

    def test_postinstall_clean_install_clears_stale_marker(self):
        """A clean postinstall run (no scan involved) clears a stale marker
        left by a prior failed bootstrap attempt."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "ws"
            workspace.mkdir()
            (workspace / ".claude").mkdir()
            marker = Path(tmp) / "marker.json"
            # Seed stale marker
            marker.write_text('{"step": "previous failure"}')

            patches = self._patch_helpers_noop()
            patches.append(patch("cli.install._run_bootstrap", return_value={"rc": 0, "detail": ""}))
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
            patches.append(patch("cli.install._run_bootstrap", return_value={"rc": 0, "detail": ""}))
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
