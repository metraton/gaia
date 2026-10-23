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
    "git -C {repo} push --help",
    "git -C {repo} push -h",
    "cd {repo} && git push origin feat --help",
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


def test_content_a_remote_already_holds_is_free_for_any_target(over):
    git(over, "update-ref", "refs/remotes/origin/x", "HEAD")
    commit_files(over, {"b.py": "# one fact\nVALUE = 1\n"})
    detach(over)
    assert check(f"git -C {over} push origin HEAD:x", cwd=str(over)).allowed
    assert check(f"git -C {over} push origin HEAD:y", cwd=str(over)).allowed
    git(over, "update-ref", "-d", "refs/remotes/origin/x")
    assert not check(f"git -C {over} push origin HEAD:y", cwd=str(over)).allowed


ACCUMULATING = "feat/gaia-5-7"
PRE_GATE_BLOCK = "# one\n# two\n# three\n"


def accumulating_branch(tmp_path):
    """A pushed accumulating branch that already carries comment-heavy work from before the gate."""
    repo = make_change(tmp_path, BASE, {"old.py": PRE_GATE_BLOCK + interleave(60, 5)}, branch=ACCUMULATING)
    git(repo, "update-ref", f"refs/remotes/origin/{ACCUMULATING}", "HEAD")
    return repo


def push_accumulating(repo):
    return check(f"git -C {repo} push origin {ACCUMULATING}", cwd=str(repo))


@pytest.mark.parametrize("task,allowed", [
    ({"x.py": "# one fact\n" + code(80)}, True),
    ({"x.py": PRE_GATE_BLOCK + code(80)}, False),
])
def test_a_task_cherry_picked_after_others_landed_is_measured_on_its_own_lines(tmp_path, task, allowed):
    repo = accumulating_branch(tmp_path)
    git(repo, "checkout", "-q", "-b", "feat/x")
    picked = commit_files(repo, task, "task x")
    git(repo, "update-ref", "refs/remotes/origin/feat/x", "HEAD")
    git(repo, "checkout", "-q", ACCUMULATING)
    commit_files(repo, {"y.py": PRE_GATE_BLOCK + interleave(60, 5)}, "task y landed")
    git(repo, "update-ref", f"refs/remotes/origin/{ACCUMULATING}", "HEAD")
    git(repo, "cherry-pick", picked)
    verdict = push_accumulating(repo)
    assert verdict.allowed is allowed
    assert allowed or ("x.py:1-3" in verdict.reason and "y.py" not in verdict.reason)


@pytest.mark.parametrize("new_work,allowed", [
    ({}, True),
    ({"n.py": PRE_GATE_BLOCK + code(80)}, False),
])
def test_merging_main_into_the_accumulating_branch_counts_only_new_commits(tmp_path, new_work, allowed):
    repo = accumulating_branch(tmp_path)
    git(repo, "checkout", "-q", "main")
    commit_files(repo, {"a.py": PRE_GATE_BLOCK + interleave(60, 6), "m.py": "# --- M ---\n" + code(5)},
                 "main moves")
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(repo, "checkout", "-q", ACCUMULATING)
    if new_work:
        commit_files(repo, new_work, "new work")
    git(repo, "merge", "-q", "--no-edit", "main")
    verdict = push_accumulating(repo)
    assert verdict.allowed is allowed
    assert allowed or ("n.py:1-3" in verdict.reason and "a.py" not in verdict.reason)


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
