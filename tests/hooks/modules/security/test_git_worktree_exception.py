#!/usr/bin/env python3
"""Tests for the `git worktree` family classifier and its recycling exception.

The economy the classifier used to encode was inverted: `git worktree add` fell
through to safe-by-elimination (its verb is deliberately absent from
MUTATIVE_VERBS) while `git worktree remove` was gated only because the generic
"remove" token happens to live in that table.  Creating a worktree was free and
discharging the obligation it contracted cost an approval.

What this pins:
  * add / move            -- anchored MUTATIVE by an explicit model
  * remove                -- MUTATIVE, recognized as `worktree remove` rather
                             than by the incidental generic-token match
  * remove inside the root -- downgraded to T0 (recycling managed state)
  * ADVERSARIAL           -- the same removal aimed at a real repository outside
                             the root still demands approval; without this the
                             calibration fix would be a hole
  * the exception is anchored to the RUNTIME-resolved root (GAIA_DATA_DIR
    honoured, realpath applied), not to a string prefix
  * no other classification drops a tier as a side effect

Covered surfaces:
  * predicate       -- mutative_verbs._git_worktree_recycles_only_managed_root
  * classifier lane -- mutative_verbs._check_git_worktree via detect_mutative_command
  * end-to-end      -- modules.tools.bash_validator.BashValidator.validate
"""

import os
import sys
import pytest
from pathlib import Path

HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
REPO_ROOT = Path(__file__).parent.parent.parent.parent.parent
sys.path.insert(0, str(HOOKS_DIR))
sys.path.insert(0, str(REPO_ROOT))  # for the `gaia` package (resolver)

from modules.security.mutative_verbs import (
    detect_mutative_command,
    _gaia_worktrees_root,
    _git_worktree_positionals,
    _git_worktree_has_force_signal,
    _git_worktree_recycles_only_managed_root,
)
from modules.tools.bash_validator import BashValidator
from modules.security.tiers import SecurityTier


@pytest.fixture
def worktrees(monkeypatch, tmp_path):
    """Isolated GAIA_DATA_DIR with a populated worktrees tree.

    Returns a dict with the realpath'd root plus the fabricated paths the
    containment check has to reject.  Creates:
      worktrees/wt-a          (a managed worktree)
      worktrees/escape -> OUTSIDE   (symlink leaving the root)
      <root>-evil/            (sibling sharing the root's string prefix)
      real-repo/wt-a          (a worktree inside a real repository)
    """
    data = tmp_path / "gaia-data"
    monkeypatch.setenv("GAIA_DATA_DIR", str(data))
    from gaia.paths import ensure_layout, worktrees_dir
    ensure_layout()
    root = Path(os.path.realpath(str(worktrees_dir())))
    root.mkdir(parents=True, exist_ok=True)
    (root / "wt-a").mkdir(exist_ok=True)

    outside = tmp_path / "outside"
    outside.mkdir(exist_ok=True)
    escape = root / "escape"
    if not escape.exists():
        escape.symlink_to(outside)

    # A sibling directory whose absolute path starts with the root's text.
    prefix_sibling = Path(str(root) + "-evil")
    prefix_sibling.mkdir(exist_ok=True)

    # A real repository, with a worktree-shaped directory inside it.
    real_repo = tmp_path / "real-repo"
    (real_repo / ".git").mkdir(parents=True, exist_ok=True)
    (real_repo / "wt-a").mkdir(exist_ok=True)

    detect_mutative_command.cache_clear()
    return {
        "root": str(root),
        "wt": str(root / "wt-a"),
        "escape": str(escape),
        "prefix_sibling": str(prefix_sibling),
        "real_repo": str(real_repo),
        "real_repo_wt": str(real_repo / "wt-a"),
    }


@pytest.fixture
def validator():
    return BashValidator()


