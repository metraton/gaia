"""
gaia.retention.worktree_reclaim -- capture-before-recycle for Gaia's
agentic worktrees (AC-9 of this brief: no worktree carrying work is ever
recycled in silence).

THE HARD PROPERTY: if a worktree carries uncommitted changes or commits no
remote holds, its full diff is deposited through the producer lane opened in
task 3 (``gaia.evidence.store.insert_evidence`` / ``gaia.evidence.fs.
write_blob``) BEFORE the worktree is touched at all. If that deposit fails
for any reason, ``reclaim_worktree`` returns without having mutated the
worktree in any way -- no reset, no file removed, no ``git worktree
remove``. A rejected capture leaves the directory and its contents exactly
as found, which is what turns "we tried to be careful" into an actual
guarantee rather than a promise a bug in the deposit path could silently
break.

THE DESIGN TENSION, AND WHY THE FIRST ANSWER TO IT WAS WRONG: git refuses,
on its own, to ``worktree remove`` a dirty OR locked worktree without
``--force`` (a locked worktree needs ``--force`` regardless of cleanliness --
every worktree ``gaia.worktree.create_agentic_worktree`` makes is born
locked). Task 11's economy deliberately never exempts the forced form:
destroying uncaptured work stays behind human approval, on purpose (see
``_git_worktree_recycles_only_managed_root`` in ``hooks/modules/security/
mutative_verbs.py``). An earlier version of this module tried to route
around that by cleaning the worktree itself first -- ``git reset --hard``
plus a direct filesystem removal of untracked entries -- reasoning that
both operations were "local-safe" and so an UNFORCED ``git worktree
remove`` would then suffice with `--force`` never invoked at all.

**That reasoning was checked against the actual classification suite and
was FALSE.** ``git reset --hard`` is not local-safe by elimination: a
dedicated flag override in ``hooks/modules/security/flag_classifiers.py``
(``"--hard" in args`` -> MUTATIVE, "git reset --hard permanently discards
uncommitted changes") escalates it to T3 specifically BECAUSE it discards
uncommitted work -- the same class of destruction this module exists to
prevent. And deleting the untracked files directly has no exemption either:
the existing ``rm``-is-free carve-out (``_rm_targets_only_scratch``) covers
only Gaia's scratch root, by a different verb, for a different reason; a
worktree living under the worktrees root gets no such pass. So "capture,
then clean" did not remove the approval demand -- it MOVED it one step
earlier, onto the cleanup commands, while still claiming the recycle needed
no signature. Verified directly by classifying the exact commands through
``modules.tools.bash_validator.BashValidator`` (not by reading the tables):
``git -C <worktree> reset --hard HEAD`` -> T3; ``rm <untracked-file-under-
worktrees-root>`` -> T3; only the final ``git worktree remove`` (already
clean, unforced) was ever actually free.

THE REAL CHOICE, ONCE THAT WAS KNOWN: extend the FORCE-remove exemption
itself, but ONLY when the work about to be destroyed is already captured
AND that fact is independently verifiable -- not merely claimed. The
obstacle: since task 3, ANY declared specialist (not just a curator) may
insert an evidence row for its own AC. If the exemption's condition were
"an evidence row exists for this worktree," a producer could deposit a
trivial or empty row and buy itself a signature-free path to force-delete
ANY worktree, live or not -- a privilege escalation strictly worse than the
friction this module exists to remove. Binding the exemption to "a row
exists" is therefore unsound; binding it to "the row's CONTENT matches what
is actually about to be destroyed, independently RECOMPUTED at the moment of
removal from the live worktree" is the only version that is not forgeable
by construction -- an attacker can only satisfy it by having genuinely
captured the real content, which is exactly the property wanted. That
binding is real (a fresh ``capture_worktree_diff``-equivalent recomputation,
hashed and compared against the durably stored blob's bytes, scoped only to
``git worktree remove --force`` inside the managed root, fail-closed on any
mismatch, timeout, or ambiguity -- mirroring the bounded-subprocess pattern
``hooks/modules/security/approval_grants.py::_run_git_query`` already uses
for its own environment-snapshot capture at block time).

**That predicate is NOT implemented here.** Building it safely needs three
things this task does not have the budget to do responsibly in one pass: a
schema change to the ``evidence`` table (to carry a content fingerprint and
the worktree's stable identity, since neither exists on the row today), a
new predicate added to the system-wide classification hot path that every
Bash command in Gaia passes through (a change that must be bounded against
DoS -- an adversarial worktree could otherwise make its own force-remove
classification arbitrarily slow), and the adversarial test coverage every
other predicate in that file already carries before it can be trusted.
Shipping a version of it without that hardening is exactly the outcome the
decision below exists to avoid: an exemption that LOOKS conditional and
is not.

**THE DECISION THIS MODULE ACTS ON:** a dirty worktree is always captured.
It is auto-recycled (unlocked and removed, unforced) ONLY when it is
already clean by the time this function inspects it -- the existing,
unconditionally safe task-11 exemption, which needs nothing new. A worktree
that DID carry uncommitted changes or unpushed commits is captured and then
LEFT IN PLACE, deliberately: ``reclaim_worktree`` returns ``status:
"captured_pending_removal"`` rather than removing it, because forcing that
removal today would either (a) silently re-demand a signature per recycle
(the original hole) or (b) require the not-yet-built, not-yet-hardened
exemption above. This is the user's own fallback, chosen with eyes open: do
not auto-discard what has work, and leave that call to a human or curator
until a genuinely unforgeable exemption exists.

Public API::

    worktree_needs_capture(worktree_path) -> bool
    capture_worktree_diff(worktree_path) -> str | None
    reclaim_worktree(repo_path, worktree_path, *, workspace, brief_slug,
                      ac_id, task_id=None, created_by_agent=None,
                      db_path=None) -> dict
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import List, Optional


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


def _other_local_branches(worktree_path: Path, current_branch: Optional[str]) -> List[str]:
    """Every local branch name except *current_branch*."""
    out = _run_git(worktree_path, ["for-each-ref", "--format=%(refname:short)", "refs/heads"])
    return [name for name in out.splitlines() if name and name != current_branch]


def _unpushed_revision_args(worktree_path: Path) -> List[str]:
    """Revision-walk arguments selecting commits unique to this checkout.

    "Unique" means: reachable from HEAD but from no OTHER ref in the
    repository -- no other local branch, no remote-tracking branch.
    Removing a worktree never deletes the branch it had checked out, so a
    commit reachable from some OTHER ref survives regardless; only a commit
    reachable ONLY through this exact HEAD is at risk if that branch is ever
    discarded too, which is the case this exists to cover.

    Every OTHER local branch is named explicitly and negated (``--not
    <branch> ...``) rather than negating ``--branches`` as a whole with the
    current branch excluded via ``--exclude=<glob>``. Both forms work in
    git 2.43.0 when spelled correctly -- ``git rev-list --exclude=<name>
    --branches`` DOES drop that ref (verified), provided the glob carries no
    ``refs/heads/`` prefix, which the documented grammar forbids when the
    pattern is paired with ``--branches``/``--tags``/``--remotes``. Naming
    the other branches directly avoids that prefix pitfall entirely and
    needs no glob-matching semantics to reason about.
    """
    branch = _current_branch(worktree_path)
    others = _other_local_branches(worktree_path, branch)
    return ["HEAD", "--not", *others, "--remotes"]


def _has_unpushed_commits(worktree_path: Path) -> bool:
    out = _run_git(worktree_path, ["rev-list", *_unpushed_revision_args(worktree_path)])
    return bool(out.strip())


def _has_uncommitted_changes(worktree_path: Path) -> bool:
    out = _run_git(worktree_path, ["status", "--porcelain"])
    return bool(out.strip())


def _untracked_files(worktree_path: Path) -> List[str]:
    out = _run_git(worktree_path, ["ls-files", "--others", "--exclude-standard"])
    return [line for line in out.splitlines() if line]


def worktree_needs_capture(worktree_path: Path) -> bool:
    """True when *worktree_path* carries uncommitted changes or unpushed commits."""
    return _has_uncommitted_changes(worktree_path) or _has_unpushed_commits(worktree_path)


# ---------------------------------------------------------------------------
# Diff capture
# ---------------------------------------------------------------------------

def _untracked_files_as_diff(worktree_path: Path) -> str:
    """Render every untracked file as a synthetic unified-diff hunk.

    ``git diff`` never shows untracked content, so without this an
    untracked file's data would be silently absent from the capture. Binary
    or undecodable content is noted by name and size rather than embedded --
    the file's presence is still on record, which is what "nothing lost
    silently" requires; embedding raw bytes into a text diff is not.
    """
    blocks = []
    for rel in _untracked_files(worktree_path):
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
    parts: List[str] = []

    unpushed = _run_git(
        worktree_path, ["log", "-p", "--reverse", *_unpushed_revision_args(worktree_path)]
    )
    if unpushed.strip():
        parts.append(unpushed)

    uncommitted = _run_git(worktree_path, ["diff", "HEAD"])
    if uncommitted.strip():
        parts.append(uncommitted)

    untracked = _untracked_files_as_diff(worktree_path)
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

    Row and blob confirm together or neither exists, exactly like the
    producer lane opened in task 3: the blob is written first because
    ``insert_evidence`` is where permission and row-level validation can
    still reject the write, and a rejection here deletes the just-written
    blob instead of leaving it orphaned.
    """
    from gaia.evidence.fs import delete_blob, write_blob
    from gaia.evidence.store import insert_evidence

    payload = diff_text.encode("utf-8")
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

