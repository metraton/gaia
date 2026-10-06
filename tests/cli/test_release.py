"""
Tests for bin/cli/release.py -- `gaia release check` (Phase 2, AC-2) and
`gaia release publish` (Phase 3, AC-3) of the
gaia-install-release-cli-workflow brief.

Hygiene: mirrors tests/cli/test_dev.py -- every test that could touch a real
filesystem writes into a `tempfile.TemporaryDirectory()`. The heavy gates
(npm sandbox install, plugin dryrun, npm test) are mocked at the
`subprocess.run` / `_pack_helpers.pack_tarball` boundary so this suite never
spawns a real `npm pack`, `npm install`, `bash validate-sandbox.sh`, or
`claude` process. One real, lightweight invocation is included: gate 1
(`node bin/pre-publish-validate.js --validate-only`) is read-only (no
version bump, no node_modules write) and fast, so it is exercised for real
against the actual source tree -- the load-bearing proof that the gate
wiring (argv, cwd, exit-code interpretation) genuinely works end to end.

The Phase 3 `publish` tests never spawn a real `git`, `gh`, `npm test`, or
`node scripts/release-prepare.mjs` -- every one of those is mocked at the
`subprocess.run` boundary (or the `step_*`/`gate_tests` boundary for the
orchestration tests). Nothing in this suite pushes to a remote, creates a
GitHub Release, or writes to the real repo's git state.
"""

import argparse
import inspect
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from unittest.mock import patch

_BIN_DIR = Path(__file__).resolve().parents[2] / "bin"
if str(_BIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BIN_DIR))

from cli import release as release_mod  # noqa: E402
from cli.release import (  # noqa: E402
    register,
    cmd_release,
    cmd_release_check,
    cmd_release_publish,
    run_release_check,
    run_release_publish,
    resolve_publish_version,
    resolve_source_root,
    _is_source_checkout,
    build_publish_plan,
    step_release_prepare,
    step_git_commit,
    step_git_tag,
    step_git_push,
    step_gh_release_create,
    gate_pre_publish_validate,
    gate_npm_sandbox,
    gate_plugin_dryrun,
    gate_npm_test,
    gate_convergence,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]


from tests.conftest import require_tool  # noqa: E402

require_tool("node")


# ---------------------------------------------------------------------------
# register() / argparse -- "release check" grouped subcommand shape
# ---------------------------------------------------------------------------

class TestRegisterSubcommand(unittest.TestCase):
    def test_register_creates_release_check_parser(self):
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="subcommand")
        register(subparsers)
        args = parser.parse_args(["release", "check"])
        self.assertEqual(args.subcommand, "release")
        self.assertEqual(args.release_cmd, "check")
        self.assertFalse(args.functional)

    def test_functional_flag(self):
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="subcommand")
        register(subparsers)
        args = parser.parse_args(["release", "check", "--functional"])
        self.assertTrue(args.functional)

    def test_quiet_flag(self):
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="subcommand")
        register(subparsers)
        args = parser.parse_args(["release", "check", "--quiet"])
        self.assertTrue(args.quiet)

    def test_check_sets_func_default(self):
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="subcommand")
        register(subparsers)
        args = parser.parse_args(["release", "check"])
        self.assertIs(args.func, cmd_release_check)

    def test_missing_subcommand_is_required(self):
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="subcommand")
        register(subparsers)
        with self.assertRaises(SystemExit):
            parser.parse_args(["release"])


class TestRegisterPublishSubcommand(unittest.TestCase):
    """AC-4: `gaia release publish --help` and `gaia release --help` must
    resolve and list `publish` alongside `check`."""

    def test_register_creates_publish_parser_with_defaults(self):
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="subcommand")
        register(subparsers)
        args = parser.parse_args(["release", "publish"])
        self.assertEqual(args.release_cmd, "publish")
        self.assertEqual(args.version, "patch")
        self.assertFalse(args.dry_run)

    def test_publish_accepts_explicit_semver_positional(self):
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="subcommand")
        register(subparsers)
        args = parser.parse_args(["release", "publish", "5.1.0-rc.1"])
        self.assertEqual(args.version, "5.1.0-rc.1")

    def test_publish_dry_run_flag(self):
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="subcommand")
        register(subparsers)
        args = parser.parse_args(["release", "publish", "--dry-run", "patch"])
        self.assertTrue(args.dry_run)
        self.assertEqual(args.version, "patch")

    def test_publish_sets_func_default(self):
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="subcommand")
        register(subparsers)
        args = parser.parse_args(["release", "publish"])
        self.assertIs(args.func, cmd_release_publish)

    def test_release_group_help_lists_both_subcommands(self):
        parser = argparse.ArgumentParser(prog="gaia")
        subparsers = parser.add_subparsers(dest="subcommand")
        register(subparsers)
        p_release = subparsers.choices["release"]
        buf = io.StringIO()
        with redirect_stdout(buf):
            p_release.print_help()
        text = buf.getvalue()
        self.assertIn("check", text)
        self.assertIn("publish", text)


class TestHelpOutput(unittest.TestCase):
    def test_help_lists_release_subcommand(self):
        parser = argparse.ArgumentParser(prog="gaia")
        subparsers = parser.add_subparsers(dest="subcommand")
        register(subparsers)
        buf = io.StringIO()
        with redirect_stdout(buf):
            parser.print_help()
        self.assertIn("release", buf.getvalue())

    def test_release_check_help_exits_zero_without_side_effects(self):
        parser = argparse.ArgumentParser(prog="gaia")
        subparsers = parser.add_subparsers(dest="subcommand")
        register(subparsers)
        with patch("cli.release.run_release_check") as mock_run:
            with self.assertRaises(SystemExit) as cm:
                parser.parse_args(["release", "check", "--help"])
            self.assertEqual(cm.exception.code, 0)
            mock_run.assert_not_called()

    def test_release_publish_help_exits_zero_without_side_effects(self):
        """AC-3/AC-4 verify: `gaia release publish --help` resolves and spawns
        nothing -- no subprocess, no git/gh call."""
        parser = argparse.ArgumentParser(prog="gaia")
        subparsers = parser.add_subparsers(dest="subcommand")
        register(subparsers)
        with patch("cli.release.run_release_publish") as mock_run, \
             patch("cli.release.subprocess.run") as mock_subproc:
            with self.assertRaises(SystemExit) as cm:
                parser.parse_args(["release", "publish", "--help"])
            self.assertEqual(cm.exception.code, 0)
            mock_run.assert_not_called()
            mock_subproc.assert_not_called()


# ---------------------------------------------------------------------------
# gate_pre_publish_validate -- gate 1 (mocked subprocess + one real run)
# ---------------------------------------------------------------------------

