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

    When it IS the finalize path's job, and when it falls here. The dispatch
    mints a REAL, adoptable identity for the row (``dispatch_identity``: an
    ``a``+hex ``agent_id`` and a ``{agent_id}.{token}`` ``contract_id``),
    pre-creates the on-disk draft under it (``dispatch_binding._precreate_draft``),
    and SubagentStart hands both halves to the turn inside the dispatch kernel
    (``# Your Contract``). A turn that writes that draft (its first
    ``gaia contract set/add/fill --draft-id ...``) finalizes under the same
    ``contract_id`` the row was born with, so its own finalize CONVERGES the
    born row -- there is one row, already closed, and nothing here to supersede.
    The closure below is for the turn that did NOT adopt: no kernel reached it
    (the claim failed or was refused), or it minted a rival id anyway, so its
    verdict landed on a DIFFERENT row and the born one is left behind -- with a
    corrected fence or without one, crash or no crash. A turn that adopted but
    never finalized no longer reaches that closure at all: the CAPTURE keys on
    the born row and converges it first, and the closure then finds nothing left
    to close. THIS MODULE IS THE ONLY LAYER THAT HOLDS BOTH
    IDENTITIES, which is why that exit belongs here and nowhere else. (Earlier
    revisions of this docstring claimed the backstop "is also the REAPER" that
    converges the nascent row on a crash. It never could: it looked the orphan up
    by the MINTED id, which a born row did not carry back when birth used a
    synthetic dispatch key. Zero rows were ever reaped. Both halves of that -- the
    wrong key space, and the assumption that only a crash leaves an orphan -- are
    what the two closure modes below fix.)

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
from typing import Optional

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
# a provenance regression, not a correctness fix.
#
# WHERE A RESUMED TURN'S OWN CLOSE GOES, and why this module is unaffected: an
# agent that already declared a close and writes again lands in a CONTINUATION
# (gaia.store.writer.open_contract_continuation), so its later
# ``gaia contract finalize`` converges the LINK -- a fresh DISPATCHED row -- and
# never the record it closed. This module reaches that same link because
# ``dispatch_row_by_harness_id`` collapses the chain to its live tip before
# judging ambiguity; the passive-when-a-row-exists posture above is unchanged by
# any of it.


def _minted_agent_id_from_transcript(task_info: dict):
    """Recover the CLI-minted agent id from the turn's own transcript.

    Imported lazily and fully guarded: this module is also loaded from the
    harness-free test entry path, and a missing/unreadable transcript must
    degrade to "unknown", never raise inside a stop hook.
    """
    path = task_info.get("agent_transcript_path")
    if not path:
        return None
    try:
        from .transcript_reader import extract_minted_agent_id_from_transcript
        return extract_minted_agent_id_from_transcript(str(path))
    except Exception as exc:
        logger.debug("Minted-id recovery from transcript failed: %s", exc)
        return None


# How many rows the harness-id lookup fetches before collapsing a continuation
# chain. It must exceed the longest chain a turn can accumulate (a handful of
# resumptions in practice) so a chain is never truncated into a false ambiguity,
# while staying small enough that a genuine multi-row collision is still cheap to
# detect and decline.
_HARNESS_ID_ROW_FETCH_LIMIT = 16


