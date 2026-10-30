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


def _mutative(command: str, cwd: str | None = None) -> bool:
    detect_mutative_command.cache_clear()
    return detect_mutative_command(command, cwd=cwd).is_mutative


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


PR_OWN_REPO = "gh -R o/r pr create --head feat/x --base main --title t --body-file body.md"


def test_a_pr_on_the_checkouts_own_origin_is_free(repo):
    assert not _mutative(PR_OWN_REPO, cwd=repo)


def test_a_pr_with_no_checkout_to_compare_signs():
    assert _mutative(PR_OWN_REPO)


@pytest.mark.parametrize("command", [
    "gh -R O/R pr create --title t",
    "ghx pr create --repo=EXAMPLE.INVALID/O/R --title t",
    "gh pr edit 4 -R https://Example.Invalid/O/R.git --title t",
])
def test_a_repo_named_in_another_letter_case_is_the_checkouts_own_origin(repo, command):
    assert not _mutative(command, cwd=repo), command


def test_a_repo_differing_by_more_than_case_still_signs(repo):
    assert _mutative("gh -R O/R2 pr create --title t", cwd=repo)


@pytest.mark.parametrize("command", PR_ASKS)
def test_merging_or_retargeting_a_pr_still_asks(command):
    assert _mutative(command), command


def test_pr_create_is_free_at_the_bash_boundary():
    assert _validated_tier("gh pr create --title t --body b") == "T0"


# -- Wrappers inherit the exceptions of the CLI they are declared as ---------

@pytest.mark.parametrize("template", [
    f"{WRAPPER} -C {{repo}} pr create --repo o/r --head feat/x --title t",
    f"{WRAPPER} pr edit 42 --title t",
])
def test_declared_wrapper_inherits_the_pr_exception(declare_alias, repo, template):
    declare_alias(f"{WRAPPER}=gh")
    assert not _mutative(template.format(repo=repo)), template


def test_declared_wrapper_naming_a_repo_with_no_checkout_signs(declare_alias):
    declare_alias(f"{WRAPPER}=gh")
    assert _mutative(f"{WRAPPER} -C /work/tree pr create --repo o/r --head feat/x --title t")


@pytest.mark.parametrize("command", [
    f"{WRAPPER} pr merge 42 --squash",
    f"{WRAPPER} pr edit 42 --base main",
])
def test_declared_wrapper_still_asks_where_gh_asks(declare_alias, command):
    declare_alias(f"{WRAPPER}=gh")
    assert _mutative(command), command


def test_undeclared_wrapper_is_judged_by_the_change_request_shape():
    assert not _mutative(f"{WRAPPER} pr create --title t")
    assert _mutative(f"{WRAPPER} pr edit 42 --base main")
    assert _mutative(f"{WRAPPER} pr merge 42 --squash")


def test_an_alias_named_after_a_known_cli_keeps_that_cli_gates(declare_alias):
    declare_alias("kubectl=gh")
    assert _mutative("kubectl pr create --title t")


# -- git merge --ff-only -----------------------------------------------------

def test_ff_only_merge_inside_a_managed_worktree_is_free(managed_worktree):
    assert not _mutative(f"git -C {managed_worktree} merge --ff-only origin/main")


def test_ff_only_merge_of_another_branchs_ref_in_the_live_checkout_still_asks(repo):
    assert _mutative(f"git -C {repo} merge --ff-only origin/develop")


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
    "push origin feat/x:main",
    "push origin feat/x:feat/y",
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
    _git("-C", bare, "remote", "add", "origin", "https://example.invalid/o/r.git")
    assert not _mutative(f"git -C {bare} push origin develop")
    assert _mutative(f"git -C {bare} push origin master")


def test_a_remote_the_repository_does_not_configure_asks(tmp_path):
    bare = _init_repo(tmp_path / "no-remote")
    assert _mutative(f"git -C {bare} push origin develop")


@pytest.mark.parametrize("push", [
    "push origin refs/tags/v1.4.0",
    "push origin refs/heads/main",
])
def test_a_tag_or_the_default_spelled_as_a_ref_still_asks(repo, push):
    assert _mutative(f"git -C {repo} {push}"), push


def test_a_feature_branch_spelled_as_a_ref_is_free(repo):
    assert not _mutative(f"git -C {repo} push origin refs/heads/feat/x")


@pytest.mark.parametrize("refspec", ["feat/x:feat/x", "feat/x:refs/heads/feat/x"])
def test_a_refspec_onto_the_same_feature_branch_is_free(repo, refspec):
    assert not _mutative(f"git -C {repo} push origin {refspec}")