class TestGatePrePublishValidateMocked(unittest.TestCase):
    def test_pass_on_zero_exit(self):
        with patch(
            "cli.release.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, "all good", ""),
        ):
            res = gate_pre_publish_validate(_REPO_ROOT)
        self.assertEqual(res["name"], "pre-publish:validate")
        self.assertEqual(res["status"], "PASS")

    def test_fail_on_nonzero_exit(self):
        with patch(
            "cli.release.subprocess.run",
            return_value=subprocess.CompletedProcess([], 1, "", "drift detected"),
        ):
            res = gate_pre_publish_validate(_REPO_ROOT)
        self.assertEqual(res["status"], "FAIL")
        self.assertIn("drift detected", res["detail"])

    def test_missing_script_fails_without_invoking_subprocess(self):
        calls = []
        with tempfile.TemporaryDirectory() as tmp:
            with patch("cli.release.subprocess.run", side_effect=lambda *a, **k: calls.append(1)):
                res = gate_pre_publish_validate(Path(tmp))
        self.assertEqual(res["status"], "FAIL")
        self.assertIn("not found", res["detail"])
        self.assertEqual(calls, [])

    def test_invokes_node_with_validate_only_flag(self):
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["cwd"] = kwargs.get("cwd")
            return subprocess.CompletedProcess(cmd, 0, "ok", "")

        with patch("cli.release.subprocess.run", side_effect=fake_run):
            gate_pre_publish_validate(_REPO_ROOT)

        self.assertEqual(captured["cmd"][0], "node")
        self.assertIn("pre-publish-validate.js", captured["cmd"][1])
        self.assertIn("--validate-only", captured["cmd"])
        self.assertEqual(captured["cwd"], str(_REPO_ROOT))

    def test_oserror_invoking_node_returns_fail(self):
        with patch("cli.release.subprocess.run", side_effect=OSError("node not found")):
            res = gate_pre_publish_validate(_REPO_ROOT)
        self.assertEqual(res["status"], "FAIL")
        self.assertIn("node not found", res["detail"])


class TestGatePrePublishValidateReal(unittest.TestCase):
    """Real, read-only invocation against the actual source tree.

    `--validate-only` never bumps a version or writes node_modules -- it is
    the lightweight gate the brief asks to exercise for real rather than
    mocked end to end.
    """

    def test_real_validate_only_runs_and_returns_a_verdict(self):
        res = gate_pre_publish_validate(_REPO_ROOT, timeout=60)
        self.assertEqual(res["name"], "pre-publish:validate")
        self.assertIn(res["status"], ("PASS", "FAIL"))
        self.assertIsInstance(res["duration_ms"], int)
        self.assertGreater(len(res["detail"]), 0)


# ---------------------------------------------------------------------------
# gate_npm_sandbox -- gate 2 (mocked pack_tarball + subprocess)
# ---------------------------------------------------------------------------

_CREATED_PACK = {
    "action": "created", "path": "/tmp/pkg.tgz", "details": "ok",
    "tarball": Path("/tmp/pkg.tgz"), "name": "@jaguilar87/gaia", "version": "9.9.9",
}


class TestGateNpmSandbox(unittest.TestCase):
    def test_pack_failure_short_circuits_without_running_script(self):
        calls = []
        with patch("cli.release.subprocess.run", side_effect=lambda *a, **k: calls.append(1)):
            res = gate_npm_sandbox(
                _REPO_ROOT, {"action": "error", "path": "", "details": "npm pack failed"}
            )

        self.assertEqual(res["name"], "gaia:verify-install:local")
        self.assertEqual(res["status"], "FAIL")
        self.assertIn("npm pack failed", res["detail"])
        self.assertEqual(calls, [])

    def test_pass_invokes_validate_sandbox_with_tarball_and_target_sandbox(self):
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["cwd"] = kwargs.get("cwd")
            return subprocess.CompletedProcess(cmd, 0, "RESULT: PASS", "")

        with patch("cli.release.subprocess.run", side_effect=fake_run):
            res = gate_npm_sandbox(_REPO_ROOT, _CREATED_PACK)

        self.assertEqual(res["status"], "PASS")
        self.assertEqual(captured["cmd"][0], "bash")
        self.assertIn("validate-sandbox.sh", captured["cmd"][1])
        self.assertEqual(captured["cmd"][captured["cmd"].index("--tarball") + 1], "/tmp/pkg.tgz")
        self.assertIn("--target", captured["cmd"])
        self.assertIn("sandbox", captured["cmd"])
        self.assertEqual(captured["cwd"], str(_REPO_ROOT))

    def test_nonzero_exit_returns_fail(self):
        with patch(
            "cli.release.subprocess.run",
            return_value=subprocess.CompletedProcess([], 1, "", "RESULT: FAIL"),
        ):
            res = gate_npm_sandbox(_REPO_ROOT, _CREATED_PACK)

        self.assertEqual(res["status"], "FAIL")


# ---------------------------------------------------------------------------
# gate_opencode_surface -- gate 6, on fixture packages built from this repo
# ---------------------------------------------------------------------------

def _opencode_package_fixture(tmp: Path) -> Path:
    """Stage this repo's OpenCode-facing slice as an npm package root."""
    package = tmp / "package"
    for rel in ("opencode", "agents", "skills"):
        shutil.copytree(_REPO_ROOT / rel, package / rel)
    (package / "bin").mkdir()
    shutil.copy2(_REPO_ROOT / "bin" / "gaia", package / "bin" / "gaia")
    return package


def _pack_fixture(package: Path) -> dict:
    tarball = package.parent / "gaia-fixture.tgz"
    with tarfile.open(tarball, "w:gz") as archive:
        archive.add(package, arcname="package")
    return {"action": "created", "path": str(tarball), "details": "ok", "tarball": tarball}


class TestGateOpenCodeSurface(unittest.TestCase):
    def _gate(self, mutate=lambda package: None):
        with tempfile.TemporaryDirectory() as tmp:
            package = _opencode_package_fixture(Path(tmp))
            mutate(package)
            return release_mod.gate_opencode_surface(_pack_fixture(package))

    def test_opencode_complete_package_passes(self):
        res = self._gate()
        self.assertEqual(res["name"], "opencode:surface")
        self.assertEqual(res["status"], "PASS", res["detail"])

    def test_opencode_missing_plugin_ts_fails_naming_it(self):
        res = self._gate(lambda package: (package / "opencode" / "plugin.ts").unlink())
        self.assertEqual(res["status"], "FAIL")
        self.assertIn("opencode/plugin.ts", res["detail"])

    def test_opencode_missing_agent_file_fails_naming_it(self):
        res = self._gate(lambda package: (package / "agents" / "developer.md").unlink())
        self.assertEqual(res["status"], "FAIL")
        self.assertIn("agents/developer.md", res["detail"])

    def test_opencode_missing_bin_gaia_fails_naming_it(self):
        res = self._gate(lambda package: (package / "bin" / "gaia").unlink())
        self.assertEqual(res["status"], "FAIL")
        self.assertIn("../bin/gaia", res["detail"])

    def test_opencode_missing_bridge_fails_naming_it(self):
        res = self._gate(lambda package: (package / "opencode" / "bridge.py").unlink())
        self.assertEqual(res["status"], "FAIL")
        self.assertIn("./bridge.py", res["detail"])

    def test_opencode_pack_failure_fails(self):
        res = release_mod.gate_opencode_surface({"action": "error", "path": "", "details": "npm pack failed"})
        self.assertEqual(res["status"], "FAIL")
        self.assertIn("npm pack failed", res["detail"])

    def test_opencode_check_help_names_the_gate(self):
        parser = argparse.ArgumentParser()
        register(parser.add_subparsers(dest="command"))
        with redirect_stdout(io.StringIO()) as out, self.assertRaises(SystemExit):
            parser.parse_args(["release", "check", "--help"])
        self.assertIn("opencode:surface", out.getvalue())


