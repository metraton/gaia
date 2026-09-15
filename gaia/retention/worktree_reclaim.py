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
It is auto-recycled (unlocked and removed) ONLY when it is already clean --
by AGENT content -- by the time this function inspects it: the existing,
unconditionally safe task-11 exemption, which needs nothing new, EXTENDED
by one later addition (see ``_exempt_metadata_filename``) to also ignore
Gaia's own untracked ``.gaia-worktree.json`` accounting file when its
content independently re-parses as this exact worktree's own metadata.
That extension is what makes the removal call FORCED in exactly that one
case -- overriding only the sidecar git itself would otherwise refuse to
see past, never an agent's own untracked work, which this function has
already recomputed and confirmed absent before reaching that call. A
worktree that DID carry uncommitted changes or unpushed commits is captured and then
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

import hashlib
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


def _exempt_metadata_filename(worktree_path: Path) -> Optional[str]:
    """The ``.gaia-worktree.json`` sidecar's name, when exempt from dirtiness -- else None.

    Gaia's own accounting file is born UNTRACKED inside every canonical
    worktree (task 12's ``create_canonical_worktree`` writes it directly, with
    no ``git add``), so an unused worktree's own identity record made every
    such worktree look dirty from the moment it was created -- the defect this
    module exists to close. The exemption is bound to CONTENT, never to the
    filename: ``gaia.worktree.is_valid_own_metadata_sidecar`` re-parses the
    file and confirms it carries exactly this worktree's own required key set
    with a matching ``path``. A file named ``.gaia-worktree.json`` that does
    not parse that way -- forged, truncated, or pointed at a different
    worktree -- is NOT exempt and counts as ordinary untracked work, which is
    what keeps this exemption unforgeable: an agent cannot hide real work
    behind the same filename to buy a capture-free release.
    """
    from gaia.worktree import is_valid_own_metadata_sidecar, worktree_metadata_path

    if is_valid_own_metadata_sidecar(worktree_path):
        return worktree_metadata_path(worktree_path).name
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

    unpushed = _run_git(
        worktree_path, ["log", "-p", "--reverse", *_unpushed_revision_args(worktree_path)]
    )
    if unpushed.strip():
        parts.append(unpushed)

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

    Row and blob confirm together or neither exists, exactly like the
    producer lane opened in task 3: the blob is written first because
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
    to capture, so this stays exactly the managed-root-scoped exemption task
    11 already covers. Unlocks first: every worktree
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
    uncaptured agent work -- the exact distinction the module docstring's
    design-tension section requires before ``--force`` is ever safe to use.
    """
    subprocess.run(
        ["git", "-C", str(repo_path), "worktree", "unlock", str(worktree_path)],
        capture_output=True, text=True,
    )
    remove_cmd = ["git", "-C", str(repo_path), "worktree", "remove", str(worktree_path)]
    if exempt is not None:
        remove_cmd.append("--force")
    subprocess.run(remove_cmd, check=True, capture_output=True, text=True)


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
) -> dict:
    """Recycle a clean worktree; capture and STOP for a dirty one.

    Ordering is the whole point (AC-9): if *worktree_path* carries
    uncommitted changes or commits no remote holds, the full diff is
    deposited through the evidence producer lane BEFORE anything about the
    worktree changes. If that deposit fails for any reason, this function
    returns immediately, having made zero changes to the worktree.

    What happens next depends on what was found, and this is the corrected
    half of the design (see the module docstring for why): a worktree that
    was ALREADY CLEAN of agent content is unlocked and removed -- the
    existing, unconditionally safe exemption, forced only to override
    Gaia's own untracked metadata sidecar when present and valid (see
    ``_exempt_metadata_filename``), never anything else. A worktree that carried
    real work is captured and then LEFT IN PLACE: destroying it would need
    either a signature every time (defeating the point) or a not-yet-built,
    not-yet-hardened content-bound exemption (see the module docstring).
    Auto-discarding captured work without either is not a choice this
    function makes silently.

    ``workspace``/``brief_slug``/``ac_id``/``contract_id`` are all OPTIONAL
    and are never even inspected for an ALREADY-CLEAN worktree: a worktree
    nobody touched has nothing to attribute anything to, so demanding
    attribution unconditionally (as the CLI used to) forced a real brief and
    AC onto a release that never needed one. On the path that actually
    deposits a diff, exactly one attribution is required, and either
    satisfies it: the full ``workspace``/``brief_slug``/``ac_id`` triple
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