def _tier(validator, cmd):
    """End-to-end classification via the real bash_validator pipeline.

    Returns one of: 'T0', 'T3', 'BLOCKED'.
    """
    detect_mutative_command.cache_clear()
    r = validator.validate(cmd)
    if not r.allowed:
        if r.tier == SecurityTier.T3_BLOCKED and r.block_response is None:
            return "BLOCKED"  # permanent floor block (exit 2, never approvable)
        return "T3"           # approvable (ask)
    if r.tier == SecurityTier.T0_READ_ONLY:
        return "T0"
    return str(r.tier)


def _detect(cmd):
    detect_mutative_command.cache_clear()
    return detect_mutative_command(cmd)


# ---------------------------------------------------------------------------
# (a) Recycling inside the managed root classifies without approval
# ---------------------------------------------------------------------------

def test_remove_inside_root_is_t0(worktrees, validator):
    assert _tier(validator, f"git worktree remove {worktrees['wt']}") == "T0"


def test_remove_inside_root_predicate_holds(worktrees):
    tokens = ("git", "worktree", "remove", worktrees["wt"])
    assert _git_worktree_recycles_only_managed_root(tokens) is True


def test_remove_inside_root_with_dash_c_repo_is_t0(worktrees, validator):
    """`git -C <repo>` is the canonical invocation form; the absorbed repo path
    must not be mistaken for a removal target."""
    cmd = f"git -C {worktrees['real_repo']} worktree remove {worktrees['wt']}"
    assert _tier(validator, cmd) == "T0"


def test_remove_inside_root_is_not_mutative(worktrees):
    result = _detect(f"git worktree remove {worktrees['wt']}")
    assert result.is_mutative is False
    assert result.verb == "worktree remove"


# ---------------------------------------------------------------------------
# (b) Creating a worktree is the act that contracts the cleanup obligation
# ---------------------------------------------------------------------------

def test_add_is_mutative(worktrees, validator):
    assert _tier(validator, f"git worktree add {worktrees['wt']}") == "T3"


def test_add_is_not_unknown_and_names_the_obligation(worktrees):
    """It must no longer be an unrecognized, harmless-by-elimination verb."""
    result = _detect(f"git worktree add {worktrees['wt']} -b feature")
    assert result.is_mutative is True
    assert result.verb == "worktree add"
    assert result.confidence == "high"
    assert "obligation" in result.reason.lower()


def test_add_inside_the_managed_root_is_still_mutative(worktrees, validator):
    """The recycling exception covers removal only -- creation is never exempt,
    wherever the new worktree is placed."""
    inside = os.path.join(worktrees["root"], "wt-new")
    assert _tier(validator, f"git worktree add {inside}") == "T3"


# ---------------------------------------------------------------------------
# (c) Removal is recognized by an explicit model, not by a generic token
# ---------------------------------------------------------------------------

def test_remove_outside_root_is_recognized_as_worktree_remove(worktrees):
    """Before, this was gated only because "remove" sits in MUTATIVE_VERBS.
    The verb reported now names the modelled operation."""
    result = _detect(f"git worktree remove {worktrees['real_repo_wt']}")
    assert result.is_mutative is True
    assert result.verb == "worktree remove"


def test_move_is_recognized_as_worktree_move(worktrees):
    result = _detect(
        f"git worktree move {worktrees['wt']} {worktrees['real_repo_wt']}"
    )
    assert result.is_mutative is True
    assert result.verb == "worktree move"


# ---------------------------------------------------------------------------
# (d) ADVERSARIAL -- same shape, aimed inside a real repo, still gated
# ---------------------------------------------------------------------------

def test_remove_inside_a_real_repo_still_requires_approval(worktrees, validator):
    cmd = f"git worktree remove {worktrees['real_repo_wt']}"
    assert _tier(validator, cmd) == "T3"


def test_remove_inside_this_repository_still_requires_approval(validator, worktrees):
    """The live Gaia checkout is a real repository outside the managed root."""
    cmd = f"git worktree remove {REPO_ROOT / 'wt-a'}"
    assert _tier(validator, cmd) == "T3"


def test_remove_of_a_real_repo_predicate_declines(worktrees):
    tokens = ("git", "worktree", "remove", worktrees["real_repo_wt"])
    assert _git_worktree_recycles_only_managed_root(tokens) is False


