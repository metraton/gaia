"""
Contract validation for agent output: structural checks, evidence parsing,
command extraction, PLAN_STATUS parsing, and exit code derivation.

The canonical fenced-block format is ``agent_contract_handoff`` with field
name ``plan_status``. Legacy HTML-comment blocks (``<!-- AGENT_STATUS -->``,
etc.) are **not** parsed. As a tolerant fallback, a ```json``` fence is also
accepted when its body already has the shape of a handoff envelope (an
``agent_status.plan_status`` key) -- this covers the recurring case of an
agent mislabeling the fence out of the generic-JSON habit. The fallback is
content-based, not a relaxation of field validation: once a dict is
extracted, its SHAPE is delegated to the portable core
(``gaia.contract.validator.validate_form``) -- the SAME core the CLI and the
hook gate validate against. This module owns only fence extraction (the one
migration-only piece the core deliberately does not do, since it takes an
already-parsed dict, never raw text); it does not re-implement shape
validation. ``validate()`` / ``validate_response_contract()`` add only the
task-context-dependent checks the form layer cannot own (consolidation_report,
approval/loop-state blocking) on top of that shared core.

Both fence regexes require the closing ``` `` `` to start its own line
(preceded by a real newline) and be followed only by whitespace/end-of-string.
This tolerates triple-backtick sequences quoted *inside* the JSON body (e.g.
a code block cited verbatim in ``verbatim_outputs``) without truncating the
capture at the first such sequence -- a valid JSON string cannot contain a
literal embedded newline, so an inline quoted fence is never mistaken for the
block's real closing fence.

Provides:
    - parse_contract(): Extract structured dict from an agent_contract_handoff
                        fenced block
    - validate(): Check agent output against contract requirements -> ValidationResult
    - extract_commands_from_evidence(): Parse COMMANDS_RUN field
    - requires_consolidation_report(): Check if consolidation is needed
    - extract_plan_status_from_output(): Extract PLAN_STATUS string
    - extract_exit_code_from_output(): Derive exit code from PLAN_STATUS
    - parse_loop_state(): Parse loop_state clause (blocking check on COMPLETE)
    - parse_update_contracts(): Parse update_contracts array clause
    - parse_rollback_executed(): Parse rollback_executed clause (advisory)
    - parse_context_consumption(): Parse context_consumption clause (advisory)
    - parse_memory_suggestions(): Parse memory_suggestions clause (advisory)
    - parse_user_facing_summary(): Parse user_facing_summary clause (advisory)
"""

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

# SSOT for SHAPE validation (M6/AC-13): the fence parsing below (parse_contract)
# remains this module's job -- extracting a dict out of legacy fenced text is a
# migration-only concern the portable core deliberately does not own (it takes
# an already-parsed dict, never raw text). Once extracted, the dict's SHAPE is
# validated by the SAME core the CLI and the hook gate use, not by a second,
# locally re-implemented shape check. See gaia/contract/validator.py.
from gaia.contract.validator import (
    CANONICAL_REPAIR_MESSAGE,
    VALID_PLAN_STATUSES as _FORM_VALID_PLAN_STATUSES,
    FormErrorCode,
    validate_form,
)

logger = logging.getLogger(__name__)

_NOT_RUN_INDICATORS = re.compile(
    r"\b(not\s+run|not\s+executed|skipped|n/a)\b",
    re.IGNORECASE,
)

_LITERAL_NONE_COMMANDS = {"none", "not run", "not executed", "n/a", "skipped"}

# Required evidence fields
_EVIDENCE_REQUIRED_FIELDS = [
    "PATTERNS_CHECKED", "FILES_CHECKED", "COMMANDS_RUN", "KEY_OUTPUTS",
    "VERBATIM_OUTPUTS", "CROSS_LAYER_IMPACTS", "OPEN_GAPS",
]

# Required consolidation fields
_CONSOLIDATION_REQUIRED_FIELDS = [
    "OWNERSHIP_ASSESSMENT", "CONFIRMED_FINDINGS", "SUSPECTED_FINDINGS",
    "CONFLICTS", "OPEN_GAPS", "NEXT_BEST_AGENT",
]


@dataclass
class ValidationResult:
    """Result of contract validation.

    Attributes:
        is_valid: True if all required contract blocks are present and complete.
        missing: List of missing block/field names.
        error_message: Descriptive error for stderr output when is_valid is False.
    """
    is_valid: bool
    missing: List[str]
    error_message: str


# ============================================================================
# JSON contract parser (single-mode: agent_contract_handoff with plan_status)
# ============================================================================

# Single supported fenced tag for agent handoff envelope.
_TAG_HANDOFF = "agent_contract_handoff"