def _remove_worktree(repo_path: Path, worktree_path: Path) -> None:
    """Deregister and delete *worktree_path*, unforced.

    Only ever called on a worktree ``capture_worktree_diff`` has already
    found clean (see ``reclaim_worktree``) -- there is nothing left for
    ``--force`` to override, so this is exactly the unforced,
    managed-root-scoped call task 11 already exempts. Unlocks first: every
    worktree ``gaia.worktree.create_agentic_worktree`` creates is born
    locked, and an unforced ``remove`` refuses a locked worktree regardless
    of how clean it is. The unlock call's own failure (e.g. it was never
    locked, or already unlocked) is swallowed -- the ``remove`` immediately
    after is what actually verifies removability, so a spurious unlock
    error here would only be noise ahead of the real check.
    """
    subprocess.run(
        ["git", "-C", str(repo_path), "worktree", "unlock", str(worktree_path)],
        capture_output=True, text=True,
    )
    subprocess.run(
        ["git", "-C", str(repo_path), "worktree", "remove", str(worktree_path)],
        check=True, capture_output=True, text=True,
    )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def reclaim_worktree(
    repo_path: Path,
    worktree_path: Path,
    *,
    workspace: str,
    brief_slug: str,
    ac_id: str,
    task_id: Optional[str] = None,
    created_by_agent: Optional[str] = None,
    db_path=None,
) -> dict:
    """Recycle a clean worktree; capture and STOP for a dirty one.

    Ordering is the whole point (AC-9): if *worktree_path* carries
    uncommitted changes or commits no remote holds, the full diff is
    deposited through the evidence producer lane BEFORE anything about the
    worktree changes. If that deposit fails for any reason, this function
    returns immediately, having made zero changes to the worktree.

    What happens next depends on what was found, and this is the corrected
    half of the design (see the module docstring for why): a worktree that
    was ALREADY CLEAN is unlocked and removed, unforced -- the existing,
    unconditionally safe exemption, nothing new. A worktree that carried
    real work is captured and then LEFT IN PLACE: destroying it would need
    either a signature every time (defeating the point) or a not-yet-built,
    not-yet-hardened content-bound exemption (see the module docstring).
    Auto-discarding captured work without either is not a choice this
    function makes silently.

    Returns a dict shaped::

        {"status": "recycled" | "captured_pending_removal"
                  | "capture_failed" | "deposit_failed" | "removal_failed",
         "recycled": bool, "captured": bool,
         "evidence_id": int | None, "reason": str | None}

    ``recycled`` is True only for ``status == "recycled"``. ``reason`` is
    set on every non-recycled status, naming what stopped it or, for
    ``captured_pending_removal``, why removal was deliberately withheld.
    """
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
        try:
            _remove_worktree(repo_path, worktree_path)
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
        }

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

    return {
        "status": "captured_pending_removal",
        "recycled": False,
        "captured": True,
        "evidence_id": row["id"],
        "reason": (
            "worktree carried uncommitted changes or unpushed commits; its diff "
            "is durably captured (evidence_id above), but forced removal is "
            "deliberately withheld -- no unforgeable, content-bound exemption "
            "for forcing a dirty worktree's removal exists yet (see module "
            "docstring). Left in place pending a human/curator decision."
        ),
    }
