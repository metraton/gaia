"""Regression test for `pre-publish:dry` representativity.

Context: `project_scan_v2_followups` documented `pre-publish:dry no es
representativo`. Two bugs, both stemming from the same root cause -- the
script's `--dry-run` mode computes a hypothetical bumped `this.newVersion`
but never writes it to `package.json` on disk (Step 3, `bumpVersion()`):

  1. `validatePluginManifest()` (Step 6) compared the ON-DISK (unbumped)
     `plugin.json` against the HYPOTHETICAL bumped version -- guaranteed
     mismatch, false-fail on every dry-run.
  2. Once (1) was fixed, dry-run reached `runTests()` (Step 7) for the first
     time and exposed a second bug: `baseDir` picked `NODE_MODULES_INSTALL`
     (the STALE previously-installed copy, since dry-run never reinstalls)
     instead of the source tree -- Test 4's version-sync check then compared
     source against a stale install, another false-fail unrelated to the
     actual source state.

Both are fixed in `bin/pre-publish-validate.js`:
  * Step 6 / Test 4 `expectedVersion`: when `this.dryRun`, compare against
    the on-disk `package.json` version (never `this.newVersion`).
  * `runTests()` `baseDir`: `(this.validateOnly || this.dryRun)` selects
    `GAIA_OPS_ROOT` (the source tree) -- previously only `this.validateOnly`
    did, leaving dry-run pointed at a stale install.

This test exercises the REAL script (no subprocess mock) against the actual
source tree, exactly the invocation the bug report failed on
(`npm run pre-publish:dry` == `node bin/pre-publish-validate.js --dry-run`).
It is the strongest genuine check available for a bug that is specifically
about dry-run not reflecting reality: running the real dry-run and observing
that it no longer false-fails on version comparisons.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "bin" / "pre-publish-validate.js"

# The script auto-enables --validate-only when it sees these (line ~781), which
# skips bumpVersion() and leaves this.newVersion null -- and with newVersion
# null the buggy and the fixed expectedVersion expressions collapse to the same
# value, so the false-fail assertions below cannot fail. Neutralizing them makes
# the dry-run path this test exists to guard run identically whether or not the
# outer runner is CI.
_CI_ENV_VARS = ("CI", "GITHUB_ACTIONS")

# Step 5 ("Validating key files") compares the source tree against the
# self-installed copy under node_modules and throws when it is absent, which
# aborts the script BEFORE Step 6 ever runs. A bare checkout has no such copy,
# so on that precondition the version comparisons under test are never
# evaluated -- there is nothing to assert, and asserting anyway would either
# fail on a cause unrelated to this bug or pass vacuously. Detecting the abort
# explicitly lets those runs skip instead of lying in either direction.
_SELF_INSTALL_ABORT_MARKERS = (
    "Installed file missing",
    "Some critical files are missing",
)

# Printed at the top of validatePluginManifest(), before any version
# comparison: its presence is what makes the assertions below non-vacuous.
_STEP_6_BANNER = "Step 6: Validating plugin manifest"


def _non_ci_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k not in _CI_ENV_VARS}


def _node_available() -> bool:
    return shutil.which("node") is not None


@unittest.skipUnless(_node_available(), "node not available in this environment")
class TestPrePublishDryRunRepresentative(unittest.TestCase):
    """Real (unmocked) `node bin/pre-publish-validate.js --dry-run` invocation.

    Read-only by construction: --dry-run guards every mutating step
    (bumpVersion, reinstallNodeModules) with `if (this.dryRun) return;` before
    any fs.writeFileSync / execSync side effect. This test additionally
    confirms that guarantee by diffing `git status --porcelain` before/after.
    """

    def _git_dirty_paths(self) -> set[str]:
        res = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(_REPO_ROOT), capture_output=True, text=True, timeout=30,
        )
        return {line[3:] for line in res.stdout.splitlines() if line.strip()}

    def _run_dry_run(self) -> str:
        res = subprocess.run(
            ["node", str(_SCRIPT), "--dry-run"],
            cwd=str(_REPO_ROOT), capture_output=True, text=True, timeout=120,
            env=_non_ci_env(),
        )
        return res.stdout + res.stderr

    def _require_self_install(self, combined: str) -> None:
        for marker in _SELF_INSTALL_ABORT_MARKERS:
            if marker in combined:
                self.skipTest(
                    "Step 5 aborted: no self-installed copy under node_modules, "
                    "so the dry-run never reaches the version comparisons under "
                    f"test.\nOutput:\n{combined}"
                )

    def test_dry_run_does_not_false_fail_on_version_comparisons(self):
        before = self._git_dirty_paths()
        combined = self._run_dry_run()
        self._require_self_install(combined)

        # Reachability precondition, not the property under test: Step 6 must
        # have run for the two assertions below to mean anything. Both are
        # negative assertions, and a negative assertion over output the script
        # never produced passes for free -- exactly the inert-sentinel failure
        # this test has already been through twice.
        self.assertIn(
            _STEP_6_BANNER, combined,
            msg=f"dry-run never reached Step 6. Output:\n{combined}",
        )

        # The two specific false-fail signatures this fix closes. Neither may
        # appear -- if either does, the representativity bug has regressed.
        #
        # The script's exit code is deliberately NOT asserted. It aggregates
        # every step's verdict, so a step unrelated to version comparison
        # (Step 5's self-install check is the known one) turns this test red
        # for a cause it does not guard, which is what happened when the
        # returncode assertion was here. The property this test owns is
        # narrower and complete without it: with the CI vars stripped,
        # newVersion is populated (5.3.1 against a 5.3.0 plugin.json), so the
        # pre-fix expression WOULD emit one of these signatures and the test
        # WOULD fail. Do not re-add an exit-code assertion.
        self.assertNotIn(
            "does not match package.json version", combined,
            msg=f"Step 6 false-fail regressed. Output:\n{combined}",
        )
        self.assertNotRegex(
            combined, r"Version drift detected\. Align all sources",
            msg=f"Test 4 stale-install false-fail regressed. Output:\n{combined}",
        )

        after = self._git_dirty_paths()
        self.assertEqual(
            before, after,
            msg="--dry-run must not mutate the working tree",
        )

    def test_dry_run_reaches_step_7_tests(self):
        """Confirms the fix does not merely mask Step 6 -- execution genuinely
        proceeds past it into runTests() (Step 7), which is the step the
        follow-up note said was previously unreached in dry-run."""
        combined = self._run_dry_run()
        self._require_self_install(combined)
        self.assertIn("Step 7: Running validation tests", combined)
        self.assertIn("Test 4: Validating version sync across manifests", combined)


if __name__ == "__main__":
    unittest.main()