#
# The closing fence must start its own line (preceded by ``\n``) and be
# followed only by whitespace or end-of-string. A naive non-greedy
# ``(.*?)```` `` matches the FIRST literal ``` `` `` anywhere in the body --
# which truncates the capture (and breaks ``json.loads``) whenever the
# contract legitimately quotes triple-backtick fenced content inline (e.g.
# a code block cited in ``verbatim_outputs``). A valid JSON string cannot
# contain a real embedded newline (it must be escaped as ``\n`` -- two
# literal characters), so a quoted fence inside a JSON string value is never
# preceded by an actual line break; requiring the closing ``` `` `` to start
# a real line is therefore sufficient to skip over it and find the fence
# that actually closes the block.
_RE_HANDOFF = re.compile(r'```agent_contract_handoff\s*\n(.*?)\n```(?=\s|\Z)', re.DOTALL)

# Tolerant fallback (see module docstring): a ```json``` fence is accepted
# ONLY when its body already has the shape of a handoff envelope. This does
# not widen what counts as a valid contract -- it widens which fence label
# is accepted for a body that already is one. Same own-line closing-fence
# requirement as _RE_HANDOFF, for the same reason.
_RE_JSON_FALLBACK = re.compile(r'```json\s*\n(.*?)\n```(?=\s|\Z)', re.DOTALL)


def _looks_like_handoff_envelope(parsed: Any) -> bool:
    """Return True when a parsed JSON value has the shape of a handoff envelope.

    Content-based check, deliberately narrow: a dict with an
    ``agent_status.plan_status`` key. This is what distinguishes an
    ``agent_contract_handoff`` payload from an arbitrary ```json``` block the
    agent may have emitted for an unrelated reason (e.g. quoting a command's
    JSON output in ``verbatim_outputs``).
    """
    if not isinstance(parsed, dict):
        return False
    agent_status = parsed.get("agent_status")
    return isinstance(agent_status, dict) and bool(agent_status.get("plan_status"))


def parse_contract(agent_output: str) -> Optional[dict]:
    """Extract structured contract dict from an ``agent_contract_handoff`` block.

    The single supported envelope uses ``plan_status`` as the canonical status
    field (matching the database column ``episodes.plan_status`` and the
    ``AgentStatus.plan_status`` dataclass).

    The parsed dict is augmented with a ``_contract_tag`` key
    (``"agent_contract_handoff"``) so downstream callers can identify the
    source uniformly.

    When no correctly-tagged fence is found, falls back to scanning ```json```
    fences for one whose body already has the shape of a handoff envelope
    (see ``_looks_like_handoff_envelope``); the LAST such block wins, matching
    the protocol convention that the contract is the final block of the turn.
    This fallback only accepts payloads that are already structurally a
    handoff -- it never accepts a fence whose body lacks ``agent_status``,
    and it never relaxes the required-field validation performed downstream
    by ``validate()`` / ``validate_response_contract()``.

    Args:
        agent_output: Complete output from agent execution.

    Returns:
        Parsed dict augmented with ``_contract_tag`` when a valid block is
        found, None otherwise.
    """
    m = _RE_HANDOFF.search(agent_output)
    if m is not None:
        try:
            parsed = json.loads(m.group(1))
        except json.JSONDecodeError:
            return None
        if isinstance(parsed, dict):
            parsed["_contract_tag"] = _TAG_HANDOFF
            return parsed
        return None

    fallback: Optional[dict] = None
    for candidate in _RE_JSON_FALLBACK.finditer(agent_output):
        try:
            parsed = json.loads(candidate.group(1))
        except json.JSONDecodeError:
            continue
        if _looks_like_handoff_envelope(parsed):
            fallback = parsed
    if fallback is not None:
        fallback["_contract_tag"] = _TAG_HANDOFF
        return fallback

    return None


def _resolve_status(agent_status: dict) -> str:
    """Resolve the effective status string from an agent_status dict.

    The canonical field is ``plan_status`` (matches DB ``episodes.plan_status``
    and the ``AgentStatus.plan_status`` dataclass).
    """
    plan_status = str(agent_status.get("plan_status", "")).strip()
    return plan_status.upper().rstrip(".,;")


# ============================================================================
# JSON contract validation helpers
# ============================================================================

# Translation table: gaia.contract.validator.FormError.field (dotted, the
# core's vocabulary) -> this module's pre-existing legacy uppercase token
# vocabulary (what callers of validate()/ValidationResult.missing already
# depend on -- see tests/hooks/test_response_contract.py and
# tests/hooks/modules/agents/test_contract_validator.py). This module does
# NOT re-decide shape validity; it only relabels the SSOT's verdict so its
# existing callers keep working unchanged.
_FORM_MISSING_FIELD_TOKENS = {
    "agent_contract_handoff": ("AGENT_STATUS", "PLAN_STATUS", "AGENT_ID"),
    "agent_status": ("AGENT_STATUS", "PLAN_STATUS", "AGENT_ID"),
    "agent_status.plan_status": ("PLAN_STATUS",),
    "agent_status.agent_id": ("AGENT_ID",),
    "agent_status.pending_steps": ("PENDING_STEPS",),
    "agent_status.next_action": ("NEXT_ACTION",),
    "evidence_report": ("EVIDENCE_REPORT",),
}

