"""
gaia.retention.worktree_collector -- distinguishes a LIVE agentic worktree
from an ABANDONED one and recycles only the second (AC-8 of this brief).

An agentic worktree (``gaia.worktree.create_agentic_worktree``) carries its
owner's identity in its git lock's *reason*, never in a filesystem timestamp
or a name (see that module's docstring). This module reads that identity
back with ``gaia.worktree.parse_lock_reason``, cross-references the owning
``agent_contract_handoffs`` row, and decides collectibility -- then hands any
collectible worktree to ``gaia.retention.worktree_reclaim.reclaim_worktree``
(task 13) to actually capture-and-recycle it. This module never touches a
worktree directly; it only decides WHICH ones ``reclaim_worktree`` may see.

THE TRAP THIS MODULE DOES NOT FALL INTO: ``cut_reason`` is stamped
``CUT_REASON_NEVER_FINALIZED`` the moment ANY row is born at dispatch (see
``gaia.state``'s "cut vocabulary" section) and is cleared only by a clean
``gaia contract finalize``. A turn running RIGHT NOW and a turn abandoned
mid-run both carry that exact birth value for as long as neither has closed
-- so a criterion that read ``cut_reason`` as "the turn is unfinished, treat
it as abandoned" would never collect anything (every live turn is also
"unfinished" by that same value) or, read the other way, would collect a
turn that is very much still running. The only ``cut_reason`` values that
prove death WITHOUT ambiguity are the ones a mechanism OTHER than the
owning agent sets, after the fact, because it already determined the row
would never be finalized by its own turn: ``CUT_REASON_REAPED`` (a
convergence swept up a row nobody finalized) and
``CUT_REASON_BACKSTOP_CAPTURE`` (the SubagentStop backstop wrote the row
itself because none existed). ``CUT_REASON_SALVAGED_TRUNCATION`` and the
birth value ``CUT_REASON_NEVER_FINALIZED`` are deliberately excluded from
``EXPLICIT_DEATH_CUT_REASONS`` below -- the first is a rescue of content, not
proof the session is gone, and the second is the value every live turn
shares with every abandoned one.

Liveness -- never this module's own arithmetic -- comes from
``gaia.retention.liveness.session_dead_past_grace`` (task 9's three-valued
signal composed with the grace window): DEAD is reachable only through a
stale heartbeat found in a readable registry; every other shape (no
session id, no row, unreadable registry, no entry, a legacy entry with no
heartbeat) reads UNKNOWN, and UNKNOWN is treated exactly like ALIVE -- do
nothing. A caller that branches only on "is this DEAD" gets that asymmetry
for free, which is why the predicate below never asks "is this alive"
separately.

Five-way property this module exists to prove (AC-8's five worktrees):
    (a) contract TERMINAL (COMPLETE) and quiet past the grace window
        -> collect.
    (b) contract not yet closed, session ALIVE (running right now)
        -> protected, never collected.
    (c) contract not yet closed, session DEAD and quiet past the grace
        window -> collect. This is the pair with (b): without the liveness
        signal, (b) and (c) are the SAME row shape (both non-terminal,
        both carrying the birth ``cut_reason``) and indistinguishable by
        anything else recorded on the row.
    (d) an explicit death-proving ``cut_reason`` (REAPED or
        BACKSTOP_CAPTURE) -- collected immediately, no grace wait, because
        the row already proves an external mechanism determined this turn
        is over.
    (e) the database is unreadable -- nothing is collected. Every row
        lookup fails closed (returns ``None``), so every worktree reads as
        "cannot determine," which this module treats exactly like "leave
        it," the same posture ``gaia.retention.fs_rules`` already commits
        to for the whole package.

The decision fires at session start, driven by ``gaia cleanup`` (mirroring
``fs_rules``' wiring note: no automatic hook sweeps this yet), never mid-turn
-- a worktree that is the ACTIVE subject of the very turn evaluating it would
otherwise be at risk of judging itself.

Public API::

    EXPLICIT_DEATH_CUT_REASONS
    worktree_collect_reason(contract_id, mtime, *, grace_hours=None, now=None)
        -> str | None
    list_managed_worktrees(repo_path) -> list[dict]
    collect_worktrees(repo_path, *, workspace, brief_slug, ac_id, ...)
        -> list[dict]
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Dict, List, Optional

from gaia.retention.infra import _ro_db_connect, resolve_grace_hours
from gaia.retention.liveness import session_dead_past_grace
from gaia.retention.worktree_reclaim import reclaim_worktree
from gaia.state import (
    CUT_REASON_BACKSTOP_CAPTURE,
    CUT_REASON_REAPED,
    TERMINAL_PLAN_STATUSES,
)
from gaia.worktree import parse_lock_reason

# The ONLY cut_reason values that prove death without ambiguity -- see the
# module docstring's "trap" section for why CUT_REASON_NEVER_FINALIZED (the
# birth value every row, live or abandoned, shares) and
# CUT_REASON_SALVAGED_TRUNCATION (a content rescue, not a death proof) are
# deliberately absent from this set.
EXPLICIT_DEATH_CUT_REASONS = frozenset(
    {CUT_REASON_REAPED, CUT_REASON_BACKSTOP_CAPTURE}
)


# ---------------------------------------------------------------------------
# Row lookup -- fail closed on every shape of "cannot tell" (case (e)).
# ---------------------------------------------------------------------------

def _contract_row(contract_id: str) -> Optional[Dict[str, Optional[str]]]:
    """``{"agent_state": ..., "cut_reason": ...}`` for *contract_id*, or None.

    None covers every shape of "cannot determine": no contract_id, no
    readable database, no such row. A caller must treat None exactly like
    UNKNOWN liveness -- never as license to collect.
    """
    if not contract_id:
        return None
    con = _ro_db_connect()
    if con is None:
        return None
    try:
        row = con.execute(
            "select agent_state, cut_reason from agent_contract_handoffs "
            "where contract_id = ?",
            (contract_id,),
        ).fetchone()
    except Exception:
        return None
    finally:
        try:
            con.close()
        except Exception:
            pass
    if not row:
        return None
    return {"agent_state": row[0], "cut_reason": row[1]}


# ---------------------------------------------------------------------------
# The decision -- the one place all five cases are adjudicated.
# ---------------------------------------------------------------------------

def worktree_collect_reason(
    contract_id: str,
    mtime: float,
    *,
    grace_hours: Optional[int] = None,
    now: Optional[float] = None,
) -> Optional[str]:
    """Why *contract_id*'s worktree is collectible, or None to protect it.

    Checked in this order, each one a DIFFERENT death proof:

      1. An explicit death-proving cut_reason (case (d)) -- collected
         immediately, no grace wait: the row already records that an
         external mechanism determined this turn will never be finalized
         by its own agent.
      2. A TERMINAL verdict (case (a)) -- collected once quiet past the
         grace window, mirroring ``fs_rules.collectable_turn_scoped``.
      3. Otherwise (the contract has neither closed nor been explicitly
         reaped): fall through to the liveness signal alone (cases (b) and
         (c)) -- ``session_dead_past_grace`` is the only source of truth
         here, never this contract's own state.

    A row that cannot be read at all (case (e)) returns None here via
    ``_contract_row`` -- there is nothing to check, so nothing is
    collected.
    """
    row = _contract_row(contract_id)
    if row is None:
        return None

    cut_reason = row.get("cut_reason")
    if cut_reason in EXPLICIT_DEATH_CUT_REASONS:
        return f"explicit cut_reason={cut_reason} proves the turn was reaped, not running"

    hours = resolve_grace_hours() if grace_hours is None else grace_hours
    current = time.time() if now is None else now

    if row.get("agent_state") in TERMINAL_PLAN_STATUSES:
        if (current - mtime) < hours * 3600:
            return None
        return f"owning contract {contract_id} reached a terminal verdict, quiet {hours}h"

    if session_dead_past_grace(contract_id, mtime, grace_hours=hours, now=current):
        return f"owning contract {contract_id} is unfinished but its session is dead, quiet {hours}h"

    return None


# ---------------------------------------------------------------------------
# Enumeration -- reads the git lock, never a directory name (see gaia.worktree).
# ---------------------------------------------------------------------------

def list_managed_worktrees(repo_path: Path) -> List[Dict[str, object]]:
    """Every git-registered worktree of *repo_path* that carries a
    Gaia-minted lock identity.

    Runs ``git worktree list --porcelain`` (machine output, one blank-line-
    separated record per worktree) and keeps only entries whose ``locked``
    line's reason round-trips through ``gaia.worktree.parse_lock_reason`` --
    a worktree locked by a human or another tool for an unrelated purpose
    carries no parseable identity and is left out entirely; this module
    only ever judges worktrees it can prove Gaia created.

    Returns a list of ``{"path": Path, "contract_id": str, "agent_id": str}``.
    Raises ``subprocess.CalledProcessError`` if git itself fails (an invalid
    *repo_path*, for instance) -- callers decide how to handle that.
    """
    out = subprocess.run(
        ["git", "-C", str(repo_path), "worktree", "list", "--porcelain"],
        check=True, capture_output=True, text=True,
    ).stdout

    results: List[Dict[str, object]] = []
    current_path: Optional[str] = None
    current_locked_reason: Optional[str] = None

    def _flush() -> None:
        if current_path is None:
            return
        identity = parse_lock_reason(current_locked_reason)
        if identity is None:
            return
        results.append({
            "path": Path(current_path),
            "contract_id": identity["contract_id"],
            "agent_id": identity["agent_id"],
        })

    for line in out.splitlines():
        if not line.strip():
            _flush()
            current_path = None
            current_locked_reason = None
            continue
        if line.startswith("worktree "):
            current_path = line[len("worktree "):]
        elif line.startswith("locked"):
            rest = line[len("locked"):].strip()
            current_locked_reason = rest or None
    _flush()

    return results


# ---------------------------------------------------------------------------
# Orchestration -- decide, then hand off to task 13's mechanism verbatim.
# ---------------------------------------------------------------------------

def collect_worktrees(
    repo_path: Path,
    *,
    workspace: str,
    brief_slug: str,
    ac_id: str,
    grace_hours: Optional[int] = None,
    now: Optional[float] = None,
    task_id: Optional[str] = None,
    created_by_agent: Optional[str] = None,
    db_path=None,
    dry_run: bool = False,
) -> List[Dict[str, object]]:
    """Reclaim every collectible agentic worktree under *repo_path*.

    For each Gaia-minted worktree (``list_managed_worktrees``), asks
    ``worktree_collect_reason`` whether it is collectible; if so, hands it
    to ``gaia.retention.worktree_reclaim.reclaim_worktree`` UNCHANGED --
    this function never captures or removes anything itself. A worktree
    ``worktree_collect_reason`` protects (or cannot judge) is left
    completely alone and does not appear in the returned list at all.

    ``repo_path`` needs only to belong to the repository's worktree
    family, not to be its main working tree -- ``git worktree list`` run
    from ANY member (main or linked) enumerates every worktree the
    repository has registered, wherever it physically lives. This is what
    lets task 17's caller (``gaia cleanup``) inventory both Gaia's central
    worktrees root and the harness-native ``.claude/worktrees`` folder
    inside a repo with a single call: git's own worktree registry is
    per-repository, not per-directory, so there is no separate per-root
    scan to write here.

    ``dry_run=True`` runs the exact same ``worktree_collect_reason``
    decision per entry but never calls ``reclaim_worktree`` -- no capture,
    no deposit, no removal. This keeps preview and real sweep on one
    decision path rather than two that could silently diverge (the same
    class of bug ``bin/cli/cleanup.py``'s contract-drafts rule was found
    to have before it was fixed to delegate wholesale to a shared
    predicate).

    Returns one dict per COLLECTIBLE worktree. When ``dry_run`` is False,
    each is ``{"path": Path, "contract_id": str, "collect_reason": str,
    **reclaim_worktree's own result}``; when True, ``status`` is
    ``"would_reclaim"`` and no capture/removal fields are meaningful.
    """
    out: List[Dict[str, object]] = []
    for entry in list_managed_worktrees(repo_path):
        worktree_path = entry["path"]
        contract_id = entry["contract_id"]
        try:
            mtime = Path(worktree_path).stat().st_mtime
        except OSError:
            continue

        reason = worktree_collect_reason(
            contract_id, mtime, grace_hours=grace_hours, now=now
        )
        if reason is None:
            continue

        if dry_run:
            out.append({
                "path": worktree_path,
                "contract_id": contract_id,
                "collect_reason": reason,
                "status": "would_reclaim",
                "recycled": False,
                "captured": False,
                "evidence_id": None,
            })
            continue

        result = reclaim_worktree(
            repo_path, worktree_path,
            workspace=workspace, brief_slug=brief_slug, ac_id=ac_id,
            task_id=task_id, created_by_agent=created_by_agent, db_path=db_path,
        )
        out.append({
            "path": worktree_path,
            "contract_id": contract_id,
            "collect_reason": reason,
            **result,
        })
    return out
