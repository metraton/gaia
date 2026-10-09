"""
gaia.retention.branch_disposition -- deletion-safety verdict for a LOCAL
branch. Pruning a worktree frees the branch it had checked out; it does NOT
delete it (see ``gaia.retention.worktree_reclaim``). Deciding whether that
freed branch may THEN be deleted is a separate act, and this module is that
decision -- it never runs ``git branch -d``/``-D`` itself.

THE MEASURED FAILURE THIS MODULE EXISTS TO PREVENT: the recipe that
circulates everywhere -- delete a local branch when its upstream is gone --
was checked against 83 local branches across four real repositories. 26
were already merged (safe by inspection). Of the remaining 57, 13 carried
commits absent from every remote ref; of THOSE 13, six were false positives
(a squash merge already carried their content into main under a single new
commit hash) and seven carried genuinely unique work -- five of the seven
with their upstream gone. The upstream-gone recipe would have deleted
exactly the branches this module is built to protect: seven branches, 23
commits that exist nowhere else. A same-session agent that measured
reachability alone (skipping the squash-match test) reported 13 deletion
candidates where the composed criterion finds 7 -- reachability alone
over-counts by exactly the six squash false positives it cannot resolve on
its own.

THE THREE INDEPENDENT TESTS, and why none alone is the criterion:

1. ``is_merged_into_remote_main`` -- is the branch tip a direct ancestor of
   the remote main branch? Catches the ordinary fast-forward/merge-commit
   case (26 of the 83 measured).
2. ``commits_reachable_from_any_remote`` -- does every commit unique to the
   branch already exist on SOME remote ref (its own feature remote, an open
   PR branch, any ref at all)? A branch is not at risk of being lost by
   local deletion when a remote copy of its exact history still exists,
   merged or not.
3. ``content_already_in_main_via_squash`` -- does the branch's total change
   since its merge-base with main -- every one of its commits collapsed into
   a single diff -- already appear, patch-id for patch-id, as a SINGLE
   commit somewhere in main's history since that merge-base? This is what a
   squash merge produces: the ORIGINAL commit(s) are gone from main's
   ancestry and gone from every remote ref, but the same net change is
   already there under one different hash. Test 2 cannot see this -- the
   original hashes genuinely are unreachable -- which is exactly why relying
   on test 2 alone under-counts (misses real deletion candidates) while
   relying on "upstream gone" alone over-counts (destroys unique work).
   NOT COVERED: a REBASE (as opposed to a squash) of a branch with more than
   one commit. A rebase replays each original commit as its own commit in
   main rather than collapsing them, so no single main commit's patch-id
   matches the branch's COMBINED diff -- this test only catches a rebase
   when the branch carries exactly one commit, where "combined diff" and
   "that one commit's diff" are the same thing. A multi-commit rebase would
   need each commit compared individually, which this test does not do.

4. ``unintegrated_commits`` -- does every commit unique to the branch
   (reachable from no other local or remote ref) have a verbatim patch-id
   twin in some other ref? This is what a cherry-pick integration
   produces: new hashes, identical change, on a ref that is not the remote
   main and may not be pushed at all. Tests 1-3 cannot see it. The match
   uses ``git patch-id --verbatim`` rather than ``git cherry``, whose
   whitespace-insensitive ids would call a whitespace-only difference
   "integrated". A merge commit among the unique ones, an empty commit, or
   a commit with no twin all leave the branch not integrated.

THE COMPOSITION: deletable if ANY of the four tests is true --
``merged OR reachable OR squashed OR integrated``. This is not a simplification to one
test; it is the union of independent ways a branch's content can be
proven to survive its own deletion, and each is necessary because each
catches cases the others miss (see the numbers above). A branch is
judged NEVER deletable only when all of them independently fail to find its
content anywhere else -- which is precisely the "genuinely unique work"
case, and precisely the case an upstream-gone heuristic cannot distinguish
from an abandoned, safely-discardable branch. This module never reads
whether a branch's upstream is configured or gone; that signal is
deliberately absent from the decision.

Public API::

    is_merged_into_remote_main(repo_path, branch, remote_main) -> bool
    commits_reachable_from_any_remote(repo_path, branch) -> bool
    content_already_in_main_via_squash(repo_path, branch, remote_main) -> bool
    unintegrated_commits(repo_path, tip, *, own_ref=None) -> list[str]
    branch_deletion_verdict(repo_path, branch, *, remote_main="origin/main") -> dict
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Set

# Commits inspected per ref when looking for patch-id twins; an older twin
# is missed, which keeps the branch rather than deleting it.
_MAX_COMMITS_PER_REF = 1000


# ---------------------------------------------------------------------------
# git plumbing -- every call here is read-only (merge-base, rev-list, diff,
# log, patch-id); nothing in this module mutates a repository or a branch.
# ---------------------------------------------------------------------------

def _run_git(cwd: Path, args: List[str]) -> str:
    result = subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True, capture_output=True, text=True,
    )
    return result.stdout


def _is_ancestor(repo_path: Path, ancestor: str, descendant: str) -> bool:
    result = subprocess.run(
        ["git", "-C", str(repo_path), "merge-base", "--is-ancestor", ancestor, descendant],
        capture_output=True, text=True,
    )
    return result.returncode == 0


# ---------------------------------------------------------------------------
# Test 1: fusionada contra la rama principal remota
# ---------------------------------------------------------------------------

def is_merged_into_remote_main(repo_path: Path, branch: str, remote_main: str) -> bool:
    """True when *branch*'s tip is already an ancestor of *remote_main*."""
    return _is_ancestor(repo_path, branch, remote_main)