# evidence_report.verification / evidence_report.verification.result both
# surface as FormErrorCode.VERIFICATION_RESULT; the legacy token differs by
# WHICH of the two fired (missing block vs. present-but-not-"pass").
_FORM_VERIFICATION_TOKENS = {
    "evidence_report.verification": "VERIFICATION_RESULT_REQUIRED_FOR_COMPLETE",
    "evidence_report.verification.result": "VERIFICATION_RESULT_MUST_BE_PASS",
}


def _legacy_tokens_for_form_error(error) -> List[str]:
    """Translate one gaia.contract.validator.FormError into legacy tokens.

    The SSOT (validate_form) is the single decision-maker for shape validity;
    this is a pure relabeling so pre-existing callers of validate() keep
    reading the uppercase token vocabulary they already assert on.
    """
    if error.code is FormErrorCode.AGENT_ID_FORMAT:
        return ["AGENT_ID"]
    if error.code is FormErrorCode.PLAN_STATUS:
        return ["PLAN_STATUS"]
    if error.code is FormErrorCode.VERIFICATION_RESULT:
        return [_FORM_VERIFICATION_TOKENS.get(error.field, "VERIFICATION_RESULT_MUST_BE_PASS")]
    if error.code is FormErrorCode.MISSING_FIELD:
        if error.field in _FORM_MISSING_FIELD_TOKENS:
            return list(_FORM_MISSING_FIELD_TOKENS[error.field])
        if error.field.startswith("evidence_report."):
            # evidence_report.<key> -> <KEY> (matches _EVIDENCE_REQUIRED_FIELDS)
            return [error.field.split(".", 1)[1].upper()]
        return [error.field.upper() or error.code.value]
    return [error.code.value]  # pragma: no cover -- exhaustive over FormErrorCode


def _validate_from_handoff(contract: Optional[dict], task_info: Dict[str, Any]) -> ValidationResult:
    """Validate agent output using the parsed agent_contract_handoff dict.

    SHAPE (M1/AC-1 four named codes: AGENT_ID_FORMAT, PLAN_STATUS,
    VERIFICATION_RESULT, MISSING_FIELD) is delegated entirely to the portable
    core, ``gaia.contract.validator.validate_form`` -- the SAME core the CLI
    (M2) and the hook gate (M4) validate against (AC-13: fence fallback shares
    one core, it does not re-implement shape validation). ``contract`` may be
    ``None`` (no parseable fenced block at all): validate_form(None) reports
    that uniformly as a MISSING_FIELD, so the "no contract" and "malformed
    contract" cases funnel through one call.

    What stays HERE, additively, because it is task-context-dependent or
    otherwise out of the form layer's scope by design (see
    gaia/contract/validator.py's "NO TASK CONTEXT" design note):
    - consolidation_report (needs task_info to know if it's required)
    - approval_request.verification blocking check
    - loop_state blocking check (T2.3)

    Args:
        contract: Parsed dict from parse_contract(), or None when no fenced
            block was found at all.
        task_info: Task metadata including injected_context for multi-surface detection.

    Returns:
        ValidationResult with is_valid, missing fields list, and error_message.
    """
    all_missing: List[str] = []

    # 1 & 2. agent_status + evidence_report SHAPE -- delegated to the SSOT core.
    form_result = validate_form(contract)
    for error in form_result.errors:
        for token in _legacy_tokens_for_form_error(error):
            if token not in all_missing:
                all_missing.append(token)

    # Determine effective status for the additive, non-shape checks below.
    # (validate_form already rejected an out-of-enum status via PLAN_STATUS;
    # a valid-shape status is safe to resolve the same way the core does.)
    effective_status = ""
    agent_status = contract.get("agent_status") if isinstance(contract, dict) else None
    if isinstance(agent_status, dict):
        effective_status = _resolve_status(agent_status)
        if effective_status not in _FORM_VALID_PLAN_STATUSES:
            effective_status = ""

    # 3. Check consolidation_report (only when required) -- task-context
    # dependent, not a form-layer concern.
    if isinstance(contract, dict) and requires_consolidation_report(task_info):
        consolidation = contract.get("consolidation_report")
        if not consolidation or not isinstance(consolidation, dict):
            all_missing.append("CONSOLIDATION_REPORT")
        else:
            for field in _CONSOLIDATION_REQUIRED_FIELDS:
                key_lower = field.lower()
                if not consolidation.get(key_lower) and not consolidation.get(field):
                    all_missing.append(field)

    # 4b. approval_request.verification must be present (blocking).
    # approval_request.rollback is advisory only (non-blocking): the hook
    # hardcodes rollback_hint=None by design (bash_validator.py
    # _build_sealed_payload), so a well-formed APPROVAL_REQUEST always
    # relays rollback=null -- treating that as a blocking violation
    # produced ~600 of 678 recorded false-positive anomalies (AC-5).
    approval_req = contract.get("approval_request") if isinstance(contract, dict) else None
    if approval_req and isinstance(approval_req, dict):
        if not approval_req.get("rollback"):
            logger.warning(
                "approval_request.rollback is null/missing (expected -- "
                "the hook relays rollback_hint=None by design); advisory only, not blocking"
            )
        if not approval_req.get("verification"):
            all_missing.append("APPROVAL_REQUEST_VERIFICATION_REQUIRED")

    # 5. Loop-state blocking check (T2.3)
    loop_anomaly = _check_loop_state_blocking(contract, effective_status) if isinstance(contract, dict) else None
    if loop_anomaly:
        all_missing.append(loop_anomaly)

    if all_missing:
        fields_str = ", ".join(all_missing)
        # The rich repair guidance is the SAME core's canonical message (AC-13):
        # the fence fallback does not carry its own, second copy of the repair
        # template. error_summary() adds the specific defect list the core
        # found (empty when the only failures are the additive checks above).
        form_detail = form_result.error_summary()
        detail_line = f"\nDefects: {form_detail}\n" if form_detail else "\n"
        error_message = (
            f"Contract incomplete. Missing: {fields_str}.{detail_line}\n"
            f"{CANONICAL_REPAIR_MESSAGE}"
        )
        return ValidationResult(
            is_valid=False,
            missing=all_missing,
            error_message=error_message,
        )

    return ValidationResult(is_valid=True, missing=[], error_message="")