# ---------------------------------------------------------------------------
# gate_plugin_dryrun -- gate 3, the SKIP-when-claude-absent contract (AC-2)
# ---------------------------------------------------------------------------

class TestGatePluginDryrun(unittest.TestCase):
    def test_skips_when_claude_unavailable_without_invoking_subprocess(self):
        calls = []
        with patch("cli.release.subprocess.run", side_effect=lambda *a, **k: calls.append(1)):
            res = gate_plugin_dryrun(_REPO_ROOT, claude_available=False)

        self.assertEqual(res["name"], "gaia:plugin-dryrun")
        self.assertEqual(res["status"], "SKIP")
        self.assertIn("claude", res["detail"].lower())
        self.assertEqual(calls, [])

    def test_runs_when_claude_available_pass(self):
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return subprocess.CompletedProcess(cmd, 0, "RESULT: PASS", "")

        with patch("cli.release.subprocess.run", side_effect=fake_run):
            res = gate_plugin_dryrun(_REPO_ROOT, claude_available=True)

        self.assertEqual(res["status"], "PASS")
        self.assertEqual(captured["cmd"][0], "bash")
        self.assertIn("plugin-dryrun.sh", captured["cmd"][1])
        self.assertNotIn("--functional", captured["cmd"])

    def test_functional_flag_forwarded(self):
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return subprocess.CompletedProcess(cmd, 0, "RESULT: PASS", "")

        with patch("cli.release.subprocess.run", side_effect=fake_run):
            gate_plugin_dryrun(_REPO_ROOT, functional=True, claude_available=True)

        self.assertIn("--functional", captured["cmd"])

    def test_nonzero_exit_returns_fail_when_claude_available(self):
        with patch(
            "cli.release.subprocess.run",
            return_value=subprocess.CompletedProcess([], 1, "", "RESULT: FAIL"),
        ):
            res = gate_plugin_dryrun(_REPO_ROOT, claude_available=True)
        self.assertEqual(res["status"], "FAIL")

    def test_auto_detects_claude_absence_via_shutil_which(self):
        calls = []
        with patch("cli.release.shutil.which", return_value=None), \
             patch("cli.release.subprocess.run", side_effect=lambda *a, **k: calls.append(1)):
            res = gate_plugin_dryrun(_REPO_ROOT)
        self.assertEqual(res["status"], "SKIP")
        self.assertEqual(calls, [])


# ---------------------------------------------------------------------------
# gate_npm_test -- gate 4 (mocked subprocess)
# ---------------------------------------------------------------------------

class TestGateNpmTest(unittest.TestCase):
    def test_pass_on_zero_exit(self):
        with patch(
            "cli.release.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, "42 passed", ""),
        ):
            res = gate_npm_test(_REPO_ROOT)
        self.assertEqual(res["name"], "npm test")
        self.assertEqual(res["status"], "PASS")

    def test_fail_on_nonzero_exit(self):
        with patch(
            "cli.release.subprocess.run",
            return_value=subprocess.CompletedProcess([], 1, "", "1 failed"),
        ):
            res = gate_npm_test(_REPO_ROOT)
        self.assertEqual(res["status"], "FAIL")

    def test_invokes_npm_test_in_repo_root(self):
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["cwd"] = kwargs.get("cwd")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with patch("cli.release.subprocess.run", side_effect=fake_run):
            gate_npm_test(_REPO_ROOT)

        self.assertEqual(captured["cmd"], ["npm", "test"])
        self.assertEqual(captured["cwd"], str(_REPO_ROOT))


# ---------------------------------------------------------------------------
# gate_convergence -- gate 5 (shared cli/_converge, schema-direction guard)
# ---------------------------------------------------------------------------

class TestGateConvergence(unittest.TestCase):
    """The 5th gate wires `gaia release` onto the SAME `cli/_converge` inspector
    `gaia dev` uses -- origin = the release artifact, guard = reverse-direction
    schema drift is a hard FAIL. Every case mocks `_converge.run_convergence_report`
    at the release-module boundary, so no test touches the real machine DB, PATH,
    or npm bin dir; the shared driver itself is covered by test_converge.py."""

    def _pkg_repo(self, tmp: str, version: str) -> Path:
        repo = Path(tmp) / "repo"
        repo.mkdir()
        (repo / "package.json").write_text(f'{{"version": "{version}"}}')
        return repo

    def test_pass_when_converged_no_reverse(self):
        report = {"surfaces": [], "converged": True, "reverse_direction": False}
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._pkg_repo(tmp, "5.2.0")
            with patch("cli.release._converge.run_convergence_report", return_value=report):
                res = gate_convergence(repo)
        self.assertEqual(res["name"], "convergence")
        self.assertEqual(res["status"], "PASS")

    def test_fail_on_reverse_direction_drift(self):
        """Live DB newer than the artifact -> the schema-direction guard fails
        the gate (installing this artifact would be REFUSED by bootstrap)."""
        report = {
            "surfaces": [
                {"surface": "db_schema", "state": "stale",
                 "detail": "schema v99 > code expects v37", "direction": "reverse"},
            ],
            "converged": False,
            "reverse_direction": True,
        }
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._pkg_repo(tmp, "5.2.0")
            with patch("cli.release._converge.run_convergence_report", return_value=report):
                res = gate_convergence(repo)
        self.assertEqual(res["status"], "FAIL")
        self.assertIn("reverse-direction", res["detail"])

    def test_forward_skew_is_pass_not_fail(self):
        """Forward/stale surfaces are informational -- release does not reconcile
        the machine, so only the reverse-direction guard is a hard stop."""
        report = {
            "surfaces": [
                {"surface": "db_schema", "state": "stale",
                 "detail": "schema v35 < code expects v37", "direction": "forward"},
            ],
            "converged": False,
            "reverse_direction": False,
        }
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._pkg_repo(tmp, "5.2.0")
            with patch("cli.release._converge.run_convergence_report", return_value=report):
                res = gate_convergence(repo)
        self.assertEqual(res["status"], "PASS")

    def test_inspection_error_is_skip(self):
        report = {"surfaces": [], "converged": False, "reverse_direction": False,
                  "error": "boom"}
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._pkg_repo(tmp, "5.2.0")
            with patch("cli.release._converge.run_convergence_report", return_value=report):
                res = gate_convergence(repo)
        self.assertEqual(res["status"], "SKIP")
        self.assertIn("boom", res["detail"])

    def test_origin_version_read_from_package_json(self):
        """The origin passed to the shared inspector is the artifact version
        (repo package.json), NOT the local source -- the one difference from dev."""
        captured = {}

        def fake_report(ws, *, origin_version, **kwargs):
            captured["origin_version"] = origin_version
            return {"surfaces": [], "converged": True, "reverse_direction": False}

        with tempfile.TemporaryDirectory() as tmp:
            repo = self._pkg_repo(tmp, "9.9.9")
            with patch("cli.release._converge.run_convergence_report", side_effect=fake_report):
                gate_convergence(repo)
        self.assertEqual(captured["origin_version"], "9.9.9")

    def test_unreadable_package_json_gives_none_origin_not_crash(self):
        captured = {}

        def fake_report(ws, *, origin_version, **kwargs):
            captured["origin_version"] = origin_version
            return {"surfaces": [], "converged": True, "reverse_direction": False}

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()  # no package.json
            with patch("cli.release._converge.run_convergence_report", side_effect=fake_report):
                res = gate_convergence(repo)
        self.assertIsNone(captured["origin_version"])
        self.assertEqual(res["status"], "PASS")


