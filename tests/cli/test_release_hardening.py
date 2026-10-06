"""
Tests for the release-hardening package (P0 preconditions gate, P1a idempotent
git tag, P1b configurable npm-test timeout) added to bin/cli/release.py.

Kept in a separate module from tests/cli/test_release.py so the hardening
coverage lands without churning the existing suite. Hygiene mirrors
test_release.py: every subprocess boundary is mocked at `cli.release._run`
or `cli.release.subprocess.run`; nothing here spawns a real git, gh, npm, or
network call, and nothing touches the real repo's git state.
"""

import argparse
import os
import subprocess
import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

_BIN_DIR = Path(__file__).resolve().parents[2] / "bin"
if str(_BIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BIN_DIR))

from cli.release import (  # noqa: E402
    _check_gh_push_permission,
    _check_tag_absent,
    _check_xdist_importable,
    _resolve_npm_test_timeout,
    _DEFAULT_NPM_TEST_TIMEOUT,
    _NPM_TEST_TIMEOUT_ENV,
    build_publish_plan,
    preflight_publish,
    register,
    run_release_check,
    run_release_publish,
    step_git_tag,
    gate_npm_test,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# P0 gate sub-check: gh push permission (mocked gh via _run / shutil.which)
# ---------------------------------------------------------------------------

class TestCheckGhPushPermission(unittest.TestCase):
    def test_confirmed_push_true_is_no_problem(self):
        with patch("cli.release.shutil.which", return_value="/usr/bin/gh"), \
             patch("cli.release._run", return_value=(0, "true\n", "")):
            self.assertIsNone(_check_gh_push_permission(_REPO_ROOT))

    def test_push_false_is_actionable_error(self):
        with patch("cli.release.shutil.which", return_value="/usr/bin/gh"), \
             patch("cli.release._run", return_value=(0, "false\n", "")):
            err = _check_gh_push_permission(_REPO_ROOT)
        self.assertIsNotNone(err)
        self.assertIn("does NOT have push access", err)
        self.assertIn("--gh <program>", err)
        # `switch` may only appear as the thing NOT to do, never as the remedy.
        self.assertNotIn("gh auth switch -u", err)
        self.assertIn("Do NOT `gh auth switch`", err)

    def test_not_authenticated_is_actionable_error(self):
        with patch("cli.release.shutil.which", return_value="/usr/bin/gh"), \
             patch("cli.release._run", return_value=(1, "", "You are not logged in to any GitHub hosts. Run gh auth login")):
            err = _check_gh_push_permission(_REPO_ROOT)
        self.assertIsNotNone(err)
        self.assertIn("gh auth login", err)
        self.assertIn("--gh <program>", err)
        # `switch` may only appear as the thing NOT to do, never as the remedy.
        self.assertNotIn("gh auth switch -u", err)
        self.assertIn("Do NOT `gh auth switch`", err)

    def test_missing_gh_program_blocks_because_step_6_needs_it(self):
        with patch("cli.release.shutil.which", return_value=None):
            err = _check_gh_push_permission(_REPO_ROOT)
        self.assertIsNotNone(err)
        self.assertIn("not found", err)
        self.assertIn("--gh <program>", err)

    def test_network_failure_is_could_not_verify_not_a_block(self):
        # rc != 0 with a network-shaped error (not an auth error) is ambiguous.
        with patch("cli.release.shutil.which", return_value="/usr/bin/gh"), \
             patch("cli.release._run", return_value=(1, "", "could not resolve host: api.github.com")):
            self.assertIsNone(_check_gh_push_permission(_REPO_ROOT))

    def test_timeout_or_invocation_error_is_could_not_verify(self):
        # _run returns rc None on timeout / OSError -> ambiguous -> do not block.
        with patch("cli.release.shutil.which", return_value="/usr/bin/gh"), \
             patch("cli.release._run", return_value=(None, "", "timed out")):
            self.assertIsNone(_check_gh_push_permission(_REPO_ROOT))


# ---------------------------------------------------------------------------
# P0 gate sub-check: tag absent (local rev-parse + remote ls-remote)
# ---------------------------------------------------------------------------

class TestCheckTagAbsent(unittest.TestCase):
    def test_absent_local_and_remote_is_no_problem(self):
        # local rev-parse fails (rc 1), remote ls-remote succeeds but empty.
        with patch("cli.release._run", side_effect=[(1, "", ""), (0, "", "")]):
            self.assertIsNone(_check_tag_absent(_REPO_ROOT, "5.0.5"))

    def test_local_tag_exists_is_actionable_error(self):
        with patch("cli.release._run", side_effect=[(0, "abc123\n", ""), (0, "", "")]):
            err = _check_tag_absent(_REPO_ROOT, "5.0.5")
        self.assertIsNotNone(err)
        self.assertIn("v5.0.5 already exists", err)
        self.assertIn("local", err)
        self.assertIn("gh release create v5.0.5", err)

    def test_remote_tag_exists_is_actionable_error(self):
        with patch("cli.release._run", side_effect=[(1, "", ""), (0, "abc123\trefs/tags/v5.0.5\n", "")]):
            err = _check_tag_absent(_REPO_ROOT, "5.0.5")
        self.assertIsNotNone(err)
        self.assertIn("remote", err)
        self.assertIn("refs/tags/v5.0.5", err)


# ---------------------------------------------------------------------------
# P0 gate sub-check: pytest-xdist importable
# ---------------------------------------------------------------------------

class TestCheckXdistImportable(unittest.TestCase):
    def test_present_is_no_problem(self):
        with patch("cli.release._xdist_available", return_value=True):
            self.assertIsNone(_check_xdist_importable())

    def test_absent_is_actionable_error(self):
        with patch("cli.release._xdist_available", return_value=False):
            err = _check_xdist_importable()
        self.assertIsNotNone(err)
        self.assertIn("pytest-xdist", err)
        self.assertIn("-n auto", err)


# ---------------------------------------------------------------------------
# P0 gate: preflight_publish aggregation
# ---------------------------------------------------------------------------

class TestPreflightPublish(unittest.TestCase):
    def setUp(self):
        for target, value in (
            ("cli.release.resolve_push_branch", ("feat/x", None)),
            ("cli.release._check_push_fast_forward", None),
        ):
            patcher = patch(target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_all_clear_is_pass(self):
        with patch("cli.release._check_gh_push_permission", return_value=None), \
             patch("cli.release._check_tag_absent", return_value=None), \
             patch("cli.release._check_xdist_importable", return_value=None), \
             patch("cli.release._check_stable_from_main", return_value=None):
            res = preflight_publish(_REPO_ROOT, "5.0.5")
        self.assertEqual(res["name"], "preconditions")
        self.assertEqual(res["status"], "PASS")

    def test_gh_permission_failure_fails_the_gate(self):
        with patch("cli.release._check_gh_push_permission", return_value="no push access to metraton/gaia"), \
             patch("cli.release._check_tag_absent", return_value=None), \
             patch("cli.release._check_xdist_importable", return_value=None), \
             patch("cli.release._check_stable_from_main", return_value=None):
            res = preflight_publish(_REPO_ROOT, "5.0.5")
        self.assertEqual(res["status"], "FAIL")
        self.assertIn("no push access", res["detail"])

    def test_existing_tag_fails_the_gate(self):
        with patch("cli.release._check_gh_push_permission", return_value=None), \
             patch("cli.release._check_tag_absent", return_value="tag v5.0.5 already exists (local)"), \
             patch("cli.release._check_xdist_importable", return_value=None), \
             patch("cli.release._check_stable_from_main", return_value=None):
            res = preflight_publish(_REPO_ROOT, "5.0.5")
        self.assertEqual(res["status"], "FAIL")
        self.assertIn("v5.0.5 already exists", res["detail"])

    def test_missing_xdist_fails_the_gate(self):
        with patch("cli.release._check_gh_push_permission", return_value=None), \
             patch("cli.release._check_tag_absent", return_value=None), \
             patch("cli.release._check_xdist_importable", return_value="pytest-xdist is not importable"), \
             patch("cli.release._check_stable_from_main", return_value=None):
            res = preflight_publish(_REPO_ROOT, "5.0.5")
        self.assertEqual(res["status"], "FAIL")
        self.assertIn("pytest-xdist", res["detail"])

    def test_multiple_failures_all_reported(self):
        with patch("cli.release._check_gh_push_permission", return_value="gh problem"), \
             patch("cli.release._check_tag_absent", return_value="tag problem"), \
             patch("cli.release._check_xdist_importable", return_value="xdist problem"), \
             patch("cli.release._check_stable_from_main", return_value="branch problem"):
            res = preflight_publish(_REPO_ROOT, "5.0.5")
        self.assertEqual(res["status"], "FAIL")
        self.assertIn("gh problem", res["detail"])
        self.assertIn("tag problem", res["detail"])
        self.assertIn("xdist problem", res["detail"])
        self.assertIn("branch problem", res["detail"])


# ---------------------------------------------------------------------------
# P0 gate: a stable version is pushed only to main; a pre-release to any
# branch. The push target comes from `resolve_push_branch`, so the local branch
# name never decides it.
# ---------------------------------------------------------------------------

class TestPreflightStableOnlyFromMain(unittest.TestCase):
    def _preflight_on_branch(self, branch, version):
        resolved = (branch, None) if branch else (None, "cannot tell which origin branch; pass --branch <name>")
        with patch("cli.release._check_gh_push_permission", return_value=None), \
             patch("cli.release._check_tag_absent", return_value=None), \
             patch("cli.release._check_xdist_importable", return_value=None), \
             patch("cli.release.resolve_push_branch", return_value=resolved), \
             patch("cli.release._check_push_fast_forward", return_value=None):
            return preflight_publish(_REPO_ROOT, version)

    def test_stable_version_off_main_fails_naming_main(self):
        res = self._preflight_on_branch("feat/token-usage-ledger", "5.5.0")
        self.assertEqual(res["status"], "FAIL")
        self.assertIn("main", res["detail"])
        self.assertIn("feat/token-usage-ledger", res["detail"])
        self.assertIn("-rc.", res["detail"])

    def test_unresolved_push_target_is_not_main(self):
        res = self._preflight_on_branch(None, "5.5.0")
        self.assertEqual(res["status"], "FAIL")
        self.assertIn("main", res["detail"])
        self.assertIn("--branch", res["detail"])

    def test_rc_version_off_main_passes(self):
        res = self._preflight_on_branch("feat/token-usage-ledger", "5.5.0-rc.4")
        self.assertEqual(res["status"], "PASS")

    def test_stable_version_on_main_passes(self):
        res = self._preflight_on_branch("main", "5.5.0")
        self.assertEqual(res["status"], "PASS")


# ---------------------------------------------------------------------------
# P0 gate wiring: run_release_publish stops BEFORE step 1 when preflight fails
# ---------------------------------------------------------------------------

class TestRunReleasePublishPreflightWiring(unittest.TestCase):
    def test_failed_preflight_returns_only_that_result_and_runs_no_step(self):
        preflight_fail = {"name": "preconditions", "status": "FAIL", "detail": "blocked", "duration_ms": 1}
        with patch("cli.release.preflight_publish", return_value=preflight_fail), \
             patch("cli.release.step_release_prepare") as m_prep, \
             patch("cli.release.gate_tests") as m_test, \
             patch("cli.release.step_git_commit") as m_commit, \
             patch("cli.release.step_git_tag") as m_tag, \
             patch("cli.release.step_git_push") as m_push, \
             patch("cli.release.step_gh_release_create") as m_gh:
            results = run_release_publish(_REPO_ROOT, "5.0.5")

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "FAIL")
        self.assertEqual(results[0]["name"], "preconditions")
        m_prep.assert_not_called()
        m_test.assert_not_called()
        m_commit.assert_not_called()
        m_tag.assert_not_called()
        m_push.assert_not_called()
        m_gh.assert_not_called()

    def test_passing_preflight_is_transparent_six_step_contract_unchanged(self):
        preflight_pass = {"name": "preconditions", "status": "PASS", "detail": "ok", "duration_ms": 1}

        def make_step(name):
            return lambda *a, **k: {"name": name, "status": "PASS", "detail": "ok", "duration_ms": 1}

        with patch("cli.release.preflight_publish", return_value=preflight_pass), \
             patch("cli.release.step_release_prepare", side_effect=make_step("release:prepare")), \
             patch("cli.release.gate_tests", side_effect=make_step("npm test")), \
             patch("cli.release.step_git_commit", side_effect=make_step("git commit")), \
             patch("cli.release.step_git_tag", side_effect=make_step("git tag")), \
             patch("cli.release.step_git_push", side_effect=make_step("git push")), \
             patch("cli.release.step_gh_release_create", side_effect=make_step("gh release create")):
            results = run_release_publish(_REPO_ROOT, "5.0.5")

        # Preflight PASS is not prepended -- the returned list is exactly the six steps.
        self.assertEqual(len(results), 6)
        self.assertEqual([r["name"] for r in results][0], "release:prepare")


# ---------------------------------------------------------------------------
# Tests gate/step: reuse a green CI verdict, fall back to the local suite
# ---------------------------------------------------------------------------

_HEAD = "a" * 40
_RUN_URL = "https://github.com/metraton/gaia/actions/runs/424242"
_GREEN = (0, f"Reusable CI verdict for {_HEAD}: run 424242 on {_HEAD} (same commit) {_RUN_URL}\n", "")
_NO_VERDICT = (1, f"No reusable CI verdict for {_HEAD}: its tree has not passed. The suite runs.\n", "")
_API_DOWN = (2, "", f"No CI verdict lookup for {_HEAD}: gh api failed: could not resolve host. The suite runs.\n")
_TIMED_OUT = (None, "", "timed out after 60 seconds")
_BUMPED_BY_PREPARE = " M package.json\n M CHANGELOG.md\n M .claude-plugin/plugin.json\n M hooks/hooks.json\n"


def _fake_run(*, status="", helper=_GREEN, calls):
    """`_run` stand-in answering git status, git rev-parse HEAD and the verdict helper."""
    def fake(cmd, **kwargs):
        calls.append(cmd)
        if cmd[:2] == ["git", "status"]:
            return 0, status, ""
        if cmd[:3] == ["git", "rev-parse", "HEAD"]:
            return 0, _HEAD + "\n", ""
        if any(str(part).endswith("ci_verdict.py") for part in cmd):
            return helper
        raise AssertionError(f"unexpected command: {cmd}")
    return fake


def _helper_calls(calls):
    return [cmd for cmd in calls if any(str(part).endswith("ci_verdict.py") for part in cmd)]


_PASS = {"name": "other", "status": "PASS", "detail": "ok", "duration_ms": 1}
_SUITE_PASS = {"name": "npm test", "status": "PASS", "detail": "local suite ok", "duration_ms": 1}


class TestReleaseCheckReusesCiVerdict(unittest.TestCase):
    """Gate 4 of `release check`: HEAD's green CI verdict replaces the local suite."""

    def _check(self, **fake_kwargs):
        calls = []
        local_suite = fake_kwargs.pop("local_suite", False)
        with ExitStack() as stack:
            stack.enter_context(patch("cli.release._pack_helpers.pack_tarball", return_value={"action": "error"}))
            for gate in (
                "gate_pre_publish_validate", "gate_npm_sandbox", "gate_plugin_dryrun",
                "gate_convergence", "gate_opencode_surface",
            ):
                stack.enter_context(patch(f"cli.release.{gate}", return_value=_PASS))
            npm_test = stack.enter_context(patch("cli.release.gate_npm_test", return_value=_SUITE_PASS))
            stack.enter_context(patch("cli.release._run", side_effect=_fake_run(calls=calls, **fake_kwargs)))
            results = run_release_check(_REPO_ROOT, local_suite=local_suite)
        return results[3], npm_test, calls

    def test_green_verdict_on_head_passes_citing_the_run_without_the_suite(self):
        result, npm_test, calls = self._check()
        npm_test.assert_not_called()
        self.assertEqual(result["status"], "PASS")
        self.assertIn("424242", result["detail"])
        self.assertIn(_RUN_URL, result["detail"])
        [helper] = _helper_calls(calls)
        self.assertIn(_HEAD, helper)
        self.assertEqual(helper[helper.index("--repo") + 1], "metraton/gaia")

    def test_no_verdict_red_pending_or_unpushed_runs_the_local_suite(self):
        result, npm_test, _ = self._check(helper=_NO_VERDICT)
        npm_test.assert_called_once()
        self.assertEqual(result["detail"].splitlines()[-1], "local suite ok")

    def test_api_unreachable_runs_the_local_suite(self):
        _, npm_test, _ = self._check(helper=_API_DOWN)
        npm_test.assert_called_once()

    def test_helper_timeout_runs_the_local_suite(self):
        _, npm_test, _ = self._check(helper=_TIMED_OUT)
        npm_test.assert_called_once()

    def test_uncommitted_change_runs_the_local_suite_without_asking_ci(self):
        _, npm_test, calls = self._check(status=" M bin/cli/release.py\n")
        npm_test.assert_called_once()
        self.assertEqual(_helper_calls(calls), [])

    def test_local_suite_flag_skips_the_lookup_even_when_ci_is_green(self):
        _, npm_test, calls = self._check(local_suite=True)
        npm_test.assert_called_once()
        self.assertEqual(_helper_calls(calls), [])


class TestReleasePublishReusesCiVerdict(unittest.TestCase):
    """Step 2 of `release publish`: after release:prepare HEAD is the parent of
    the version-only bump commit, and its green verdict replaces the suite."""

    def _publish(self, **fake_kwargs):
        calls = []
        local_suite = fake_kwargs.pop("local_suite", False)
        preflight = {"name": "preconditions", "status": "PASS", "detail": "ok", "duration_ms": 1}
        with ExitStack() as stack:
            stack.enter_context(patch("cli.release.preflight_publish", return_value=preflight))
            for step in ("step_release_prepare", "step_git_commit", "step_git_tag",
                         "step_git_push", "step_gh_release_create"):
                stack.enter_context(patch(f"cli.release.{step}", return_value=_PASS))
            npm_test = stack.enter_context(patch("cli.release.gate_npm_test", return_value=_SUITE_PASS))
            stack.enter_context(patch("cli.release._run", side_effect=_fake_run(calls=calls, **fake_kwargs)))
            results = run_release_publish(_REPO_ROOT, "5.5.0-rc.99", local_suite=local_suite)
        return results[1], npm_test, calls

    def test_green_verdict_on_the_bump_parent_skips_the_suite(self):
        result, npm_test, calls = self._publish(status=_BUMPED_BY_PREPARE)
        npm_test.assert_not_called()
        self.assertEqual(result["status"], "PASS")
        self.assertIn(_RUN_URL, result["detail"])
        [helper] = _helper_calls(calls)
        self.assertIn(_HEAD, helper)

    def test_no_verdict_runs_the_local_suite(self):
        _, npm_test, _ = self._publish(status=_BUMPED_BY_PREPARE, helper=_NO_VERDICT)
        npm_test.assert_called_once()

    def test_change_outside_version_sources_runs_the_local_suite(self):
        _, npm_test, calls = self._publish(status=_BUMPED_BY_PREPARE + "?? tests/cli/test_new.py\n")
        npm_test.assert_called_once()
        self.assertEqual(_helper_calls(calls), [])

    def test_local_suite_flag_forces_the_suite(self):
        _, npm_test, calls = self._publish(status=_BUMPED_BY_PREPARE, local_suite=True)
        npm_test.assert_called_once()
        self.assertEqual(_helper_calls(calls), [])


class TestLocalSuiteFlagAndDryRun(unittest.TestCase):
    def _parse(self, argv):
        parser = argparse.ArgumentParser()
        register(parser.add_subparsers(dest="cmd"))
        return parser.parse_args(argv)

    def test_check_and_publish_accept_local_suite(self):
        self.assertTrue(self._parse(["release", "check", "--local-suite"]).local_suite)
        self.assertTrue(self._parse(["release", "publish", "--local-suite"]).local_suite)
        self.assertFalse(self._parse(["release", "check"]).local_suite)

    def test_dry_run_shows_ci_verdict_or_local_suite(self):
        step = build_publish_plan("5.5.0-rc.99")[1]
        self.assertEqual(step["name"], "CI verdict or local suite")
        self.assertIn("ci_verdict.py", step["cmd"])
        self.assertIn("npm test", step["cmd"])

    def test_dry_run_with_local_suite_shows_only_npm_test(self):
        step = build_publish_plan("5.5.0-rc.99", local_suite=True)[1]
        self.assertNotIn("ci_verdict.py", step["cmd"])
        self.assertIn("npm test", step["cmd"])


# ---------------------------------------------------------------------------
# P1a: idempotent git tag
# ---------------------------------------------------------------------------

class TestStepGitTagIdempotency(unittest.TestCase):
    def test_fresh_create_is_pass(self):
        with patch("cli.release._run", return_value=(0, "", "")):
            res = step_git_tag(_REPO_ROOT, "5.0.5")
        self.assertEqual(res["status"], "PASS")

    def test_existing_tag_at_head_is_idempotent_skip(self):
        def fake_run(cmd, **kwargs):
            if cmd[:2] == ["git", "tag"]:
                return (128, "", "fatal: tag 'v5.0.5' already exists")
            if cmd[:2] == ["git", "rev-list"]:
                return (0, "abc123def456\n", "")
            if cmd[:2] == ["git", "rev-parse"]:
                return (0, "abc123def456\n", "")
            return (0, "", "")

        with patch("cli.release._run", side_effect=fake_run):
            res = step_git_tag(_REPO_ROOT, "5.0.5")

        self.assertEqual(res["status"], "PASS")
        self.assertIn("idempotent skip", res["detail"])

    def test_existing_tag_at_different_commit_is_clear_fail(self):
        def fake_run(cmd, **kwargs):
            if cmd[:2] == ["git", "tag"]:
                return (128, "", "fatal: tag 'v5.0.5' already exists")
            if cmd[:2] == ["git", "rev-list"]:
                return (0, "1111111111aa\n", "")
            if cmd[:2] == ["git", "rev-parse"]:
                return (0, "2222222222bb\n", "")
            return (0, "", "")

        with patch("cli.release._run", side_effect=fake_run):
            res = step_git_tag(_REPO_ROOT, "5.0.5")

        self.assertEqual(res["status"], "FAIL")
        self.assertIn("DIFFERENT commit", res["detail"])
        self.assertIn("git tag -d v5.0.5", res["detail"])

    def test_never_uses_force_flag(self):
        captured = []

        def fake_run(cmd, **kwargs):
            captured.append(cmd)
            if cmd[:2] == ["git", "tag"]:
                return (128, "", "fatal: tag 'v5.0.5' already exists")
            if cmd[:2] == ["git", "rev-list"]:
                return (0, "abc\n", "")
            if cmd[:2] == ["git", "rev-parse"]:
                return (0, "abc\n", "")
            return (0, "", "")

        with patch("cli.release._run", side_effect=fake_run):
            step_git_tag(_REPO_ROOT, "5.0.5")

        for cmd in captured:
            self.assertNotIn("-f", cmd)
            self.assertNotIn("--force", cmd)


# ---------------------------------------------------------------------------
# P1b: configurable npm-test timeout + distinct timeout message
# ---------------------------------------------------------------------------

class TestResolveNpmTestTimeout(unittest.TestCase):
    def test_default_is_1800(self):
        self.assertEqual(_DEFAULT_NPM_TEST_TIMEOUT, 1800)

    def test_explicit_argument_wins(self):
        with patch.dict(os.environ, {_NPM_TEST_TIMEOUT_ENV: "900"}):
            self.assertEqual(_resolve_npm_test_timeout(500), 500)

    def test_env_var_override(self):
        with patch.dict(os.environ, {_NPM_TEST_TIMEOUT_ENV: "2400"}):
            self.assertEqual(_resolve_npm_test_timeout(), 2400)

    def test_no_env_falls_back_to_default(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(_NPM_TEST_TIMEOUT_ENV, None)
            self.assertEqual(_resolve_npm_test_timeout(), _DEFAULT_NPM_TEST_TIMEOUT)

    def test_malformed_env_falls_back_to_default(self):
        with patch.dict(os.environ, {_NPM_TEST_TIMEOUT_ENV: "not-a-number"}):
            self.assertEqual(_resolve_npm_test_timeout(), _DEFAULT_NPM_TEST_TIMEOUT)

    def test_nonpositive_env_falls_back_to_default(self):
        with patch.dict(os.environ, {_NPM_TEST_TIMEOUT_ENV: "0"}):
            self.assertEqual(_resolve_npm_test_timeout(), _DEFAULT_NPM_TEST_TIMEOUT)


class TestGateNpmTestTimeout(unittest.TestCase):
    def test_timeout_reports_distinct_actionable_message(self):
        with patch(
            "cli.release.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["npm", "test"], timeout=1800),
        ):
            res = gate_npm_test(_REPO_ROOT, timeout=1800)
        self.assertEqual(res["status"], "FAIL")
        self.assertIn("TIMEOUT after 1800s", res["detail"])
        self.assertIn(_NPM_TEST_TIMEOUT_ENV, res["detail"])
        self.assertIn("not a test failure", res["detail"])

    def test_env_var_timeout_is_honored_by_the_gate(self):
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["timeout"] = kwargs.get("timeout")
            return subprocess.CompletedProcess(cmd, 0, "ok", "")

        with patch.dict(os.environ, {_NPM_TEST_TIMEOUT_ENV: "1234"}), \
             patch("cli.release.subprocess.run", side_effect=fake_run):
            res = gate_npm_test(_REPO_ROOT)

        self.assertEqual(res["status"], "PASS")
        self.assertEqual(captured["timeout"], 1234)

    def test_pass_and_fail_still_work_normally(self):
        with patch(
            "cli.release.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, "42 passed", ""),
        ):
            self.assertEqual(gate_npm_test(_REPO_ROOT)["status"], "PASS")
        with patch(
            "cli.release.subprocess.run",
            return_value=subprocess.CompletedProcess([], 1, "", "1 failed"),
        ):
            self.assertEqual(gate_npm_test(_REPO_ROOT)["status"], "FAIL")


if __name__ == "__main__":
    unittest.main()