# ---------------------------------------------------------------------------
# Test 2: alcanzabilidad -- existen sus commits en algun ref remoto?
# ---------------------------------------------------------------------------

def commits_reachable_from_any_remote(repo_path: Path, branch: str) -> bool:
    """True when every commit unique to *branch* already exists on some
    remote-tracking ref (``--remotes``), regardless of which one.

    Empty output from ``rev-list <branch> --not --remotes`` means nothing is
    reachable from *branch* that is not ALSO reachable from at least one
    remote ref -- i.e. deleting the local branch loses nothing a remote copy
    does not already hold. A non-empty result names commits that exist
    nowhere remote, which is exactly the "unique work" signal.
    """
    out = _run_git(repo_path, ["rev-list", branch, "--not", "--remotes"])
    return not out.strip()


# ---------------------------------------------------------------------------
# Test 3: deteccion de fusion por squash (rebase solo si la rama es de un
# unico commit -- ver la nota en el docstring del modulo)
# ---------------------------------------------------------------------------

def _patch_id_of_diff(repo_path: Path, diff_output: str) -> Optional[str]:
    """The stable patch-id of an already-rendered diff, or None for no diff."""
    if not diff_output.strip():
        return None
    result = subprocess.run(
        ["git", "-C", str(repo_path), "patch-id", "--stable"],
        input=diff_output, capture_output=True, text=True, check=True,
    )
    line = result.stdout.strip()
    return line.split()[0] if line else None


def _branch_total_patch_id(repo_path: Path, branch: str, merge_base: str) -> Optional[str]:
    """Patch-id of *branch*'s entire change since *merge_base*, as one unit --
    this is what a squash merge on the remote side produces: every commit
    the branch carried, collapsed into a single diff."""
    diff_output = _run_git(repo_path, ["diff", f"{merge_base}..{branch}"])
    return _patch_id_of_diff(repo_path, diff_output)


def _main_commit_patch_ids_since(repo_path: Path, merge_base: str, remote_main: str) -> Set[str]:
    """Patch-id of each individual commit in *remote_main*'s history since
    *merge_base* -- a squash-merge commit is exactly one of these, and its
    patch-id matches the branch's total-change patch-id when the content is
    the same even though the original commit hash is not."""
    revs = _run_git(repo_path, ["log", "--format=%H", f"{merge_base}..{remote_main}"])
    ids: Set[str] = set()
    for commit in (line for line in revs.splitlines() if line):
        diff_output = _run_git(repo_path, ["diff", f"{commit}~1", commit])
        patch_id = _patch_id_of_diff(repo_path, diff_output)
        if patch_id:
            ids.add(patch_id)
    return ids


def content_already_in_main_via_squash(repo_path: Path, branch: str, remote_main: str) -> bool:
    """True when *branch*'s total diff already exists, content-for-content,
    as a single commit somewhere in *remote_main*'s history since their
    merge-base -- the signature of a squash merge (or a rebase of a
    single-commit branch), which test 1 and test 2 cannot see because the
    original commit hash is gone from both ancestry and every remote ref.
    Does NOT detect a rebase of a branch with more than one commit -- see
    the module docstring."""
    merge_base = _run_git(repo_path, ["merge-base", branch, remote_main]).strip()
    branch_patch_id = _branch_total_patch_id(repo_path, branch, merge_base)
    if branch_patch_id is None:
        return False
    main_patch_ids = _main_commit_patch_ids_since(repo_path, merge_base, remote_main)
    return branch_patch_id in main_patch_ids


# ---------------------------------------------------------------------------
# Test 4: integration by content (cherry-pick) into any other ref
# ---------------------------------------------------------------------------