# ---------------------------------------------------------------------------
# run_release_check / cmd_release_check -- orchestration (all 5 gates, mocked)
# ---------------------------------------------------------------------------

class TestRunReleaseCheckOrchestration(unittest.TestCase):
    _GATES = (
        "gate_pre_publish_validate", "gate_npm_sandbox", "gate_plugin_dryrun",
        "gate_tests", "gate_convergence", "gate_opencode_surface",
    )

    def _run_check(self, gates, **kwargs):
        """Run the orchestration with every gate and the shared pack replaced."""
        with ExitStack() as stack:
            stack.enter_context(patch("cli.release._pack_helpers.pack_tarball", return_value=_CREATED_PACK))
            for name in self._GATES:
                stack.enter_context(patch(f"cli.release.{name}", side_effect=gates[name]))
            return run_release_check(_REPO_ROOT, **kwargs)

    def _recording_gates(self, call_order, status_of=lambda name: "PASS"):
        def make_gate(name):
            def _gate(*a, **k):
                call_order.append((name, a, k))
                return {"name": name, "status": status_of(name), "detail": "ok", "duration_ms": 1}
            return _gate
        return {name: make_gate(name) for name in self._GATES}

    def test_runs_all_six_gates_in_order(self):
        call_order = []
        results = self._run_check(self._recording_gates(call_order))

        self.assertEqual([name for name, _, _ in call_order], list(self._GATES))
        self.assertEqual(len(results), 6)

    def test_release_check_runs_the_opencode_gate_on_the_shared_pack(self):
        call_order = []
        results = self._run_check(self._recording_gates(call_order))

        calls = {name: (a, k) for name, a, k in call_order}
        self.assertEqual(calls["gate_npm_sandbox"][0][1], _CREATED_PACK)
        self.assertEqual(calls["gate_opencode_surface"][0][0], _CREATED_PACK)
        self.assertEqual(results[5]["name"], "gate_opencode_surface")

    def test_does_not_short_circuit_on_early_failure(self):
        """All 6 gates run even when gate 1 fails -- full picture, per AC-2."""
        call_order = []
        gates = self._recording_gates(
            call_order, status_of=lambda name: "FAIL" if name == "gate_pre_publish_validate" else "PASS"
        )
        results = self._run_check(gates)

        self.assertEqual([name for name, _, _ in call_order], list(self._GATES))
        self.assertEqual(results[0]["status"], "FAIL")

    def test_functional_flag_forwarded_to_plugin_dryrun_gate(self):
        call_order = []
        self._run_check(self._recording_gates(call_order), functional=True)

        calls = {name: k for name, _, k in call_order}
        self.assertTrue(calls["gate_plugin_dryrun"]["functional"])


class TestCmdReleaseCheck(unittest.TestCase):
    def _all_pass(self):
        return [
            {"name": "pre-publish:validate", "status": "PASS", "detail": "ok", "duration_ms": 1},
            {"name": "gaia:verify-install:local", "status": "PASS", "detail": "ok", "duration_ms": 1},
            {"name": "gaia:plugin-dryrun", "status": "SKIP", "detail": "claude absent", "duration_ms": 1},
            {"name": "npm test", "status": "PASS", "detail": "ok", "duration_ms": 1},
        ]

    def test_returns_zero_when_all_pass_or_skip(self):
        args = argparse.Namespace(functional=False, quiet=True)
        with patch("cli.release.run_release_check", return_value=self._all_pass()):
            with redirect_stdout(io.StringIO()) as out:
                rc = cmd_release_check(args)
        self.assertEqual(rc, 0)
        self.assertIn("PASS", out.getvalue())

    def test_returns_nonzero_when_any_gate_fails(self):
        results = self._all_pass()
        results[3]["status"] = "FAIL"
        args = argparse.Namespace(functional=False, quiet=True)
        with patch("cli.release.run_release_check", return_value=results):
            with redirect_stdout(io.StringIO()):
                rc = cmd_release_check(args)
        self.assertEqual(rc, 1)

    def test_skip_alone_does_not_fail(self):
        """A SKIPped gate (claude absent) must not, by itself, fail the command."""
        results = self._all_pass()
        self.assertTrue(all(r["status"] != "FAIL" for r in results))
        args = argparse.Namespace(functional=False, quiet=True)
        with patch("cli.release.run_release_check", return_value=results):
            with redirect_stdout(io.StringIO()):
                rc = cmd_release_check(args)
        self.assertEqual(rc, 0)

    def test_report_lists_all_gate_names_when_not_quiet(self):
        args = argparse.Namespace(functional=False, quiet=False)
        with patch("cli.release.run_release_check", return_value=self._all_pass()):
            with redirect_stdout(io.StringIO()) as out:
                cmd_release_check(args)
        text = out.getvalue()
        for name in ("pre-publish:validate", "gaia:verify-install:local", "gaia:plugin-dryrun", "npm test"):
            self.assertIn(name, text)


class TestCmdReleaseDispatcher(unittest.TestCase):
    def test_dispatches_to_func_when_set(self):
        called = []
        args = argparse.Namespace(func=lambda a: called.append(a) or 0)
        rc = cmd_release(args)
        self.assertEqual(rc, 0)
        self.assertEqual(called, [args])

    def test_falls_back_to_default_when_no_func(self):
        args = argparse.Namespace()
        with redirect_stdout(io.StringIO()) as out:
            rc = cmd_release(args)
        self.assertEqual(rc, 0)
        self.assertIn("Usage: gaia release", out.getvalue())


