"""
Handoff persistence helper -- CONDITIONAL BACKSTOP finalizer (T9).

Shared module used by both the production adapter path
(adapters/claude_code.py -> adapt_subagent_stop) and the legacy test-entry
path (subagent_stop.py -> subagent_stop_hook).

Moved here from subagent_stop.py to break the circular-import risk that would
arise if the adapter imported _persist_handoff directly from subagent_stop
(which itself imports from the adapter's dependency tree).

Role (brief contract-as-managed-data, task T9 -- SUPERSEDES the original
"persist_handoff inserts the row" role):
    The PRIMARY writer of the terminal ``agent_contract_handoffs`` row is the
    agent itself, via ``gaia contract finalize`` ->
    ``gaia.store.writer.finalize_agent_contract_handoff`` (an idempotent
    UPSERT keyed on ``contract_id``). This SubagentStop hook path is now a
    CONDITIONAL BACKSTOP: on stop, it writes a row ONLY IF no row exists yet
    for the resolved ``contract_id``, marking that backstop-written row
    ``degraded=true`` / ``auto_captured=true`` (it was captured by the hook,
    NOT produced by the agent's own verified finalize). Together this gives:

      * never-lost   -- a turn that crashes / forgets / is truncated before
                        finalize still leaves a row (the draft finalized as
                        degraded, or a minimal degraded row when no draft
                        exists) -- exactly one row, never zero.
      * exactly-once -- under a race between the agent finalize and this hook
                        backstop, both key on the SAME ``contract_id`` and the
                        writer's ``ON CONFLICT(contract_id) DO NOTHING`` leaves
                        exactly one row. The existence check here is the
                        fast-path that lets the backstop stay fully passive
                        when the agent already finalized; the UPSERT is the
                        hard guarantee under true concurrency.

SECOND, INDEPENDENT RESPONSIBILITY -- closing the born-at-dispatch row:
    This module also owns the EXIT of the nascent ``agent_state='DISPATCHED'``
    row that the dispatch stamped (``insert_dispatched_handoff``). That is a
    separate job from the capture above, and it is unconditional: it runs on
    EVERY turn, not only on a crash.

    Why it cannot be the finalize path's job -- the TWO KEY SPACES. A born row
    is keyed by a synthetic dispatch id (``dispatch.{session}.{agent-name}.{key}``,
    with the agent NAME in its ``agent_id`` column, because at dispatch time the
    agent has not minted anything yet), while the agent's own ``gaia contract
    finalize`` keys on the draft id it minted for itself
    (``{minted-agent-id}.{token}``). The two never intersect. So a perfectly
    healthy turn writes its verdict to a DIFFERENT row, and the born row is left
    behind -- with a corrected fence or without one, crash or no crash. THIS
    MODULE IS THE ONLY LAYER THAT HOLDS BOTH IDENTITIES, which is why the exit
    belongs here and nowhere else. (Earlier revisions of this docstring claimed
    the backstop "is also the REAPER" that converges the nascent row on a crash.
    It never could: it looked the orphan up by the MINTED id, which a born row
    never carries. Zero rows were ever reaped. Both halves of that -- the wrong
    key space, and the assumption that only a crash leaves an orphan -- are what
    the two closure modes below fix.)

    Two closure modes, distinguished so the row never lies about the turn:
      * SUPERSEDED -- the turn DID record its own terminal contract row (its own
        finalize, a T11 salvage, or this module's capture on a prior fire). The
        born row is closed to a NON-COMPLETE state carrying
        ``superseded_by_contract_id``, the pointer to the row that holds the real
        verdict. It is deliberately NOT marked degraded (nothing degraded -- the
        turn worked) and deliberately NOT marked COMPLETE, so the outcome is
        counted exactly once, on the contract row that earned it.
      * REAPED -- no terminal row exists for the turn at all: it never finalized.
        The born row converges to a degraded, NON-COMPLETE verdict
        (``degraded`` + ``reaped``). Never a false COMPLETE -- an unfinalized turn
        never truly completed, and a false COMPLETE would falsely satisfy the
        briefs "plan closed => a COMPLETE handoff row exists" invariant.

    Either way the row leaves 'DISPATCHED', so a row still in that state means
    this hook never ran for its turn -- a genuine signal rather than the routine
    residue it used to be.

Agnosticism: the finalize logic lives in the harness-free core
(``gaia.store.writer`` + ``gaia.contract.drafts``). This module is the
Claude-Code adapter seam -- it only INVOKES that core as a backstop and maps
the harness session id and dispatch binding onto the row.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Mirrors the agent_contract_handoffs.agent_state CHECK enum (schema.sql) --
# the canonical agent_state values. A backstop row whose source envelope
# carries none of these (a crash / partial / missing agent_state) is recorded
# as IN_PROGRESS: honest ("this turn did not reach a verified terminal state")
# and -- crucially -- NOT 'COMPLETE', so it never falsely satisfies the
# briefs "plan closed => a COMPLETE handoff row exists" invariant
# (gaia/briefs/store.py, invariant 5). Only a genuine, valid COMPLETE envelope
# yields agent_state='COMPLETE'; the degraded flag then distinguishes it from
# an agent-finalized COMPLETE for any reader that checks finalize-verification.
_VALID_TASK_STATUSES = frozenset(
    {
        "IN_PROGRESS", "APPROVAL_REQUEST", "COMPLETE", "BLOCKED", "NEEDS_INPUT",
        "NEEDS_VERIFICATION",
    }
)

# NOTE on this module's relationship to gaia.state.TERMINAL_PLAN_STATUSES:
# the writer's own guard (finalize_agent_contract_handoff) is the AUTHORITATIVE
# convergence gate -- it now blocks a write ONLY when the row is already
# COMPLETE, and CONVERGES from every other (non-terminal) state, including a
# resumed draft's later, truer verdict. This module's pre-check below is
# DELIBERATELY more conservative than that: it stays passive whenever ANY row
# already exists (not only a COMPLETE one), because its job here is dedup
# between TWO hook-side capture mechanisms racing for the SAME turn (T11
# salvage vs. this T9 backstop -- see adapters/claude_code.py
# ``_salvage_truncated_draft``, "salvage wins, backstop stays passive"), not
# resuming a draft across separate turns. Broadening this pre-check to mirror
# the writer's guard 1:1 would let the backstop clobber a salvage-marked row
# with an equivalent, less-informative capture of the identical crashed turn --
# a provenance regression, not a correctness fix. The writer's own guard is
# what actually closes the "frozen non-terminal row" defect for the agent's
# OWN resumed ``gaia contract finalize`` calls, which this module never blocks.


def resolve_minted_agent_id(parsed_contract, task_info: dict):
    """Best available minted agent id (``^a[0-9a-f]{5,}$``) used to key drafts.

    Prefers the authoritative ``agent_status.agent_id`` from the parsed
    envelope (the exact value the CLI minted the draft with), falling back to
    ``task_info['agent_id']`` -- which on SubagentStop is the Claude-Code
    hook's ``agent_id``, the SAME identifier space drafts are keyed by (the
    ``^a[0-9a-f]{5,}$`` format, see ``_adapt_send_message``). This is why the
    fallback still locates the right draft when the fence is MISSING (no
    parsed envelope) -- the exact case the M4 reconstruction path relies on.
    Returns None when nothing usable is present.

    SHARED helper: the SINGLE resolver reused by the T9 backstop
    (``persist_handoff`` below), the truncation salvage
    (``ClaudeCodeAdapter._salvage_truncated_draft``), and the M4 missing-fence
    reconstruction (``ClaudeCodeAdapter._reconstruct_contract_from_finalized_draft``),
    so all three resolve the SAME draft (hence the SAME ``contract_id``) rather
    than each inlining the logic.
    """
    if isinstance(parsed_contract, dict):
        agent_status = parsed_contract.get("agent_status")
        if isinstance(agent_status, dict):
            aid = agent_status.get("agent_id")
            if aid:
                return str(aid)
    return task_info.get("agent_id") or task_info.get("agent") or None


# Backward-compatible private alias (pre-factorization name). Kept so any
# existing importer/reference continues to resolve to the shared helper.
_resolve_minted_agent_id = resolve_minted_agent_id


# The agent_state a closed born row is converged to. It must be a valid enum
# value that is NOT terminal-COMPLETE: the born row records a DISPATCH, never an
# outcome, so counting it as COMPLETE would double-count a turn whose verdict
# already lives on its own contract row. IN_PROGRESS is the honest floor this
# module already uses for "did not reach a verified terminal state by itself".
_CLOSED_DISPATCH_STATE = "IN_PROGRESS"


def dispatch_identity_candidates(minted_agent_id, task_info: dict) -> list:
    """Every identity a born-at-dispatch row could have been stamped with.

    Ordered most-likely-first and de-duplicated. The FIRST candidate is the
    harness agent NAME (``task_info['agent']`` -- ``gaia-verifier``,
    ``platform-architect``), because that is what production births under: the
    row is stamped in PreToolUse:Agent, before the agent has minted any id of
    its own, so the dispatch's only available identity is the target agent's
    name. The minted id and the harness ``agent_id`` follow as fallbacks, for
    tests and for any caller that births under the minted space.

    Passing this whole list (rather than one identity) to
    ``find_orphaned_dispatched_handoff`` is what makes orphan discovery
    key-space-agnostic: looking only under the minted id -- the previous
    behaviour -- matched nothing, ever.
    """
    ordered = [
        task_info.get("agent"),
        minted_agent_id,
        task_info.get("agent_id"),
    ]
    seen = set()
    candidates = []
    for value in ordered:
        if not value:
            continue
        text = str(value)
        if text in seen:
            continue
        seen.add(text)
        candidates.append(text)
    return candidates


def _extract_brief_id(envelope: dict):
    """Resolve brief_id from the envelope (direct field or update_contracts)."""
    if not isinstance(envelope, dict):
        return None
    brief_id = envelope.get("brief_id")
    if not brief_id:
        for entry in envelope.get("update_contracts", []) or []:
            if isinstance(entry, dict) and isinstance(entry.get("payload"), dict):
                candidate = entry["payload"].get("brief_id")
                if candidate:
                    brief_id = candidate
                    break
    if isinstance(brief_id, str):
        try:
            return int(brief_id)
        except (ValueError, TypeError):
            return None
    return brief_id or None


def close_born_dispatch_row(
    _writer,
    *,
    session_id: str,
    identity_candidates: list,
    workspace: str,
    contract_pointer: "str | None",
    turn_recorded_own_contract: bool,
    db_path=None,
    skip_contract_id: "str | None" = None,
) -> "dict | None":
    """Take the turn's born-at-dispatch row out of 'DISPATCHED'. Runs EVERY turn.

    See the module docstring ("SECOND, INDEPENDENT RESPONSIBILITY") for why this
    is owned here and not by the finalize path, and for the SUPERSEDED vs REAPED
    distinction.

    TWO ORTHOGONAL FACTS are recorded, deliberately kept apart because they
    answer different questions and a reader orients by them differently:

      * ``contract_pointer`` -> ``superseded_by_contract_id``. A pure LINK: where
        this turn's contract row actually is. It is written in BOTH modes and
        carries no judgement about that row's quality. This is what makes the
        chain ``plan task -> producer contract -> verifier contract`` walkable
        across the two key spaces.
      * ``turn_recorded_own_contract`` -> the VERDICT flags. False means the turn
        never recorded a contract row of its own, so the born row is REAPED:
        ``degraded`` + ``reaped``, an honest non-COMPLETE. True means the turn
        worked, and NO verdict flag is added -- in particular not ``degraded``,
        which would otherwise stamp a fresh degraded row on every healthy bound
        dispatch and drown the population that flag exists to identify.

    Never COMPLETE in either mode: the born row records a DISPATCH, not an
    outcome. The outcome is counted exactly once, on the row the pointer names.

    ``skip_contract_id`` names a row the CALLER already converged in its own
    capture step. Passing it keeps the two steps from writing the same row twice.

    Idempotent and race-safe without a lock: only a row still in 'DISPATCHED' is
    touched, and the convergence goes through the same UPSERT the capture uses.
    Whoever gets there first moves the row out of 'DISPATCHED'; a second arrival
    finds no orphan and does nothing. So this adds NO row -- it only changes the
    state of one that the dispatch already created.

    Returns the writer outcome dict when a row was closed, else None. Never
    raises -- a failure here must not cost the turn its captured contract row.
    """
    import json as _json

    try:
        orphan = _writer.find_orphaned_dispatched_handoff(
            session_id, identity_candidates, db_path=db_path
        )
        if orphan is None:
            return None
        born_contract_id = orphan["contract_id"]
        if skip_contract_id and born_contract_id == skip_contract_id:
            return None

        envelope = {
            "born_at_dispatch": True,
            "dispatch_closed_at_subagent_stop": True,
        }
        if contract_pointer:
            envelope["superseded_by_contract_id"] = contract_pointer
        if not turn_recorded_own_contract:
            envelope["degraded"] = True
            envelope["auto_captured"] = True
            envelope["backstop"] = "hook_subagent_stop"
            envelope["reaped"] = True

        outcome = _writer.finalize_agent_contract_handoff(
            contract_id=born_contract_id,
            # The born row's own dispatch identity -- preserved, never rewritten
            # to the minted id, so the row stays queryable in the space it was
            # born in and the two identity spaces are not silently merged.
            agent_id=identity_candidates[0],
            workspace=workspace,
            agent_state=_CLOSED_DISPATCH_STATE,
            raw_handoff_json=_json.dumps(envelope),
            session_id=session_id,
            db_path=db_path,
        )
        logger.info(
            "Dispatch row closed: contract_id=%s mode=%s (contract=%s)",
            born_contract_id,
            "superseded" if turn_recorded_own_contract else "reaped",
            contract_pointer,
        )
        return outcome
    except Exception as _exc:
        logger.warning(
            "Closing the born-at-dispatch row failed (non-blocking): %s", _exc
        )
        return None


def persist_handoff(
    parsed_contract,
    agent_output: str,
    task_info: dict,
    session_id: str,
    plan_task_id: "int | None" = None,
) -> None:
    """Conditional BACKSTOP finalize of the agent_contract_handoffs row.

    Called synchronously inside the SubagentStop hook lifecycle. Failures are
    suppressed so a DB write error never interrupts hook processing.

    TWO INDEPENDENT JOBS, in this order (see the module docstring for both):
    the CAPTURE (steps 1-4, conditional -- ensure the turn has a contract row)
    and the CLOSE (step 5, unconditional -- take the born-at-dispatch row out of
    'DISPATCHED'). They target DIFFERENT rows in different key spaces, so
    neither competes with the other for a row and the turn's row count is
    unchanged: the capture never adopts the born row, and the close never
    touches the capture's.

    Logic (see module docstring for the never-lost / exactly-once rationale):
    1. Resolve the ``contract_id`` (idempotency key) and a source envelope:
       prefer the agent's own on-disk draft (same key its ``gaia contract
       finalize`` UPSERTs on, so a race converges to one row); otherwise
       synthesize a deterministic backstop id for this (agent, session).
    2. CONDITIONAL on the current row state:
       * a TERMINAL row already exists (the agent finalized) -> capture nothing,
         stay fully passive; step 5 still runs.
       * a NASCENT 'DISPATCHED' row exists under the CAPTURE's own contract_id
         -> converge it to a degraded NON-COMPLETE verdict.
       * no row at all -> write a degraded row.
    3. Finalize (convergent, idempotent writer) a row marked ``degraded=true`` /
       ``auto_captured`` (and ``reaped=true`` when reconciling an orphan),
       without fabricating fields the hook lacks. A reaped orphan is never
       recorded COMPLETE -- an unfinalized turn never truly completed.
    4. If the backstop actually wrote the row AND the envelope carried an
       approval_request, record the linked approvals audit row.
    5. ALWAYS: close the born-at-dispatch row (``close_born_dispatch_row``),
       superseded when the turn recorded its own terminal contract row, reaped
       when it never finalized at all.

    ``plan_task_id`` is the binding the SubagentStop adapter already resolved for
    this turn; passing it stamps the attribution the CLI finalize path cannot
    supply on its own. None leaves any existing binding untouched.
    """
    import json as _json
    import os as _os
    import pathlib as _pl
    import sys as _sys

    try:
        # Prefer a sibling gaia package if installed; fall back to the repo
        # layout where gaia/ lives two levels above hooks/.
        try:
            from gaia.store import writer as _writer
        except ImportError:
            _repo_root = _pl.Path(__file__).resolve().parent.parent.parent.parent
            _sys.path.insert(0, str(_repo_root))
            from gaia.store import writer as _writer

        minted_agent_id = resolve_minted_agent_id(parsed_contract, task_info)
        # agent_id stored in the NOT NULL row column.
        agent_id = minted_agent_id or task_info.get("agent") or "unknown"

        workspace = (
            task_info.get("workspace")
            or _os.environ.get("GAIA_WORKSPACE")
            or "global"
        )
        db_path_str = task_info.get("db_path")
        db_path = _pl.Path(db_path_str) if db_path_str else None

        # --- 1. Resolve contract_id (idempotency key) + source envelope ------
        contract_id = None
        source_envelope = None

        # 1a. Prefer the agent's own on-disk draft -- the SAME contract_id its
        #     `gaia contract finalize` would UPSERT on, so a finalize+backstop
        #     race converges to one row.
        try:
            from gaia.contract.drafts import load_draft, resolve_draft_id

            if minted_agent_id:
                draft_id = resolve_draft_id(
                    explicit=None, agent_id=str(minted_agent_id)
                )
                if draft_id:
                    loaded = load_draft(draft_id)
                    if loaded is not None:
                        contract_id = draft_id
                        source_envelope = loaded
        except Exception:
            # drafts substrate unavailable / unreadable -> synthesize below.
            pass

        # 1b. NO adoption of the born-at-dispatch row as the capture target --
        #     deliberately. A previous revision, when no draft resolved, re-keyed
        #     this capture onto the orphaned nascent row's contract_id. That was
        #     dead code (it looked the orphan up in the wrong identity space and
        #     never once resolved), and reviving it would be actively harmful:
        #     the capture id is what distinguishes a FENCE-ONLY turn -- one that
        #     emitted a valid fence but never ran `gaia contract init`, so no
        #     draft exists -- by its synthetic `hook-backstop.*` id. Folding
        #     those turns onto the dispatch key space would merge two structurally
        #     distinct populations into one indistinguishable route, losing the
        #     ability to tell which one happened. The capture keeps its own key
        #     space; the born row is closed separately, in step 5, by the one
        #     writer that owns it.
        #
        # 1c. No draft: synthesize a deterministic backstop id. A turn with no
        #     draft never ran finalize, so there is no primary-path row to
        #     converge with; the deterministic id only makes a re-fire of the
        #     hook for the same (agent, session) idempotent against itself.
        if not contract_id:
            sid = session_id or "nosession"
            contract_id = f"hook-backstop.{agent_id}.{sid}"
            if isinstance(parsed_contract, dict):
                source_envelope = parsed_contract

        # --- 2-4. The CAPTURE, as a closure ---------------------------------
        # Scoped as a nested function for one structural reason: the capture has
        # several legitimate early exits (already-terminal row, nothing created,
        # no handoff id), and step 5 must run on EVERY path anyway. Returning out
        # of a closure ends the capture, not the whole function, so "the born row
        # is always closed" is guaranteed by the shape of the code rather than by
        # remembering to duplicate a call before each return.
        #
        # Returns True when a TERMINAL row for this turn ALREADY existed before
        # the capture ran -- i.e. the turn recorded its own contract (its own
        # finalize, or a T11 salvage) and this backstop had nothing to add. Step 5
        # uses that to decide superseded-vs-reaped.
        def _capture() -> bool:
            # A row for this contract_id may already exist in one of two ways --
            #   * TERMINAL (agent_state != 'DISPATCHED') -> some capture already
            #     ran for this turn (the agent's own finalize, T11 salvage, or a
            #     prior backstop) -> stay PASSIVE. See the module-level NOTE above
            #     for why this pre-check deliberately does not narrow to "only
            #     COMPLETE blocks" the way the writer's own guard does.
            #   * NASCENT 'DISPATCHED' -> only reachable when the born row and the
            #     capture happen to share a contract_id (they do not in
            #     production; see step 1b) -> converge it to a degraded
            #     NON-COMPLETE verdict, never a false COMPLETE, through the same
            #     idempotent convergent writer.
            #   * None -> no row at all -> write a degraded row.
            existing_state = _writer.agent_contract_handoff_state(
                contract_id, db_path=db_path
            )
            if existing_state is not None and existing_state != "DISPATCHED":
                logger.debug(
                    "T9 backstop: terminal row already exists for contract_id=%s "
                    "(state=%s); no-op.",
                    contract_id, existing_state,
                )
                return True
            reaping = existing_state == "DISPATCHED"

            # --- 3. Build the degraded / auto-captured row -------------------
            if isinstance(source_envelope, dict):
                envelope = dict(source_envelope)
                agent_status = envelope.get("agent_status")
                agent_state = (
                    agent_status.get("agent_state")
                    if isinstance(agent_status, dict)
                    else None
                )
            else:
                # No draft AND no parsed contract (a truncated / crashed turn):
                # a MINIMAL row -- do not fabricate evidence fields we lack.
                envelope = {
                    "agent_output_preview": agent_output[:200] if agent_output else "",
                }
                agent_state = None

            agent_state = (
                agent_state if agent_state in _VALID_TASK_STATUSES else "IN_PROGRESS"
            )

            # An orphaned nascent DISPATCHED row is being reconciled after a crash
            # -- the agent never ran its own verified `gaia contract finalize`, so
            # this turn MUST NOT be recorded COMPLETE. A false COMPLETE would
            # falsely satisfy the briefs "plan closed => a COMPLETE handoff row
            # exists" invariant (gaia/briefs/store.py, invariant 5) for a turn that
            # never truly completed. Downgrade a COMPLETE claim to the honest
            # IN_PROGRESS; any other (already non-COMPLETE) state is faithful and
            # left untouched.
            if reaping and agent_state == "COMPLETE":
                agent_state = "IN_PROGRESS"

            # Backstop PROVENANCE -- unchanged in meaning by this revision. It
            # records WHERE the row came from (captured by the hook, not written
            # by the agent's own verified `gaia contract finalize`), NOT how much
            # its contents can be trusted: a degraded row routinely carries the
            # agent's complete verbatim fence. We add flags only -- never
            # synthetic evidence -- and nothing here is set on a turn that
            # recorded its own contract row.
            envelope["degraded"] = True
            envelope["auto_captured"] = True
            envelope["backstop"] = "hook_subagent_stop"
            if reaping:
                # Distinguish a reaped-orphan row (nascent DISPATCHED converged by
                # the backstop) from a plain no-row-yet degraded capture.
                envelope["reaped"] = True

            raw_handoff_json = _json.dumps(envelope)
            brief_id = _extract_brief_id(envelope)

            outcome = _writer.finalize_agent_contract_handoff(
                contract_id=contract_id,
                agent_id=agent_id,
                workspace=workspace,
                agent_state=agent_state,
                raw_handoff_json=raw_handoff_json,
                session_id=session_id,
                plan_task_id=plan_task_id,
                brief_id=brief_id,
                db_path=db_path,
            )

            # --- 4. Approvals audit row (only when the backstop wrote the row) -
            # If the row already existed (another writer won the race), the
            # backstop stays passive -- consistent with the conditional contract.
            if not outcome.get("created"):
                return False
            handoff_id = outcome.get("handoff_id")
            if handoff_id is None:
                return False

            if isinstance(source_envelope, dict):
                approval_req = source_envelope.get("approval_request")
                if approval_req and isinstance(approval_req, dict):
                    approval_id = approval_req.get("approval_id")
                    if approval_id:
                        try:
                            grants = _writer.list_approval_grants(
                                session_id=session_id
                            )
                            decision = "APPROVED"
                            decided_at_val = _writer._now_iso()
                            for g in grants:
                                if g.get("approval_id") == approval_id:
                                    grant_status = g.get("status", "PENDING")
                                    if grant_status == "CONSUMED":
                                        decision = "APPROVED"
                                    elif grant_status == "REVOKED":
                                        decision = "REVOKED"
                                    elif grant_status == "EXPIRED":
                                        decision = "EXPIRED"
                                    else:
                                        # PENDING treated as granted
                                        decision = "APPROVED"
                                    decided_at_val = (
                                        g.get("consumed_at")
                                        or g.get("revoked_at")
                                        or decided_at_val
                                    )
                                    break

                            _writer.insert_handoff_approval(
                                handoff_id=handoff_id,
                                approval_id=approval_id,
                                decision=decision,
                                decided_at=decided_at_val,
                                db_path=db_path,
                            )
                        except Exception as _approval_exc:
                            logger.warning(
                                "T9 backstop: approval row write failed for "
                                "handoff_id=%s: %s",
                                handoff_id, _approval_exc,
                            )
            return False

        turn_recorded_own_contract = _capture()

        # --- 5. ALWAYS: close the born-at-dispatch row -----------------------
        # Runs on every turn, healthy or not, because a born row is left behind
        # by a NORMAL turn too -- the capture above and the dispatch row live in
        # different key spaces (step 1b), so the capture never converges it. The
        # turn's contract row is at `contract_id` either way (already there, or
        # just written), so that is always the pointer; whether the turn got there
        # ITSELF is the separate fact that decides reaped-vs-superseded.
        # `skip_contract_id` keeps this from re-writing a row the capture already
        # converged.
        close_born_dispatch_row(
            _writer,
            session_id=session_id,
            identity_candidates=dispatch_identity_candidates(
                minted_agent_id, task_info
            ),
            workspace=workspace,
            contract_pointer=contract_id,
            turn_recorded_own_contract=turn_recorded_own_contract,
            db_path=db_path,
            skip_contract_id=contract_id,
        )

    except Exception as _exc:
        logger.error(
            "T9 backstop: handoff persistence failed (non-blocking): %s",
            _exc, exc_info=True,
        )