# ---------------------------------------------------------------------------
# Rubric (1): anchored to the runtime-resolved root, not to a string prefix
# ---------------------------------------------------------------------------

def test_root_honours_the_data_dir_override(worktrees):
    assert _gaia_worktrees_root() == worktrees["root"]
    assert str(Path(worktrees["root"]).parent).endswith("gaia-data")


def test_symlink_escaping_the_root_is_rejected(worktrees, validator):
    """A path spelled inside the root whose realpath leaves it does not qualify."""
    target = os.path.join(worktrees["escape"], "wt-a")
    assert _git_worktree_recycles_only_managed_root(
        ("git", "worktree", "remove", target)
    ) is False
    assert _tier(validator, f"git worktree remove {target}") == "T3"


def test_parent_traversal_is_rejected(worktrees, validator):
    target = os.path.join(worktrees["root"], "..", "..", "real-repo", "wt-a")
    assert _git_worktree_recycles_only_managed_root(
        ("git", "worktree", "remove", target)
    ) is False
    assert _tier(validator, f"git worktree remove {target}") == "T3"


def test_sibling_sharing_the_root_string_prefix_is_rejected(worktrees, validator):
    """`<root>-evil/wt` satisfies a naive startswith(root) and must not qualify."""
    target = os.path.join(worktrees["prefix_sibling"], "wt-a")
    assert target.startswith(worktrees["root"])  # the trap being closed
    assert _git_worktree_recycles_only_managed_root(
        ("git", "worktree", "remove", target)
    ) is False
    assert _tier(validator, f"git worktree remove {target}") == "T3"


def test_the_root_itself_is_not_a_worktree(worktrees, validator):
    assert _git_worktree_recycles_only_managed_root(
        ("git", "worktree", "remove", worktrees["root"])
    ) is False
    assert _tier(validator, f"git worktree remove {worktrees['root']}") == "T3"


def test_relative_target_is_rejected(worktrees, validator):
    """`git -C <repo>` resolves a relative target against the repo, not the
    hook's cwd, so its real destination is unknowable here."""
    assert _git_worktree_recycles_only_managed_root(
        ("git", "worktree", "remove", "wt-a")
    ) is False
    assert _tier(validator, "git worktree remove wt-a") == "T3"


def test_glob_target_is_rejected(worktrees, validator):
    target = os.path.join(worktrees["root"], "wt-*")
    assert _git_worktree_recycles_only_managed_root(
        ("git", "worktree", "remove", target)
    ) is False
    assert _tier(validator, f"git worktree remove {target}") == "T3"


def test_missing_target_is_rejected(worktrees):
    assert _git_worktree_recycles_only_managed_root(
        ("git", "worktree", "remove")
    ) is False


def test_one_outside_target_disqualifies_the_whole_command(worktrees):
    tokens = (
        "git", "worktree", "remove",
        worktrees["wt"], worktrees["real_repo_wt"],
    )
    assert _git_worktree_recycles_only_managed_root(tokens) is False


def test_unresolvable_root_declines(worktrees, monkeypatch):
    """Fail-closed: no root, no exception."""
    import modules.security.mutative_verbs as mv
    monkeypatch.setattr(mv, "_gaia_worktrees_root", lambda: None)
    assert mv._git_worktree_recycles_only_managed_root(
        ("git", "worktree", "remove", worktrees["wt"])
    ) is False


# ---------------------------------------------------------------------------
# The calibration: destroying uncaptured work is what still needs a signature
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("force_flag", ["--force", "-f", "-ff"])
def test_force_inside_the_root_still_requires_approval(worktrees, validator, force_flag):
    """`git worktree remove` refuses on uncommitted changes; --force overrides
    that refusal, which is exactly the act consent exists for."""
    cmd = f"git worktree remove {force_flag} {worktrees['wt']}"
    assert _git_worktree_recycles_only_managed_root(
        ("git", "worktree", "remove", force_flag, worktrees["wt"])
    ) is False
    assert _tier(validator, cmd) == "T3"