# ---------------------------------------------------------------------------
# resolve_publish_version -- semver passthrough + patch/minor/major bump
# ---------------------------------------------------------------------------

class TestResolvePublishVersion(unittest.TestCase):
    def test_explicit_semver_passes_through_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            version, err = resolve_publish_version(Path(tmp), "5.1.0-rc.1")
        self.assertIsNone(err)
        self.assertEqual(version, "5.1.0-rc.1")

    def test_rejects_leading_v(self):
        with tempfile.TemporaryDirectory() as tmp:
            version, err = resolve_publish_version(Path(tmp), "v5.0.0")
        self.assertIsNone(version)
        self.assertIsNotNone(err)

    def test_rejects_garbage(self):
        with tempfile.TemporaryDirectory() as tmp:
            version, err = resolve_publish_version(Path(tmp), "not-a-version")
        self.assertIsNone(version)
        self.assertIn("patch/minor/major", err)

    def _write_pkg(self, tmp: str, version: str) -> Path:
        root = Path(tmp)
        (root / "package.json").write_text(json.dumps({"name": "@jaguilar87/gaia", "version": version}))
        return root

    def test_patch_keyword_bumps_patch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._write_pkg(tmp, "5.2.3")
            version, err = resolve_publish_version(root, "patch")
        self.assertIsNone(err)
        self.assertEqual(version, "5.2.4")

    def test_minor_keyword_bumps_minor_and_resets_patch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._write_pkg(tmp, "5.2.3")
            version, err = resolve_publish_version(root, "minor")
        self.assertIsNone(err)
        self.assertEqual(version, "5.3.0")

    def test_major_keyword_bumps_major_and_resets_minor_patch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._write_pkg(tmp, "5.2.3")
            version, err = resolve_publish_version(root, "major")
        self.assertIsNone(err)
        self.assertEqual(version, "6.0.0")

    def test_bump_keyword_strips_prerelease_suffix_from_current(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._write_pkg(tmp, "5.2.3-rc.1")
            version, err = resolve_publish_version(root, "patch")
        self.assertIsNone(err)
        self.assertEqual(version, "5.2.4")

    def test_missing_package_json_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            version, err = resolve_publish_version(Path(tmp), "patch")
        self.assertIsNone(version)
        self.assertIsNotNone(err)


# ---------------------------------------------------------------------------
# resolve_source_root -- release check/publish ALWAYS validate the source tree,
# never the slim installed copy, regardless of which bin/gaia copy was invoked.
# ---------------------------------------------------------------------------

class TestIsSourceCheckout(unittest.TestCase):
    def _make_source(self, tmp: str) -> Path:
        root = Path(tmp)
        (root / "build").mkdir()
        (root / "build" / "gaia.manifest.json").write_text("{}")
        (root / "tests").mkdir()
        (root / "package.json").write_text('{"name": "@jaguilar87/gaia"}')
        return root

    def test_true_for_full_source_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertTrue(_is_source_checkout(self._make_source(tmp)))

    def test_false_for_slim_install_copy_missing_manifest_and_tests(self):
        # A slim npm-packed copy has package.json but neither build/gaia.manifest.json
        # nor tests/ -- exactly the copy that produced the 4/4 spurious failures.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text('{"name": "@jaguilar87/gaia"}')
            self.assertFalse(_is_source_checkout(root))

    def test_false_when_only_manifest_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "build").mkdir()
            (root / "build" / "gaia.manifest.json").write_text("{}")
            (root / "package.json").write_text("{}")
            self.assertFalse(_is_source_checkout(root))


