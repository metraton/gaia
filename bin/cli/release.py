"""
gaia release -- release-flow CLI group. Phase 2 added `gaia release check`;
Phase 3 adds `gaia release publish`.

`gaia release check [--functional]` collapses today's manual gaia-release
Layer 2 runbook (npm scripts run by hand, in the right order, remembered
correctly) into ONE local/offline command. It runs, in order, exactly the
four gates the `gaia-release` skill documents as Layer 2 -- "prove a clean
install works on BOTH surfaces, reproducing CI":

  1. pre-publish:validate  -- the version-drift / manifest gate
     (`bin/pre-publish-validate.js --validate-only`).
  2. gaia:verify-install:local -- packs the CURRENT source tree (via the
     shared `_pack_helpers.pack_tarball`, the same primitive `gaia dev`
     uses -- Phase 1) and installs it into a throwaway sandbox
     (`bin/validate-sandbox.sh --tarball <tgz> --target sandbox`). This
     proves the npm/pnpm surface of exactly what `npm publish` would ship.
  3. gaia:plugin-dryrun -- packs the tarball again (bin/plugin-dryrun.sh
     does its own internal `npm pack`, so this stays a wrap-not-reimplement
     call, not a second use of `pack_tarball`), extracts it, and mounts the
     extracted root in a real Claude Code via `claude plugin validate` /
     `claude --plugin-dir` -- the plugin-mode test that replaces needing a
     separate repo. `--functional` forwards to the script's own opt-in live
     `claude --plugin-dir -p ...` probe. SKIPs (not fails) when the `claude`
     binary is not on PATH: with no `claude`, the plugin loader this gate
     exists to exercise cannot run at all.
  4. npm test -- the L1 pytest suite CI runs.

Every gate is a subprocess call to the EXISTING script/binary -- this module
never reimplements pre-publish-validate.js, validate-sandbox.sh, or
plugin-dryrun.sh. All four gates always run (no short-circuit) so the
summary reports a complete PASS/FAIL/SKIP picture per gate, mirroring how
`bin/validate-sandbox.sh`'s own check harness aggregates through to a
summary rather than stopping at the first failure.

Fully local/offline: no npm registry publish, no external repo, no network
beyond what the gates already reach out for (npm pack/install against the
local registry cache).

`gaia release publish [version]` (Phase 3, this module's second subcommand)
is the separate Layer-3 trigger sequence -- it TRIGGERS a release, it does
not run the local confidence gate. It runs, strictly in order and stopping
at the first failure (these steps are causally dependent, unlike `check`'s
always-run-all-4-gates design):

  1. `release:prepare <version>` (`scripts/release-prepare.mjs`) -- the
     atomic multi-source version bump + manifest regen + validate.
  2. `npm test` -- reuses `gate_npm_test` from Phase 2, unchanged.
  3. `git add` + `git commit` -- LOCAL-SAFE (see `GIT_LOCAL_SAFE_SUBCOMMANDS`
     in `hooks/modules/security/mutative_verbs.py`), not Tier 3.
  4. `git tag -a v<version>` -- a NEW, force-free tag; never moves one.
  5. `git push --follow-tags` -- Tier 3, mutates the remote.
  6. `gh release create v<version>` -- Tier 3, triggers
     `.github/workflows/publish.yml` in CI.

Steps 5-6 are the only Tier-3 operations and are deliberately last and
separable: the hook layer will block them and require the user's approval
at runtime, and that is the intended, expected behaviour -- this module
never retries around it. This module NEVER invokes npm's own
registry-publish command itself: that command runs only inside
`publish.yml`, gated behind `NODE_AUTH_TOKEN` from GitHub Secrets. See
`tests/cli/test_release.py` for the invocation-shape assertion that
guarantees this module never constructs that argv.

`--dry-run` prints the six-step sequence (with the resolved version and the
Tier-3 steps called out) without executing anything -- no subprocess is
spawned, so nothing to approve.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

# bin/cli/release.py -> bin/cli -> bin -> gaia/ (the tree THIS module lives in).
# NOTE: when `bin/gaia` is invoked via the installed launcher, __file__ resolves
# to the slim installed copy under node_modules -- NOT the source checkout. That
# copy lacks the dev-only files a release gate must validate (build/gaia.manifest.json,
# tests/, devDependencies). `release check`/`publish` therefore resolve the
# canonical SOURCE via `resolve_source_root()` below, not this raw path.
_PACKAGE_ROOT = Path(__file__).resolve().parent.parent.parent

if str(_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_ROOT))

from cli import _pack_helpers  # type: ignore  # noqa: E402
from cli._pack_helpers import _is_source_checkout  # type: ignore  # noqa: E402

# How much of a gate's combined stdout+stderr each gate function keeps (from
# the END -- see the gate_* functions below). A gate's own summary/RESULT
# line is always the LAST thing it prints, so slicing from the end is what
# keeps that line in frame; slicing from the start would keep only whatever
# ran first and lose the actual verdict.
_DETAIL_TAIL = 4000

# How much of that captured tail `_report()` actually prints for a FAILED
# gate. Must stay well below `_DETAIL_TAIL` so there is always more captured
# than displayed by default, and large enough that a failing gate's own
# summary line (e.g. plugin-dryrun.sh's "RESULT: FAIL", validate-sandbox.sh's
# per-check [FAIL] lines) survives the print, not just whatever text happens
# to fall in an arbitrary 300-char window.
_REPORT_DETAIL_TAIL = 2000

# Mirrors SEMVER_RE in scripts/release-prepare.mjs -- a bare semver, no
# leading "v" (the tag adds it, the sources never carry it).
_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")
_BUMP_KEYWORDS = ("patch", "minor", "major")


def _now_ms() -> int:
    return int(time.time() * 1000)


def _run(cmd: list[str], *, cwd: Path, timeout: int) -> tuple[int | None, str, str]:
    """Run *cmd*, never raising. Returns (returncode, stdout, stderr).

    returncode is None when the subprocess failed to invoke or timed out --
    callers treat that the same as a hard FAIL, with the exception detail
    carried in stderr.
    """
    try:
        result = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
        return result.returncode, (result.stdout or ""), (result.stderr or "")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, "", str(exc)


# ---------------------------------------------------------------------------
# Canonical source-checkout resolution
# ---------------------------------------------------------------------------
# `gaia release check`/`publish` validate what will be PUBLISHED. What ships
# lives ONLY in the source checkout: the pre-publish validator needs
# devDependencies (chalk), the plugin-dryrun/pack steps need
# build/gaia.manifest.json, and `npm test` needs tests/. The npm-packed
# install copy is slim -- it excludes all three -- so validating it is
# meaningless and fails spuriously (4/4). These commands therefore ALWAYS
# resolve the canonical source tree, independent of which copy of `bin/gaia`
# the launcher invoked. When source cannot be located we FAIL LOUDLY rather
# than silently validate the slim copy.

def _git_toplevel(start: Path) -> Path | None:
    """Return the git worktree root containing *start*, or None."""
    rc, out, _ = _run(
        ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
        cwd=start,
        timeout=10,
    )
    if rc == 0 and out.strip():
        return Path(out.strip()).resolve()
    return None


def resolve_source_root() -> tuple[Path | None, str | None]:
    """Resolve the canonical Gaia SOURCE checkout for release operations.

    Order (first hit wins):
      1. The executing copy itself, when it IS a source checkout -- this is the
         `python3 <checkout>/bin/gaia release check` path (the common developer
         invocation), where __file__ already lands inside the source tree.
      2. The git worktree root of the executing copy, when that root is a source
         checkout.

    Returns (root, None) on success, or (None, error) when no source checkout is
    reachable -- the caller surfaces the error and refuses to validate the slim
    installed copy.
    """
    if _is_source_checkout(_PACKAGE_ROOT):
        return _PACKAGE_ROOT, None

    top = _git_toplevel(_PACKAGE_ROOT)
    if top is not None and _is_source_checkout(top):
        return top, None

    return None, (
        "no Gaia source checkout found. `release check`/`publish` validate what "
        "will be PUBLISHED, which lives only in the source tree (the installed "
        "package is a slim copy without build/gaia.manifest.json or tests/). "
        "Run from the source checkout, e.g. "
        "`python3 <checkout>/bin/gaia release check`."
    )


# ---------------------------------------------------------------------------
# Gates -- each wraps one existing script/binary via subprocess.
# ---------------------------------------------------------------------------

def gate_pre_publish_validate(repo_root: Path, *, timeout: int = 180) -> dict[str, Any]:
    """Gate 1: `node bin/pre-publish-validate.js --validate-only`.

    The version-drift / manifest gate (`validate-manifests` in CI). Read-only
    -- `--validate-only` never bumps a version or touches node_modules.
    """
    t0 = _now_ms()
    name = "pre-publish:validate"
    script = repo_root / "bin" / "pre-publish-validate.js"
    if not script.is_file():
        return {"name": name, "status": "FAIL", "detail": f"script not found: {script}", "duration_ms": _now_ms() - t0}

    rc, out, err = _run(["node", str(script), "--validate-only"], cwd=repo_root, timeout=timeout)
    duration = _now_ms() - t0
    if rc is None:
        return {"name": name, "status": "FAIL", "detail": err, "duration_ms": duration}
    detail = (out + err).strip()[-_DETAIL_TAIL:]
    return {"name": name, "status": "PASS" if rc == 0 else "FAIL", "detail": detail or "ok", "duration_ms": duration}


def gate_npm_sandbox(repo_root: Path, *, timeout: int = 600) -> dict[str, Any]:
    """Gate 2: pack (via shared `_pack_helpers.pack_tarball`) + `bin/validate-sandbox.sh
    --tarball <tgz> --target sandbox`.

    Proves the npm/pnpm surface of exactly what `npm publish` would ship,
    installed into a throwaway `/tmp` sandbox that cleans itself up.
    """
    t0 = _now_ms()
    name = "gaia:verify-install:local"

    with tempfile.TemporaryDirectory(prefix="gaia-release-check-pack-") as tmp:
        pack_res = _pack_helpers.pack_tarball(repo_root, dest_dir=Path(tmp), timeout=timeout)
        if pack_res["action"] == "error":
            return {
                "name": name, "status": "FAIL",
                "detail": f"npm pack failed: {pack_res['details']}",
                "duration_ms": _now_ms() - t0,
            }

        tarball = pack_res["tarball"]
        script = repo_root / "bin" / "validate-sandbox.sh"
        rc, out, err = _run(
            ["bash", str(script), "--tarball", str(tarball), "--target", "sandbox"],
            cwd=repo_root,
            timeout=timeout,
        )

    duration = _now_ms() - t0
    if rc is None:
        return {"name": name, "status": "FAIL", "detail": err, "duration_ms": duration}
    detail = (out + err).strip()[-_DETAIL_TAIL:]
    return {"name": name, "status": "PASS" if rc == 0 else "FAIL", "detail": detail or "ok", "duration_ms": duration}


def gate_plugin_dryrun(
    repo_root: Path,
    *,
    functional: bool = False,
    timeout: int = 600,
    claude_available: bool | None = None,
) -> dict[str, Any]:
    """Gate 3: `bin/plugin-dryrun.sh [--functional]`.

    Packs the tarball itself (its own internal `npm pack`, deliberately not
    routed through `_pack_helpers` -- see module docstring), extracts it, and
    mounts the extracted root in Claude Code via `claude plugin validate` /
    the opt-in `claude --plugin-dir` functional probe.

    SKIPs (does not FAIL) when `claude` is not on PATH: the gate exists
    specifically to exercise the plugin loader, and with no `claude` binary
    there is nothing meaningful left of that gate to run.
    """
    t0 = _now_ms()
    name = "gaia:plugin-dryrun"
    available = shutil.which("claude") is not None if claude_available is None else claude_available

    if not available:
        return {
            "name": name,
            "status": "SKIP",
            "detail": "claude CLI not on PATH -- skipping the plugin-loader dry-run (bin/plugin-dryrun.sh)",
            "duration_ms": _now_ms() - t0,
        }

    script = repo_root / "bin" / "plugin-dryrun.sh"
    cmd = ["bash", str(script)]
    if functional:
        cmd.append("--functional")

    rc, out, err = _run(cmd, cwd=repo_root, timeout=timeout)
    duration = _now_ms() - t0
    if rc is None:
        return {"name": name, "status": "FAIL", "detail": err, "duration_ms": duration}
    detail = (out + err).strip()[-_DETAIL_TAIL:]
    return {"name": name, "status": "PASS" if rc == 0 else "FAIL", "detail": detail or "ok", "duration_ms": duration}


def gate_npm_test(repo_root: Path, *, timeout: int = 1200) -> dict[str, Any]:
    """Gate 4: `npm test` -- the L1 pytest suite CI runs.

    `npm test` now runs the L1 suite under pytest-xdist (`-n auto`, wired
    into the `test`/`test:layer1` package.json scripts). Measured parallel
    wall time on a 4-core box is ~527s (8:47); the 1200s timeout keeps a
    generous ~2.3x margin over that for slower CI runners.
    """
    t0 = _now_ms()
    name = "npm test"
    rc, out, err = _run(["npm", "test"], cwd=repo_root, timeout=timeout)
    duration = _now_ms() - t0
    if rc is None:
        return {"name": name, "status": "FAIL", "detail": err, "duration_ms": duration}
    detail = (out + err).strip()[-_DETAIL_TAIL:]
    return {"name": name, "status": "PASS" if rc == 0 else "FAIL", "detail": detail or "ok", "duration_ms": duration}


# ---------------------------------------------------------------------------
# Phase 3 -- `gaia release publish` trigger sequence (AC-3).
# ---------------------------------------------------------------------------

def resolve_publish_version(repo_root: Path, version_arg: str) -> tuple[str | None, str | None]:
    """Resolve *version_arg* to a bare semver.

    Accepts either an explicit semver (validated the same way
    `scripts/release-prepare.mjs` validates it) or one of the bump keywords
    "patch"/"minor"/"major", computed from the CURRENT `package.json`
    version -- the same default the `gaia-release` skill's Layer 3 step (a)
    applies when no version is named ("Default to the next patch").

    Returns (version, None) on success or (None, error_message) on failure.
    Never raises.
    """
    if _SEMVER_RE.match(version_arg):
        return version_arg, None

    if version_arg not in _BUMP_KEYWORDS:
        return None, (
            f'"{version_arg}" is not a valid semver and not one of the bump '
            f"keywords {'/'.join(_BUMP_KEYWORDS)}."
        )

    pkg = repo_root / "package.json"
    try:
        current = json.loads(pkg.read_text())["version"]
    except (OSError, json.JSONDecodeError, KeyError) as exc:
        return None, f"could not read current version from {pkg}: {exc}"

    base = current.split("-")[0].split("+")[0]
    parts = base.split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        return None, f"current version {current!r} in {pkg} is not a plain MAJOR.MINOR.PATCH"

    major, minor, patch = (int(p) for p in parts)
    if version_arg == "major":
        major, minor, patch = major + 1, 0, 0
    elif version_arg == "minor":
        minor, patch = minor + 1, 0
    else:
        patch += 1
    return f"{major}.{minor}.{patch}", None


def _git_commit_paths(repo_root: Path) -> list[str]:
    """Return the version-source paths `release:prepare` writes, restricted
    to the ones that exist -- mirrors the source list in
    `scripts/release-prepare.mjs` (marketplace.json is conditional there
    too). A targeted `git add` of exactly these paths, not `git add -A`, so
    an unrelated dirty file in the tree is never swept into the release
    commit.
    """
    candidates = [
        "package.json",
        "pyproject.toml",
        ".claude-plugin/marketplace.json",
        ".claude-plugin/plugin.json",
        "hooks/hooks.json",
        "CHANGELOG.md",
    ]
    return [p for p in candidates if (repo_root / p).is_file()]


def step_release_prepare(repo_root: Path, version: str, *, timeout: int = 600) -> dict[str, Any]:
    """Step 1: `node scripts/release-prepare.mjs <version>`.

    The atomic bump: writes every hand-owned version source, regenerates the
    root plugin manifests, then runs `pre-publish:validate` internally --
    see `scripts/release-prepare.mjs`. This module wraps it via subprocess,
    exactly like the Phase 2 gates wrap their scripts; it never reimplements
    the bump logic.
    """
    t0 = _now_ms()
    name = "release:prepare"
    script = repo_root / "scripts" / "release-prepare.mjs"
    if not script.is_file():
        return {"name": name, "status": "FAIL", "detail": f"script not found: {script}", "duration_ms": _now_ms() - t0}

    rc, out, err = _run(["node", str(script), version], cwd=repo_root, timeout=timeout)
    duration = _now_ms() - t0
    if rc is None:
        return {"name": name, "status": "FAIL", "detail": err, "duration_ms": duration}
    detail = (out + err).strip()[-_DETAIL_TAIL:]
    return {"name": name, "status": "PASS" if rc == 0 else "FAIL", "detail": detail or "ok", "duration_ms": duration}


def step_git_commit(repo_root: Path, version: str, *, timeout: int = 60) -> dict[str, Any]:
    """Step 3: `git add <version sources>` + `git commit`. LOCAL-SAFE, not
    Tier 3 (see `GIT_LOCAL_SAFE_SUBCOMMANDS` in `mutative_verbs.py` -- add
    and commit only touch the working tree and local refs).

    Idempotent: re-running `gaia release publish` against a tree already at
    the target version (nothing changed since a prior `release:prepare`)
    reports PASS with "nothing to commit", not a failure.
    """
    t0 = _now_ms()
    name = "git commit"
    paths = _git_commit_paths(repo_root)
    if not paths:
        return {"name": name, "status": "FAIL", "detail": "no version-source files found to commit", "duration_ms": _now_ms() - t0}

    rc, out, err = _run(["git", "add"] + paths, cwd=repo_root, timeout=timeout)
    if rc != 0:
        return {
            "name": name, "status": "FAIL",
            "detail": f"git add exited {rc}: {(out + err).strip()[-_DETAIL_TAIL:]}",
            "duration_ms": _now_ms() - t0,
        }

    message = f"chore(release): v{version}"
    rc, out, err = _run(["git", "commit", "-m", message], cwd=repo_root, timeout=timeout)
    duration = _now_ms() - t0
    combined = (out + err).strip()
    if rc == 0:
        return {"name": name, "status": "PASS", "detail": combined[-_DETAIL_TAIL:] or "ok", "duration_ms": duration}
    if "nothing to commit" in combined.lower():
        return {
            "name": name, "status": "PASS",
            "detail": "nothing to commit -- sources already at target version",
            "duration_ms": duration,
        }
    return {"name": name, "status": "FAIL", "detail": combined[-_DETAIL_TAIL:], "duration_ms": duration}


def step_git_tag(repo_root: Path, version: str, *, timeout: int = 30) -> dict[str, Any]:
    """Step 4: `git tag -a v<version>`. LOCAL-SAFE. A NEW tag only -- never
    force-moves an existing one (`git tag -f` is hard-denied by
    `blocked_commands.py` regardless; this step never attempts it).
    """
    t0 = _now_ms()
    name = "git tag"
    tag = f"v{version}"
    rc, out, err = _run(["git", "tag", "-a", tag, "-m", f"Release {tag}"], cwd=repo_root, timeout=timeout)
    duration = _now_ms() - t0
    detail = (out + err).strip()[-_DETAIL_TAIL:]
    return {"name": name, "status": "PASS" if rc == 0 else "FAIL", "detail": detail or f"created {tag}", "duration_ms": duration}


def step_git_push(repo_root: Path, *, timeout: int = 180) -> dict[str, Any]:
    """Step 5: `git push --follow-tags`. TIER 3 -- mutates the remote.

    `--follow-tags` pushes the commit on the current branch AND the
    annotated tag created in step 4 in one push, so this is the single
    `git push` the Layer 3 sequence performs (see the `gaia-release` skill,
    Layer 3 step (f)). The hook layer classifies this as Tier 3 and blocks
    it for approval at runtime -- expected, not retried.
    """
    t0 = _now_ms()
    name = "git push"
    rc, out, err = _run(["git", "push", "--follow-tags"], cwd=repo_root, timeout=timeout)
    duration = _now_ms() - t0
    detail = (out + err).strip()[-_DETAIL_TAIL:]
    return {"name": name, "status": "PASS" if rc == 0 else "FAIL", "detail": detail or "ok", "duration_ms": duration}


def step_gh_release_create(repo_root: Path, version: str, *, timeout: int = 180) -> dict[str, Any]:
    """Step 6: `gh release create v<version>`. TIER 3 -- creates the GitHub
    Release that triggers `.github/workflows/publish.yml` in CI.

    This is the ONLY step that reaches the registry-publish pipeline --
    the workflow itself runs npm's own registry-publish command, gated
    behind `NODE_AUTH_TOKEN` (GitHub Secrets); this module never constructs
    that invocation. RC/beta/alpha versions are marked `--prerelease`,
    mirroring the `gaia-release` skill's "Mark RC as pre-release" note.
    """
    t0 = _now_ms()
    name = "gh release create"
    tag = f"v{version}"
    prerelease = any(marker in version for marker in ("-rc.", "-beta.", "-alpha."))
    cmd = ["gh", "release", "create", tag, "--title", tag, "--generate-notes"]
    if prerelease:
        cmd.append("--prerelease")

    rc, out, err = _run(cmd, cwd=repo_root, timeout=timeout)
    duration = _now_ms() - t0
    detail = (out + err).strip()[-_DETAIL_TAIL:]
    return {"name": name, "status": "PASS" if rc == 0 else "FAIL", "detail": detail or "ok", "duration_ms": duration}


def run_release_publish(repo_root: Path, version: str) -> list[dict[str, Any]]:
    """Run the Layer-3 publish trigger sequence in order, STOPPING at the
    first failure.

    Unlike `run_release_check`'s always-run-all-4-gates design, these steps
    are causally dependent: tagging an untested tree, or pushing before the
    tag exists, is actively harmful, not just incomplete reporting. Step 2
    reuses `gate_npm_test` unchanged rather than duplicating it.
    """
    steps = (
        lambda: step_release_prepare(repo_root, version),
        lambda: gate_npm_test(repo_root),
        lambda: step_git_commit(repo_root, version),
        lambda: step_git_tag(repo_root, version),
        lambda: step_git_push(repo_root),
        lambda: step_gh_release_create(repo_root, version),
    )
    results: list[dict[str, Any]] = []
    for step in steps:
        result = step()
        results.append(result)
        if result["status"] == "FAIL":
            break
    return results


def build_publish_plan(version: str) -> list[dict[str, str]]:
    """Describe the Layer-3 trigger sequence WITHOUT executing anything --
    the `--dry-run` preview. No subprocess is spawned building this, so
    there is nothing to approve.
    """
    tag = f"v{version}"
    return [
        {
            "name": "release:prepare",
            "cmd": f"node scripts/release-prepare.mjs {version}",
            "tier": "local (bump + validate)",
        },
        {"name": "npm test", "cmd": "npm test", "tier": "local"},
        {
            "name": "git commit",
            "cmd": f"git add <version sources> && git commit -m 'chore(release): {tag}'",
            "tier": "local-safe",
        },
        {"name": "git tag", "cmd": f"git tag -a {tag} -m 'Release {tag}'", "tier": "local-safe"},
        {"name": "git push", "cmd": "git push --follow-tags", "tier": "T3"},
        {
            "name": "gh release create",
            "cmd": f"gh release create {tag} --title {tag} --generate-notes",
            "tier": "T3",
        },
    ]


# ---------------------------------------------------------------------------
# Orchestration -- run all four gates, always, then aggregate.
# ---------------------------------------------------------------------------

def run_release_check(repo_root: Path, *, functional: bool = False) -> list[dict[str, Any]]:
    """Run the full Layer-2 pre-release gate in order and return all 4 results.

    Every gate runs regardless of earlier gate outcomes -- the summary must
    report a complete pass/fail/skip picture per gate (AC-2), not stop at
    the first red light.
    """
    return [
        gate_pre_publish_validate(repo_root),
        gate_npm_sandbox(repo_root),
        gate_plugin_dryrun(repo_root, functional=functional),
        gate_npm_test(repo_root),
    ]


def _report(
    results: list[dict[str, Any]],
    *,
    quiet: bool = False,
    title: str = "gaia release check -- Layer 2 pre-release gate (local/offline)",
) -> None:
    if not quiet:
        print(f"\n  {title}\n")
        for r in results:
            print(f"  [{r['status']:<4}] {r['name']:<28} ({r['duration_ms']}ms)")
            if r["status"] != "PASS":
                # Take the END of the already-tail-sliced detail, not the
                # start -- a gate's own summary/verdict line (RESULT: FAIL,
                # the specific failing [FAIL] assertion) is always the LAST
                # thing it printed, so a start-anchored slice shows an
                # arbitrary early fragment instead of the actual failure.
                # Multi-line (indented per line) instead of collapsed to one
                # line, since the diagnosable content is usually more than
                # one line (e.g. a [FAIL] assertion plus the RESULT line).
                detail = r["detail"][-_REPORT_DETAIL_TAIL:]
                for line in detail.splitlines() or [detail]:
                    print(f"           {line}")

    passed = sum(1 for r in results if r["status"] == "PASS")
    failed = sum(1 for r in results if r["status"] == "FAIL")
    skipped = sum(1 for r in results if r["status"] == "SKIP")

    print("\n  === Summary ===")
    print(f"  Passed:  {passed}")
    print(f"  Failed:  {failed}")
    print(f"  Skipped: {skipped}")
    print(f"\n  RESULT: {'FAIL' if failed else 'PASS'}\n")


def _report_publish_plan(version: str, plan: list[dict[str, str]], *, quiet: bool = False) -> None:
    """Print the `--dry-run` preview of the Layer-3 trigger sequence.

    No subprocess is run to build this -- it is a static description of
    `build_publish_plan()`'s output.
    """
    if quiet:
        return
    print(f"\n  gaia release publish -- Layer 3 trigger sequence (DRY RUN, v{version})\n")
    for i, step in enumerate(plan, start=1):
        marker = " [T3 -- requires approval]" if step["tier"] == "T3" else ""
        print(f"  {i}. {step['name']:<20} {step['cmd']}{marker}")
    print(
        "\n  DRY RUN -- nothing executed. Steps 5-6 (git push, gh release create) are\n"
        "  Tier-3 remote mutations and will require your approval when actually run.\n"
        "  This flow never runs npm's own registry-publish step directly -- that\n"
        "  happens in CI (.github/workflows/publish.yml), gated behind NODE_AUTH_TOKEN.\n"
    )


# ---------------------------------------------------------------------------
# Plugin interface
# ---------------------------------------------------------------------------

def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    """Register the 'release' subcommand group with the root parser."""
    p = subparsers.add_parser(
        "release",
        help="Local pre-release gates and the release-trigger sequence",
        description="Run the offline gaia-release Layer 2 gate, or trigger the Layer 3 publish sequence.",
    )
    sub = p.add_subparsers(dest="release_cmd", metavar="SUBCOMMAND")
    sub.required = True

    p_check = sub.add_parser(
        "check",
        help="Run the full local/offline pre-release gate (gaia-release Layer 2)",
        description=(
            "Runs, in order, as ONE local/offline command:\n"
            "  1. pre-publish:validate       -- drift/manifest gate\n"
            "                                    (bin/pre-publish-validate.js --validate-only)\n"
            "  2. gaia:verify-install:local  -- npm-surface sandbox install\n"
            "                                    (pack + bin/validate-sandbox.sh --target sandbox)\n"
            "  3. gaia:plugin-dryrun         -- plugin-surface dry-run via `claude plugin\n"
            "                                    validate` / `claude --plugin-dir`\n"
            "                                    (bin/plugin-dryrun.sh); SKIPs when the `claude`\n"
            "                                    binary is not on PATH\n"
            "  4. npm test                   -- the L1 pytest suite CI runs\n"
            "\n"
            "Fully local: no npm publish, no external repo, no network beyond what the\n"
            "gates already reach for.\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_check.add_argument(
        "--functional",
        action="store_true",
        default=False,
        help=(
            "Also run the opt-in live `claude --plugin-dir -p ...` functional probe "
            "in gate 3 (needs Claude auth/tokens; never implicit)"
        ),
    )
    p_check.add_argument(
        "--quiet",
        action="store_true",
        default=False,
        help="Suppress per-gate progress output; only print the final summary",
    )
    p_check.set_defaults(func=cmd_release_check)

    p_publish = sub.add_parser(
        "publish",
        help="Trigger the Layer-3 release pipeline (bump -> test -> commit -> tag -> push -> gh release create)",
        description=(
            "Runs, in order, the gaia-release Layer 3 trigger sequence -- STOPS at the\n"
            "first failure (unlike `check`'s always-run-all-4-gates design):\n"
            "  1. release:prepare <version>  -- atomic multi-source version bump +\n"
            "                                    manifest regen + validate\n"
            "                                    (scripts/release-prepare.mjs)\n"
            "  2. npm test                   -- the L1 pytest suite\n"
            "  3. git commit                 -- local-safe; the bumped version sources\n"
            "  4. git tag                    -- a NEW, force-free v<version> tag\n"
            "  5. git push --follow-tags     -- Tier 3, mutates the remote\n"
            "  6. gh release create          -- Tier 3, triggers\n"
            "                                    .github/workflows/publish.yml\n"
            "\n"
            "This command never runs npm's own registry-publish step directly -- that\n"
            "stays in CI, gated behind NODE_AUTH_TOKEN (GitHub Secrets). Steps 5-6 are\n"
            "Tier-3 remote mutations; the hook layer will require your approval before\n"
            "they run -- that is expected, not a bug.\n"
            "\n"
            "<version> accepts a bare semver (e.g. 5.0.5, 5.1.0-rc.1) or one of the bump\n"
            "keywords patch/minor/major, computed from the current package.json version.\n"
            "Defaults to patch when omitted.\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_publish.add_argument(
        "version",
        nargs="?",
        default="patch",
        help="Target version: a bare semver, or patch/minor/major (default: patch)",
    )
    p_publish.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=False,
        help="Show the trigger sequence without executing anything (no mutation, nothing to approve)",
    )
    p_publish.add_argument(
        "--quiet",
        action="store_true",
        default=False,
        help="Suppress step-by-step progress output; only print the final summary",
    )
    p_publish.set_defaults(func=cmd_release_publish)

    return p


def cmd_release_check(args: argparse.Namespace) -> int:
    """Execute `gaia release check`."""
    functional = bool(getattr(args, "functional", False))
    quiet = bool(getattr(args, "quiet", False))

    root, err = resolve_source_root()
    if err:
        print(f"gaia release check: {err}", file=sys.stderr)
        return 1

    results = run_release_check(root, functional=functional)
    _report(results, quiet=quiet)

    return 1 if any(r["status"] == "FAIL" for r in results) else 0


def cmd_release_publish(args: argparse.Namespace) -> int:
    """Execute `gaia release publish [version]`.

    Resolves the version, then either prints the dry-run plan (no
    subprocess spawned) or runs the six-step trigger sequence for real via
    `run_release_publish`, stopping at the first failure. This function
    itself never invokes npm's own registry-publish command -- see the
    module docstring and `tests/cli/test_release.py`.
    """
    version_arg = getattr(args, "version", None) or "patch"
    dry_run = bool(getattr(args, "dry_run", False))
    quiet = bool(getattr(args, "quiet", False))

    root, src_err = resolve_source_root()
    if src_err:
        print(f"gaia release publish: {src_err}", file=sys.stderr)
        return 1

    version, err = resolve_publish_version(root, version_arg)
    if err:
        print(f"gaia release publish: {err}", file=sys.stderr)
        return 1

    if dry_run:
        _report_publish_plan(version, build_publish_plan(version), quiet=quiet)
        return 0

    results = run_release_publish(root, version)
    _report(results, quiet=quiet, title=f"gaia release publish -- Layer 3 trigger sequence (v{version})")

    return 1 if any(r["status"] == "FAIL" for r in results) else 0


def cmd_release(args: argparse.Namespace) -> int:
    """Top-level dispatcher for 'gaia release'.

    Called by bin/gaia which invokes cmd_{subcommand}(args). For grouped
    subcommands like release, this delegates to the specific handler set
    via set_defaults(func=...) in register() -- mirrors bin/cli/approvals.py.
    """
    func = getattr(args, "func", None)
    if func is not None:
        return func(args)
    return _release_default(args)


def _release_default(args: argparse.Namespace) -> int:
    """Default handler when no sub-subcommand is given."""
    print("Usage: gaia release SUBCOMMAND [options]")
    print("")
    print("  check [--functional]         -- run the full local/offline pre-release gate")
    print("  publish [version] [--dry-run] -- trigger the Layer 3 release pipeline")
    print("")
    print("Run 'gaia release --help' for more information.")
    return 0
