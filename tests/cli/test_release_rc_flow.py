"""A stable is promoted only from a published RC that passed the live smoke, and `rc` names the next candidate."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT / "bin") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "bin"))

from cli import release  # noqa: E402


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t.invalid", *args],
                          check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path, monkeypatch, _isolate_gaia_data_dir):
    """A checkout whose origin holds an annotated v5.7.2-rc.1 tag on its tip."""
    origin, work = tmp_path / "origin.git", tmp_path / "work"
    subprocess.run(["git", "init", "--bare", str(origin)], check=True, capture_output=True)
    subprocess.run(["git", "init", str(work)], check=True, capture_output=True)
    (work / "package.json").write_text(json.dumps({"name": "@jaguilar87/gaia", "version": "5.7.2-rc.1"}))
    (work / "main.py").write_text("print('rc')\n")
    _git(work, "add", ".")
    _git(work, "commit", "-m", "rc")
    _git(work, "tag", "-a", "v5.7.2-rc.1", "-m", "rc")
    _git(work, "remote", "add", "origin", str(origin))
    _git(work, "push", "origin", "HEAD:refs/heads/main", "refs/tags/v5.7.2-rc.1")
    return work


def _record(version: str) -> None:
    record = release.live_smoke_record(version)
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text("{}")


def test_a_stable_without_an_rc_tag_is_refused(repo):
    error = release._check_promoted_from_rc(repo, "5.8.0")
    assert "no v5.8.0-rc.N tag" in error


def test_an_rc_without_a_live_smoke_is_refused(repo):
    error = release._check_promoted_from_rc(repo, "5.7.2")
    assert "5.7.2-rc.1 has no passed live smoke" in error
    assert "gaia release smoke --live --version 5.7.2-rc.1" in error


def test_a_live_smoked_rc_whose_tree_head_keeps_is_promoted(repo):
    _record("5.7.2-rc.1")
    (repo / "package.json").write_text(json.dumps({"name": "@jaguilar87/gaia", "version": "5.7.2"}))
    _git(repo, "commit", "-am", "bump")
    assert release._check_promoted_from_rc(repo, "5.7.2") is None


def test_code_changed_after_the_rc_is_refused(repo):
    _record("5.7.2-rc.1")
    (repo / "main.py").write_text("print('untested')\n")
    _git(repo, "commit", "-am", "untested change")
    assert "main.py" in release._check_promoted_from_rc(repo, "5.7.2")


def test_a_prerelease_needs_no_rc(repo):
    assert release._check_promoted_from_rc(repo, "5.7.3-rc.1") is None


@pytest.mark.parametrize(("current", "tagged", "expected"), [
    ("5.7.2-rc.1", False, "5.7.2-rc.2"),
    ("5.7.2", False, "5.7.2-rc.1"),
    ("5.7.2", True, "5.7.3-rc.1"),
])
def test_rc_names_the_next_candidate(tmp_path, current, tagged, expected):
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    (tmp_path / "package.json").write_text(json.dumps({"version": current}))
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "x")
    if tagged:
        _git(tmp_path, "tag", f"v{current}")
    assert release.resolve_publish_version(tmp_path, "rc") == (expected, None)


@pytest.mark.skipif(shutil.which("node") is None, reason="the changelog bump is a node module")
def test_a_stable_folds_its_own_section_and_its_candidates_into_one(tmp_path):
    changelog = (
        "# Changelog\n\n## [Unreleased]\n\n## [5.7.2-rc.1] - 2026-11-07\n\n"
        "## [5.7.2] - 2026-11-07\n\n### Added\n\n- smoke\n\n## [5.7.1] - 2026-11-07\n\n- old\n"
    )
    script = (
        "import(process.argv[1]).then(m => process.stdout.write("
        "m.bumpChangelogText(process.argv[2], '5.7.2', '2026-11-08').text))"
    )
    module = (_REPO_ROOT / "scripts" / "changelog-bump.mjs").as_uri()
    out = subprocess.run(["node", "-e", script, module, changelog], check=True, capture_output=True, text=True).stdout
    assert out.count("## [5.7.2]") == 1
    assert "5.7.2-rc.1" not in out
    assert "- smoke" in out and "## [5.7.1]" in out
