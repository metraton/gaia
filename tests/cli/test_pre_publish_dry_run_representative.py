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

import re
import shutil
import subprocess
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "bin" / "pre-publish-validate.js"


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

    def test_dry_run_does_not_false_fail_on_version_comparisons(self):
        before = self._git_dirty_paths()

        res = subprocess.run(
            ["node", str(_SCRIPT), "--dry-run"],
            cwd=str(_REPO_ROOT), capture_output=True, text=True, timeout=120,
        )
        combined = res.stdout + res.stderr

        # The two specific false-fail signatures this fix closes. Neither may
        # appear -- if either does, the representativity bug has regressed.
        self.assertNotIn(
            "does not match package.json version", combined,
            msg=f"Step 6 false-fail regressed. Output:\n{combined}",
        )
        self.assertNotRegex(
            combined, r"Version drift detected\. Align all sources",
            msg=f"Test 4 stale-install false-fail regressed. Output:\n{combined}",
        )
        self.assertEqual(
            res.returncode, 0,
            msg=f"dry-run should complete cleanly on a clean tree. Output:\n{combined}",
        )
        self.assertIn("Dry run completed - no changes made", combined)

        after = self._git_dirty_paths()
        self.assertEqual(
            before, after,
            msg="--dry-run must not mutate the working tree",
        )

    def test_dry_run_reaches_step_7_tests(self):
        """Confirms the fix does not merely mask Step 6 -- execution genuinely
        proceeds past it into runTests() (Step 7), which is the step the
        follow-up note said was previously unreached in dry-run."""
        res = subprocess.run(
            ["node", str(_SCRIPT), "--dry-run"],
            cwd=str(_REPO_ROOT), capture_output=True, text=True, timeout=120,
        )
        combined = res.stdout + res.stderr
        self.assertIn("Step 7: Running validation tests", combined)
        self.assertIn("Test 4: Validating version sync across manifests", combined)


if __name__ == "__main__":
    unittest.main()