class TestResolveSourceRoot(unittest.TestCase):
    def _make_source(self, tmp: str) -> Path:
        root = Path(tmp)
        (root / "build").mkdir()
        (root / "build" / "gaia.manifest.json").write_text("{}")
        (root / "tests").mkdir()
        (root / "package.json").write_text('{"name": "@jaguilar87/gaia"}')
        return root

    def test_uses_executing_copy_when_it_is_a_source_checkout(self):
        # The `python3 <checkout>/bin/gaia release check` path: _PACKAGE_ROOT is
        # itself the source tree.
        with tempfile.TemporaryDirectory() as tmp:
            src = self._make_source(tmp)
            with patch("cli.release._PACKAGE_ROOT", src):
                root, err = resolve_source_root()
        self.assertIsNone(err)
        self.assertEqual(root, src)

    def test_falls_back_to_git_toplevel_when_executing_copy_is_slim(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = self._make_source(tmp)
            slim = Path(tmp) / "node_modules" / "gaia"
            slim.mkdir(parents=True)
            (slim / "package.json").write_text("{}")  # slim, not a source checkout
            with patch("cli.release._PACKAGE_ROOT", slim):
                with patch("cli.release._git_toplevel", return_value=src):
                    root, err = resolve_source_root()
        self.assertIsNone(err)
        self.assertEqual(root, src)

    def test_fails_loudly_when_no_source_reachable(self):
        # Slim executing copy, git finds nothing -> error, and crucially it
        # does NOT return the slim copy. No env-var escape hatch exists.
        with tempfile.TemporaryDirectory() as tmp:
            slim = Path(tmp)
            (slim / "package.json").write_text("{}")
            with patch("cli.release._PACKAGE_ROOT", slim):
                with patch("cli.release._git_toplevel", return_value=None):
                    root, err = resolve_source_root()
        self.assertIsNone(root)
        self.assertIn("no Gaia source checkout found", err)

    def test_cmd_release_check_refuses_slim_copy_without_running_gates(self):
        # cmd_release_check must surface the source-resolution error and never
        # invoke the gates against a slim copy.
        args = argparse.Namespace(functional=False, quiet=True)
        with patch("cli.release.resolve_source_root", return_value=(None, "no Gaia source checkout found. ...")):
            with patch("cli.release.run_release_check") as mock_run:
                with redirect_stdout(io.StringIO()):
                    rc = cmd_release_check(args)
        self.assertEqual(rc, 1)
        mock_run.assert_not_called()

    def test_cmd_release_check_passes_resolved_source_to_gates(self):
        args = argparse.Namespace(functional=False, quiet=True)
        sentinel = Path("/tmp/canonical-source")
        with patch("cli.release.resolve_source_root", return_value=(sentinel, None)):
            with patch("cli.release.run_release_check", return_value=[]) as mock_run:
                with redirect_stdout(io.StringIO()):
                    cmd_release_check(args)
        mock_run.assert_called_once()
        self.assertEqual(mock_run.call_args.args[0], sentinel)


# ---------------------------------------------------------------------------
# build_publish_plan -- the --dry-run preview (AC-3)
# ---------------------------------------------------------------------------

class TestBuildPublishPlan(unittest.TestCase):
    def test_plan_lists_all_eight_steps_in_order(self):
        plan = build_publish_plan("5.0.5")
        names = [s["name"] for s in plan]
        self.assertEqual(
            names,
            ["node deps", "release:prepare", "sandbox install", "CI verdict or local suite", "git commit",
             "git tag", "git push", "gh release create"],
        )

    def test_plan_marks_push_and_gh_release_as_t3(self):
        plan = build_publish_plan("5.0.5")
        by_name = {s["name"]: s for s in plan}
        self.assertEqual(by_name["git push"]["tier"], "T3")
        self.assertEqual(by_name["gh release create"]["tier"], "T3")
        self.assertNotEqual(by_name["release:prepare"]["tier"], "T3")
        self.assertNotEqual(by_name["git commit"]["tier"], "T3")
        self.assertNotEqual(by_name["git tag"]["tier"], "T3")

    def test_plan_embeds_the_resolved_version_in_tag_commands(self):
        plan = build_publish_plan("9.9.9")
        by_name = {s["name"]: s for s in plan}
        self.assertIn("v9.9.9", by_name["git tag"]["cmd"])
        self.assertIn("v9.9.9", by_name["gh release create"]["cmd"])


# ---------------------------------------------------------------------------
# step_release_prepare -- Phase 3 step 1 (mocked)
# ---------------------------------------------------------------------------

class TestStepReleasePrepare(unittest.TestCase):
    def test_invokes_node_with_version_arg(self):
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["cwd"] = kwargs.get("cwd")
            return subprocess.CompletedProcess(cmd, 0, "release:prepare complete", "")

        with patch("cli.release.subprocess.run", side_effect=fake_run):
            res = step_release_prepare(_REPO_ROOT, "5.0.5")

        self.assertEqual(res["status"], "PASS")
        self.assertEqual(captured["cmd"][0], "node")
        self.assertIn("release-prepare.mjs", captured["cmd"][1])
        self.assertIn("5.0.5", captured["cmd"])
        self.assertEqual(captured["cwd"], str(_REPO_ROOT))

    def test_missing_script_fails_without_invoking_subprocess(self):
        calls = []
        with tempfile.TemporaryDirectory() as tmp:
            with patch("cli.release.subprocess.run", side_effect=lambda *a, **k: calls.append(1)):
                res = step_release_prepare(Path(tmp), "5.0.5")
        self.assertEqual(res["status"], "FAIL")
        self.assertEqual(calls, [])

    def test_nonzero_exit_fails(self):
        with patch(
            "cli.release.subprocess.run",
            return_value=subprocess.CompletedProcess([], 1, "", "version drift"),
        ):
            res = step_release_prepare(_REPO_ROOT, "5.0.5")
        self.assertEqual(res["status"], "FAIL")


# ---------------------------------------------------------------------------
# step_git_commit -- LOCAL-SAFE, not T3 (mocked)
# ---------------------------------------------------------------------------

class TestStepGitCommit(unittest.TestCase):
    def test_adds_only_existing_version_source_paths(self):
        captured = {}

        def fake_run(cmd, **kwargs):
            if cmd[:2] == ["git", "add"]:
                captured["add_cmd"] = cmd
                return subprocess.CompletedProcess(cmd, 0, "", "")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text("{}")
            (root / "CHANGELOG.md").write_text("# changelog")
            # pyproject.toml, .claude-plugin/*, hooks/hooks.json deliberately absent.
            with patch("cli.release.subprocess.run", side_effect=fake_run):
                res = step_git_commit(root, "5.0.5")

        self.assertEqual(res["status"], "PASS")
        self.assertIn("package.json", captured["add_cmd"])
        self.assertIn("CHANGELOG.md", captured["add_cmd"])
        self.assertNotIn("pyproject.toml", captured["add_cmd"])

    def test_no_version_source_files_fails_without_invoking_subprocess(self):
        calls = []
        with tempfile.TemporaryDirectory() as tmp:
            with patch("cli.release.subprocess.run", side_effect=lambda *a, **k: calls.append(1)):
                res = step_git_commit(Path(tmp), "5.0.5")
        self.assertEqual(res["status"], "FAIL")
        self.assertEqual(calls, [])

    def test_nothing_to_commit_is_treated_as_pass(self):
        def fake_run(cmd, **kwargs):
            if cmd[:2] == ["git", "add"]:
                return subprocess.CompletedProcess(cmd, 0, "", "")
            return subprocess.CompletedProcess(cmd, 1, "", "nothing to commit, working tree clean")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text("{}")
            with patch("cli.release.subprocess.run", side_effect=fake_run):
                res = step_git_commit(root, "5.0.5")
        self.assertEqual(res["status"], "PASS")
        self.assertIn("nothing to commit", res["detail"])

    def test_real_commit_failure_is_fail(self):
        def fake_run(cmd, **kwargs):
            if cmd[:2] == ["git", "add"]:
                return subprocess.CompletedProcess(cmd, 0, "", "")
            return subprocess.CompletedProcess(cmd, 1, "", "fatal: not a git repository")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text("{}")
            with patch("cli.release.subprocess.run", side_effect=fake_run):
                res = step_git_commit(root, "5.0.5")
        self.assertEqual(res["status"], "FAIL")

    def test_git_add_failure_short_circuits_before_commit(self):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 1, "", "add failed")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text("{}")
            with patch("cli.release.subprocess.run", side_effect=fake_run):
                res = step_git_commit(root, "5.0.5")

        self.assertEqual(res["status"], "FAIL")
        self.assertEqual(len(calls), 1)  # commit never attempted


# ---------------------------------------------------------------------------
# step_git_tag -- LOCAL-SAFE, create-only (mocked)
# ---------------------------------------------------------------------------

class TestStepGitTag(unittest.TestCase):
    def test_creates_annotated_tag_with_v_prefix(self):
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with patch("cli.release.subprocess.run", side_effect=fake_run):
            res = step_git_tag(_REPO_ROOT, "5.0.5")

        self.assertEqual(res["status"], "PASS")
        self.assertEqual(captured["cmd"][0], "git")
        self.assertIn("tag", captured["cmd"])
        self.assertIn("-a", captured["cmd"])
        self.assertIn("v5.0.5", captured["cmd"])

    def test_tag_already_exists_fails(self):
        with patch(
            "cli.release.subprocess.run",
            return_value=subprocess.CompletedProcess([], 128, "", "fatal: tag 'v5.0.5' already exists"),
        ):
            res = step_git_tag(_REPO_ROOT, "5.0.5")
        self.assertEqual(res["status"], "FAIL")


# ---------------------------------------------------------------------------
# step_git_push / step_gh_release_create -- Tier 3, never actually run here
# ---------------------------------------------------------------------------