def dispatch_row_by_harness_id(task_info: dict, session_id=None, db_path=None):
    """THE bridge between the two identifier spaces: the row that holds both.

    A dispatch row is the only artifact that knows both halves. ``agent_id`` is
    the minted handle it was born under (``insert_dispatched_handoff``);
    ``harness_agent_id`` is the harness's own per-run id for the SAME dispatch
    (``gaia.store.writer.stamp_harness_agent_id``, written at the SubagentStart
    claim). ``task_info['agent_id']`` at SubagentStop time IS that harness id.

    THE JOIN IS ON harness_agent_id ALONE, and the session is a CONSISTENCY
    CHECK rather than a SQL filter. The harness mints that id per dispatch run,
    so it already identifies one turn; adding ``session_id`` to the WHERE clause
    cannot make the match more exact, but it CAN lose a correct row whose
    session attribution is absent or recorded differently. Checking it after the
    fact keeps both properties: a mismatch is refused out loud, an unknown or
    absent session is not treated as a mismatch.

    Ambiguity is DECLINED, never resolved by recency -- the same refusal, for
    the same reason, as ``gaia.store.writer.find_dispatched_row_by_agent_name``.
    A recency tiebreak here is precisely what bound a turn to a residue row
    instead of its own.

    A CONTINUATION CHAIN IS NOT AN AMBIGUITY, and this is the one case where
    several rows under one harness id are not rival candidates. A resumption
    carries the SAME harness agent id as the turn it continues (the harness
    stamps it per run, not per resumption), so once a resumed turn opens a
    continuation, this lookup returns EVERY link of that chain. Read as rival
    matches they would trip the refusal above, the bridge would resolve nothing,
    and the gate would reject the close of a turn whose work is perfectly
    recorded -- turning the fix for lost work into a lost close. The rows are
    therefore collapsed to the chain's live link first
    (``gaia.store.writer.collapse_continuation_chains``, a pure reduction that
    drops only rows another row in the SAME result set continues). Two unrelated
    rows still collapse to two, so a genuine ambiguity is still declined; the
    fetch limit rises from 2 to a small bound so a multi-link chain is seen
    whole rather than truncated into a false ambiguity.

    Returns the full row dict, or None. Fully guarded: this runs inside a stop
    hook, so an unavailable store degrades to None, never to a raise.
    """
    harness_agent_id = task_info.get("agent_id")
    if not harness_agent_id or str(harness_agent_id) == "unknown":
        return None
    try:
        from pathlib import Path as _Path

        from gaia.store.writer import (
            collapse_continuation_chains,
            list_agent_contract_handoffs,
        )

        db_path_str = task_info.get("db_path")
        rows = list_agent_contract_handoffs(
            harness_agent_id=str(harness_agent_id),
            limit=_HARNESS_ID_ROW_FETCH_LIMIT,
            db_path=db_path or (_Path(db_path_str) if db_path_str else None),
        )
        if not rows:
            return None
        rows = collapse_continuation_chains(rows)
        if len(rows) > 1:
            logger.warning(
                "Dispatch-row bridge declined: harness_agent_id=%s matches %d "
                "rows; refusing to guess which is this turn's.",
                harness_agent_id, len(rows),
            )
            return None
        row = rows[0]
        row_session = row.get("session_id")
        if session_id and row_session and str(row_session) != str(session_id):
            logger.warning(
                "Dispatch-row bridge declined: harness_agent_id=%s resolves row "
                "contract_id=%s, but its session_id=%r disagrees with this "
                "turn's %r.",
                harness_agent_id, row.get("contract_id"), row_session, session_id,
            )
            return None
        return row
    except Exception as exc:
        logger.debug("Dispatch-row bridge failed: %s", exc)
        return None


def is_minted_handle(value) -> bool:
    """True iff ``value`` has the shape a draft is keyed by
    (``gaia.contract.validator.AGENT_ID_PATTERN_TEXT``).

    Every value that reaches a draft glob passes through here first, whatever
    lane produced it -- a row column, a fence field, a transcript scrape. The
    shape check cannot tell WHOSE handle it is (the harness id matches the same
    pattern), so it is not an ownership proof; it only keeps a value that could
    never key a draft out of the key space.

    Fails CLOSED: an unavailable validator answers False. Every caller has a lane
    that survives a False -- the bridge returns None, the capture keys on the
    born row or its synthetic id -- whereas answering True on an unverifiable
    value would put an arbitrary string back into the space this check protects.
    """
    if not value:
        return False
    try:
        import re as _re

        from gaia.contract.validator import AGENT_ID_PATTERN_TEXT

        return bool(_re.match(AGENT_ID_PATTERN_TEXT, str(value)))
    except Exception as exc:
        logger.debug("Minted-handle validation unavailable: %s", exc)
        return False


def _minted_agent_id_from_dispatch_row(task_info: dict, session_id):
    """The minted handle carried by this turn's own dispatch row, or None.

    Accepted ONLY when it satisfies ``AGENT_ID_PATTERN_TEXT``. A legacy row born
    before the identity was minted carries the agent NAME in ``agent_id``, and
    returning that would recreate, one lane lower, exactly the space confusion
    this resolver exists to prevent.
    """
    row = dispatch_row_by_harness_id(task_info, session_id)
    if row is None:
        return None
    candidate = row.get("agent_id")
    if is_minted_handle(candidate):
        return str(candidate)
    logger.warning(
        "Minted-id bridge found row contract_id=%s but its agent_id=%r is "
        "not a minted handle; not usable as a draft key.",
        row.get("contract_id"), candidate,
    )
    return None