# ============================================================================
# Main validation entry point
# ============================================================================

def validate(agent_output: str, task_info: Dict[str, Any]) -> ValidationResult:
    """Validate agent output against contract requirements.

    Accepts the single canonical ``agent_contract_handoff`` fenced-block format.

    This is the MIGRATION-ONLY fallback path for the legacy fenced block
    (AC-13): ``parse_contract`` extracts a plain dict from the raw fenced
    text -- fence extraction is the one piece of work the portable core
    intentionally does not do -- and the resulting dict is handed to
    ``_validate_from_handoff``, which validates SHAPE through the SAME core
    (``gaia.contract.validator.validate_form``) the CLI (M2) and the hook
    gate (M4) use. There is no second, locally re-implemented shape
    validator here.

    Checks:
    1. AGENT_STATUS block with plan_status and agent_id (SSOT core)
    2. EVIDENCE_REPORT with required fields, when status requires it (SSOT core)
    3. CONSOLIDATION_REPORT (when multi-surface task requires it) -- additive,
       task-context dependent, not a form-layer concern
    4. Blocking promotions: verification.result=pass for COMPLETE (SSOT core);
       approval_request.verification when present -- additive
    5. Loop-state blocking: iteration < max_iterations with metric below
       threshold -- additive

    Args:
        agent_output: Complete output from agent execution.
        task_info: Task metadata including injected_context for multi-surface detection.

    Returns:
        ValidationResult with is_valid, missing fields list, and error_message.
    """
    contract = parse_contract(agent_output)
    return _validate_from_handoff(contract, task_info)


# ============================================================================
# Functions absorbed from evidence_parser.py (backward compatible)
# ============================================================================

def extract_commands_from_evidence(agent_output: str) -> List[str]:
    """Extract command strings from the EVIDENCE_REPORT COMMANDS_RUN field.

    Reads from the ``agent_contract_handoff`` fenced-block, specifically the
    ``evidence_report.commands_run`` list.

    Commands whose result indicates they were NOT actually run (e.g. "not run",
    "skipped", "n/a", "not executed") are excluded from the returned list.

    Returns a list of command strings (without surrounding backticks).
    """
    contract = parse_contract(agent_output)
    if contract is None:
        return []

    evidence = contract.get("evidence_report", {}) or {}
    commands_run = evidence.get("commands_run", [])
    if not isinstance(commands_run, list):
        return []

    commands: List[str] = []
    for entry in commands_run:
        if isinstance(entry, dict):
            cmd = entry.get("command", entry.get("cmd", ""))
        elif isinstance(entry, str):
            cmd = entry
        else:
            continue
        if cmd and cmd.lower() not in _LITERAL_NONE_COMMANDS:
            if not _NOT_RUN_INDICATORS.search(cmd):
                commands.append(cmd)
    return commands


