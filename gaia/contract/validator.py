"""
Form-layer validator (layer 1) -- pure, portable, harness-agnostic.

This module is the SINGLE SOURCE OF TRUTH for the *shape* of an
``agent_contract_handoff`` envelope. It validates a parsed envelope (a plain
``dict``) by SHAPE ONLY, rejecting each malformed case with a NAMED error code
drawn from a small, stable enum, and always exposing the canonical rich repair
message so the runtime can hand the agent an actionable fix.

It unifies the shape logic of the two pre-existing validators
(``hooks/modules/agents/contract_validator.py`` and
``hooks/modules/agents/response_contract.py``) into one portable core that the
CLI (M2), the hook gate (M4), the fence fallback (M6) and the packaging smoke
test (M6) all import. Downstream tasks (T2-T17) treat the public surface below
as a stable interface -- the four error codes, the ``validate_form`` signature,
the ``FormValidationResult`` shape, and ``CANONICAL_REPAIR_MESSAGE`` -- so it
must not change without a plan-level decision.

PORTABILITY CONTRACT (enforced by tests/contract/test_validator_portable.py):
    - Imports ONLY the Python standard library plus ``gaia.state`` (itself
      stdlib-pure), which is the SSOT for ``VALID_PLAN_STATUSES``.
    - NEVER imports from ``hooks/`` and NEVER pulls in a third-party package.
    - The ``gaia.state`` import degrades to an inline stdlib fallback when the
      package is not on the path, so the module remains importable in a bare
      stdlib subprocess.

The named codes (AC-1; VERIFICATION_SHAPE added additively in R3 per a
plan-level decision -- brief contract-type-conditional-validation-harness-r3;
APPROVAL_REQUEST_SHAPE and COMPLETE_SHAPE added additively in R4, closing the
two pure-shape cross-field conditionals the form layer previously missed):
    AGENT_ID_FORMAT     -- agent_id is present but does not match ^a[0-9a-f]{5,}$
    PLAN_STATUS         -- agent_state is present but outside the canonical enum
                           (the error CODE keeps the name PLAN_STATUS -- a stable
                           public-surface identifier -- while the FIELD it guards
                           is now agent_state)
    VERIFICATION_RESULT -- agent_state is COMPLETE but verification.result != "pass"
                           (including a missing/malformed verification block)
    MISSING_FIELD       -- a required field (agent_status, an agent_status
                           sub-field, evidence_report, or a required
                           evidence_report key) is absent
    VERIFICATION_SHAPE  -- verification.type declares a known type but the field
                           that type requires is missing/empty (a by-TYPE SHAPE
                           check, independent of agent_state; DISTINCT from
                           VERIFICATION_RESULT). Absent type == no check.
    APPROVAL_REQUEST_SHAPE -- agent_state is APPROVAL_REQUEST but the top-level
                           approval_request object is absent/null, or present
                           without a non-empty exact_content (the verbatim
                           content the user must see for informed consent).
                           approval_id is deliberately NOT required here --
                           agent-response documents a legitimate
                           approval_request with no approval_id yet (a plan
                           presented before the hook has blocked anything and
                           minted a grant).
    COMPLETE_SHAPE      -- agent_state is COMPLETE but next_action != "done"
                           (when next_action is present) or pending_steps is
                           non-empty (when pending_steps is present). Pure
                           cross-field coherence, independent of
                           VERIFICATION_RESULT; the MISSING_FIELD checks above
                           already own the case where either sub-field is
                           absent, so this never stacks with MISSING_FIELD on
                           the same field.

Design notes:
    - SHAPE ONLY: the form layer takes the already-parsed envelope dict. Fence
      extraction (the ```agent_contract_handoff``` regex) and any DB cross-check
      (approval_id / nonce) live in other layers, not here.
    - NO TASK CONTEXT: consolidation_report is context-dependent (needs
      task_info / multi-surface signals) and is therefore NOT a form-layer
      concern -- it belongs to a higher layer that has that context.
    - ONE CODE PER INVALIDITY: an out-of-enum agent_state yields exactly
      PLAN_STATUS and suppresses the downstream evidence requirement (an invalid
      status cannot be classified as evidence-requiring), so a single defect
      does not fan out into multiple codes. This matches AC-9's "one anomaly per
      invalidity".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, List, Tuple

# ---------------------------------------------------------------------------
# Canonical plan_status enum -- SSOT is gaia.state.VALID_PLAN_STATUSES.
#
# Imported with a stdlib-only fallback so the module stays importable in a bare
# stdlib subprocess (AC-2). gaia.state is itself stdlib-pure (only
# ``from __future__ import annotations``), so importing it never violates the
# portability contract; the fallback exists solely for a path on which the gaia
# package root is absent. The fallback is kept byte-identical to the canonical
# tuple so behaviour cannot drift between the two paths.
# ---------------------------------------------------------------------------
try:
    from gaia.state import VALID_PLAN_STATUSES as _CANONICAL_PLAN_STATUSES

    VALID_PLAN_STATUSES: Tuple[str, ...] = tuple(_CANONICAL_PLAN_STATUSES)
except ImportError:  # pragma: no cover -- exercised only on a bare stdlib path
    VALID_PLAN_STATUSES = (
        "IN_PROGRESS",
        "APPROVAL_REQUEST",
        "COMPLETE",
        "BLOCKED",
        "NEEDS_INPUT",
        "NEEDS_VERIFICATION",
    )

# ---------------------------------------------------------------------------
# Canonical verification_type enum -- SSOT is gaia.state.VALID_VERIFICATION_TYPES.
#
# Imported with the SAME stdlib-only fallback idiom as VALID_PLAN_STATUSES above
# so the module stays importable in a bare stdlib subprocess (portability
# contract). Unlike plan statuses this maps to no DB column -- it is the SSOT for
# the ``type`` field of a contract-envelope verification block. The fallback is
# kept byte-identical to gaia.state.VALID_VERIFICATION_TYPES so behaviour cannot
# drift between the two paths.
# ---------------------------------------------------------------------------
try:
    from gaia.state import VALID_VERIFICATION_TYPES as _CANONICAL_VERIFICATION_TYPES

    VALID_VERIFICATION_TYPES: Tuple[str, ...] = tuple(_CANONICAL_VERIFICATION_TYPES)
except ImportError:  # pragma: no cover -- exercised only on a bare stdlib path
    VALID_VERIFICATION_TYPES = (
        "command",
        "code",
        "semantic",
        "self_review",
    )

# ---------------------------------------------------------------------------
# Envelope-only verification.type enum (plan 34 task 7).
#
# The CONTRACT ENVELOPE additionally accepts ``verification.type == "none"`` for
# a turn that performed NO plan-task-bound verification -- an investigation or
# memory turn that carries no ``plan_task_id`` and is therefore free to
# self-COMPLETE (the finalize gate keys on plan_task_id, not role). "none" names
# "no external oracle was required"; it demands no additional field.
#
# This extension is DELIBERATELY scoped to the envelope and MUST NOT widen
# VALID_VERIFICATION_TYPES -- that tuple is the shared SSOT (gaia.state) backing
# the persisted CHECK on task_gates.verification_type, which must stay exactly
# command / code / semantic / self_review. Extending the envelope enum here can
# never contaminate the task_gates CHECK: the two vocabularies are now distinct
# on purpose (a gate's verification_type is a promise to run a real oracle; the
# envelope's "none" is the explicit absence of one).
# ---------------------------------------------------------------------------
_ENVELOPE_ONLY_VERIFICATION_TYPES: Tuple[str, ...] = ("none",)
ENVELOPE_VERIFICATION_TYPES: Tuple[str, ...] = (
    VALID_VERIFICATION_TYPES + _ENVELOPE_ONLY_VERIFICATION_TYPES
)

# Evidence is required for every valid status (no exclusions), matching
# EVIDENCE_REQUIRED_PLAN_STATUSES in response_contract.py.
_EVIDENCE_REQUIRING_STATUSES = frozenset(VALID_PLAN_STATUSES)

# Canonical agent_id shape (revives the previously-dead regex from
# response_contract._AGENT_ID_PATTERN and the brief spec).
_AGENT_ID_PATTERN = re.compile(r"^a[0-9a-f]{5,}$")

# Required evidence_report keys (canonical lower-case JSON form). Upper-case
# variants are also accepted for backward compatibility, matching both existing
# validators. Presence is checked, not truthiness: an explicit empty list [] is
# valid.
REQUIRED_EVIDENCE_FIELDS: Tuple[str, ...] = (
    "patterns_checked",
    "files_checked",
    "commands_run",
    "key_outputs",
    "verbatim_outputs",
    "cross_layer_impacts",
    "open_gaps",
)

# Required agent_status sub-fields. agent_state and agent_id have dedicated
# codes for the "present-but-malformed" case; all four are subject to
# MISSING_FIELD when absent. pending_steps accepts an empty list (presence
# check); next_action must be a non-empty value.
#
# agent_state is the canonical name of the TURN-status field (renamed from the
# former ``plan_status`` envelope key, plan 34 task 4). It carries a
# VALID_PLAN_STATUSES value and matches the persisted
# ``agent_contract_handoffs.agent_state`` column. The enum constant and the
# PLAN_STATUS error code keep their names -- the enum is still the SSOT shared
# with the ``episodes.plan_status`` lifecycle column, and the error code is a
# stable public-surface identifier.
REQUIRED_AGENT_STATUS_FIELDS: Tuple[str, ...] = (
    "agent_state",
    "agent_id",
    "pending_steps",
    "next_action",
)


class FormErrorCode(str, Enum):
    """Named, stable error codes emitted by the form layer (AC-1).

    ``str`` mixin: members compare equal to and serialize as their string
    value, so a code round-trips cleanly through JSON and CLI output without a
    custom encoder.
    """

    AGENT_ID_FORMAT = "AGENT_ID_FORMAT"
    PLAN_STATUS = "PLAN_STATUS"
    VERIFICATION_RESULT = "VERIFICATION_RESULT"
    MISSING_FIELD = "MISSING_FIELD"
    # Additive (R3): a verification.type was declared but the field that type
    # requires is missing/empty. DISTINCT from VERIFICATION_RESULT (which is the
    # by-VALUE "COMPLETE but result != pass" check); this is a by-TYPE SHAPE
    # check, independent of agent_state. Absent verification.type == no check.
    VERIFICATION_SHAPE = "VERIFICATION_SHAPE"
    # Additive (R4): APPROVAL_REQUEST without a usable approval_request block
    # (absent, or present but missing a non-empty exact_content). A pure-shape
    # cross-field check, independent of the evidence_report/verification
    # checks above.
    APPROVAL_REQUEST_SHAPE = "APPROVAL_REQUEST_SHAPE"
    # Additive (R4): COMPLETE without next_action == "done" or with a
    # non-empty pending_steps. A pure-shape cross-field coherence check,
    # independent of VERIFICATION_RESULT.
    COMPLETE_SHAPE = "COMPLETE_SHAPE"


@dataclass(frozen=True)
class FormError:
    """A single shape violation.

    Attributes:
        code: the named FormErrorCode.
        field: dotted path of the offending field (e.g. "agent_status.agent_id",
            "evidence_report.commands_run"). Empty when not field-specific.
        detail: human-readable specifics (the bad value, the expected enum, ...).
    """

    code: FormErrorCode
    field: str
    detail: str

    def __str__(self) -> str:  # pragma: no cover -- convenience only
        loc = f" [{self.field}]" if self.field else ""
        return f"{self.code.value}{loc}: {self.detail}"


@dataclass(frozen=True)
class FormValidationResult:
    """Outcome of form-layer validation.

    Attributes:
        ok: True when the envelope is shape-valid (no errors).
        errors: tuple of FormError, one per distinct invalidity.
        repair_message: ALWAYS the canonical rich repair message
            (``CANONICAL_REPAIR_MESSAGE``). It is byte-stable regardless of which
            errors fired, so a caller that injects it (hook gate, CLI) keeps a
            cache-stable surface; the specific defects live in ``errors``.
    """

    ok: bool
    errors: Tuple[FormError, ...] = ()
    repair_message: str = ""

    @property
    def codes(self) -> List[FormErrorCode]:
        """The distinct error codes present, in first-seen order."""
        seen: List[FormErrorCode] = []
        for err in self.errors:
            if err.code not in seen:
                seen.append(err.code)
        return seen

    def error_summary(self) -> str:
        """One-line summary of the specific defects (for stderr / logs).

        Empty string when valid. Callers that want the full guidance combine
        this with ``repair_message``.
        """
        return "; ".join(str(err) for err in self.errors)


# ---------------------------------------------------------------------------
# Canonical rich repair message
#
# Unified from the two prior validators' repair blocks. Always returned (see
# FormValidationResult.repair_message). Kept as a module constant so it is
# byte-stable across calls.
# ---------------------------------------------------------------------------
CANONICAL_REPAIR_MESSAGE = (
    "Repair: your response must carry an agent_contract_handoff envelope whose "
    "body is valid JSON (parsed with json.loads -- NOT YAML: comments, trailing "
    "commas, or unquoted keys will fail to parse and the block is treated as "
    "missing).\n"
    "\n"
    "```agent_contract_handoff\n"
    "{\n"
    '  "agent_status": {\n'
    '    "agent_state": "<IN_PROGRESS|APPROVAL_REQUEST|COMPLETE|BLOCKED|NEEDS_INPUT|NEEDS_VERIFICATION>",\n'
    '    "agent_id": "<a + 5+ hex chars, e.g. a1b2c3>",\n'
    '    "pending_steps": [],\n'
    '    "next_action": "<done or the next concrete step>"\n'
    "  },\n"
    '  "evidence_report": {\n'
    '    "patterns_checked": [],\n'
    '    "files_checked": [],\n'
    '    "commands_run": [],\n'
    '    "key_outputs": [],\n'
    '    "verbatim_outputs": [],\n'
    '    "cross_layer_impacts": [],\n'
    '    "open_gaps": [],\n'
    '    "verification": { "method": "<method>", "result": "pass", "details": "<...>" }\n'
    "  },\n"
    '  "consolidation_report": null,\n'
    '  "approval_request": null\n'
    "}\n"
    "```\n"
    "\n"
    "Required: agent_status (agent_state in the enum above; agent_id matching "
    "^a[0-9a-f]{5,}$; pending_steps; next_action) and evidence_report with keys "
    "patterns_checked, files_checked, commands_run, key_outputs, "
    "verbatim_outputs, cross_layer_impacts, open_gaps. "
    "When agent_state is COMPLETE, evidence_report.verification.result must be "
    '"pass".'
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize_status(raw: Any) -> str:
    """Uppercase and strip trailing punctuation, matching the legacy resolvers."""
    return str(raw or "").strip().upper().rstrip(".,;")


def _evidence_has_key(evidence: dict, key_lower: str) -> bool:
    """Presence check accepting both lower-case (JSON) and UPPER-CASE keys."""
    return key_lower in evidence or key_lower.upper() in evidence


def _is_nonempty_str(value: Any) -> bool:
    """True when ``value`` is a non-empty (after strip) string.

    Used for the per-type required fields that must carry a declared value (a
    command/oracle to run, or a statement of what was reviewed).
    """
    return isinstance(value, str) and value.strip() != ""


def _verification_type_shape_error(vtype: str, verification: dict) -> Tuple[Any, str]:
    """Return ``(field, detail)`` for a missing type-required field, else ``(None, "")``.

    Given a KNOWN ``verification.type`` (caller has already checked membership
    in ENVELOPE_VERIFICATION_TYPES), enforce the field that type requires:

      * "command"/"code" (DETERMINISTIC) -- a non-empty ``command`` naming the
        command/oracle a third-party verifier would run.
      * "semantic" -- a truthy ``requires_human`` marker: the contract declares
        it needs human/rubric validation and stays open pending that judgement.
      * "self_review" -- a non-empty ``reviewed`` statement of what was checked
        and observed.
      * "none" (envelope-only, plan 34 task 7) -- no plan-task-bound verification
        was performed; demands NO field (falls through to the ``(None, "")``
        return below).

    A ``(None, "")`` return means the type-required field is satisfied.
    """
    if vtype == "none":
        # No external oracle was required (a turn with no plan_task_id). Nothing
        # to enforce -- the explicit absence of a check is itself well-formed.
        return (None, "")
    if vtype in ("command", "code"):
        if not _is_nonempty_str(verification.get("command")):
            return (
                "evidence_report.verification.command",
                (
                    f"verification.type {vtype!r} (deterministic) requires a "
                    "non-empty 'command' naming the command/oracle to run"
                ),
            )
    elif vtype == "semantic":
        if not bool(verification.get("requires_human")):
            return (
                "evidence_report.verification.requires_human",
                (
                    "verification.type 'semantic' requires a truthy "
                    "'requires_human' marker (needs human/rubric validation; "
                    "contract stays open)"
                ),
            )
    elif vtype == "self_review":
        if not _is_nonempty_str(verification.get("reviewed")):
            return (
                "evidence_report.verification.reviewed",
                (
                    "verification.type 'self_review' requires a non-empty "
                    "'reviewed' statement of what was checked"
                ),
            )
    return (None, "")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def validate_form(envelope: Any) -> FormValidationResult:
    """Validate an ``agent_contract_handoff`` envelope by SHAPE ONLY.

    Args:
        envelope: the already-parsed contract dict. A non-dict (including None,
            e.g. an unparseable / missing block) is reported as a single
            MISSING_FIELD on ``agent_contract_handoff``.

    Returns:
        FormValidationResult. ``ok`` is True only when there are no errors.
        ``repair_message`` is always ``CANONICAL_REPAIR_MESSAGE``.
    """
    errors: List[FormError] = []

    if not isinstance(envelope, dict):
        errors.append(
            FormError(
                code=FormErrorCode.MISSING_FIELD,
                field="agent_contract_handoff",
                detail=(
                    "no parseable agent_contract_handoff envelope (expected a "
                    f"JSON object, got {type(envelope).__name__})"
                ),
            )
        )
        return FormValidationResult(
            ok=False, errors=tuple(errors), repair_message=CANONICAL_REPAIR_MESSAGE
        )

    # --- agent_status -------------------------------------------------------
    agent_status = envelope.get("agent_status")
    normalized_status = ""
    if not isinstance(agent_status, dict) or not agent_status:
        errors.append(
            FormError(
                code=FormErrorCode.MISSING_FIELD,
                field="agent_status",
                detail="agent_status object is missing",
            )
        )
    else:
        # agent_state: absent -> MISSING_FIELD; present-but-invalid -> PLAN_STATUS
        raw_status = agent_status.get("agent_state")
        if raw_status is None or str(raw_status).strip() == "":
            errors.append(
                FormError(
                    code=FormErrorCode.MISSING_FIELD,
                    field="agent_status.agent_state",
                    detail="agent_state is missing",
                )
            )
        else:
            normalized_status = _normalize_status(raw_status)
            if normalized_status not in VALID_PLAN_STATUSES:
                errors.append(
                    FormError(
                        code=FormErrorCode.PLAN_STATUS,
                        field="agent_status.agent_state",
                        detail=(
                            f"{raw_status!r} is not one of "
                            f"{list(VALID_PLAN_STATUSES)}"
                        ),
                    )
                )
                # Suppress evidence classification for an unknown status
                # (one code per invalidity).
                normalized_status = ""

        # agent_id: absent -> MISSING_FIELD; present-but-malformed -> AGENT_ID_FORMAT
        raw_agent_id = agent_status.get("agent_id")
        if raw_agent_id is None or str(raw_agent_id).strip() == "":
            errors.append(
                FormError(
                    code=FormErrorCode.MISSING_FIELD,
                    field="agent_status.agent_id",
                    detail="agent_id is missing",
                )
            )
        elif not _AGENT_ID_PATTERN.match(str(raw_agent_id)):
            errors.append(
                FormError(
                    code=FormErrorCode.AGENT_ID_FORMAT,
                    field="agent_status.agent_id",
                    detail=(
                        f"{raw_agent_id!r} does not match ^a[0-9a-f]{{5,}}$"
                    ),
                )
            )

        # pending_steps: presence only (empty list [] is valid).
        if "pending_steps" not in agent_status:
            errors.append(
                FormError(
                    code=FormErrorCode.MISSING_FIELD,
                    field="agent_status.pending_steps",
                    detail="pending_steps is missing",
                )
            )

        # next_action: must be present and non-empty.
        raw_next = agent_status.get("next_action")
        if raw_next is None or str(raw_next).strip() == "":
            errors.append(
                FormError(
                    code=FormErrorCode.MISSING_FIELD,
                    field="agent_status.next_action",
                    detail="next_action is missing",
                )
            )

        # --- COMPLETE cross-field coherence (pure SHAPE, R4) -----------------
        # A COMPLETE turn must actually be done: next_action == "done" and
        # pending_steps == [] are pure-shape cross-field checks, independent
        # of evidence/verification. Each only fires when the field in question
        # is itself PRESENT -- the MISSING_FIELD checks above already own the
        # absent case, so this never stacks a second code on the same field
        # (one code per invalidity).
        if normalized_status == "COMPLETE":
            if raw_next is not None and str(raw_next).strip() != "":
                if str(raw_next).strip().lower() != "done":
                    errors.append(
                        FormError(
                            code=FormErrorCode.COMPLETE_SHAPE,
                            field="agent_status.next_action",
                            detail=(
                                "COMPLETE requires next_action == 'done', got "
                                f"{raw_next!r}"
                            ),
                        )
                    )
            if "pending_steps" in agent_status:
                raw_pending = agent_status.get("pending_steps")
                if raw_pending:
                    errors.append(
                        FormError(
                            code=FormErrorCode.COMPLETE_SHAPE,
                            field="agent_status.pending_steps",
                            detail=(
                                "COMPLETE requires pending_steps == [], got "
                                f"{raw_pending!r}"
                            ),
                        )
                    )

    # --- evidence_report ----------------------------------------------------
    # Required for every valid status. An unknown/absent status leaves
    # normalized_status == "" and skips this block (already flagged above).
    if normalized_status in _EVIDENCE_REQUIRING_STATUSES:
        evidence = envelope.get("evidence_report")
        if not isinstance(evidence, dict) or not evidence:
            errors.append(
                FormError(
                    code=FormErrorCode.MISSING_FIELD,
                    field="evidence_report",
                    detail="evidence_report object is missing",
                )
            )
        else:
            for key in REQUIRED_EVIDENCE_FIELDS:
                if not _evidence_has_key(evidence, key):
                    errors.append(
                        FormError(
                            code=FormErrorCode.MISSING_FIELD,
                            field=f"evidence_report.{key}",
                            detail=f"required evidence_report key {key!r} is missing",
                        )
                    )

        # --- verification.type (type-conditional SHAPE, any status) ---------
        # Mirrors the conditional-by-VALUE pattern below (VERIFICATION_RESULT):
        # if verification declares a KNOWN type, require the field that type
        # demands and reject an omission with VERIFICATION_SHAPE. This is a
        # SHAPE check independent of agent_state; it is DISTINCT from the
        # by-VALUE COMPLETE/result==pass check and may co-occur with it (two
        # different invalidities -> two codes). Backward compatible: an ABSENT
        # verification.type (or a type outside the SSOT enum) fires no new
        # requirement, preserving every pre-R3 contract.
        verification = evidence.get("verification") if isinstance(evidence, dict) else None
        if isinstance(verification, dict) and verification.get("type") is not None:
            vtype = str(verification.get("type")).strip().lower()
            # Membership is tested against the ENVELOPE enum (which adds "none"),
            # NOT the task_gates SSOT VALID_VERIFICATION_TYPES -- so "none" is a
            # first-class envelope type while the task_gates CHECK stays at its
            # four deterministic/judgement types (plan 34 task 7).
            if vtype in ENVELOPE_VERIFICATION_TYPES:
                shape_field, shape_detail = _verification_type_shape_error(vtype, verification)
                if shape_field is not None:
                    errors.append(
                        FormError(
                            code=FormErrorCode.VERIFICATION_SHAPE,
                            field=shape_field,
                            detail=shape_detail,
                        )
                    )

        # --- verification (COMPLETE only) -----------------------------------
        # COMPLETE without verification.result == "pass" -> VERIFICATION_RESULT
        # (covers a missing or malformed verification block too).
        if normalized_status == "COMPLETE":
            verification = evidence.get("verification") if isinstance(evidence, dict) else None
            if not isinstance(verification, dict):
                errors.append(
                    FormError(
                        code=FormErrorCode.VERIFICATION_RESULT,
                        field="evidence_report.verification",
                        detail="COMPLETE requires a verification object with result == 'pass'",
                    )
                )
            else:
                result_val = str(verification.get("result", "")).strip().lower()
                if result_val != "pass":
                    errors.append(
                        FormError(
                            code=FormErrorCode.VERIFICATION_RESULT,
                            field="evidence_report.verification.result",
                            detail=(
                                f"COMPLETE requires verification.result == 'pass', "
                                f"got {verification.get('result')!r}"
                            ),
                        )
                    )

    # --- approval_request (APPROVAL_REQUEST only, pure SHAPE, R4) -----------
    # A pure-shape cross-field check: when agent_state is APPROVAL_REQUEST the
    # top-level approval_request object must itself be present, and its
    # exact_content -- the verbatim content the user must see to give
    # informed consent (the orchestrator-present-approval iron law) -- must
    # be non-empty. approval_id is deliberately NOT required here:
    # agent-response documents a legitimate approval_request with no
    # approval_id yet (an agent presenting a T3 plan before the hook has
    # blocked anything and minted a grant) -- requiring it would reject that
    # documented, in-use protocol state.
    if normalized_status == "APPROVAL_REQUEST":
        approval_request = envelope.get("approval_request")
        if not isinstance(approval_request, dict) or not approval_request:
            errors.append(
                FormError(
                    code=FormErrorCode.APPROVAL_REQUEST_SHAPE,
                    field="approval_request",
                    detail=(
                        "APPROVAL_REQUEST requires a non-null approval_request "
                        "object"
                    ),
                )
            )
        elif not _is_nonempty_str(approval_request.get("exact_content")):
            errors.append(
                FormError(
                    code=FormErrorCode.APPROVAL_REQUEST_SHAPE,
                    field="approval_request.exact_content",
                    detail=(
                        "APPROVAL_REQUEST requires a non-empty 'exact_content' "
                        "(the verbatim command/content the user must see)"
                    ),
                )
            )

    return FormValidationResult(
        ok=not errors,
        errors=tuple(errors),
        repair_message=CANONICAL_REPAIR_MESSAGE,
    )


__all__ = [
    "FormErrorCode",
    "FormError",
    "FormValidationResult",
    "validate_form",
    "CANONICAL_REPAIR_MESSAGE",
    "VALID_PLAN_STATUSES",
    "VALID_VERIFICATION_TYPES",
    "ENVELOPE_VERIFICATION_TYPES",
    "REQUIRED_EVIDENCE_FIELDS",
    "REQUIRED_AGENT_STATUS_FIELDS",
]