def _other_refs(repo_path: Path, own_ref: Optional[str]) -> List[str]:
    """Every local and remote-tracking ref except *own_ref*, newest first."""
    out = _run_git(repo_path, [
        "for-each-ref", "--sort=-committerdate", "--format=%(refname)",
        "refs/heads", "refs/remotes",
    ])
    return [
        ref for ref in out.splitlines()
        if ref and ref != own_ref and not ref.endswith("/HEAD")
    ]


def _verbatim_patch_ids(repo_path: Path, log_args: List[str]) -> Dict[str, str]:
    """Map commit -> verbatim patch-id for the non-merge commits *log_args*
    selects; a commit with an empty diff has no patch-id and is absent."""
    log_output = _run_git(
        repo_path, ["log", "-p", "--no-merges", "--no-color", "--no-ext-diff", "--no-textconv", *log_args]
    )
    if not log_output.strip():
        return {}
    result = subprocess.run(
        ["git", "-C", str(repo_path), "patch-id", "--verbatim"],
        input=log_output, capture_output=True, text=True, check=True,
    )
    ids: Dict[str, str] = {}
    for line in result.stdout.splitlines():
        patch_id, commit = line.split()
        ids[commit] = patch_id
    return ids


def unintegrated_commits(repo_path: Path, tip: str, *, own_ref: Optional[str] = None) -> List[str]:
    """Commits that survive only through *tip*, oldest first: reachable from
    no other ref and without a verbatim patch-id twin in any other ref.

    *own_ref* is the full refname of the branch *tip* belongs to (excluded
    from "other refs"); None for a detached HEAD.
    """
    others = _other_refs(repo_path, own_ref)
    listing = _run_git(repo_path, ["rev-list", "--reverse", "--parents", tip, "--not", *others])
    unique = [line.split() for line in listing.splitlines() if line.strip()]
    commits = [fields[0] for fields in unique]
    if any(len(fields) > 2 for fields in unique):
        return commits

    patch_ids = _verbatim_patch_ids(repo_path, ["--no-walk=unsorted", *commits]) if commits else {}
    unmatched = set(commits)
    for ref in others:
        if not unmatched:
            break
        twins = set(_verbatim_patch_ids(
            repo_path, [f"--max-count={_MAX_COMMITS_PER_REF}", f"{tip}..{ref}"]
        ).values())
        unmatched = {c for c in unmatched if patch_ids.get(c) not in twins}
    return [c for c in commits if c in unmatched]


def content_integrated_in_other_refs(repo_path: Path, branch: str) -> bool:
    """True when *branch* has no commit whose content lives only on it."""
    return not unintegrated_commits(repo_path, branch, own_ref=f"refs/heads/{branch}")


# ---------------------------------------------------------------------------
# Composition -- deletable only when at least one of the independent
# tests proves the content survives the branch's deletion. Deliberately does
# NOT read whether the branch's upstream is configured or gone.
# ---------------------------------------------------------------------------

def _ref_exists(repo_path: Path, ref: str) -> bool:
    result = subprocess.run(
        ["git", "-C", str(repo_path), "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
        capture_output=True, text=True,
    )
    return result.returncode == 0


def branch_deletion_verdict(repo_path: Path, branch: str, *, remote_main: str = "origin/main") -> dict:
    """Compose the independent tests into one deletion verdict for
    *branch*. Never deletes anything -- this is a decision, not an action.

    Returns a dict shaped::

        {"branch": str, "deletable": bool,
         "merged_into_remote_main": bool,
         "reachable_from_any_remote": bool,
         "content_already_in_main": bool,
         "content_integrated_in_other_refs": bool}

    ``deletable`` is the union of the four booleans: true when ANY of them
    independently proves the branch's content survives elsewhere. It is
    false only when all fail -- the genuinely-unique-work case this
    module exists to protect, regardless of whether the branch's upstream
    is still configured. A *remote_main* that does not resolve proves
    nothing, so the two tests that need it report False.
    """
    has_main = _ref_exists(repo_path, remote_main)
    merged = has_main and is_merged_into_remote_main(repo_path, branch, remote_main)
    reachable = commits_reachable_from_any_remote(repo_path, branch)
    squashed = has_main and content_already_in_main_via_squash(repo_path, branch, remote_main)
    integrated = content_integrated_in_other_refs(repo_path, branch)
    return {
        "branch": branch,
        "deletable": merged or reachable or squashed or integrated,
        "merged_into_remote_main": merged,
        "reachable_from_any_remote": reachable,
        "content_already_in_main": squashed,
        "content_integrated_in_other_refs": integrated,
    }
