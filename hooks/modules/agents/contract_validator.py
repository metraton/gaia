"""
Contract validation for agent output: structural checks, evidence parsing,
command extraction, PLAN_STATUS parsing, and exit code derivation.

The fenced-block format parsed here is ``agent_contract_handoff`` with field
name ``agent_state``. HTML-comment blocks (``<!-- AGENT_STATUS -->``, etc.) are **not** parsed. As a
tolerant fallback, a ```json``` fence is also accepted when its body already
has the shape of a handoff envelope (an ``agent_status.agent_state`` key).
Once a dict is extracted, its SHAPE is delegated to
``gaia.contract.validator.validate_form``, the core the CLI and the hook gate
also use; this module owns the fence extraction, since the core takes an
already-parsed dict. ``validate()`` adds only the approval-request check.

Both fence regexes require the closing ``` `` `` to start its own line and be
followed only by whitespace/end-of-string, so triple-backtick sequences quoted
inside the JSON body (a code block in ``verbatim_outputs``) do not truncate
the capture: a valid JSON string cannot contain a literal newline.

memory_delta is parsed by response_contract.parse_memory_delta, not here; the
form layer deliberately leaves that proposal-only field advisory.
"""

import difflib
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from gaia.contract.validator import (
    CANONICAL_REPAIR_MESSAGE,
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


_TAG_HANDOFF = "agent_contract_handoff"

_RE_HANDOFF = re.compile(r'```agent_contract_handoff\s*\n(.*?)\n```(?=\s|\Z)', re.DOTALL)

# Fallback fence label (see module docstring), with the same own-line closing fence.
_RE_JSON_FALLBACK = re.compile(r'```json\s*\n(.*?)\n```(?=\s|\Z)', re.DOTALL)


def _looks_like_handoff_envelope(parsed: Any) -> bool:
    """Return True when a parsed JSON value has the shape of a handoff envelope.

    Content-based check, deliberately narrow: a dict with an
    ``agent_status.agent_state`` key. This is what distinguishes an
    ``agent_contract_handoff`` payload from an arbitrary ```json``` block the
    agent may have emitted for an unrelated reason (e.g. quoting a command's
    JSON output in ``verbatim_outputs``).
    """
    if not isinstance(parsed, dict):
        return False
    agent_status = parsed.get("agent_status")
    return isinstance(agent_status, dict) and bool(agent_status.get("agent_state"))


def parse_contract(agent_output: str) -> Optional[dict]:
    """Extract structured contract dict from an ``agent_contract_handoff`` block.

    The parsed dict is augmented with a ``_contract_tag`` key
    (``"agent_contract_handoff"``) so downstream callers can identify the
    source uniformly.

    When no correctly-tagged fence is found, falls back to scanning ```json```
    fences for one whose body has the shape of a handoff envelope (see
    ``_looks_like_handoff_envelope``); the LAST such block wins, since the
    contract is the final block of the turn. Required-field validation is
    unchanged downstream.

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
    """Resolve the effective status string (upper-cased ``agent_state``) from an agent_status dict."""
    agent_state = str(agent_status.get("agent_state", "")).strip()
    return agent_state.upper().rstrip(".,;")


# gaia.contract.validator.FormError.field (dotted) -> the uppercase tokens that
# callers of validate()/ValidationResult.missing read; shape validity is not
# decided here.
_FORM_MISSING_FIELD_TOKENS = {
    "agent_contract_handoff": ("AGENT_STATUS", "PLAN_STATUS", "AGENT_ID"),
    "agent_status": ("AGENT_STATUS", "PLAN_STATUS", "AGENT_ID"),
    "agent_status.agent_state": ("PLAN_STATUS",),
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
    if error.code is FormErrorCode.VERIFICATION_SHAPE:
        return ["VERIFICATION_SHAPE"]
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

    SHAPE is delegated to ``gaia.contract.validator.validate_form``; ``None``
    (no parseable fenced block) is reported by it as a MISSING_FIELD. What
    stays here is the blocking approval_request.verification check, which the
    form layer does not own (see its "NO TASK CONTEXT" design note).

    Args:
        contract: Parsed dict from parse_contract(), or None when no fenced
            block was found at all.
        task_info: Unused; kept so callers' signatures stay stable.

    Returns:
        ValidationResult with is_valid, missing fields list, and error_message.
    """
    all_missing: List[str] = []

    form_result = validate_form(contract)
    for error in form_result.errors:
        for token in _legacy_tokens_for_form_error(error):
            if token not in all_missing:
                all_missing.append(token)

    # verification blocks; rollback is advisory, because bash_validator's
    # _STATEMENTS_BY_VERB seals one for only five verbs (push, apply, delete,
    # destroy, create) and every other verb relays rollback=null.
    approval_req = contract.get("approval_request") if isinstance(contract, dict) else None
    if approval_req and isinstance(approval_req, dict):
        if not approval_req.get("rollback"):
            logger.warning(
                "approval_request.rollback is null/missing (expected for verbs "
                "the hook authors no statement for); advisory only, not blocking"
            )
        if not approval_req.get("verification"):
            all_missing.append("APPROVAL_REQUEST_VERIFICATION_REQUIRED")

    if all_missing:
        fields_str = ", ".join(all_missing)
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


def validate(agent_output: str, task_info: Dict[str, Any]) -> ValidationResult:
    """Validate agent output against contract requirements.

    ``parse_contract`` extracts the dict from the fenced text and
    ``_validate_from_handoff`` validates it.

    Checks:
    1. AGENT_STATUS block with agent_state and agent_id (validate_form)
    2. EVIDENCE_REPORT with required fields (validate_form)
    3. verification.result=pass for COMPLETE (validate_form);
       approval_request.verification when present (additive)

    Args:
        agent_output: Complete output from agent execution.
        task_info: Task metadata (see ``_validate_from_handoff``).

    Returns:
        ValidationResult with is_valid, missing fields list, and error_message.
    """
    contract = parse_contract(agent_output)
    return _validate_from_handoff(contract, task_info)


def _commands_from_contract_dict(contract: Optional[dict]) -> List[str]:
    """The COMMANDS_RUN filter, given an already-parsed contract dict.

    The fenced text and a turn's persisted row envelope both pass through this
    one function, so an entry (a plain string or a ``{"command": ...}`` /
    ``{"cmd": ...}`` dict) resolves identically for both.

    Commands whose result indicates they were NOT actually run (e.g. "not
    run", "skipped", "n/a", "not executed") are excluded.
    """
    if not isinstance(contract, dict):
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


def extract_commands_from_evidence(agent_output: str) -> List[str]:
    """Extract command strings from the EVIDENCE_REPORT COMMANDS_RUN field.

    Reads from the ``agent_contract_handoff`` fenced-block, specifically the
    ``evidence_report.commands_run`` list.

    Commands whose result indicates they were NOT actually run (e.g. "not run",
    "skipped", "n/a", "not executed") are excluded from the returned list.

    Returns a list of command strings (without surrounding backticks).
    """
    contract = parse_contract(agent_output)
    return _commands_from_contract_dict(contract)


def merge_commands_executed(
    row_commands: List[str], fence_commands: List[str]
) -> List[str]:
    """Union two already-extracted command sequences into one ordered list.

    ``row_commands`` is this turn's persisted dispatch-row envelope;
    ``fence_commands`` is the fenced ``agent_contract_handoff`` block in the
    final message text. Both are plain command strings
    (``_commands_from_contract_dict`` normalizes them).

    Dedup is by literal equality at the ALIGNED position
    (:class:`difflib.SequenceMatcher`), not set membership, so a command run
    twice on purpose stays twice. The cost: the same command listed once by
    each source inside a stretch where they otherwise disagree can surface
    twice.

    Order is row-first: an agreeing run is emitted once, and where the
    sequences diverge the row's stretch precedes the fence's exclusive items,
    because the row is written in flight and the fence composed at the close.

    ``autojunk=False`` keeps the junk heuristic from down-weighting a command
    that recurs often in one list, which is the intentional-repeat case.
    """
    matcher = difflib.SequenceMatcher(
        None, row_commands, fence_commands, autojunk=False
    )
    merged: List[str] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            merged.extend(row_commands[i1:i2])
        elif tag == "delete":
            merged.extend(row_commands[i1:i2])
        elif tag == "insert":
            merged.extend(fence_commands[j1:j2])
        elif tag == "replace":
            merged.extend(row_commands[i1:i2])
            merged.extend(fence_commands[j1:j2])
    return merged


def extract_commands_executed(
    *, agent_output: str, row_envelope: Optional[Any] = None
) -> List[str]:
    """The ``commands_executed`` union: this turn's persisted row envelope
    merged with the fenced ``agent_contract_handoff`` text, so neither
    source's gap loses commands the other source has.

    ``row_envelope`` is the dict the SubagentStop gate already resolved
    (``_authoritative_envelope`` in ``hooks/adapters/subagent_stop_core.py``);
    this function reads neither the DB nor the disk.

    See :func:`merge_commands_executed` for the dedup and order decisions.
    """
    fence_commands = extract_commands_from_evidence(agent_output)
    row_commands = _commands_from_contract_dict(
        row_envelope if isinstance(row_envelope, dict) else None
    )
    return merge_commands_executed(row_commands, fence_commands)


_VALID_EVIDENCE_TYPES = frozenset({
    "text", "file", "command_output", "url", "screenshot",
})


def validate_evidence_update_contract_payload(payload: dict) -> List[str]:
    """Validate the payload of an evidence update_contracts clause.

    Enforces the flat-field shape:
    - ``brief_id`` required, must be int (or string coercible to int)
    - ``ac_id``    required, non-empty string
    - ``type``     required, must be in the valid evidence type enum
    - ``text`` and ``artifact_path`` are mutually exclusive (exactly one)
    - ``artifact_path``, when present, must already be under the canonical
      evidence root (``gaia.evidence.fs.require_canonical_artifact_path``)
    - ``task_id``, ``created_by_agent``, ``size_bytes`` are optional

    Returns a list of error strings.  An empty list means the payload is valid.
    """
    errors: List[str] = []

    if not isinstance(payload, dict):
        errors.append("evidence payload must be an object/dict")
        return errors

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

    ac_id = payload.get("ac_id")
    if not ac_id or not str(ac_id).strip():
        errors.append("evidence payload missing or empty required field: ac_id")

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

    # bin/cli/ac.py and bin/cli/brief.py apply the same
    # require_canonical_artifact_path guard.
    artifact_path = payload.get("artifact_path")
    if artifact_path is not None:
        try:
            from gaia.evidence.fs import require_canonical_artifact_path
            require_canonical_artifact_path(str(artifact_path))
        except ValueError as exc:
            errors.append(
                f"evidence payload artifact_path {artifact_path!r} rejected: {exc}"
            )

    return errors


UPDATE_CONTRACTS_ENTRY_KEYS = ("contract", "payload")


def update_contracts_entry_problem(entry: Any) -> Optional[str]:
    """Why ``parse_update_contracts`` drops ``entry``, naming the literal keys it lacks, or None when it keeps it."""
    if not isinstance(entry, dict):
        return f"is a {type(entry).__name__}, not an object"
    missing = [f'"{key}"' for key in UPDATE_CONTRACTS_ENTRY_KEYS if key not in entry]
    if missing:
        found = ", ".join(f'"{key}"' for key in sorted(map(str, entry))) or "no keys"
        return f"has {found} and lacks the literal key {' and '.join(missing)}"
    if not isinstance(entry["payload"], dict):
        return f'"payload" is a {type(entry["payload"]).__name__}, not an object'
    return None


def parse_update_contracts(contract: dict) -> List[Dict[str, Any]]:
    """Parse the ``update_contracts`` clause from a contract dict.

    The clause is an array of ``{contract, payload}`` objects.  Each
    structurally well-formed entry (has both ``contract`` and ``payload`` keys,
    is a dict, and carries a ``payload`` that is itself a JSON object) is
    returned as-is.

    A non-object ``payload`` is rejected here because a payload is a DELTA the
    writer deep-merges into the stored section: any other type could only
    replace the whole section. ``context_writer.apply_update`` refuses it too;
    this gate stops the entry before a permission query or write transaction.

    Structural failures (not a dict, missing required keys) are skipped and
    logged.  Payload-level validation per contract type (e.g. ``evidence``) is
    left to the callers; ``validate_evidence_update_contract_payload()``
    pre-validates evidence payloads.

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
        problem = update_contracts_entry_problem(entry)
        if problem:
            logger.warning("update_contracts[%d] %s, skipping", i, problem)
            continue

        # Evidence entries are validated at write time by
        # context_writer._apply_evidence_entries, which fails the batch together.
        results.append(entry)
    return results


def parse_conclusion(contract: dict) -> Optional[str]:
    """The specialist's verdict for the orchestrator, as ``gaia.contract.validator.conclusion_of`` reads it."""
    from gaia.contract.validator import conclusion_of

    return conclusion_of(contract)


def parse_failure_report(contract: dict) -> Optional[Dict[str, Any]]:
    """Parse the optional top-level ``failure_report`` clause (advisory).

    The block is the failure axis: a concrete defect the turn suffered --
    what it attempted, what broke, and the observed proof -- kept separate
    from ``open_gaps`` (the unknown, not a defect).

    Absent, explicit null, or malformed all return None, never an exception or
    a partial dict. Well-formedness is ``validate_form``'s FAILURE_REPORT_SHAPE
    code, the same check the SubagentStop gate applies.

    Returns the normalized dict on success::

        {"attempted": str, "symptom": str, "evidence": [str, ...],
         "component": str | None, "severity": str | None}
    """
    raw = contract.get("failure_report")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        logger.warning("failure_report: expected object, got %s", type(raw).__name__)
        return None

    form_result = validate_form(contract)
    if any(err.code is FormErrorCode.FAILURE_REPORT_SHAPE for err in form_result.errors):
        logger.warning("failure_report: malformed block, treating as absent")
        return None

    evidence = [
        item.strip() for item in raw.get("evidence", [])
        if isinstance(item, str) and item.strip()
    ]
    component = raw.get("component")
    severity = raw.get("severity")

    return {
        "attempted": str(raw["attempted"]).strip(),
        "symptom": str(raw["symptom"]).strip(),
        "evidence": evidence,
        "component": component.strip() if isinstance(component, str) and component.strip() else None,
        "severity": severity.strip().lower() if isinstance(severity, str) and severity.strip() else None,
    }


def extract_plan_status_from_output(agent_output: str) -> str:
    """Extract the effective agent_state string from agent output.

    Reads ``agent_state`` from the ``agent_contract_handoff`` block. Returns the
    upper-cased status string (e.g. "COMPLETE", "BLOCKED", "NEEDS_INPUT") or
    empty string if not found.
    """
    contract = parse_contract(agent_output)
    if contract is None:
        return ""

    agent_status = contract.get("agent_status", {}) or {}
    return _resolve_status(agent_status)


def extract_exit_code_from_output(agent_output: str) -> int:
    """Derive exit code from the agent_state in the handoff envelope.

    A status containing COMPLETE -> 0, BLOCKED or ERROR -> 1. Falls back
    to 0 when no envelope is found (optimistic default).
    """
    status_value = extract_plan_status_from_output(agent_output)
    if status_value:
        if "COMPLETE" in status_value:
            return 0
        if "BLOCKED" in status_value or "ERROR" in status_value:
            return 1
    return 0


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


_APPROVAL_STATUSES = {"APPROVAL_REQUEST"}

_APPROVAL_REQUIRED_FIELDS = [
    "operation", "exact_content", "scope", "risk_level", "rollback", "verification",
]

_VALID_RISK_LEVELS = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}

_NONCE_HEX_RE = re.compile(r"^[a-f0-9]{32}$")


def validate_approval_request(
    contract: dict,
    agent_state: str,
) -> Optional[Dict[str, Any]]:
    """Validate the approval_request block when agent_state is APPROVAL_REQUEST.

    Advisory only -- returns an anomaly dict if validation fails, None if OK
    or if the check does not apply.

    Args:
        contract: Parsed dict from parse_contract().
        agent_state: The agent's reported agent_state string (already uppercased).

    Returns:
        An anomaly dict (severity: info or warning) when the check triggers, None otherwise.
    """
    if agent_state.upper() not in _APPROVAL_STATUSES:
        return None

    approval_req = contract.get("approval_request")
    if not approval_req or not isinstance(approval_req, dict):
        return {
            "type": "approval_request_missing",
            "severity": "info",
            "detail": (
                f"Agent returned {agent_state} without an approval_request block. "
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
            f"approval_request block for {agent_state} has issues: "
            f"{'; '.join(issues)}"
        ),
        "missing_fields": missing_fields,
    }
