"""Bringing a checkout up to date is free; this relief adds no signature anywhere."""

from __future__ import annotations

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


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "gaia-data"))
    monkeypatch.delenv("GAIA_CLI_ALIASES", raising=False)
    for key, value in (("NAME", "t"), ("EMAIL", "t@example.invalid")):
        monkeypatch.setenv(f"GIT_AUTHOR_{key}", value)
        monkeypatch.setenv(f"GIT_COMMITTER_{key}", value)
    detect_mutative_command.cache_clear()
    tiers_module._classify_command_tier_cached.cache_clear()
    yield
    detect_mutative_command.cache_clear()
    tiers_module._classify_command_tier_cached.cache_clear()


@pytest.fixture
def base(tmp_path) -> str:
    """A clone on main: feat tracks origin/feat, side tracks origin/main, tag v1."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    clone = tmp_path / "base"
    subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True, capture_output=True)
    _git(clone, "checkout", "-q", "-b", "main")
    (clone / "f.txt").write_text("one\n")
    _git(clone, "add", "f.txt")
    _git(clone, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "init")
    _git(clone, "push", "-q", "-u", "origin", "main")
    _git(clone, "branch", "feat")
    _git(clone, "push", "-q", "-u", "origin", "feat")
    _git(clone, "branch", "side", "origin/main")
    _git(clone, "tag", "v1")
    return str(clone)


def _on(base: str, branch: str) -> None:
    _git(Path(base), "checkout", "-q", branch)


def _dirty(base: str) -> None:
    (Path(base) / "f.txt").write_text("changed\n")


def _asks(command: str, cwd: str | None = None) -> bool:
    return detect_mutative_command(command, cwd=cwd).is_mutative


def _boundary_tier(command: str) -> str:
    result = BashValidator().validate(
        command, is_subagent=True, session_id="s-sync", agent_type="gitops-operator",
    )
    return "T0" if result.allowed and result.tier == SecurityTier.T0_READ_ONLY else "T3"


def test_trigger_ff_only_merge_is_free_with_the_checkout_on_main(base):
    command = f"git -C {base} merge --ff-only origin/main"
    assert not _asks(command)
    assert _boundary_tier(command) == "T0"


def test_trigger_ff_only_merge_asks_with_the_checkout_on_another_branch(base):
    _on(base, "feat")
    command = f"git -C {base} merge --ff-only origin/main"
    assert _asks(command)
    assert _boundary_tier(command) == "T3"


def test_a_verdict_is_read_fresh_when_the_branch_changes_between_calls(base):
    command = f"git -C {base} merge --ff-only origin/main"
    assert not _asks(command)
    _on(base, "feat")
    assert _asks(command)


def test_ff_only_merge_of_the_current_branch_is_free_on_a_feature_branch(base):
    _on(base, "feat")
    assert not _asks(f"git -C {base} merge --ff-only origin/feat")


def test_ff_only_merge_in_the_callers_directory_is_free(base):
    assert not _asks("git merge --ff-only origin/main", cwd=base)


@pytest.mark.parametrize("command", [
    "fetch",
    "fetch origin",
    "fetch --prune origin",
    "pull --ff-only",
    "pull --ff-only origin main",
    "switch feat",
    "checkout feat",
    "checkout v1",
    "switch -c fresh",
    "checkout -b fresh",
    "restore --staged f.txt",
])
def test_syncs_and_clean_branch_switches_are_free(base, command):
    assert not _asks(f"git -C {base} {command}")


@pytest.mark.parametrize("command", ["switch feat", "checkout feat"])
def test_a_switch_to_a_branch_is_free_in_the_callers_directory(base, command):
    assert not _asks(f"git {command}", cwd=base)


def test_ff_only_merge_of_another_branchs_ref_asks(base):
    assert _asks(f"git -C {base} merge --ff-only origin/feat")


def test_ff_only_merge_of_a_local_branch_asks(base):
    _on(base, "feat")
    assert _asks(f"git -C {base} merge --ff-only main")


def test_ff_only_merge_on_a_detached_head_asks(base):
    _git(Path(base), "checkout", "-q", "--detach")
    assert _asks(f"git -C {base} merge --ff-only origin/main")


@pytest.mark.parametrize("merge", [
    "merge origin/main",
    "merge --no-ff origin/main",
    "merge --squash origin/main",
    "merge --ff origin/main",
    "merge --ff-only -X theirs origin/main",
    "merge --ff-only origin/main origin/feat",
    "merge --ff-only nowhere/main",
])
def test_merges_that_can_create_commits_ask(base, merge):
    assert _asks(f"git -C {base} {merge}")


@pytest.mark.parametrize("command", [
    "checkout -f feat",
    "checkout -B feat",
    "switch -C feat",
    "switch -f feat",
    "switch --force feat",
    "rebase origin/main",
    "reset --hard",
    "reset --hard origin/main",
])
def test_rewriting_or_forcing_commands_ask(base, command):
    assert _asks(f"git -C {base} {command}")


@pytest.mark.parametrize("command", [
    "git merge --ff-only origin/main",
    "git -C . merge --ff-only origin/main",
])
def test_an_unknown_repository_fails_closed(command):
    assert _asks(command)


def test_a_path_that_is_not_a_repository_fails_closed(tmp_path):
    assert _asks(f"git -C {tmp_path} merge --ff-only origin/main")


@pytest.mark.parametrize("command", [
    "GIT_DIR=/elsewhere git -C {base} merge --ff-only origin/main",
    "env GIT_WORK_TREE=/elsewhere git -C {base} merge --ff-only origin/main",
    "git -C {base} -c merge.ff=false merge --ff-only origin/main",
    "git --git-dir=/elsewhere merge --ff-only origin/main",
])
def test_a_prefix_or_extra_global_option_keeps_the_merge_signed(base, command):
    assert _asks(command.format(base=base))


@pytest.mark.parametrize("command", [
    "pull",
    "pull --rebase",
    "pull origin feat",
    "checkout -- f.txt",
    "checkout .",
    "restore f.txt",
    "switch --discard-changes feat",
    "switch nowhere",
])
def test_commands_outside_the_relief_keep_their_verdict(base, command):
    assert not _asks(f"git -C {base} {command}")


@pytest.mark.parametrize("verb", ["switch", "checkout"])
def test_a_dirty_tree_switch_keeps_its_verdict(base, verb):
    _dirty(base)
    assert not _asks(f"git -C {base} {verb} feat")