def requires_consolidation_report(task_info: Dict[str, Any]) -> bool:
    """Determine whether runtime should require a CONSOLIDATION_REPORT block.

    Checks injected_context for agent_contract_handoff.consolidation_required,
    agent_contract_handoff.cross_check_required, or surface_routing.multi_surface.
    Also checks the legacy ``investigation_brief`` key for backward compatibility.

    Falls back to reading from the transcript if injected_context was not
    pre-extracted.
    """
    payload = task_info.get("injected_context") or {}
    if not payload:
        # Fallback: read from transcript if injected_context was not pre-extracted
        from .transcript_reader import extract_injected_context_payload_from_transcript
        payload = extract_injected_context_payload_from_transcript(
            task_info.get("agent_transcript_path", ""),
            task_info.get("agent", ""),
        )
    if not payload:
        return False

    # New field name (T2.1a) -- check first
    agent_contract_handoff = payload.get("agent_contract_handoff", {}) or {}
    # Legacy field name -- backward compatibility during dual-mode window
    investigation_brief = payload.get("investigation_brief", {}) or {}
    surface_routing = payload.get("surface_routing", {}) or {}
    return bool(
        agent_contract_handoff.get("consolidation_required")
        or agent_contract_handoff.get("cross_check_required")
        or investigation_brief.get("consolidation_required")
        or investigation_brief.get("cross_check_required")
        or surface_routing.get("multi_surface")
    )


# ============================================================================
# T2.3 Clause parsers (new envelope fields)
# ============================================================================

_VALID_EVIDENCE_TYPES = frozenset({
    "text", "file", "command_output", "url", "screenshot",
})


def validate_evidence_update_contract_payload(payload: dict) -> List[str]:
    """Validate the payload of an evidence update_contracts clause.

    Enforces the flat-field shape from D6 (brief plan decisions):
    - ``brief_id`` required, must be int (or string coercible to int)
    - ``ac_id``    required, non-empty string
    - ``type``     required, must be in the valid evidence type enum
    - ``text`` and ``artifact_path`` are mutually exclusive (exactly one)
    - ``task_id``, ``created_by_agent``, ``size_bytes`` are optional

    Returns a list of error strings.  An empty list means the payload is valid.
    """
    errors: List[str] = []

    if not isinstance(payload, dict):
        errors.append("evidence payload must be an object/dict")
        return errors

    # brief_id: required, must be coercible to int
    raw_brief_id = payload.get("brief_id")
    if raw_brief_id is None:
        errors.append("evidence payload missing required field: brief_id")
    else:
        try:
            int(raw_brief_id)
        except (TypeError, ValueError):
            errors.append(
                f"evidence payload brief_id must be an integer, got {type(raw_brief_id).__name__!r}: {raw_brief_id!r}"
            )

    # ac_id: required, non-empty string
    ac_id = payload.get("ac_id")
    if not ac_id or not str(ac_id).strip():
        errors.append("evidence payload missing or empty required field: ac_id")

    # type: required, must be in enum
    ev_type = payload.get("type")
    if not ev_type:
        errors.append(
            f"evidence payload missing required field: type "
            f"(must be one of {sorted(_VALID_EVIDENCE_TYPES)})"
        )
    elif ev_type not in _VALID_EVIDENCE_TYPES:
        errors.append(
            f"evidence payload type {ev_type!r} is invalid; "
            f"must be one of {sorted(_VALID_EVIDENCE_TYPES)}"
        )

    # text / artifact_path: mutually exclusive, at least one required
    has_text = payload.get("text") is not None
    has_artifact = payload.get("artifact_path") is not None

    if has_text and has_artifact:
        errors.append(
            "evidence payload fields 'text' and 'artifact_path' are mutually exclusive; "
            "supply exactly one"
        )
    elif not has_text and not has_artifact:
        errors.append(
            "evidence payload requires exactly one of 'text' or 'artifact_path'"
        )

    return errors


def parse_update_contracts(contract: dict) -> List[Dict[str, Any]]:
    """Parse the ``update_contracts`` clause from a contract dict.

    The clause is an array of ``{contract, payload}`` objects.  Each
    structurally well-formed entry (has both ``contract`` and ``payload`` keys
    and is a dict) is returned as-is.

    Structural failures (not a dict, missing required keys) are skipped and
    logged.  Payload-level validation for specific contract types (e.g.
    ``evidence``) is intentionally **not** performed here so that callers can
    apply type-specific semantics (e.g. fail-together for evidence batches per
    D8).  Use ``validate_evidence_update_contract_payload()`` directly when
    pre-validating evidence payloads before calling the writer.

    Returns an empty list when the clause is absent or entirely malformed.
    """
    raw = contract.get("update_contracts")
    if raw is None:
        return []
    if not isinstance(raw, list):
        logger.warning("update_contracts: expected array, got %s", type(raw).__name__)
        return []

    results: List[Dict[str, Any]] = []
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            logger.warning("update_contracts[%d]: not an object, skipping", i)
            continue
        if "contract" not in entry or "payload" not in entry:
            logger.warning(
                "update_contracts[%d]: missing required keys 'contract'/'payload', skipping", i
            )
            continue

        # EXTENSION_POINT: add additional contract-type validators here
        # Note: evidence payload validation (validate_evidence_update_contract_payload)
        # is applied at write time by context_writer._apply_evidence_entries() with
        # fail-together semantics (D8). Do not filter evidence entries here.

        results.append(entry)
    return results