class TestStepGitPush(unittest.TestCase):
    def test_pushes_head_and_tag_to_the_named_origin_branch_in_one_atomic_push(self):
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with patch("cli.release.subprocess.run", side_effect=fake_run):
            res = step_git_push(_REPO_ROOT, "5.0.5", "feat/x")

        self.assertEqual(res["status"], "PASS")
        self.assertEqual(
            captured["cmd"],
            ["git", "push", "--atomic", "origin", "HEAD:refs/heads/feat/x", "refs/tags/v5.0.5"],
        )

    def test_nonzero_exit_fails(self):
        with patch(
            "cli.release.subprocess.run",
            return_value=subprocess.CompletedProcess([], 1, "", "rejected"),
        ):
            res = step_git_push(_REPO_ROOT, "5.0.5", "feat/x")
        self.assertEqual(res["status"], "FAIL")


class TestStepGhReleaseCreate(unittest.TestCase):
    def test_stable_version_is_not_marked_prerelease(self):
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with patch("cli.release.subprocess.run", side_effect=fake_run):
            res = step_gh_release_create(_REPO_ROOT, "5.0.5")

        self.assertEqual(res["status"], "PASS")
        self.assertEqual(captured["cmd"][:3], ["gh", "release", "create"])
        self.assertIn("v5.0.5", captured["cmd"])
        self.assertNotIn("--prerelease", captured["cmd"])

    def test_rc_version_is_marked_prerelease(self):
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with patch("cli.release.subprocess.run", side_effect=fake_run):
            step_gh_release_create(_REPO_ROOT, "5.1.0-rc.1")

        self.assertIn("--prerelease", captured["cmd"])

    def test_nonzero_exit_fails(self):
        with patch(
            "cli.release.subprocess.run",
            return_value=subprocess.CompletedProcess([], 1, "", "release already exists"),
        ):
            res = step_gh_release_create(_REPO_ROOT, "5.0.5")
        self.assertEqual(res["status"], "FAIL")


# ---------------------------------------------------------------------------
# run_release_publish -- sequential, stops at first failure
# ---------------------------------------------------------------------------

class TestRunReleasePublishOrchestration(unittest.TestCase):
    def test_runs_all_eight_steps_in_order_when_all_pass(self):
        call_order = []

        def make_step(name):
            def _step(*a, **k):
                call_order.append(name)
                return {"name": name, "status": "PASS", "detail": "ok", "duration_ms": 1}
            return _step

        with patch("cli.release.preflight_publish",
                   return_value={"name": "preconditions", "status": "PASS", "detail": "ok", "duration_ms": 1}), \
             patch("cli.release.step_node_deps", side_effect=make_step("node deps")), \
             patch("cli.release.step_release_prepare", side_effect=make_step("release:prepare")), \
             patch("cli.release.step_sandbox_install", side_effect=make_step("sandbox install")), \
             patch("cli.release.gate_tests", side_effect=make_step("npm test")), \
             patch("cli.release.step_git_commit", side_effect=make_step("git commit")), \
             patch("cli.release.step_git_tag", side_effect=make_step("git tag")), \
             patch("cli.release.step_git_push", side_effect=make_step("git push")), \
             patch("cli.release.step_gh_release_create", side_effect=make_step("gh release create")):
            results = run_release_publish(_REPO_ROOT, "5.0.5")

        self.assertEqual(
            call_order,
            ["node deps", "release:prepare", "sandbox install", "npm test", "git commit", "git tag", "git push",
             "gh release create"],
        )
        self.assertEqual(len(results), 8)

    def test_stops_at_first_failure_unlike_release_check(self):
        """Contrasts run_release_check's always-run-all-4 design: publish's
        steps are causally dependent, so a failed test run must never reach
        git tag / git push / gh release create."""
        call_order = []

        def failing_step(*a, **k):
            call_order.append("npm test")
            return {"name": "npm test", "status": "FAIL", "detail": "1 failed", "duration_ms": 1}

        def make_step(name):
            def _step(*a, **k):
                call_order.append(name)
                return {"name": name, "status": "PASS", "detail": "ok", "duration_ms": 1}
            return _step

        with patch("cli.release.preflight_publish",
                   return_value={"name": "preconditions", "status": "PASS", "detail": "ok", "duration_ms": 1}), \
             patch("cli.release.step_node_deps", side_effect=make_step("node deps")), \
             patch("cli.release.step_release_prepare", side_effect=make_step("release:prepare")), \
             patch("cli.release.step_sandbox_install", side_effect=make_step("sandbox install")), \
             patch("cli.release.gate_tests", side_effect=failing_step), \
             patch("cli.release.step_git_commit", side_effect=make_step("git commit")) as mock_commit, \
             patch("cli.release.step_git_tag", side_effect=make_step("git tag")) as mock_tag, \
             patch("cli.release.step_git_push", side_effect=make_step("git push")) as mock_push, \
             patch("cli.release.step_gh_release_create", side_effect=make_step("gh release create")) as mock_gh:
            results = run_release_publish(_REPO_ROOT, "5.0.5")

        self.assertEqual(call_order, ["node deps", "release:prepare", "sandbox install", "npm test"])
        self.assertEqual(len(results), 4)
        self.assertEqual(results[-1]["status"], "FAIL")
        mock_commit.assert_not_called()
        mock_tag.assert_not_called()
        mock_push.assert_not_called()
        mock_gh.assert_not_called()


# ---------------------------------------------------------------------------
# cmd_release_publish -- dry-run vs real (both fully mocked)
# ---------------------------------------------------------------------------

class TestCmdReleasePublish(unittest.TestCase):
    def test_dry_run_prints_plan_and_never_calls_run_release_publish(self):
        args = argparse.Namespace(version="patch", dry_run=True, quiet=False)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text(json.dumps({"version": "5.0.5"}))
            # publish resolves the canonical source first, then reads the
            # version from it -- point source resolution at the tempdir.
            with patch("cli.release.resolve_source_root", return_value=(root, None)), \
                 patch("cli.release.run_release_publish") as mock_run:
                with redirect_stdout(io.StringIO()) as out:
                    rc = cmd_release_publish(args)
        self.assertEqual(rc, 0)
        mock_run.assert_not_called()
        text = out.getvalue()
        self.assertIn("DRY RUN", text)
        self.assertIn("git push", text)
        self.assertIn("gh release create", text)

    def test_dry_run_resolves_bump_keyword_to_next_version(self):
        args = argparse.Namespace(version="patch", dry_run=True, quiet=True)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text(json.dumps({"version": "5.0.5"}))
            with patch("cli.release.resolve_source_root", return_value=(root, None)):
                with redirect_stdout(io.StringIO()):
                    rc = cmd_release_publish(args)
        self.assertEqual(rc, 0)

    def test_invalid_version_returns_nonzero_without_running_anything(self):
        args = argparse.Namespace(version="not-a-version", dry_run=False, quiet=True)
        with patch("cli.release.run_release_publish") as mock_run:
            rc = cmd_release_publish(args)
        self.assertEqual(rc, 1)
        mock_run.assert_not_called()

    def test_real_run_all_pass_returns_zero(self):
        args = argparse.Namespace(version="5.0.5", dry_run=False, quiet=True)
        passing = [
            {"name": n, "status": "PASS", "detail": "ok", "duration_ms": 1}
            for n in ("release:prepare", "npm test", "git commit", "git tag", "git push", "gh release create")
        ]
        with patch("cli.release.run_release_publish", return_value=passing):
            with redirect_stdout(io.StringIO()):
                rc = cmd_release_publish(args)
        self.assertEqual(rc, 0)

    def test_real_run_failure_returns_nonzero(self):
        args = argparse.Namespace(version="5.0.5", dry_run=False, quiet=True)
        results = [
            {"name": "release:prepare", "status": "PASS", "detail": "ok", "duration_ms": 1},
            {"name": "npm test", "status": "FAIL", "detail": "1 failed", "duration_ms": 1},
        ]
        with patch("cli.release.run_release_publish", return_value=results):
            with redirect_stdout(io.StringIO()):
                rc = cmd_release_publish(args)
        self.assertEqual(rc, 1)


