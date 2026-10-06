"""`gaia release publish` from a fresh worktree installs the locked Node
dependencies before any step needs them, and creates no tag, push or GitHub
release until the packed tree has passed the sandbox install the CI publish
job gates on.

Every subprocess is answered by a fake at `cli.release._run`, and the pack at
`cli._pack_helpers.pack_tarball`; nothing here spawns npm, node, git or gh.
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch

_BIN_DIR = Path(__file__).resolve().parents[2] / "bin"
if str(_BIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BIN_DIR))

from cli import release  # noqa: E402

_PREFLIGHT = {"name": "preconditions", "status": "PASS", "detail": "ok", "duration_ms": 1, "push_branch": "feat/x"}
_TESTS = {"name": "CI verdict", "status": "PASS", "detail": "ok", "duration_ms": 1}
_LOCK_OUT_OF_SYNC = "npm ERR! `npm ci` can only install packages when your package.json and package-lock.json are in sync"
_BOOTSTRAP_FAILED = "[FAIL] DB bootstrap: migration v61->v62 failed\nRESULT: FAIL"


def _tree(root, *, installed=False, locked=True):
    (root / "package.json").write_text(json.dumps({"version": "5.5.0-rc.4", "devDependencies": {"chalk": "^5.3.0"}}))
    if locked:
        (root / "package-lock.json").write_text("{}")
    (root / "scripts").mkdir()
    (root / "scripts" / "release-prepare.mjs").write_text("")
    (root / "bin").mkdir()
    (root / "bin" / "validate-sandbox.sh").write_text("")
    if installed:
        (root / "node_modules" / "chalk").mkdir(parents=True)
        (root / "node_modules" / "chalk" / "package.json").write_text("{}")
    return root


def _publish(root, *, npm_ci=(0, "", ""), sandbox=(0, "RESULT: PASS", ""), status=""):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[:2] == ["npm", "ci"]:
            return npm_ci
        if cmd[0] == "bash" and cmd[1].endswith("validate-sandbox.sh"):
            return sandbox
        if cmd[:2] == ["git", "status"]:
            return 0, status, ""
        return 0, "", ""

    packed = {"action": "created", "path": str(root / "gaia.tgz"), "details": "packed", "tarball": root / "gaia.tgz"}
    with patch("cli.release._run", side_effect=fake_run), \
         patch("cli.release.preflight_publish", return_value=_PREFLIGHT), \
         patch("cli.release.gate_tests", return_value=_TESTS), \
         patch("cli._pack_helpers.pack_tarball", return_value=packed):
        results = release.run_release_publish(root, "5.5.0-rc.5")
    return results, calls


def _index(calls, predicate):
    return next((i for i, cmd in enumerate(calls) if predicate(cmd)), None)


def _is_npm_ci(cmd):
    return cmd[:2] == ["npm", "ci"]


def _is_prepare(cmd):
    return cmd[0] == "node" and cmd[1].endswith("release-prepare.mjs")


def _is_sandbox(cmd):
    return cmd[0] == "bash" and cmd[1].endswith("validate-sandbox.sh")


def _is_irreversible(cmd):
    return cmd[:2] in (["git", "tag"], ["git", "push"]) or cmd[1:3] == ["release", "create"]


def test_fresh_tree_installs_the_locked_deps_without_scripts_before_release_prepare(tmp_path):
    """A worktree with no node_modules gets a frozen, script-free install before release:prepare imports chalk."""
    results, calls = _publish(_tree(tmp_path))

    assert all(r["status"] == "PASS" for r in results), results
    install = _index(calls, _is_npm_ci)
    assert install is not None, calls
    assert "--ignore-scripts" in calls[install]
    assert install < _index(calls, _is_prepare)


def test_installed_deps_are_not_reinstalled(tmp_path):
    """A tree whose declared dependencies are already installed is left as it is and still reports the deps step."""
    results, calls = _publish(_tree(tmp_path, installed=True))

    assert _index(calls, _is_npm_ci) is None
    assert results[0]["name"] == "node deps"
    assert results[0]["status"] == "PASS"
    assert _index(calls, _is_prepare) is not None


def test_failed_install_stops_before_release_prepare_naming_the_fix(tmp_path):
    """A lockfile npm refuses stops the publish before any bump, naming the command that fixes the tree."""
    results, calls = _publish(_tree(tmp_path), npm_ci=(1, "", _LOCK_OUT_OF_SYNC))

    assert results[-1]["status"] == "FAIL"
    assert "npm ci --ignore-scripts" in results[-1]["detail"]
    assert "in sync" in results[-1]["detail"]
    assert _index(calls, _is_prepare) is None


def test_missing_lockfile_fails_naming_it_without_installing(tmp_path):
    """Without package-lock.json no frozen install is possible, so the step fails naming the file instead of resolving afresh."""
    results, calls = _publish(_tree(tmp_path, locked=False))

    assert results[-1]["status"] == "FAIL"
    assert "package-lock.json" in results[-1]["detail"]
    assert _index(calls, _is_npm_ci) is None
    assert _index(calls, _is_prepare) is None


def test_sandbox_failure_creates_no_tag_push_or_release(tmp_path):
    """A packed tree the sandbox install rejects (the rc.4 bootstrap failure) never reaches tag, push or gh release."""
    results, calls = _publish(_tree(tmp_path, installed=True), sandbox=(1, _BOOTSTRAP_FAILED, ""))

    assert results[-1]["status"] == "FAIL"
    assert "v61->v62" in results[-1]["detail"]
    assert not [cmd for cmd in calls if _is_irreversible(cmd)], calls


def test_sandbox_proves_the_bumped_packed_tree_before_the_tag(tmp_path):
    """The sandbox installs the tarball packed after the bump, and the tag comes only after it passed."""
    results, calls = _publish(_tree(tmp_path, installed=True))

    assert all(r["status"] == "PASS" for r in results), results
    sandbox = _index(calls, _is_sandbox)
    assert sandbox is not None, calls
    assert calls[sandbox][calls[sandbox].index("--tarball") + 1] == str(tmp_path / "gaia.tgz")
    assert _index(calls, _is_prepare) < sandbox < _index(calls, _is_irreversible)


def test_sandbox_refuses_a_tree_the_tag_would_not_hold(tmp_path):
    """A change outside the version sources would be packed but not tagged, so the proof refuses it before the tag."""
    results, calls = _publish(_tree(tmp_path, installed=True), status=" M package.json\n?? notes.txt\n")

    assert results[-1]["status"] == "FAIL"
    assert "notes.txt" in results[-1]["detail"]
    assert _index(calls, _is_sandbox) is None
    assert not [cmd for cmd in calls if _is_irreversible(cmd)], calls


def test_dry_run_plan_puts_deps_first_and_the_sandbox_before_the_tag():
    """The preview a signer reads lists the deps step before release:prepare and the sandbox install before the tag."""
    names = [step["name"] for step in release.build_publish_plan("5.5.0-rc.5", gh="ghx", push_branch="feat/x")]

    assert names.index("node deps") < names.index("release:prepare")
    assert names.index("release:prepare") < names.index("sandbox install") < names.index("git tag")