def test_force_signal_detection(worktrees):
    assert _git_worktree_has_force_signal(("git", "worktree", "remove", "--force", "x"))
    assert _git_worktree_has_force_signal(("git", "worktree", "remove", "-f", "x"))
    assert not _git_worktree_has_force_signal(("git", "worktree", "remove", "x"))
    # A repo path absorbed after -C is never dash-prefixed and must not match.
    assert not _git_worktree_has_force_signal(
        ("git", "-C", "/srv/fine-repo", "worktree", "remove", "x")
    )


# ---------------------------------------------------------------------------
# The positional parser preserves case and skips absorbed flag values
# ---------------------------------------------------------------------------

def test_positionals_preserve_case():
    tokens = ("git", "worktree", "remove", "/home/Jorge/WS/Wt-A")
    assert _git_worktree_positionals(tokens) == [
        "worktree", "remove", "/home/Jorge/WS/Wt-A",
    ]


def test_positionals_skip_the_dash_c_value():
    tokens = ("git", "-C", "/srv/repo", "worktree", "remove", "/w/wt-a")
    assert _git_worktree_positionals(tokens) == ["worktree", "remove", "/w/wt-a"]


def test_uppercase_path_inside_root_still_qualifies(worktrees, tmp_path):
    """A lowercasing parser would resolve the wrong directory here."""
    upper = Path(worktrees["root"]) / "WT-Upper"
    upper.mkdir(exist_ok=True)
    assert _git_worktree_recycles_only_managed_root(
        ("git", "worktree", "remove", str(upper))
    ) is True


# ---------------------------------------------------------------------------
# Rubric (3): no other classification drops a tier as a side effect
# ---------------------------------------------------------------------------

def test_unmodelled_worktree_subcommands_are_untouched(worktrees):
    """The lane stands aside for anything it does not model, so those keep
    whatever the pre-existing engine said."""
    from modules.security.mutative_verbs import _check_git_worktree
    from modules.security.command_semantics import analyze_command
    for cmd in (
        "git worktree list",
        "git worktree prune",
        f"git worktree lock {worktrees['wt']}",
        f"git worktree unlock {worktrees['wt']}",
        "git worktree repair",
        "git worktree futureverb",
    ):
        semantics = analyze_command(cmd)
        assert _check_git_worktree(
            semantics, tuple(semantics.tokens), "vcs"
        ) is None, cmd


def test_lane_stands_aside_for_non_worktree_git(worktrees):
    from modules.security.mutative_verbs import _check_git_worktree
    from modules.security.command_semantics import analyze_command
    for cmd in (
        "git status",
        "git remove",
        "git add .",
        "git push origin main",
        "git commit -m 'remove the worktree'",
    ):
        semantics = analyze_command(cmd)
        assert _check_git_worktree(
            semantics, tuple(semantics.tokens), "vcs"
        ) is None, cmd


@pytest.mark.parametrize(
    "cmd,expected",
    [
        ("git status", "T0"),
        ("git worktree list", "T0"),
        ("git log --oneline", "T0"),
        # `git commit -m ...` is deliberately absent: the validator gates it on
        # commit-message convention, which is orthogonal to tier and would make
        # this table assert something other than what it is here to assert.
        ("git add .", "T0"),
        ("git push origin main", "T3"),
        ("git branch -D feature", "T3"),
        ("git rm -r src", "T3"),
        ("rm -rf /etc/passwd", "T3"),
        ("kubectl delete pod my-pod", "T3"),
    ],
)
def test_neighbouring_classifications_unchanged(validator, worktrees, cmd, expected):
    assert _tier(validator, cmd) == expected


def test_rm_scratch_exception_is_independent(worktrees, validator, tmp_path):
    """The worktree predicate is new and separate: an `rm` aimed at the
    worktrees root is NOT covered by it and stays gated."""
    assert _tier(validator, f"rm -rf {worktrees['wt']}") == "T3"