def parse_loop_state(contract: dict) -> Optional[Dict[str, Any]]:
    """Parse the ``loop_state`` clause from a contract dict.

    Expected shape::

        { "iteration": int, "max_iterations": int, "metric": float|null, "threshold": float|null }

    Returns the parsed dict, or None when the clause is absent or malformed.
    """
    raw = contract.get("loop_state")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        logger.warning("loop_state: expected object, got %s", type(raw).__name__)
        return None

    # Coerce numeric fields -- allow None/null for metric/threshold
    # iteration is required; return None when the key is absent entirely
    if "iteration" not in raw:
        logger.warning("loop_state: missing required field 'iteration'")
        return None
    try:
        iteration = int(raw["iteration"]) if raw.get("iteration") is not None else None
        max_iterations = int(raw["max_iterations"]) if raw.get("max_iterations") is not None else None
    except (TypeError, ValueError, KeyError) as exc:
        logger.warning("loop_state: could not parse numeric fields: %s", exc)
        return None

    metric_raw = raw.get("metric")
    threshold_raw = raw.get("threshold")

    try:
        metric = float(metric_raw) if metric_raw is not None else None
        threshold = float(threshold_raw) if threshold_raw is not None else None
    except (TypeError, ValueError):
        metric = None
        threshold = None

    return {
        "iteration": iteration,
        "max_iterations": max_iterations,
        "metric": metric,
        "threshold": threshold,
    }


def _check_loop_state_blocking(contract: dict, effective_status: str) -> Optional[str]:
    """Check loop_state blocking invariant (T2.3).

    Blocking condition: plan_status=COMPLETE AND iteration < max_iterations
    AND metric is not None AND metric < threshold.

    Returns an error token string if the check fails, None otherwise.
    """
    if effective_status != "COMPLETE":
        return None

    loop = parse_loop_state(contract)
    if loop is None:
        return None  # No loop_state clause -- check does not apply

    iteration = loop.get("iteration")
    max_iterations = loop.get("max_iterations")
    metric = loop.get("metric")
    threshold = loop.get("threshold")

    if (
        iteration is not None
        and max_iterations is not None
        and metric is not None
        and threshold is not None
        and iteration < max_iterations
        and metric < threshold
    ):
        return (
            f"LOOP_STATE_INCOMPLETE:"
            f"iteration={iteration}<max={max_iterations},"
            f"metric={metric}<threshold={threshold}"
        )
    return None


def parse_rollback_executed(contract: dict) -> Optional[str]:
    """Parse the ``rollback_executed`` clause from a contract dict (advisory).

    Returns the string value (or None) when present, or ``"ABSENT"`` sentinel
    when the key is not in the contract at all.

    The return value is purely informational; the validator never rejects based
    on this field.
    """
    if "rollback_executed" not in contract:
        return "ABSENT"
    val = contract.get("rollback_executed")
    return str(val) if val is not None else None


def parse_context_consumption(contract: dict) -> Optional[Dict[str, Any]]:
    """Parse the ``context_consumption`` clause from a contract dict (advisory).

    Expected shape::

        { "tokens_used": int|null, "pct_window": float|null }

    Returns the parsed dict, or None when absent or malformed.  The validator
    never rejects based on this field.
    """
    raw = contract.get("context_consumption")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        logger.warning("context_consumption: expected object, got %s", type(raw).__name__)
        return None

    tokens_raw = raw.get("tokens_used")
    pct_raw = raw.get("pct_window")

    try:
        tokens_used = int(tokens_raw) if tokens_raw is not None else None
    except (TypeError, ValueError):
        tokens_used = None

    try:
        pct_window = float(pct_raw) if pct_raw is not None else None
    except (TypeError, ValueError):
        pct_window = None

    return {"tokens_used": tokens_used, "pct_window": pct_window}


def parse_memory_suggestions(contract: dict) -> List[str]:
    """Parse the ``memory_suggestions`` clause from a contract dict (advisory).

    Returns a list of suggestion strings. Non-string entries are coerced to
    strings. Returns empty list when the clause is absent or malformed.  The
    validator never rejects based on this field.
    """
    raw = contract.get("memory_suggestions")
    if raw is None:
        return []
    if not isinstance(raw, list):
        logger.warning("memory_suggestions: expected array, got %s", type(raw).__name__)
        return []
    return [str(item) for item in raw if item is not None]


def parse_user_facing_summary(contract: dict) -> Optional[str]:
    """Parse the optional top-level ``user_facing_summary`` clause (advisory).

    The single human-audience field in the contract: a short prose summary the
    subagent writes once for the user. The orchestrator relays it near-verbatim
    on a single-agent COMPLETE (N=1) instead of re-synthesizing ``key_outputs``.

    Strictly additive and advisory -- the validator never rejects based on this
    field. Returns the trimmed string when present and non-empty, else None.
    """
    raw = contract.get("user_facing_summary")
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    return text or None


