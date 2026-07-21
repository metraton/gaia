"""
Tests for the release-hardening package (P0 preconditions gate, P1a idempotent
git tag, P1b configurable npm-test timeout) added to bin/cli/release.py.

Kept in a separate module from tests/cli/test_release.py so the hardening
coverage lands without churning the existing suite. Hygiene mirrors
test_release.py: every subprocess boundary is mocked at `cli.release._run`
or `cli.release.subprocess.run`; nothing here spawns a real git, gh, npm, or
network call, and nothing touches the real repo's git state.
"""

import os
import subprocess
import sys
import unittest
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
    preflight_publish,
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
        self.assertIn("gh auth switch", err)

    def test_not_authenticated_is_actionable_error(self):
        with patch("cli.release.shutil.which", return_value="/usr/bin/gh"), \
             patch("cli.release._run", return_value=(1, "", "You are not logged in to any GitHub hosts. Run gh auth login")):
            err = _check_gh_push_permission(_REPO_ROOT)
        self.assertIsNotNone(err)
        self.assertIn("gh auth login", err)

    def test_gh_missing_is_could_not_verify_not_a_block(self):
        # gh not on PATH -> cannot verify -> DO NOT block (transient/ambiguous).
        with patch("cli.release.shutil.which", return_value=None):
            self.assertIsNone(_check_gh_push_permission(_REPO_ROOT))

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
    def test_all_clear_is_pass(self):
        with patch("cli.release._check_gh_push_permission", return_value=None), \
             patch("cli.release._check_tag_absent", return_value=None), \
             patch("cli.release._check_xdist_importable", return_value=None):
            res = preflight_publish(_REPO_ROOT, "5.0.5")
        self.assertEqual(res["name"], "preconditions")
        self.assertEqual(res["status"], "PASS")

    def test_gh_permission_failure_fails_the_gate(self):
        with patch("cli.release._check_gh_push_permission", return_value="no push access to metraton/gaia"), \
             patch("cli.release._check_tag_absent", return_value=None), \
             patch("cli.release._check_xdist_importable", return_value=None):
            res = preflight_publish(_REPO_ROOT, "5.0.5")
        self.assertEqual(res["status"], "FAIL")
        self.assertIn("no push access", res["detail"])

    def test_existing_tag_fails_the_gate(self):
        with patch("cli.release._check_gh_push_permission", return_value=None), \
             patch("cli.release._check_tag_absent", return_value="tag v5.0.5 already exists (local)"), \
             patch("cli.release._check_xdist_importable", return_value=None):
            res = preflight_publish(_REPO_ROOT, "5.0.5")
        self.assertEqual(res["status"], "FAIL")
        self.assertIn("v5.0.5 already exists", res["detail"])

    def test_missing_xdist_fails_the_gate(self):
        with patch("cli.release._check_gh_push_permission", return_value=None), \
             patch("cli.release._check_tag_absent", return_value=None), \
             patch("cli.release._check_xdist_importable", return_value="pytest-xdist is not importable"):
            res = preflight_publish(_REPO_ROOT, "5.0.5")
        self.assertEqual(res["status"], "FAIL")
        self.assertIn("pytest-xdist", res["detail"])

    def test_multiple_failures_all_reported(self):
        with patch("cli.release._check_gh_push_permission", return_value="gh problem"), \
             patch("cli.release._check_tag_absent", return_value="tag problem"), \
             patch("cli.release._check_xdist_importable", return_value="xdist problem"):
            res = preflight_publish(_REPO_ROOT, "5.0.5")
        self.assertEqual(res["status"], "FAIL")
        self.assertIn("gh problem", res["detail"])
        self.assertIn("tag problem", res["detail"])
        self.assertIn("xdist problem", res["detail"])


# ---------------------------------------------------------------------------
# P0 gate wiring: run_release_publish stops BEFORE step 1 when preflight fails
# ---------------------------------------------------------------------------

class TestRunReleasePublishPreflightWiring(unittest.TestCase):
    def test_failed_preflight_returns_only_that_result_and_runs_no_step(self):
        preflight_fail = {"name": "preconditions", "status": "FAIL", "detail": "blocked", "duration_ms": 1}
        with patch("cli.release.preflight_publish", return_value=preflight_fail), \
             patch("cli.release.step_release_prepare") as m_prep, \
             patch("cli.release.gate_npm_test") as m_test, \
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
             patch("cli.release.gate_npm_test", side_effect=make_step("npm test")), \
             patch("cli.release.step_git_commit", side_effect=make_step("git commit")), \
             patch("cli.release.step_git_tag", side_effect=make_step("git tag")), \
             patch("cli.release.step_git_push", side_effect=make_step("git push")), \
             patch("cli.release.step_gh_release_create", side_effect=make_step("gh release create")):
            results = run_release_publish(_REPO_ROOT, "5.0.5")

        # Preflight PASS is not prepended -- the returned list is exactly the six steps.
        self.assertEqual(len(results), 6)
        self.assertEqual([r["name"] for r in results][0], "release:prepare")


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