def resolve_minted_agent_id(parsed_contract, task_info: dict, *, session_id=None):
    """Best available minted agent id (``gaia.contract.validator.
    AGENT_ID_PATTERN_TEXT``) used to key drafts.

    TWO IDENTIFIER SPACES, and only one of them keys drafts. The CLI mints its
    OWN agent id in ``gaia contract init`` and keys the on-disk draft by
    ``{minted-agent-id}.{token}``; the harness independently stamps
    ``hook_data['agent_id']`` (``task_info['agent_id']``). Both match
    ``^a[0-9a-f]{16,}$``, so they are indistinguishable by shape and nothing
    fails loudly when they are confused -- ``resolve_draft_id`` just globs
    ``{harness-id}.*``, matches no file, and returns None. That silent miss is
    what disabled BOTH draft rescues at once (the M4 reconstruction and the T9
    backstop's step 1a). The harness id is therefore NOT the draft key space,
    and this resolver never treats it as one.

    EVERY lane is shape-checked (:func:`is_minted_handle`) before it is
    returned, including the fence. The fence is the one lane whose value is
    written by the agent rather than read back from Gaia's own substrate, so it
    is also the only one that arrives with no guarantee at all -- the measured
    population includes ``execution-approved``, ``a_placeholder`` and a run of
    zeroes. Taking such a value verbatim did not fail: it globbed a draft
    directory that could not contain a match, returned no draft, and sent the
    capture to a synthetic id -- the loud symptom of a wrong key is
    indistinguishable from the quiet one of no draft, which is why it survived.

    Resolution order, most authoritative first:
      1. ``agent_status.agent_id`` from the parsed envelope -- the exact value
         the CLI minted, when a fence was emitted and the value it carries is
         actually a minted handle.
      2. ``task_info['minted_agent_id']`` -- precomputed once per turn by
         ``task_info_builder`` from the transcript.
      3. The transcript itself (``agent_transcript_path``), scanned for the
         turn's own ``gaia contract init`` mint report -- the one appearance of
         a draft id that proves the turn OWNS it, rather than merely mentions
         another agent's.
      4. The DISPATCH ROW, joined by ``harness_agent_id``
         (:func:`_minted_agent_id_from_dispatch_row`). This is the lane the
         CURRENT dispatch shape needs and the three above cannot serve: a turn
         born with its draft already open never runs ``gaia contract init``, so
         there is no mint report for lanes 2/3 to find, and a turn that stops
         emitting the fence leaves nothing for lane 1 either. The row knows both
         identities; this crosses between them instead of guessing.

    Returns None -- and says so in the log -- when nothing usable is present.
    THE ABSENT LAST RESORT IS THE POINT. This used to fall back to the harness
    ``agent_id``/agent name as a "label", which is not merely useless as a draft
    key: being non-empty, it SATISFIES every ``if not minted_agent_id`` guard
    downstream and carries the wrong value forward. Measured cost (handoff row
    11304): the M4 reconstruction globbed a draft under the harness id, found
    nothing, and returned None without a single log line, so a complete
    ``update_contracts`` proposal in that turn's envelope was never processed
    and nothing reported it. A last resort that returns something incorrect
    converts a detectable failure into a silent one; returning None keeps the
    failure visible, and the callers that genuinely need a non-empty label for a
    degraded row (``persist_handoff``'s ``agent_id`` column,
    :func:`dispatch_identity_candidates`) already supply their own fallback.

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
            if is_minted_handle(aid):
                return str(aid)
            if aid:
                logger.warning(
                    "Fence agent_status.agent_id=%r is not a minted handle; "
                    "NOT used as a draft key (agent=%s harness_agent_id=%s). "
                    "Resolution continues with the lanes below.",
                    aid, task_info.get("agent"), task_info.get("agent_id"),
                )
    precomputed = task_info.get("minted_agent_id")
    if is_minted_handle(precomputed):
        return str(precomputed)
    recovered = _minted_agent_id_from_transcript(task_info)
    if is_minted_handle(recovered):
        return str(recovered)
    bridged = _minted_agent_id_from_dispatch_row(task_info, session_id)
    if bridged:
        return bridged
    logger.warning(
        "Minted agent id UNRESOLVED for this turn (agent=%s harness_agent_id=%s "
        "session=%s): no USABLE fence agent_status.agent_id, no precomputed "
        "mint, no mint report in the transcript, and no dispatch row reachable "
        "by harness_agent_id. Every draft-keyed rescue (M4 reconstruction, "
        "truncation salvage, backstop step 1a) is unavailable for this turn.",
        task_info.get("agent"), task_info.get("agent_id"), session_id,
    )
    return None


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


def clean_rescue_envelope(envelope, *, log: Optional[list] = None):
    """Sanitize and canonicalize an envelope a RESCUE lane is about to persist.

    The two rescue lanes -- this module's T9 backstop and the adapter's T11
    truncation salvage -- serialized whatever they found (an on-disk draft, or
    the parsed fence) straight to ``raw_handoff_json``. Only the CLI write path
    cleaned before persisting, so a fifth of the rows landed carrying undeclared
    keys and uncanonical enum spellings that every downstream reader then had to
    reconcile. This is the shared cleaning both lanes apply, so the rescue route
    persists what the CLI route persists.

    Two properties make it safe to run on the rescue path specifically, and both
    are load-bearing rather than defensive habit:

    * **The rescue must survive the cleaning.** These lanes run only when
      something already went wrong, on input nobody validated -- a half-written
      draft, a fence parsed out of a truncated turn. A raise here would abort the
      capture and lose the row entirely, which is strictly worse than persisting
      it dirty. So every failure falls back to the envelope as it arrived: a
      clean row when cleaning works, a dirty row when it cannot, never no row.
    * **The turn stays findable.** ``sanitize_envelope`` gates each declared key
      on its TYPE, not on its value's format, so a malformed-but-string
      ``agent_id`` (the observed population -- ``execution-approved``,
      ``a_placeholder``, sixteen zeros) passes through untouched, and
      ``canonicalize_envelope`` never reads the field at all. A NON-string one
      would be dropped as an unrepairable type, converting a bad value into a
      missing field and erasing the only handle back to the turn; that case is
      restored here in its string form. The value stays wrong, and honestly so
      -- it is preserved as evidence of what the turn actually wrote, not
      repaired into a conforming identity this layer has no way to know.

    Args:
        envelope: the envelope about to be serialized. A non-dict is returned
            unchanged -- the caller owns what an unusable input means.
        log: optional list collecting one line per change, for a caller that
            reports them.

    Returns:
        The cleaned envelope, or ``envelope`` itself when cleaning could not be
        applied. Never raises.
    """
    if not isinstance(envelope, dict):
        return envelope
    try:
        from gaia.contract.validator import (
            canonicalize_envelope,
            sanitize_envelope,
        )

        entries = log if log is not None else []
        cleaned = sanitize_envelope(envelope, removals=entries)
        cleaned = canonicalize_envelope(cleaned, changes=entries)

        raw_status = envelope.get("agent_status")
        clean_status = cleaned.get("agent_status")
        if isinstance(raw_status, dict) and isinstance(clean_status, dict):
            if "agent_id" in raw_status and "agent_id" not in clean_status:
                clean_status["agent_id"] = str(raw_status["agent_id"])
                entries.append(
                    "restored agent_status.agent_id as a string: the row must "
                    "stay traceable to the turn that wrote it"
                )
        return cleaned
    except Exception as exc:  # noqa: BLE001 -- see the docstring's first property
        logger.debug(
            "rescue envelope cleaning failed (non-fatal, persisting raw): %s", exc
        )
        return envelope


def _carry_birth_markers(_writer, contract_id, raw_handoff_json: str, db_path) -> str:
    """Carry a born row's dispatch marks onto the envelope about to replace it.

    Only ever ADDS the birth-marker keys the target row already carries, so an
    envelope that states them itself is left exactly as it is.

    Never raises: an unreadable row returns the envelope unchanged. What is at
    risk here is the row's provenance, never the capture -- a rescue that lost
    the row to protect a marker would have the trade backwards.
    """
    try:
        rows = _writer.list_agent_contract_handoffs(
            contract_id=contract_id, limit=1, db_path=db_path
        )
        if not rows:
            return raw_handoff_json
        return _writer.merge_birth_markers(
            rows[0].get("raw_handoff_json"), raw_handoff_json
        )
    except Exception as exc:
        logger.debug("Birth-marker carry-forward failed (non-fatal): %s", exc)
        return raw_handoff_json


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
    agent_name: "str | None" = None,
    turn_row_is_born_row: bool = False,
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
    It is ALSO what makes the ADOPTED case a no-op: a turn that adopted the
    identity minted for it at dispatch has ONE row -- the born row IS its contract
    row -- so the capture and this closure resolve the same ``contract_id`` and
    there is no scaffold left to supersede. Nothing here needs to detect adoption
    separately; the identity collapsing to one value is the detection.

    ``agent_name`` is the LAST-RESORT lane, for the opposite case: a turn that
    never adopted, whose own draft id is unrelated to its row. The dispatched name
    is then the only shared coordinate, matched against the birth envelope. Two
    guards keep that lane from closing a row belonging to a CONCURRENT sibling,
    because a name is shared by every dispatch of that agent while an identity is
    not: it is skipped entirely when the turn's own row CAME FROM a dispatch
    (adoption -- there is no scaffold to close), and it declines an ambiguous
    match outright rather than picking the most recent (see the writer's
    ``find_dispatched_row_by_agent_name``). A RESUMPTION'S LINK satisfies the
    first guard too, because it continues a turn that adopted; reading it as a
    turn that never adopted is precisely what let a resumed turn close a
    concurrent sibling's live dispatch (see ``is_born_at_dispatch_row``).

    ``turn_row_is_born_row`` is that first guard ASSERTED rather than inferred,
    for the caller that already knows: a capture that keyed on the born row has
    just converged it, so the row is no longer 'DISPATCHED' and the structural
    test can no longer find a scaffold to protect. The structural test is also
    incomplete on its own -- it reads the binding columns, all of which are NULL
    on a FREE dispatch that carried no plan coordinates -- and answering False
    there would hand the name lane a turn whose scaffold is already closed, whose
    only remaining same-name match is a CONCURRENT sibling's live dispatch. The
    two are OR-ed: an assertion here can only ADD protection, never remove it.

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
        # Imported inside the try on purpose: this function must never raise --
        # a failure here would cost the turn its born-row closure -- so an
        # unavailable core degrades through the same non-blocking path as any
        # other failure below.
        from gaia.state import CUT_REASON_REAPED

        orphan = _writer.find_orphaned_dispatched_handoff(
            session_id, identity_candidates, db_path=db_path
        )
        turn_row_was_born_at_dispatch = bool(
            turn_row_is_born_row
        ) or _writer.is_born_at_dispatch_row(
            skip_contract_id or contract_pointer, db_path=db_path
        )
        if orphan is None and agent_name and not turn_row_was_born_at_dispatch:
            orphan = _writer.find_dispatched_row_by_agent_name(
                session_id, agent_name, db_path=db_path
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
        # v39: the structural twin of the degraded/reaped envelope flags below.
        # Same split, same reasoning: a REAPED row is a turn that never recorded
        # a contract of its own, so it keeps a cut mark; a SUPERSEDED scaffold is
        # a healthy turn whose verdict lives on the row `contract_pointer` names,
        # so it clears -- marking it would stamp every healthy bound dispatch and
        # drown the population the mark exists to identify, exactly as marking it
        # `degraded` would.
        cut_reason = None if turn_recorded_own_contract else CUT_REASON_REAPED
        if not turn_recorded_own_contract:
            envelope["degraded"] = True
            envelope["auto_captured"] = True
            envelope["backstop"] = "hook_subagent_stop"
            envelope["reaped"] = True

        outcome = _writer.finalize_agent_contract_handoff(
            contract_id=born_contract_id,
            # The row's OWN identity, read back from the row -- never the
            # candidate this closure happened to search by. The birth now stamps
            # a minted handle, so restamping with a candidate would overwrite it
            # with an agent NAME that no contract validator accepts, and the row
            # would stop being joinable to the draft it was adopted under.
            agent_id=orphan.get("agent_id") or identity_candidates[0],
            workspace=workspace,
            agent_state=_CLOSED_DISPATCH_STATE,
            raw_handoff_json=_json.dumps(envelope),
            session_id=session_id,
            cut_reason=cut_reason,
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
) -> "dict | None":
    """Conditional BACKSTOP finalize of the agent_contract_handoffs row.

    Called synchronously inside the SubagentStop hook lifecycle. Failures are
    suppressed so a DB write error never interrupts hook processing.

    TWO INDEPENDENT JOBS, in this order (see the module docstring for both):
    the CAPTURE (steps 1-4, conditional -- ensure the turn has a contract row)
    and the CLOSE (step 5, unconditional -- take the born-at-dispatch row out of
    'DISPATCHED'). They do not compete: when the capture keys on the born row
    the two collapse onto ONE row and ``skip_contract_id`` makes the close a
    no-op, and when they resolve different rows the close links them with a
    pointer. Either way the turn's row count is what the dispatch already
    created -- the capture no longer ADDS a row to a turn that has one.

    Logic (see module docstring for the never-lost / exactly-once rationale):
    1. Resolve the ``contract_id`` (idempotency key) and a source envelope:
       prefer the agent's own on-disk draft (same key its ``gaia contract
       finalize`` UPSERTs on, so a race converges to one row); else the row born
       at dispatch, joined by harness id -- the turn's contract named exactly,
       with no glob; else synthesize a deterministic backstop id for this
       (agent, session).
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

    Returns the capture's ``{"contract_id", "turn_recorded_own_contract"}``, or
    None when the whole persistence attempt raised (every failure here is
    non-blocking by contract). The pointer exists for callers that must act on
    THIS turn's captured row afterwards; it is not needed to persist.
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

        from gaia.state import (
            CUT_REASON_BACKSTOP_CAPTURE,
            CUT_REASON_REAPED,
        )

        minted_agent_id = resolve_minted_agent_id(
            parsed_contract, task_info, session_id=session_id,
        )
        # agent_id stored in the NOT NULL row column. The resolver no longer
        # substitutes the harness id when it cannot resolve a minted one, so
        # this fallback -- the agent NAME -- is what a genuinely unresolved turn
        # stamps a degraded row with, and it can never be mistaken for a draft
        # key the way an a+hex harness id silently was.
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
        # WHERE the capture landed and WHAT it captured, recorded on the row in
        # step 3. The contract_id alone no longer answers either question: a
        # fence-only turn used to be recognizable by its synthetic
        # `hook-backstop.*` id, and now that the born row is a key this capture
        # adopts, that distinction has to be carried by a mark of its own rather
        # than inferred from the shape of an identifier. Same distinction, moved
        # to where it can survive the key changing.
        capture_key_space = "synthetic"
        capture_notes: dict = {}

        # 1a. Prefer the agent's own on-disk draft -- the SAME contract_id its
        #     `gaia contract finalize` would UPSERT on, so a finalize+backstop
        #     race converges to one row.
        try:
            from gaia.contract import drafts as _drafts

            if minted_agent_id:
                try:
                    draft_id = _drafts.resolve_draft_id(
                        explicit=None, agent_id=str(minted_agent_id)
                    )
                except _drafts.AmbiguousDraftError as _ambiguous:
                    # NOT swallowed. This is a REAL, reachable condition, not an
                    # unexpected fault: an agent id is minted once per dispatch
                    # while a CONTINUATION opens a new contract under the SAME
                    # handle, so every resumption adds a live draft and any turn
                    # past the first resolves 2+ (five were measured under one
                    # handle). The glob cannot tell which draft is this turn's --
                    # but the born row can, and 1b keys on it below. What must
                    # not happen again is the previous behaviour: a bare `except`
                    # discarded this exception with no log at all, so the capture
                    # silently fell to a synthetic id and wrote a second row for
                    # a turn that already had one, with nothing anywhere saying
                    # why.
                    draft_id = None
                    capture_notes["draft_ambiguity"] = {
                        "agent_id": str(minted_agent_id),
                        "candidates": list(_ambiguous.candidates),
                    }
                    logger.warning(
                        "T9 backstop: draft resolution under minted agent id %s "
                        "is AMBIGUOUS (%d live drafts: %s); the glob cannot name "
                        "this turn's draft, so the capture keys on the born "
                        "dispatch row instead.",
                        minted_agent_id, len(_ambiguous.candidates),
                        ", ".join(_ambiguous.candidates),
                    )
                if draft_id:
                    loaded = _drafts.load_draft(draft_id)
                    if loaded is not None:
                        contract_id = draft_id
                        source_envelope = loaded
                        capture_key_space = "draft"
        except Exception as _drafts_exc:
            # The drafts substrate itself is unavailable / unreadable. Distinct
            # from the ambiguity above and reported as such: this one says
            # nothing about which draft is this turn's, only that no draft can
            # be read at all.
            logger.warning(
                "T9 backstop: the drafts substrate is unreadable for minted "
                "agent id %s (%s); the capture falls through to the born "
                "dispatch row.",
                minted_agent_id, _drafts_exc,
            )

        # 1b. THE BORN ROW is the capture's key when no draft resolved. The
        #     dispatch births a row for every turn and SubagentStart stamps the
        #     harness id onto it, so `dispatch_row_by_harness_id` -- the same
        #     bridge step 5 and the gate already cross on -- names THIS turn's
        #     contract exactly, with no glob and no guess.
        #
        #     This replaces a deliberate refusal to adopt that row. The refusal's
        #     stated reason was that the capture's own key space is what tells a
        #     FENCE-ONLY turn apart from a normal one; that distinction is real
        #     and is preserved, by `capture_source` / `capture_key_space` on the
        #     row rather than by the shape of the id. What the refusal cost was
        #     larger: a turn whose row already existed got a SECOND, synthetic
        #     row that by construction never pre-existed, so step 2 always read
        #     "no row" and always wrote a degraded one -- while step 5 went on to
        #     find the born row and point it at the synthetic twin.
        if not contract_id:
            born_row = dispatch_row_by_harness_id(
                task_info, session_id, db_path=db_path
            )
            born_contract_id = born_row.get("contract_id") if born_row else None
            if born_contract_id:
                contract_id = str(born_contract_id)
                capture_key_space = "dispatch_row"
                # The born row names WHICH draft is this turn's -- precisely what
                # the glob could not decide. Loading it is not an optimization:
                # finalize replaces `raw_handoff_json` wholesale, so capturing
                # under this key with only the fence in hand would overwrite the
                # evidence the agent's own `set`/`add`/`fill` calls accumulated
                # in that draft.
                try:
                    from gaia.contract.drafts import load_draft as _load_draft

                    loaded = _load_draft(contract_id)
                    if isinstance(loaded, dict):
                        source_envelope = loaded
                except Exception as _load_exc:
                    logger.debug(
                        "T9 backstop: the born row's own draft %s is unreadable "
                        "(%s); capturing from the fence instead.",
                        contract_id, _load_exc,
                    )

        # 1c. Neither a draft nor a born row: synthesize a deterministic backstop
        #     id. Reached only when the turn has no row to key on at all (no
        #     harness id, an ambiguous or session-mismatched join, an unavailable
        #     store), which is exactly when a row must still be written. The
        #     deterministic id makes a re-fire of the hook for the same (agent,
        #     session) idempotent against itself.
        if not contract_id:
            sid = session_id or "nosession"
            contract_id = f"hook-backstop.{agent_id}.{sid}"

        if source_envelope is not None:
            capture_source = "draft"
        elif isinstance(parsed_contract, dict):
            source_envelope = parsed_contract
            capture_source = "fence_only"
        else:
            capture_source = "none"

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
            #   * NASCENT 'DISPATCHED' -> the ORDINARY shape of a turn that never
            #     finalized, now that step 1 keys on the born row: this capture
            #     converges THAT row to a degraded NON-COMPLETE verdict, never a
            #     false COMPLETE, through the same idempotent convergent writer.
            #     It used to be all but unreachable, because the capture wrote in
            #     a key space the born row was never in.
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
            # Cleaned BEFORE the provenance flags are added, not after: the
            # flags below are themselves declared envelope keys, and cleaning
            # on top of them would only re-inspect what this function just
            # wrote. What needs cleaning is the part nobody validated -- the
            # draft or the parsed fence this lane found.
            cleaning_log: list = []
            if isinstance(source_envelope, dict):
                envelope = dict(
                    clean_rescue_envelope(source_envelope, log=cleaning_log)
                )
                # The VERDICT is read from the RAW envelope, deliberately, and
                # alone among everything this lane persists. Reading it from the
                # cleaned copy would let a fence that spelled its state
                # uncanonically ("complete") canonicalize into COMPLETE instead
                # of falling to IN_PROGRESS -- which is not envelope hygiene but
                # a change to what a rescued turn is RECORDED AS, and it widens
                # the intake of the COMPLETE-carrying-a-cut-reason population
                # that is currently under case-by-case review. Cleaning the
                # envelope and re-deciding a verdict are two different changes;
                # this one is only the first.
                agent_status = source_envelope.get("agent_status")
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
            # WHICH ROUTE produced this row, carried by the row instead of by the
            # id it happens to be keyed under (step 1). `capture_source` is what
            # keeps a FENCE-ONLY turn -- a valid fence with no draft behind it --
            # legible now that such a turn converges its born row rather than
            # minting a `hook-backstop.*` id of its own. `draft_ambiguity`, when
            # present, records the condition that sent the capture down the
            # dispatch-row lane, so the row itself says why.
            envelope["capture_source"] = capture_source
            envelope["capture_key_space"] = capture_key_space
            envelope.update(capture_notes)

            # Every row this capture writes is by construction a row the agent
            # did NOT finalize itself -- the step returns early when a terminal
            # row already exists -- so it always carries a cut mark. Which one
            # mirrors the envelope split just above: a converged orphan is
            # REAPED, a captured no-row turn is a BACKSTOP_CAPTURE.
            cut_reason = (
                CUT_REASON_REAPED if reaping else CUT_REASON_BACKSTOP_CAPTURE
            )

            if cleaning_log:
                logger.debug(
                    "T9 backstop: cleaned rescued envelope for contract_id=%s: %s",
                    contract_id, "; ".join(str(line) for line in cleaning_log),
                )

            raw_handoff_json = _json.dumps(envelope)
            if reaping:
                # The row being converged was BORN at dispatch, and finalize
                # replaces `raw_handoff_json` wholesale. Without this the capture
                # would erase the birth's own marks -- `born_at_dispatch` and the
                # dispatched agent's NAME -- which are the row's record of where
                # it came from and the coordinate `gaia contract list` reads a
                # readable name from. Reuses the writer's merge, the same one the
                # CLI mirror applies, so "which keys are birth marks" has one
                # definition.
                raw_handoff_json = _carry_birth_markers(
                    _writer, contract_id, raw_handoff_json, db_path
                )
            # Read from the RAW envelope, never the cleaned one. A top-level
            # `brief_id` is not a declared envelope key, so the sanitize above
            # legitimately drops it from what gets persisted -- but the brief it
            # names is the row's link to the work it belongs to, and that link is
            # carried by the row's own column, not by the JSON. Extracting after
            # cleaning would silently unlink every rescued row that had one.
            brief_id = _extract_brief_id(source_envelope)

            outcome = _writer.finalize_agent_contract_handoff(
                contract_id=contract_id,
                agent_id=agent_id,
                workspace=workspace,
                agent_state=agent_state,
                raw_handoff_json=raw_handoff_json,
                session_id=session_id,
                plan_task_id=plan_task_id,
                brief_id=brief_id,
                cut_reason=cut_reason,
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
        # Runs on every turn, healthy or not, because a turn whose contract row
        # is NOT its born row (it minted a rival id, or it was captured under a
        # synthetic one) still leaves that born row behind. When the capture
        # keyed on the born row itself, `skip_contract_id` collapses this to a
        # no-op -- the identity resolving to one value IS the detection, no
        # separate adoption check. The turn's contract row is at `contract_id`
        # either way (already there, or just written), so that is always the
        # pointer; whether the turn got there ITSELF is the separate fact that
        # decides reaped-vs-superseded.
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
            agent_name=task_info.get("agent"),
            turn_row_is_born_row=(capture_key_space == "dispatch_row"),
        )

        # The capture's own key, handed back so a caller that must reconcile
        # THIS turn's captured row can name it exactly instead of guessing at
        # the synthetic id or sweeping the session (which would reach rows
        # belonging to other turns). Returned whether or not this call is the
        # one that wrote the row: a later pass of the same turn finds it already
        # terminal and stays passive, yet still needs the pointer.
        return {
            "contract_id": contract_id,
            "turn_recorded_own_contract": turn_recorded_own_contract,
        }

    except Exception as _exc:
        logger.error(
            "T9 backstop: handoff persistence failed (non-blocking): %s",
            _exc, exc_info=True,
        )
    return None