def extract_plan_status_from_output(agent_output: str) -> str:
    """Extract the effective plan_status string from agent output.

    Reads the canonical ``plan_status`` field from the ``agent_contract_handoff``
    block. Returns the raw status string (e.g. "COMPLETE", "BLOCKED",
    "NEEDS_INPUT") or empty string if not found.
    """
    contract = parse_contract(agent_output)
    if contract is None:
        return ""

    agent_status = contract.get("agent_status", {}) or {}
    return _resolve_status(agent_status)


def extract_exit_code_from_output(agent_output: str) -> int:
    """Derive exit code from the LAST AGENT_STATUS block in agent output.

    Looks for PLAN_STATUS in the final assistant message.  If the status
    contains COMPLETE -> 0, BLOCKED or ERROR -> 1.  Falls back to 0 when
    no AGENT_STATUS is found (optimistic default).
    """
    status_value = extract_plan_status_from_output(agent_output)
    if status_value:
        if "COMPLETE" in status_value:
            return 0
        if "BLOCKED" in status_value or "ERROR" in status_value:
            return 1
    return 0


# ============================================================================
# Context-usage anomaly detection
# ============================================================================

# Reuse the anchor extraction regex from anchor_tracker for consistency
_ANCHOR_FIELDS_RE = re.compile(
    r"(path|name|cluster|project|region|namespace|service|image|"
    r"base_path|config_path|module_path|repository|bucket|sa$|"
    r"service_account|pod_name|terragrunt_path)",
    re.IGNORECASE,
)

_MIN_ANCHOR_LEN = 4


def _extract_context_anchors(project_knowledge: Dict[str, Any]) -> set:
    """Extract anchor strings (paths, names, IDs) from project_knowledge sections.

    Walks the project_knowledge dict and collects string values from fields
    whose names match anchor-worthy patterns (paths, service names, clusters, etc.).

    Args:
        project_knowledge: The project_knowledge dict from the injected context.

    Returns:
        Set of anchor strings.
    """
    anchors: set = set()

    def _walk(obj: Any, depth: int = 0) -> None:
        if depth > 10:
            return
        if isinstance(obj, dict):
            for key, value in obj.items():
                if isinstance(value, str) and value and _ANCHOR_FIELDS_RE.search(key):
                    clean = value.lstrip("./")
                    if len(clean) >= _MIN_ANCHOR_LEN:
                        anchors.add(clean)
                elif isinstance(value, (dict, list)):
                    _walk(value, depth + 1)
        elif isinstance(obj, list):
            for item in obj:
                _walk(item, depth + 1)

    _walk(project_knowledge)
    return anchors


def check_context_usage(
    project_knowledge: Dict[str, Any],
    evidence_report: Dict[str, Any],
) -> Dict[str, Any]:
    """Soft check: detect when an agent ignores injected project context.

    Extracts anchors from project_knowledge and checks whether ANY of them
    appear in the agent's evidence_report (files_checked, patterns_checked,
    commands_run). If zero overlap, flags ``context_ignored: true``.

    This is a soft check -- it never fails validation, only adds a flag.

    Args:
        project_knowledge: The ``project_knowledge`` dict from injected context.
        evidence_report: The ``evidence_report`` dict from the agent's agent_contract_handoff.

    Returns:
        Dict with ``context_ignored`` (bool), ``anchors_found`` (int),
        ``anchors_in_evidence`` (int), and ``overlap`` (list of matched anchors).
    """
    if not project_knowledge or not evidence_report:
        return {
            "context_ignored": False,
            "anchors_found": 0,
            "anchors_in_evidence": 0,
            "overlap": [],
        }

    anchors = _extract_context_anchors(project_knowledge)
    if not anchors:
        return {
            "context_ignored": False,
            "anchors_found": 0,
            "anchors_in_evidence": 0,
            "overlap": [],
        }

    # Build a single searchable string from evidence fields
    evidence_parts: List[str] = []

    for field in ("files_checked", "patterns_checked", "commands_run"):
        entries = evidence_report.get(field, [])
        if isinstance(entries, list):
            for entry in entries:
                if isinstance(entry, str):
                    evidence_parts.append(entry)
                elif isinstance(entry, dict):
                    # commands_run may be dicts with "command" or "cmd" keys
                    evidence_parts.append(
                        entry.get("command", entry.get("cmd", str(entry)))
                    )

    evidence_text = " ".join(evidence_parts)

    matched: List[str] = []
    for anchor in anchors:
        if anchor in evidence_text:
            matched.append(anchor)

    return {
        "context_ignored": len(matched) == 0,
        "anchors_found": len(anchors),
        "anchors_in_evidence": len(matched),
        "overlap": sorted(matched),
    }


# ============================================================================
# Cross-field validation: verbatim_outputs consistency (Option D)
# ============================================================================

_VERBATIM_PLACEHOLDER_PATTERNS = re.compile(
    r"^(N/?A|none|no\s+output|no\s+output\s+captured|not\s+applicable|"
    r"no\s+commands?\s+run|no\s+verbatim\s+output|n/a|\[\]|-|"
    r"no\s+output\s+to\s+capture|not\s+available)\.?$",
    re.IGNORECASE,
)