# -- Alias inheritance is limited to names Gaia has no rule for ---------------

@pytest.mark.parametrize("name", [
    "shred", "truncate", "dd", "wipefs", "srm", "mkfs.ext4", "ssh", "xargs",
    "watch", "flock", "sudo", "env", "bash", "rm", "curl",
])
def test_a_wrapper_named_after_a_governed_program_keeps_its_gate(declare_alias, name):
    declare_alias(f"{name}=gh")
    assert _mutative(f"{name} pr create --title t"), name


# -- An environment or global option can redirect what git acts on -----------

@pytest.mark.parametrize("prefix", [
    "GIT_DIR=/elsewhere/.git ",
    "GIT_WORK_TREE=/elsewhere ",
    "GIT_CONFIG_GLOBAL=/elsewhere/config ",
    "HOME=/elsewhere ",
    "env GIT_DIR=/elsewhere/.git ",
])
def test_a_push_under_an_environment_prefix_still_asks(repo, prefix):
    assert _mutative(f"{prefix}git -C {repo} push origin feat/x"), prefix


@pytest.mark.parametrize("globals_", [
    "--git-dir=/elsewhere/.git -C {path}",
    "-C {path} --git-dir=/elsewhere/.git",
    "-C {path} --work-tree /elsewhere",
    "-c remote.origin.push=refs/heads/feat/x:refs/heads/main -C {path}",
    "-C {path} -c push.default=upstream",
])
def test_a_push_under_a_redirecting_global_option_still_asks(repo, globals_):
    assert _mutative(f"git {globals_.format(path=repo)} push origin feat/x"), globals_


@pytest.mark.parametrize("prefix", ["GIT_DIR=/elsewhere/.git ", "HOME=/elsewhere "])
def test_a_ff_only_merge_under_an_environment_prefix_still_asks(managed_worktree, prefix):
    assert _mutative(f"{prefix}git -C {managed_worktree} merge --ff-only origin/main")


@pytest.mark.parametrize("globals_", [
    "-C {path} --git-dir=/elsewhere/.git",
    "-c core.hooksPath=/elsewhere -C {path}",
])
def test_a_ff_only_merge_under_a_redirecting_global_option_still_asks(managed_worktree, globals_):
    command = f"git {globals_.format(path=managed_worktree)} merge --ff-only origin/main"
    assert _mutative(command), command


# -- Push configuration can send a named branch somewhere else ---------------

def _config(repo: str, key: str, value: str) -> None:
    _git("-C", repo, "config", key, value)


def test_a_configured_remote_push_refspec_still_asks(repo):
    _config(repo, "remote.origin.push", "refs/heads/feat/x:refs/heads/main")
    assert _mutative(f"git -C {repo} push origin feat/x")


def test_a_mirror_remote_still_asks(repo):
    _config(repo, "remote.origin.mirror", "true")
    assert _mutative(f"git -C {repo} push origin feat/x")


@pytest.mark.parametrize("mode", ["upstream", "tracking"])
def test_upstream_mode_onto_the_default_branch_still_asks(repo, mode):
    _config(repo, "push.default", mode)
    _config(repo, "branch.feat/x.remote", "origin")
    _config(repo, "branch.feat/x.merge", "refs/heads/main")
    assert _mutative(f"git -C {repo} push origin feat/x")


def test_upstream_mode_without_an_upstream_still_asks(repo):
    _config(repo, "push.default", "upstream")
    assert _mutative(f"git -C {repo} push origin feat/x")


def test_upstream_mode_onto_the_same_feature_branch_is_free(repo):
    _config(repo, "push.default", "upstream")
    _config(repo, "branch.feat/x.remote", "origin")
    _config(repo, "branch.feat/x.merge", "refs/heads/feat/x")
    assert not _mutative(f"git -C {repo} push origin feat/x")


def test_an_unreadable_config_still_asks(repo):
    Path(repo, ".git", "config").write_text("[remote \"origin\"\n\tbroken")
    assert _mutative(f"git -C {repo} push origin feat/x")


# -- Without a known repository the push is not resolved ---------------------

def test_a_push_with_no_path_and_no_cwd_still_asks(repo, monkeypatch):
    monkeypatch.chdir(repo)
    assert _mutative("git push origin feat/x")


def test_a_push_with_a_relative_path_and_no_cwd_still_asks(repo, monkeypatch):
    monkeypatch.chdir(Path(repo).parent)
    assert _mutative("git -C repo push origin feat/x")


def test_a_push_resolved_against_the_given_cwd_is_free(repo):
    detect_mutative_command.cache_clear()
    assert not detect_mutative_command("git push origin feat/x", cwd=repo).is_mutative