# ---------------------------------------------------------------------------
# AC-3 CRITICAL: this module must never invoke npm's own registry-publish
# command directly -- that stays in CI (.github/workflows/publish.yml),
# which publishes through npm trusted publishing (OIDC).
# ---------------------------------------------------------------------------

_NPM_PUBLISH_INVOCATION_RE = re.compile(r"""(['"])npm\1\s*,\s*(['"])publish\2""")


class TestNeverInvokesNpmPublishDirectly(unittest.TestCase):
    def test_module_source_never_constructs_an_npm_publish_argv(self):
        """Scans for the actual invocation SHAPE -- a ["npm", "publish", ...]
        (or ('npm', 'publish', ...)) argv literal -- rather than a naive
        substring grep, which would false-positive on this module's own
        prose (e.g. "no npm publish" in the `check` subcommand's --help
        text) describing what the local gates do NOT do.
        """
        source = inspect.getsource(release_mod)
        match = _NPM_PUBLISH_INVOCATION_RE.search(source)
        self.assertIsNone(
            match,
            f"found an npm-publish argv construction in release.py: {match}",
        )

    def test_no_subprocess_call_in_this_suite_is_ever_npm_publish(self):
        """Belt-and-suspenders: every step/gate function is exercised above
        with a mocked subprocess.run whose captured argv is asserted -- none
        of them is ["npm", "publish", ...]. This test re-asserts the
        invariant at the argv level for the two Tier-3 steps specifically,
        the ones closest to the real release trigger.
        """
        for step, args in (
            (step_git_push, (_REPO_ROOT, "5.0.5", "main")),
            (step_gh_release_create, (_REPO_ROOT, "5.0.5")),
        ):
            captured = {}

            def fake_run(cmd, **kwargs):
                captured["cmd"] = cmd
                return subprocess.CompletedProcess(cmd, 0, "", "")

            with patch("cli.release.subprocess.run", side_effect=fake_run):
                step(*args)

            self.assertFalse(
                captured["cmd"][:2] == ["npm", "publish"],
                f"{step.__name__} invoked npm publish directly: {captured['cmd']}",
            )


# ---------------------------------------------------------------------------
# Regression: bin/gaia's top-level `--version` flag and `release publish`'s
# positional `version` argument both defaulted to `dest="version"` on the
# SAME argparse Namespace (argparse subparsers share one Namespace). That
# collision made `main()`'s `if args.version:` check (evaluated BEFORE
# `args.subcommand`) fire for every `gaia release publish <version>`
# invocation, short-circuiting straight to printing the bare package
# version and returning 0 -- `cmd_release_publish` / `build_publish_plan`
# were never reached, for real runs OR `--dry-run`.
#
# Every other test in this module builds an `argparse.Namespace` by hand
# (see `TestCmdReleasePublish` above), which never exercises argparse's own
# dest resolution -- the exact place this bug lived. These tests instead
# invoke the real `bin/gaia` entry point via subprocess, the only way to
# catch a dest collision on the shared top-level Namespace.
# ---------------------------------------------------------------------------

class TestGaiaEntrypointVersionDestCollision(unittest.TestCase):
    def test_release_publish_dry_run_reaches_the_publish_plan(self):
        """`gaia release publish 5.1.0-rc.1 --dry-run` must print the eight-step
        trigger sequence, not just the bare version string."""
        bin_gaia = _BIN_DIR / "gaia"
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                [sys.executable, str(bin_gaia), "release", "publish", "5.1.0-rc.1", "--dry-run"],
                capture_output=True,
                text=True,
                cwd=str(_REPO_ROOT),
                env={**os.environ, "GAIA_DATA_DIR": tmp},
            )

        self.assertEqual(
            result.returncode, 0,
            msg=f"stdout: {result.stdout}\nstderr: {result.stderr}",
        )
        # The bug's exact signature: stdout was ONLY the bare version line.
        self.assertNotEqual(result.stdout.strip(), "gaia 5.1.0-rc.1")
        self.assertIn("DRY RUN", result.stdout)
        self.assertIn("release:prepare", result.stdout)
        self.assertIn("npm test", result.stdout)
        self.assertIn("git commit", result.stdout)
        self.assertIn("git tag", result.stdout)
        self.assertIn("git push", result.stdout)
        self.assertIn("gh release create", result.stdout)

    def test_top_level_version_flag_still_prints_version(self):
        """`gaia --version` must still short-circuit to the package version --
        the fix must not break the flag it shares no dest with anymore."""
        bin_gaia = _BIN_DIR / "gaia"
        pkg_version = json.loads((_REPO_ROOT / "package.json").read_text())["version"]

        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                [sys.executable, str(bin_gaia), "--version"],
                capture_output=True,
                text=True,
                cwd=str(_REPO_ROOT),
                env={**os.environ, "GAIA_DATA_DIR": tmp},
            )

        self.assertEqual(
            result.returncode, 0,
            msg=f"stdout: {result.stdout}\nstderr: {result.stderr}",
        )
        self.assertIn(pkg_version, result.stdout)


# ---------------------------------------------------------------------------
# `gaia release sync-local` was REMOVED (the provenance intelligence moved to
# `gaia doctor`'s "Install provenance" check). Guard against reintroduction.
# ---------------------------------------------------------------------------

class TestSyncLocalRemoved(unittest.TestCase):
    def test_no_sync_local_subparser(self):
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="subcommand")
        register(subparsers)
        with self.assertRaises(SystemExit):
            parser.parse_args(["release", "sync-local", "--dry-run"])

    def test_sync_local_symbols_are_gone(self):
        self.assertFalse(hasattr(release_mod, "cmd_release_sync_local"))
        self.assertFalse(hasattr(release_mod, "enumerate_dev_pack_workspaces"))

    def test_release_default_usage_omits_sync_local(self):
        args = argparse.Namespace()
        with redirect_stdout(io.StringIO()) as out:
            cmd_release(args)
        self.assertNotIn("sync-local", out.getvalue())


if __name__ == "__main__":
    unittest.main()
