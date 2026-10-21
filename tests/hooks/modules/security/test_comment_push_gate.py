"""A branch push over the comment limit is refused before any approval exists; other pushes pass."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "hooks"))

from modules.security.comment_push_gate import check  # noqa: E402
from modules.tools.bash_validator import BashValidator  # noqa: E402
from tests.fixtures.comment_repo import code, commit_files, git, interleave, make_change  # noqa: E402

BASE = {"a.py": "VALUE = 0\n"}


@pytest.fixture
def over(tmp_path):
    return make_change(tmp_path, BASE, {"a.py": interleave(40, 6)})


@pytest.fixture
def under(tmp_path):
    return make_change(tmp_path, BASE, {"a.py": interleave(40, 1)})


@pytest.mark.parametrize("form", [
    "git -C {repo} push -u origin feat",
    "git -C {repo} push origin HEAD",
    "git -C {repo} push origin +feat:feat",
    "cd {repo} && git push origin feat",
    "git -C {repo} push",
])
def test_a_branch_push_over_the_limit_is_refused_with_the_offenders(over, form):
    verdict = check(form.format(repo=over), cwd=str(over))
    assert not verdict.allowed
    assert "[COMMENT_LIMIT]" in verdict.reason and "a.py:1: # fact 0" in verdict.reason
    assert "not approvable" in verdict.reason and verdict.warning is None


@pytest.mark.parametrize("content,says,silent", [
    (code(300) + "# --- Helpers ---\n", "section banner", "adds 1 net"),
    ("# why\n# why\n# why\n" + code(300), "comment block", "net comment and docstring"),
    (interleave(40, 6), "net comment and docstring lines over", "section banner"),
])
def test_the_refusal_names_the_reason_that_applies(tmp_path, content, says, silent):
    repo = make_change(tmp_path, BASE, {"a.py": content})
    verdict = check(f"git -C {repo} push origin feat")
    assert not verdict.allowed
    assert says in verdict.reason and silent not in verdict.reason


def test_a_branch_push_under_the_limit_passes(under):
    assert check(f"git -C {under} push -u origin feat").allowed


@pytest.mark.parametrize("form", [
    "git -C {repo} push origin v1",
    "git -C {repo} push origin --tags",
    "git -C {repo} push origin --delete feat",
    "git -C {repo} push origin :feat",
    "git -C {repo} push origin refs/tags/v1",
    "git -C {repo} push --dry-run origin feat",
    "git -C {repo} status",
    "echo push {repo}",
])
def test_pushes_with_nothing_to_compare_and_other_commands_are_not_gated(over, form):
    git(over, "tag", "v1")
    assert check(form.format(repo=over), cwd=str(over)).allowed


def detach(repo):
    git(repo, "checkout", "-q", "--detach")
    return git(repo, "rev-parse", "HEAD")


@pytest.mark.parametrize("form", [
    "git -C {repo} push origin HEAD:x",
    "git -C {repo} push origin {sha}:refs/heads/x",
    "git -C {repo} push origin {sha}:x",
])
def test_a_detached_or_sha_push_over_the_limit_is_refused(over, form):
    sha = detach(over)
    verdict = check(form.format(repo=over, sha=sha), cwd=str(over))
    assert not verdict.allowed and "[COMMENT_LIMIT]" in verdict.reason


def test_a_sha_push_from_a_checked_out_branch_is_refused(over):
    sha = git(over, "rev-parse", "HEAD")
    assert not check(f"git -C {over} push origin {sha}:refs/heads/x").allowed


@pytest.mark.parametrize("form", [
    "git -C {repo} push origin HEAD:x",
    "git -C {repo} push origin {sha}:refs/heads/x",
])
def test_a_detached_or_sha_push_under_the_limit_passes(under, form):
    sha = detach(under)
    assert check(form.format(repo=under, sha=sha), cwd=str(under)).allowed


def test_an_existing_target_is_measured_against_its_remote_ref(over):
    git(over, "update-ref", "refs/remotes/origin/x", "HEAD")
    commit_files(over, {"b.py": "# one fact\nVALUE = 1\n"})
    detach(over)
    assert check(f"git -C {over} push origin HEAD:x", cwd=str(over)).allowed
    assert not check(f"git -C {over} push origin HEAD:y", cwd=str(over)).allowed


def test_a_counter_failure_allows_the_push_with_a_visible_warning(over, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("git exploded")

    monkeypatch.setattr("gaia.comment_lint.measure", broken)
    verdict = check(f"git -C {over} push origin feat")
    assert verdict.allowed and verdict.reason is None
    assert verdict.warning.startswith("[COMMENT_GATE_SKIPPED]") and "git exploded" in verdict.warning


def test_the_validator_refuses_without_an_approval_and_carries_the_warning(over, monkeypatch):
    validator = BashValidator()
    result = validator.validate(f"git -C {over} push -u origin feat", is_subagent=True)
    assert not result.allowed and "[COMMENT_LIMIT]" in result.reason
    assert result.approval_id is None

    def broken(*args, **kwargs):
        raise RuntimeError("counter down")

    monkeypatch.setattr("gaia.comment_lint.measure", broken)
    validator.validate(f"git -C {over} push -u origin feat", is_subagent=True)
    assert validator.last_warning.startswith("[COMMENT_GATE_SKIPPED]")