def _is_real_command(entry: str) -> bool:
    """Return True if the commands_run entry represents a real executed command."""
    if not entry or not entry.strip():
        return False
    normalized = entry.strip().lower()
    if normalized in _LITERAL_NONE_COMMANDS:
        return False
    if _NOT_RUN_INDICATORS.search(normalized):
        return False
    return True


def _is_placeholder_output(entry: str) -> bool:
    """Return True if the verbatim_outputs entry is a placeholder, not real output."""
    if not entry or not entry.strip():
        return True
    return bool(_VERBATIM_PLACEHOLDER_PATTERNS.match(entry.strip()))


def validate_verbatim_outputs_consistency(
    parsed_contract: Optional[dict],
) -> Optional[Dict[str, Any]]:
    """Cross-field validation: commands_run vs verbatim_outputs.

    If commands_run has 1+ real entries, verbatim_outputs must have at least 1
    entry that is NOT a placeholder. Returns an anomaly dict if the check fails,
    None if it passes or does not apply.

    This is advisory only -- it should be logged but never block.
    """
    if parsed_contract is None:
        return None

    evidence = parsed_contract.get("evidence_report")
    if not evidence or not isinstance(evidence, dict):
        return None

    commands_run = evidence.get("commands_run", [])
    if not isinstance(commands_run, list):
        return None

    # Count real commands
    real_commands = []
    for entry in commands_run:
        if isinstance(entry, dict):
            cmd = entry.get("command", entry.get("cmd", ""))
        elif isinstance(entry, str):
            cmd = entry
        else:
            continue
        if _is_real_command(cmd):
            real_commands.append(cmd)

    if not real_commands:
        return None  # No real commands -- check does not apply

    # Check verbatim_outputs for at least 1 non-placeholder entry
    verbatim_outputs = evidence.get("verbatim_outputs", [])
    if not isinstance(verbatim_outputs, list):
        verbatim_outputs = []

    has_real_output = False
    for entry in verbatim_outputs:
        text = ""
        if isinstance(entry, str):
            text = entry
        elif isinstance(entry, dict):
            text = entry.get("output", entry.get("content", str(entry)))
        if text and not _is_placeholder_output(text):
            has_real_output = True
            break

    if has_real_output:
        return None  # Passes -- real commands have backing output

    return {
        "type": "verbatim_outputs_missing",
        "severity": "warning",
        "message": (
            f"Agent ran {len(real_commands)} command(s) but verbatim_outputs "
            f"contains no real output (only placeholders or empty). "
            f"Commands: {', '.join(c[:60] for c in real_commands[:3])}"
        ),
    }


# ============================================================================
# False pending-approval detection
# ============================================================================


# ============================================================================
# Approval request validation
# ============================================================================

_APPROVAL_STATUSES = {"APPROVAL_REQUEST"}

_APPROVAL_REQUIRED_FIELDS = [
    "operation", "exact_content", "scope", "risk_level", "rollback", "verification",
]

_VALID_RISK_LEVELS = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}

_NONCE_HEX_RE = re.compile(r"^[a-f0-9]{32}$")


def validate_approval_request(
    contract: dict,
    plan_status: str,
) -> Optional[Dict[str, Any]]:
    """Validate the approval_request block when plan_status is APPROVAL_REQUEST.

    Advisory only -- returns an anomaly dict if validation fails, None if OK
    or if the check does not apply.

    Args:
        contract: Parsed dict from parse_contract().
        plan_status: The agent's reported plan_status string (already uppercased).

    Returns:
        An anomaly dict (severity: info or warning) when the check triggers, None otherwise.
    """
    if plan_status.upper() not in _APPROVAL_STATUSES:
        return None

    approval_req = contract.get("approval_request")
    if not approval_req or not isinstance(approval_req, dict):
        return {
            "type": "approval_request_missing",
            "severity": "info",
            "detail": (
                f"Agent returned {plan_status} without an approval_request block. "
                f"Expected fields: {', '.join(_APPROVAL_REQUIRED_FIELDS)}"
            ),
        }

    missing_fields: List[str] = []
    for field in _APPROVAL_REQUIRED_FIELDS:
        if not approval_req.get(field):
            missing_fields.append(field)

    # Validate risk_level value if present
    risk = str(approval_req.get("risk_level", "")).upper()
    invalid_risk = risk and risk not in _VALID_RISK_LEVELS

    nonce_issue = None

    issues: List[str] = []
    if missing_fields:
        issues.append(f"missing fields: {', '.join(missing_fields)}")
    if invalid_risk:
        issues.append(f"invalid risk_level: {risk}")
    if nonce_issue:
        issues.append(nonce_issue)

    if not issues:
        return None

    return {
        "type": "approval_request_incomplete",
        "severity": "warning",
        "detail": (
            f"approval_request block for {plan_status} has issues: "
            f"{'; '.join(issues)}"
        ),
        "missing_fields": missing_fields,
    }
