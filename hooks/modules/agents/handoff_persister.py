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

Agnosticism: the finalize logic lives in the harness-free core
(``gaia.store.writer`` + ``gaia.contract.drafts``). This module is the
Claude-Code adapter seam -- it only INVOKES that core as a backstop and maps
the harness session id onto the row.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Mirrors the agent_contract_handoffs.task_status CHECK enum (schema.sql) --
# the canonical plan_status values. A backstop row whose source envelope
# carries none of these (a crash / partial / missing plan_status) is recorded
# as IN_PROGRESS: honest ("this turn did not reach a verified terminal state")
# and -- crucially -- NOT 'COMPLETE', so it never falsely satisfies the
# briefs "plan closed => a COMPLETE handoff row exists" invariant
# (gaia/briefs/store.py, invariant 5). Only a genuine, valid COMPLETE envelope
# yields task_status='COMPLETE'; the degraded flag then distinguishes it from
# an agent-finalized COMPLETE for any reader that checks finalize-verification.
_VALID_TASK_STATUSES = frozenset(
    {"IN_PROGRESS", "APPROVAL_REQUEST", "COMPLETE", "BLOCKED", "NEEDS_INPUT"}
)


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


def persist_handoff(
    parsed_contract,
    agent_output: str,
    task_info: dict,
    session_id: str,
) -> None:
    """Conditional BACKSTOP finalize of the agent_contract_handoffs row.

    Called synchronously inside the SubagentStop hook lifecycle. Failures are
    suppressed so a DB write error never interrupts hook processing.

    Logic (see module docstring for the never-lost / exactly-once rationale):
    1. Resolve the ``contract_id`` (idempotency key) and a source envelope:
       prefer the agent's own on-disk draft (same key its ``gaia contract
       finalize`` UPSERTs on, so a race converges to one row); otherwise
       synthesize a deterministic backstop id for this (agent, session).
    2. CONDITIONAL: if a row already exists for that ``contract_id`` (the agent
       already finalized), do NOTHING -- the backstop stays fully passive.
    3. Otherwise finalize a row marked ``degraded=true`` / ``auto_captured``,
       via the idempotent writer, without fabricating fields the hook lacks.
    4. If the backstop actually wrote the row AND the envelope carried an
       approval_request, record the linked approvals audit row.
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

        # 1b. No resolvable draft: synthesize a deterministic backstop id. A
        #     turn with no draft never ran finalize (finalize needs a draft),
        #     so there is no primary-path row to converge with; the
        #     deterministic id only makes a re-fire of the hook for the same
        #     (agent, session) idempotent against itself.
        if not contract_id:
            sid = session_id or "nosession"
            contract_id = f"hook-backstop.{agent_id}.{sid}"
            if isinstance(parsed_contract, dict):
                source_envelope = parsed_contract

        # --- 2. CONDITIONAL: a row already exists -> the backstop is passive --
        if _writer.agent_contract_handoff_exists(contract_id, db_path=db_path):
            logger.debug(
                "T9 backstop: row already exists for contract_id=%s; no-op.",
                contract_id,
            )
            return

        # --- 3. Build the degraded / auto-captured row -----------------------
        if isinstance(source_envelope, dict):
            envelope = dict(source_envelope)
            agent_status = envelope.get("agent_status")
            plan_status = (
                agent_status.get("plan_status")
                if isinstance(agent_status, dict)
                else None
            )
        else:
            # No draft AND no parsed contract (a truncated / crashed turn):
            # a MINIMAL row -- do not fabricate evidence fields we do not have.
            envelope = {
                "agent_output_preview": agent_output[:200] if agent_output else "",
            }
            plan_status = None

        task_status = (
            plan_status if plan_status in _VALID_TASK_STATUSES else "IN_PROGRESS"
        )

        # Backstop provenance: this row was captured by the hook, NOT written
        # by the agent's verified `gaia contract finalize`. degraded=true is
        # how a reader that cares about finalize-verification tells it apart
        # from an agent-finalized row; task_status stays faithful for the
        # readers that key on it (briefs invariants). We add flags only --
        # never synthetic evidence.
        envelope["degraded"] = True
        envelope["auto_captured"] = True
        envelope["backstop"] = "hook_subagent_stop"

        raw_handoff_json = _json.dumps(envelope)
        brief_id = _extract_brief_id(envelope)

        outcome = _writer.finalize_agent_contract_handoff(
            contract_id=contract_id,
            agent_id=agent_id,
            workspace=workspace,
            task_status=task_status,
            raw_handoff_json=raw_handoff_json,
            session_id=session_id,
            brief_id=brief_id,
            db_path=db_path,
        )

        # --- 4. Approvals audit row (only when the backstop wrote the row) ---
        # If the row already existed (another writer won the race), the
        # backstop stays passive -- consistent with the conditional contract.
        if not outcome.get("created"):
            return
        handoff_id = outcome.get("handoff_id")
        if handoff_id is None:
            return

        if isinstance(source_envelope, dict):
            approval_req = source_envelope.get("approval_request")
            if approval_req and isinstance(approval_req, dict):
                approval_id = approval_req.get("approval_id")
                if approval_id:
                    try:
                        grants = _writer.list_approval_grants(session_id=session_id)
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
                                    decision = "APPROVED"  # PENDING treated as granted
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

    except Exception as _exc:
        logger.error(
            "T9 backstop: handoff persistence failed (non-blocking): %s",
            _exc, exc_info=True,
        )
