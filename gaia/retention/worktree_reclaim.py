"""
gaia.retention.worktree_reclaim -- capture-before-recycle for Gaia's
agentic worktrees: no worktree carrying work is ever recycled in silence.

If a worktree carries uncommitted changes or commits no other ref holds, its
full diff is deposited BEFORE the worktree is touched at all, through the
evidence lane (``gaia.evidence.store.insert_evidence`` / ``gaia.evidence.fs.
write_blob``) or, for a turn with no brief, onto its contract row
(``gaia.evidence.fs.write_contract_blob``). If that deposit fails for any
reason, ``reclaim_worktree`` returns without having mutated the worktree --
no reset, no file removed, no ``git worktree remove``.

Git refuses to ``worktree remove`` a dirty or locked worktree without
``--force``, and every worktree ``gaia.worktree.create_agentic_worktree``
makes is born locked. Destroying uncaptured work stays behind human approval
(see ``_git_worktree_recycles_only_managed_root`` in ``hooks/modules/security/
mutative_verbs.py``, which never exempts the forced form), and cleaning the
worktree first does not avoid that: ``git reset --hard`` is T3 by the
``--hard`` override in ``hooks/modules/security/flag_classifiers.py``, and
``rm`` is free only for scratch entries (``_rm_scratch_confined_targets``).
So this module never cleans a worktree itself.

A worktree already clean of agent content is unlocked and removed. Gaia's own
untracked ``.gaia-worktree.json`` sidecar of an older worktree is ignored when
its content re-parses as this worktree's own metadata
(``_exempt_metadata_filename``), and only in that case is the removal forced.
A worktree that carried uncommitted changes or unpushed commits is captured
and then LEFT IN PLACE: ``reclaim_worktree`` returns ``status:
"captured_pending_removal"``. A force-removal exempt only when the captured
content is recomputed and matches is not implemented, so that call stays with
a human or curator.

Public API::

    worktree_needs_capture(worktree_path) -> bool
    capture_worktree_diff(worktree_path) -> str | None
    reclaim_worktree(repo_path, worktree_path, *, workspace, brief_slug,
                      ac_id, task_id=None, created_by_agent=None,
                      db_path=None) -> dict
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Iterable, List, Optional, Set

from gaia.retention.branch_disposition import branch_is_deletable, unintegrated_commits


# ---------------------------------------------------------------------------
# git plumbing
# ---------------------------------------------------------------------------

def _run_git(cwd: Path, args: List[str]) -> str:
    """Run one git subcommand inside *cwd*; return stdout.

    Every call this module makes here is read-only (status, diff, log,
    ls-files, rev-list, rev-parse, for-each-ref) -- nothing that mutates the
    worktree runs from this module anymore; see the module docstring for why.
    Raises ``subprocess.CalledProcessError`` on a non-zero exit -- callers
    treat that as "capture failed," never as "nothing found."
    """
    result = subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True, capture_output=True, text=True,
    )
    return result.stdout


def _current_branch(worktree_path: Path) -> Optional[str]:
    """The branch HEAD is on, or None when HEAD is detached."""
    name = _run_git(worktree_path, ["rev-parse", "--abbrev-ref", "HEAD"]).strip()
    return None if name == "HEAD" else name


def _unintegrated_commits(worktree_path: Path) -> List[str]:
    """Commits that survive only through this checkout's HEAD.

    A commit counts as surviving elsewhere when another ref reaches it OR
    carries a verbatim patch-id twin of it (a cherry-pick keeps the content
    under a new hash); see ``branch_disposition.unintegrated_commits``.
    """
    branch = _current_branch(worktree_path)
    own_ref = f"refs/heads/{branch}" if branch else None
    return unintegrated_commits(worktree_path, "HEAD", own_ref=own_ref)


def _has_unpushed_commits(worktree_path: Path) -> bool:
    return bool(_unintegrated_commits(worktree_path))


def _exempt_metadata_filename(worktree_path: Path) -> Optional[str]:
    """The legacy in-tree sidecar's name, when exempt from dirtiness -- else None.

    Worktrees created before the sidecar moved into the private git directory
    carry it untracked in their working tree, which would make an unused one
    look dirty. The exemption is bound to content, never to the filename: a
    forged, truncated or foreign ``.gaia-worktree.json`` counts as ordinary
    untracked work, so an agent cannot buy a capture-free release with it.
    """
    from gaia.worktree import is_valid_own_metadata_sidecar, legacy_metadata_path

    if is_valid_own_metadata_sidecar(worktree_path):
        return legacy_metadata_path(worktree_path).name
    return None


def _has_uncommitted_changes(worktree_path: Path, *, exempt: Optional[str] = None) -> bool:
    out = _run_git(worktree_path, ["status", "--porcelain"])
    for line in out.splitlines():
        if not line.strip():
            continue
        # Porcelain v1 status line: "XY PATH" (or "XY PATH1 -> PATH2" for a
        # rename). The path starts after the two status characters and the
        # separating space; comparing just that against the exempt filename
        # keeps this blind to status codes, which are not what is being
        # matched here.
        path_field = line[3:] if len(line) > 3 else ""
        if exempt is not None and path_field == exempt:
            continue
        return True
    return False


def _untracked_files(worktree_path: Path, *, exempt: Optional[str] = None) -> List[str]:
    out = _run_git(worktree_path, ["ls-files", "--others", "--exclude-standard"])
    return [line for line in out.splitlines() if line and line != exempt]


def worktree_needs_capture(worktree_path: Path) -> bool:
    """True when *worktree_path* carries uncommitted changes or unpushed commits.

    Gaia's own ``.gaia-worktree.json`` sidecar is excluded from the
    uncommitted-changes check when (and only when) it parses as this
    worktree's own valid metadata -- see ``_exempt_metadata_filename``.
    """
    exempt = _exempt_metadata_filename(worktree_path)
    return (
        _has_uncommitted_changes(worktree_path, exempt=exempt)
        or _has_unpushed_commits(worktree_path)
    )


# ---------------------------------------------------------------------------
# Diff capture
# ---------------------------------------------------------------------------

def _untracked_files_as_diff(worktree_path: Path, *, exempt: Optional[str] = None) -> str:
    """Render every untracked file as a synthetic unified-diff hunk.

    ``git diff`` never shows untracked content, so without this an
    untracked file's data would be silently absent from the capture. Binary
    or undecodable content is noted by name and size rather than embedded --
    the file's presence is still on record, which is what "nothing lost
    silently" requires; embedding raw bytes into a text diff is not.

    *exempt*, when given, is Gaia's own accounting file's name (see
    ``_exempt_metadata_filename``): it is not the agent's work, so it is left
    out of the captured diff the same way it is left out of the dirtiness
    check that decides whether to capture at all.
    """
    blocks = []
    for rel in _untracked_files(worktree_path, exempt=exempt):
        full = worktree_path / rel
        try:
            text = full.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            size = full.stat().st_size if full.exists() else 0
            blocks.append(
                f"--- /dev/null\n+++ b/{rel}\n"
                f"(binary or unreadable content, {size} bytes, not inlined)\n"
            )
            continue
        lines = text.splitlines(keepends=True)
        header = ["--- /dev/null\n", f"+++ b/{rel}\n", f"@@ -0,0 +1,{len(lines)} @@\n"]
        body = [
            "+" + (line if line.endswith("\n") else line + "\n")
            for line in lines
        ]
        blocks.append("".join(header + body))
    return "\n".join(blocks)


def capture_worktree_diff(worktree_path: Path) -> Optional[str]:
    """Full diff of everything in *worktree_path* that exists nowhere else.

    Combines three sources: commits reachable from HEAD but from no other
    ref (unpushed work, rendered as patches via ``git log -p``), uncommitted
    changes to tracked files (``git diff HEAD``), and untracked file content
    (rendered as synthetic diff hunks, since ``git diff`` omits it). Returns
    ``None`` when none of the three has anything -- the worktree is clean --
    which the caller reads as "no capture needed," never as a failure.

    Raises ``subprocess.CalledProcessError`` on any git failure. Every git
    call here is one atomic subprocess; any of them failing aborts the WHOLE
    capture rather than returning a partial diff a caller could mistake for
    complete.
    """
    exempt = _exempt_metadata_filename(worktree_path)
    parts: List[str] = []

    commits = _unintegrated_commits(worktree_path)
    if commits:
        parts.append(_run_git(worktree_path, ["log", "-p", "--no-walk=unsorted", *commits]))

    uncommitted = _run_git(worktree_path, ["diff", "HEAD"])
    if uncommitted.strip():
        parts.append(uncommitted)

    untracked = _untracked_files_as_diff(worktree_path, exempt=exempt)
    if untracked.strip():
        parts.append(untracked)

    if not parts:
        return None
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Evidence deposit -- row and blob confirm together or neither exists
# ---------------------------------------------------------------------------

def _resolve_brief_id(workspace: str, brief_slug: str, db_path=None) -> int:
    """Look up the integer brief_id for (workspace, brief_slug).

    Delegates to ``gaia.store.writer._resolve_brief_id`` -- the canonical
    lookup already used across the writer module -- rather than repeating
    the query here. That function takes an open connection and returns
    ``None`` on a miss; this wrapper owns the connection lifecycle and
    raises, matching what every caller in this module expects.
    """
    from gaia.store.writer import _connect, _resolve_brief_id as _writer_resolve_brief_id

    con = _connect(db_path)
    try:
        brief_id = _writer_resolve_brief_id(con, workspace, brief_slug)
        if brief_id is None:
            raise ValueError(
                f"brief '{brief_slug}' not found in workspace '{workspace}'"
            )
        return brief_id
    finally:
        con.close()


def _deposit_diff_evidence_to_contract(
    diff_text: str,
    *,
    contract_id: str,
    worktree_path: Path,
    db_path,
) -> dict:
    """Write *diff_text* as a blob under *contract_id*'s own namespace, then
    attach its pointer directly to that contract row.

    Mirrors ``_deposit_diff_evidence``'s write-then-attach shape and its
    write-then-cleanup-on-reject guarantee, but for the no-brief case: the
    row and blob confirm together or neither exists. Idempotent the same
    way -- a capture already on the row with the same digest is returned
    unchanged rather than duplicated, so releasing an already-captured
    worktree a second time (still dirty, still left in place) does not pile
    up blobs.
    """
    from gaia.evidence.fs import delete_blob, read_blob, write_contract_blob
    from gaia.store.writer import attach_worktree_capture_to_contract, get_contract_worktree_capture

    payload = diff_text.encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()

    existing = get_contract_worktree_capture(contract_id, db_path=db_path)
    if existing is not None and existing.get("sha256") == digest:
        existing_payload = read_blob(existing.get("artifact_path", ""))
        if existing_payload is not None and hashlib.sha256(existing_payload).hexdigest() == digest:
            return existing

    blob_path, size = write_contract_blob(contract_id, payload, ext=".diff")
    try:
        result = attach_worktree_capture_to_contract(
            contract_id,
            artifact_path=str(blob_path),
            sha256=digest,
            size_bytes=size,
            worktree_path=str(worktree_path),
            db_path=db_path,
        )
    except Exception:
        delete_blob(str(blob_path))
        raise
    if result is None:
        delete_blob(str(blob_path))
        raise ValueError(f"no contract row found for contract_id={contract_id!r}")
    return result["worktree_capture"]


def _deposit_diff_evidence(
    diff_text: str,
    *,
    workspace: str,
    brief_slug: str,
    brief_id: int,
    ac_id: str,
    task_id: Optional[str],
    created_by_agent: Optional[str],
    db_path,
) -> dict:
    """Write *diff_text* as a blob, then insert its evidence row.

    Row and blob confirm together or neither exists: the blob is written first because
    ``insert_evidence`` is where permission and row-level validation can
    still reject the write, and a rejection here deletes the just-written
    blob instead of leaving it orphaned.
    """
    from gaia.evidence.fs import delete_blob, read_blob, write_blob
    from gaia.evidence.store import insert_evidence, list_evidence_for_ac

    payload = diff_text.encode("utf-8")
    digest = hashlib.sha256(payload).digest()
    for existing in list_evidence_for_ac(brief_id, ac_id, db_path=db_path):
        artifact_path = existing.get("artifact_path")
        if not artifact_path:
            continue
        existing_payload = read_blob(artifact_path)
        if (
            existing_payload is not None
            and hashlib.sha256(existing_payload).digest() == digest
        ):
            return existing

    blob_path, size = write_blob(workspace, brief_slug, ac_id, payload, ext=".diff")
    try:
        return insert_evidence(
            workspace,
            brief_id,
            ac_id,
            type="file",
            artifact_path=str(blob_path),
            size_bytes=size,
            task_id=task_id,
            created_by_agent=created_by_agent,
            db_path=db_path,
        )
    except Exception:
        delete_blob(str(blob_path))
        raise


# ---------------------------------------------------------------------------
# Removal -- only ever reached for a worktree that is ALREADY clean
# ---------------------------------------------------------------------------

def _remove_worktree(repo_path: Path, worktree_path: Path, *, exempt: Optional[str] = None) -> None:
    """Deregister and delete *worktree_path*.

    Only ever called on a worktree ``capture_worktree_diff`` has already
    found clean (see ``reclaim_worktree``) -- there is nothing of the AGENT's
    to capture, so the removal stays within the managed-root-scoped exemption
    of ``_git_worktree_recycles_only_managed_root``. Unlocks first: every worktree
    ``gaia.worktree.create_agentic_worktree``/``create_canonical_worktree``
    creates is born locked, and ``remove`` refuses a locked worktree
    regardless of how clean it is. The unlock call's own failure (e.g. it was
    never locked, or already unlocked) is swallowed -- the ``remove``
    immediately after is what actually verifies removability, so a spurious
    unlock error here would only be noise ahead of the real check.

    ``exempt``, when not None, names Gaia's own metadata sidecar as it sits
    on disk (see ``_exempt_metadata_filename``) -- the file that made
    ``capture_worktree_diff`` return ``None`` in the first place is still
    physically present and UNTRACKED, so git's own unforced ``remove``
    refuses it exactly as it would refuse any dirty worktree. ``--force`` is
    used ONLY in that one, already-verified-safe case: ``reclaim_worktree``
    reached this call precisely because it recomputed that the worktree
    carries nothing beyond this one already-validated accounting file, so
    what ``--force`` overrides here is Gaia's own bookkeeping, never
    uncaptured agent work.
    """
    subprocess.run(
        ["git", "-C", str(repo_path), "worktree", "unlock", str(worktree_path)],
        capture_output=True, text=True,
    )
    remove_cmd = ["git", "-C", str(repo_path), "worktree", "remove", str(worktree_path)]
    if exempt is not None:
        remove_cmd.append("--force")
    subprocess.run(remove_cmd, check=True, capture_output=True, text=True)


def _remote_default_branch(repo_path: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo_path), "symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
        capture_output=True, text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else "origin/main"


def _checked_out_branches(repo_path: Path) -> Set[str]:
    """Branches checked out in the main checkout or any linked worktree."""
    listing = _run_git(repo_path, ["worktree", "list", "--porcelain"])
    prefix = "branch refs/heads/"
    return {line[len(prefix):] for line in listing.splitlines() if line.startswith(prefix)}


def _dispose_branch(
    repo_path: Path, branch: Optional[str], keep: Iterable[str] = (), *, dry_run: bool = False
) -> dict:
    """Delete the branch a recycled worktree had checked out, but only when
    ``branch_deletion_verdict`` proves its content survives elsewhere.
    With *dry_run* a deletable branch is reported as ``would_delete`` and kept.

    The forced ``git branch -D`` is what a cherry-picked branch needs (git's
    own ``-d`` judges by SHA) and is safe only because the verdict has
    established that no commit exists solely on the branch. A branch named
    in *keep* (the accumulating branch, which no ref records), the remote's
    default branch, and any branch checked out somewhere are never deleted.
    Any failure or a negative verdict keeps the branch and reports why.
    """
    if branch is None:
        return {"branch": None, "branch_deleted": False,
                "branch_kept_reason": "worktree HEAD was detached"}
    remote_main = _remote_default_branch(repo_path)
    if branch == remote_main.split("/", 1)[-1]:
        return {"branch": branch, "branch_deleted": False,
                "branch_kept_reason": "it is the remote's default branch"}
    if branch in set(keep):
        return {"branch": branch, "branch_deleted": False,
                "branch_kept_reason": "it was declared to keep"}
    if branch in _checked_out_branches(repo_path):
        return {"branch": branch, "branch_deleted": False,
                "branch_kept_reason": "it is checked out in a worktree"}
    try:
        if not branch_is_deletable(repo_path, branch, remote_main=remote_main):
            return {"branch": branch, "branch_deleted": False,
                    "branch_kept_reason": "it carries commits found nowhere else"}
        if dry_run:
            return {"branch": branch, "branch_deleted": False, "would_delete": True,
                    "branch_kept_reason": None}
        subprocess.run(
            ["git", "-C", str(repo_path), "branch", "-D", branch],
            check=True, capture_output=True, text=True,
        )
    except Exception as exc:  # noqa: BLE001 -- the worktree is gone; report, keep the branch
        return {"branch": branch, "branch_deleted": False,
                "branch_kept_reason": f"branch deletion failed: {exc}"}
    return {"branch": branch, "branch_deleted": True, "branch_kept_reason": None}


def remove_integrated_branches(
    repo_path: Path, *, keep: Iterable[str] = (), dry_run: bool = False
) -> List[dict]:
    """Post-merge pass: delete every local branch whose content the default
    branch (as last fetched) or another ref already holds; one result per
    branch, in ``_dispose_branch``'s shape. A branch that carries commits
    found nowhere else is never touched here: removing it stays a forced
    ``git branch -D``, which asks for a signature.

    A fast-forward merge leaves the branch on the default branch's tip, which
    is indistinguishable from a branch just created there, so both go.
    """
    branches = _run_git(repo_path, ["for-each-ref", "--format=%(refname:short)", "refs/heads"])
    return [_dispose_branch(repo_path, branch, keep, dry_run=dry_run) for branch in branches.split()]


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def reclaim_worktree(
    repo_path: Path,
    worktree_path: Path,
    *,
    workspace: Optional[str] = None,
    brief_slug: Optional[str] = None,
    ac_id: Optional[str] = None,
    contract_id: Optional[str] = None,
    task_id: Optional[str] = None,
    created_by_agent: Optional[str] = None,
    db_path=None,
    delete_branch: bool = False,
    keep_branches: Iterable[str] = (),
) -> dict:
    """Recycle a clean worktree; capture and STOP for a dirty one.

    Ordering is the whole point: if *worktree_path* carries
    uncommitted changes or commits no remote holds, the full diff is
    deposited BEFORE anything about the worktree changes. If that deposit
    fails for any reason, this function returns immediately, having made
    zero changes to the worktree.

    A worktree that was ALREADY CLEAN of agent content is unlocked and
    removed, forced only to override Gaia's own untracked metadata sidecar
    when present and valid (see ``_exempt_metadata_filename``). A worktree
    that carried real work is captured and then LEFT IN PLACE (see the
    module docstring).

    ``workspace``/``brief_slug``/``ac_id``/``contract_id`` are all OPTIONAL
    and are never inspected for an ALREADY-CLEAN worktree, which has nothing
    to attribute. On the path that actually deposits a diff, exactly one
    attribution is required, and either satisfies it: the full ``workspace``/``brief_slug``/``ac_id`` triple
    deposits through the brief/AC evidence lane exactly as before (a PLANNED
    turn, attributed to its brief); ``contract_id`` alone deposits directly
    onto that contract row instead (an AD-HOC turn -- every turn has one, no
    brief required). The brief triple is tried first when both are given, so
    a planned turn's attribution is unchanged. Neither supplied returns
    ``status: "capture_args_missing"`` without touching the worktree, the
    same "nothing moves until deposit succeeds" guarantee as a genuine
    deposit failure.

    Returns a dict shaped::

        {"status": "recycled" | "captured_pending_removal"
                  | "capture_failed" | "capture_args_missing"
                  | "deposit_failed" | "removal_failed",
         "recycled": bool, "captured": bool,
         "evidence_id": int | None, "reason": str | None}

    With ``delete_branch=True`` (the explicit release path; the SessionStart
    and ``gaia cleanup`` collector leave it False and never delete a branch),
    a ``recycled`` result of a worktree that was present also carries
    ``branch``, ``branch_deleted`` and ``branch_kept_reason``: the branch is
    deleted when ``branch_deletion_verdict`` finds its content elsewhere
    (including as cherry-picked twins) and it is not in ``keep_branches``,
    and kept with the reason otherwise.
    Commits count as "unpushed" only when no other ref reaches them or
    carries a patch-id twin of them.

    ``recycled`` is True only for ``status == "recycled"``. ``reason`` is
    set on every non-recycled status, naming what stopped it or, for
    ``captured_pending_removal``, why removal was deliberately withheld. On
    the ``contract_id``-only deposit path, the returned dict also carries
    ``contract_capture_path`` (the deposited blob's absolute path) --
    ``evidence_id`` stays ``None`` there, since no ``evidence`` table row was
    created; retrieve the capture later with
    ``gaia.store.writer.get_contract_worktree_capture(contract_id)``.
    """
    if not worktree_path.exists():
        return {
            "status": "recycled",
            "recycled": True,
            "captured": False,
            "evidence_id": None,
            "reason": "worktree is already absent",
        }

    try:
        diff_text = capture_worktree_diff(worktree_path)
    except Exception as exc:  # noqa: BLE001 -- any capture failure must halt here
        return {
            "status": "capture_failed",
            "recycled": False,
            "captured": False,
            "evidence_id": None,
            "reason": f"diff capture failed: {exc}",
        }

    if diff_text is None:
        branch = _current_branch(worktree_path)
        try:
            _remove_worktree(
                repo_path, worktree_path,
                exempt=_exempt_metadata_filename(worktree_path),
            )
        except Exception as exc:  # noqa: BLE001 -- report, do not mask it
            return {
                "status": "removal_failed",
                "recycled": False,
                "captured": False,
                "evidence_id": None,
                "reason": f"removal failed on an already-clean worktree: {exc}",
            }
        return {
            "status": "recycled",
            "recycled": True,
            "captured": False,
            "evidence_id": None,
            "reason": None,
            **(_dispose_branch(repo_path, branch, keep_branches) if delete_branch else {}),
        }

    have_brief_triple = bool(workspace and brief_slug and ac_id)
    if not have_brief_triple and not contract_id:
        return {
            "status": "capture_args_missing",
            "recycled": False,
            "captured": False,
            "evidence_id": None,
            "reason": (
                "worktree carries uncommitted changes or unpushed commits; "
                "depositing its diff before any release can proceed requires "
                "either workspace/brief_slug/ac_id (a planned turn's brief/AC) "
                "or contract_id (an ad-hoc turn's own contract row) -- neither "
                "was supplied"
            ),
        }

    if have_brief_triple:
        try:
            brief_id = _resolve_brief_id(workspace, brief_slug, db_path=db_path)
            row = _deposit_diff_evidence(
                diff_text,
                workspace=workspace,
                brief_slug=brief_slug,
                brief_id=brief_id,
                ac_id=ac_id,
                task_id=task_id,
                created_by_agent=created_by_agent,
                db_path=db_path,
            )
        except Exception as exc:  # noqa: BLE001 -- the worktree must stay untouched
            return {
                "status": "deposit_failed",
                "recycled": False,
                "captured": False,
                "evidence_id": None,
                "reason": f"evidence deposit failed: {exc}",
            }
        evidence_id = row["id"]
        extra = {}
    else:
        try:
            capture = _deposit_diff_evidence_to_contract(
                diff_text,
                contract_id=contract_id,
                worktree_path=worktree_path,
                db_path=db_path,
            )
        except Exception as exc:  # noqa: BLE001 -- the worktree must stay untouched
            return {
                "status": "deposit_failed",
                "recycled": False,
                "captured": False,
                "evidence_id": None,
                "reason": f"contract capture deposit failed: {exc}",
            }
        evidence_id = None
        extra = {"contract_capture_path": capture["artifact_path"]}

    return {
        "status": "captured_pending_removal",
        "recycled": False,
        "captured": True,
        "evidence_id": evidence_id,
        **extra,
        "reason": (
            "worktree carried uncommitted changes or unpushed commits; its diff "
            "is durably captured, but forced removal is "
            "deliberately withheld -- no unforgeable, content-bound exemption "
            "for forcing a dirty worktree's removal exists yet (see module "
            "docstring). Left in place pending a human/curator decision."
        ),
    }
