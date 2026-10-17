"""Opening or editing a PR and pushing a feature branch need no signature.

The act with effect is the merge into the default branch, which stays gated
together with every push that rewrites, deletes or targets the default branch.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[4]
for entry in (str(_REPO_ROOT), str(_REPO_ROOT / "hooks")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from modules.security import tiers as tiers_module  # noqa: E402
from modules.security.mutative_verbs import detect_mutative_command  # noqa: E402
from modules.security.tiers import SecurityTier  # noqa: E402
from modules.tools.bash_validator import BashValidator  # noqa: E402

WRAPPER = "ghwrap"


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "gaia-data"))
    monkeypatch.delenv("GAIA_CLI_ALIASES", raising=False)
    detect_mutative_command.cache_clear()
    tiers_module._classify_command_tier_cached.cache_clear()
    yield
    detect_mutative_command.cache_clear()
    tiers_module._classify_command_tier_cached.cache_clear()


def _git(*args: str) -> None:
    subprocess.run(["git", *args], check=True, capture_output=True)


def _init_repo(path: Path) -> str:
    """A repository holding the local branches main, develop, feat/x and feat/y and the tag v1.4.0."""
    _git("init", "-q", "-b", "main", str(path))
    _git("-C", str(path), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
         "-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty", "-m", "init")
    for branch in ("develop", "feat/x", "feat/y"):
        _git("-C", str(path), "branch", branch)
    _git("-C", str(path), "tag", "v1.4.0")
    return str(path)


@pytest.fixture
def repo(tmp_path):
    """A repository whose origin's HEAD points at `develop`."""
    path = _init_repo(tmp_path / "repo")
    _git("-C", path, "remote", "add", "origin", "https://example.invalid/o/r.git")
    _git("-C", path, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/develop")
    return path


@pytest.fixture
def managed_worktree(tmp_path):
    from gaia.paths import ensure_layout, worktrees_dir

    ensure_layout()
    wt = Path(os.path.realpath(str(worktrees_dir()))) / "wt-a"
    wt.mkdir(parents=True, exist_ok=True)
    return str(wt)


def _mutative(command: str) -> bool:
    detect_mutative_command.cache_clear()
    return detect_mutative_command(command).is_mutative


def _validated_tier(command: str) -> str:
    detect_mutative_command.cache_clear()
    tiers_module._classify_command_tier_cached.cache_clear()
    result = BashValidator().validate(command, is_subagent=True, session_id="s-relief",
                                      agent_type="developer")
    if result.allowed and result.tier == SecurityTier.T0_READ_ONLY:
        return "T0"
    return "T3" if not result.allowed else str(result.tier)


# -- PRs ---------------------------------------------------------------------

PR_FREE = [
    "gh pr create --title t --body b",
    "gh -R o/r pr create --head feat/x --base main --title t --body-file body.md",
    "gh pr edit 42 --title t --add-label ready",
    "gh pr edit 42 --body 'apply the deploy'",
]
PR_ASKS = [
    "gh pr merge 42 --squash",
    "gh pr edit 42 --base main",
    "gh pr edit 42 --base=main",
    "gh pr edit 42 -B main",
    "gh pr edit 42 --title t -Bmain",
]


@pytest.mark.parametrize("command", PR_FREE)
def test_opening_or_editing_a_pr_is_free(command):
    assert not _mutative(command), command


@pytest.mark.parametrize("command", PR_ASKS)
def test_merging_or_retargeting_a_pr_still_asks(command):
    assert _mutative(command), command


def test_pr_create_is_free_at_the_bash_boundary():
    assert _validated_tier("gh pr create --title t --body b") == "T0"


# -- Wrappers inherit the exceptions of the CLI they are declared as ---------

@pytest.mark.parametrize("command", [
    f"{WRAPPER} -C /work/tree pr create --repo o/r --head feat/x --title t",
    f"{WRAPPER} pr edit 42 --title t",
])
def test_declared_wrapper_inherits_the_pr_exception(monkeypatch, command):
    monkeypatch.setenv("GAIA_CLI_ALIASES", f"{WRAPPER}=gh")
    assert not _mutative(command), command


@pytest.mark.parametrize("command", [
    f"{WRAPPER} pr merge 42 --squash",
    f"{WRAPPER} pr edit 42 --base main",
])
def test_declared_wrapper_still_asks_where_gh_asks(monkeypatch, command):
    monkeypatch.setenv("GAIA_CLI_ALIASES", f"{WRAPPER}=gh")
    assert _mutative(command), command


def test_undeclared_wrapper_inherits_nothing():
    assert _mutative(f"{WRAPPER} pr create --title t")


def test_an_alias_named_after_a_known_cli_keeps_that_cli_gates(monkeypatch):
    monkeypatch.setenv("GAIA_CLI_ALIASES", "kubectl=gh")
    assert _mutative("kubectl pr create --title t")


# -- git merge --ff-only -----------------------------------------------------

def test_ff_only_merge_inside_a_managed_worktree_is_free(managed_worktree):
    assert not _mutative(f"git -C {managed_worktree} merge --ff-only origin/main")


def test_ff_only_merge_in_the_live_checkout_still_asks(repo):
    assert _mutative(f"git -C {repo} merge --ff-only origin/main")


@pytest.mark.parametrize("merge", [
    "merge origin/main",
    "merge --no-ff origin/main",
    "merge --squash origin/main",
    "merge --ff origin/main",
    "merge --ff-only -X theirs origin/main",
    "merge --ff-only origin/main origin/other",
])
def test_any_other_merge_in_a_managed_worktree_still_asks(managed_worktree, merge):
    assert _mutative(f"git -C {managed_worktree} {merge}")


def test_ff_only_merge_without_a_named_path_still_asks():
    assert _mutative("git merge --ff-only origin/main")


# -- git push ----------------------------------------------------------------

@pytest.mark.parametrize("push", [
    "push origin feat/x",
    "push -u origin feat/x",
    "push --set-upstream origin feat/x",
    "push origin feat/x -u",
])
def test_push_of_a_named_feature_branch_is_free(repo, push):
    assert not _mutative(f"git -C {repo} {push}")


def test_push_of_a_feature_branch_is_free_at_the_bash_boundary(repo):
    assert _validated_tier(f"git -C {repo} push -u origin feat/x") == "T0"


@pytest.mark.parametrize("push", [
    "push",
    "push origin",
    "push origin main",
    "push origin master",
    "push origin Main",
    "push origin HEAD",
    "push origin refs/heads/main",
    "push origin develop",
    "push -f origin feat/x",
    "push --force origin feat/x",
    "push --force-with-lease origin feat/x",
    "push --force-with-lease=feat/x origin feat/x",
    "push --delete origin feat/x",
    "push -d origin feat/x",
    "push --mirror origin",
    "push --all origin",
    "push --tags origin",
    "push origin +feat/x",
    "push origin feat/x:feat/x",
    "push origin :feat/x",
    "push origin feat/x feat/y",
    "push https://example.invalid/o/r.git feat/x",
    "push origin v1.4.0",
    "push origin feat/not-a-local-branch",
])
def test_push_that_rewrites_deletes_or_targets_the_default_still_asks(repo, push):
    assert _mutative(f"git -C {repo} {push}"), push


def test_unresolved_remote_head_counts_only_main_and_master_as_default(tmp_path):
    bare = _init_repo(tmp_path / "no-head")
    assert not _mutative(f"git -C {bare} push origin develop")
    assert _mutative(f"git -C {bare} push origin master")
