"""
Contract validation for agent output: structural checks, evidence parsing,
command extraction, PLAN_STATUS parsing, and exit code derivation.

The single supported fenced-block format is ``agent_contract_handoff`` with
field name ``plan_status``. Legacy HTML-comment blocks
(``<!-- AGENT_STATUS -->``, etc.) are **not** parsed.

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
"""

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

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

_RE_HANDOFF = re.compile(r'```agent_contract_handoff\s*\n(.*?)```', re.DOTALL)


def parse_contract(agent_output: str) -> Optional[dict]:
    """Extract structured contract dict from an ``agent_contract_handoff`` block.

    The single supported envelope uses ``plan_status`` as the canonical status
    field (matching the database column ``episodes.plan_status`` and the
    ``AgentStatus.plan_status`` dataclass).

    The parsed dict is augmented with a ``_contract_tag`` key
    (``"agent_contract_handoff"``) so downstream callers can identify the
    source uniformly.

    Args:
        agent_output: Complete output from agent execution.

    Returns:
        Parsed dict augmented with ``_contract_tag`` when a valid block is
        found, None otherwise.
    """
    m = _RE_HANDOFF.search(agent_output)
    if m is None:
        return None
    try:
        parsed = json.loads(m.group(1))
        if isinstance(parsed, dict):
            parsed["_contract_tag"] = _TAG_HANDOFF
        return parsed
    except json.JSONDecodeError:
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

def _validate_from_handoff(contract: dict, task_info: Dict[str, Any]) -> ValidationResult:
    """Validate agent output using the parsed agent_contract_handoff dict.

    Checks that the contract dict contains the required keys:
    - agent_status with plan_status and agent_id
    - evidence_report with required fields (when status requires it)
    - consolidation_report (when multi-surface task requires it)
    - blocking promotions (T2.2):
        * verification.result must be "pass" when status is COMPLETE
        * approval_request.rollback must be present when approval_request present
        * approval_request.verification must be present when approval_request present

    Args:
        contract: Parsed dict from parse_contract().
        task_info: Task metadata including injected_context for multi-surface detection.

    Returns:
        ValidationResult with is_valid, missing fields list, and error_message.
    """
    all_missing: List[str] = []

    # 1. Check agent_status (single-mode: plan_status is canonical)
    agent_status = contract.get("agent_status")
    if not agent_status or not isinstance(agent_status, dict):
        all_missing.extend(["AGENT_STATUS", "PLAN_STATUS", "AGENT_ID"])
    else:
        if not agent_status.get("plan_status"):
            all_missing.append("PLAN_STATUS")
        if not agent_status.get("agent_id"):
            all_missing.append("AGENT_ID")

    # Determine effective status for downstream checks
    effective_status = ""
    if agent_status and isinstance(agent_status, dict):
        effective_status = _resolve_status(agent_status)

    statuses_requiring_evidence = {
        "IN_PROGRESS", "APPROVAL_REQUEST",
        "COMPLETE", "BLOCKED", "NEEDS_INPUT",
    }

    if effective_status in statuses_requiring_evidence:
        # 2. Check evidence_report
        evidence = contract.get("evidence_report")
        if not evidence or not isinstance(evidence, dict):
            all_missing.append("EVIDENCE_REPORT")
        else:
            for field in _EVIDENCE_REQUIRED_FIELDS:
                # Accept both lower-case keys (JSON style) and upper-case (legacy)
                # Use key-presence check (not truthiness) so empty lists [] are accepted
                key_lower = field.lower()
                if key_lower not in evidence and field not in evidence:
                    all_missing.append(field)

    # 3. Check consolidation_report (only when required)
    if requires_consolidation_report(task_info):
        consolidation = contract.get("consolidation_report")
        if not consolidation or not isinstance(consolidation, dict):
            all_missing.append("CONSOLIDATION_REPORT")
        else:
            for field in _CONSOLIDATION_REQUIRED_FIELDS:
                key_lower = field.lower()
                if not consolidation.get(key_lower) and not consolidation.get(field):
                    all_missing.append(field)

    # 4. Blocking promotions (T2.2)
    # 4a. verification.result must be "pass" when status is COMPLETE
    if effective_status == "COMPLETE":
        evidence_block = contract.get("evidence_report") or {}
        verification = evidence_block.get("verification")
        if not isinstance(verification, dict):
            all_missing.append("VERIFICATION_RESULT_REQUIRED_FOR_COMPLETE")
        else:
            result_val = str(verification.get("result", "")).lower().strip()
            if result_val != "pass":
                all_missing.append("VERIFICATION_RESULT_MUST_BE_PASS")

    # 4b. approval_request.rollback and approval_request.verification must be present
    approval_req = contract.get("approval_request")
    if approval_req and isinstance(approval_req, dict):
        if not approval_req.get("rollback"):
            all_missing.append("APPROVAL_REQUEST_ROLLBACK_REQUIRED")
        if not approval_req.get("verification"):
            all_missing.append("APPROVAL_REQUEST_VERIFICATION_REQUIRED")

    # 5. Loop-state blocking check (T2.3)
    loop_anomaly = _check_loop_state_blocking(contract, effective_status)
    if loop_anomaly:
        all_missing.append(loop_anomaly)

    if all_missing:
        fields_str = ", ".join(all_missing)
        error_message = (
            f"Contract incomplete. Missing: {fields_str}.\n"
            f"\n"
            f"Repair: reissue your response ending with an agent_contract_handoff block:\n"
            f"\n"
            f"```agent_contract_handoff\n"
            f'{{\n'
            f'  "agent_status": {{\n'
            f'    "plan_status": "<STATUS>",\n'
            f'    "agent_id": "<your-id>",\n'
            f'    "pending_steps": [],\n'
            f'    "next_action": "<done or next step>"\n'
            f"  }},\n"
            f'  "evidence_report": {{\n'
            f'    "patterns_checked": [],\n'
            f'    "files_checked": [],\n'
            f'    "commands_run": [],\n'
            f'    "key_outputs": [],\n'
            f'    "verbatim_outputs": [],\n'
            f'    "cross_layer_impacts": [],\n'
            f'    "open_gaps": []\n'
            f"  }},\n"
            f'  "consolidation_report": null\n'
            f"}}\n"
            f"```\n"
            f"\n"
            f"Required fields: agent_status (plan_status, agent_id, pending_steps, next_action), evidence_report\n"
            f"Evidence required fields: patterns_checked, files_checked, commands_run, key_outputs, verbatim_outputs, cross_layer_impacts, open_gaps\n"
            f"Blocking: COMPLETE requires verification.result=pass; approval_request requires rollback and verification fields"
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

    Checks:
    1. AGENT_STATUS block with plan_status and agent_id
    2. EVIDENCE_REPORT with required fields (when status requires it)
    3. CONSOLIDATION_REPORT (when multi-surface task requires it)
    4. Blocking promotions: verification.result=pass for COMPLETE,
       approval_request.rollback and approval_request.verification when present
    5. Loop-state blocking: iteration < max_iterations with metric below threshold

    Args:
        agent_output: Complete output from agent execution.
        task_info: Task metadata including injected_context for multi-surface detection.

    Returns:
        ValidationResult with is_valid, missing fields list, and error_message.
    """
    contract = parse_contract(agent_output)
    if contract is not None:
        return _validate_from_handoff(contract, task_info)

    # No recognized contract block found -- report everything as missing.
    all_missing = ["AGENT_STATUS", "PLAN_STATUS", "AGENT_ID"]
    fields_str = ", ".join(all_missing)
    error_message = (
        f"Contract incomplete. Missing: {fields_str}. "
        f"No agent_contract_handoff fenced block found.\n"
        f"\n"
        f"Repair: your response MUST end with a contract block:\n"
        f"\n"
        f"```agent_contract_handoff\n"
        f'{{\n'
        f'  "agent_status": {{\n'
        f'    "plan_status": "<STATUS>",\n'
        f'    "agent_id": "<your-id>",\n'
        f'    "pending_steps": [],\n'
        f'    "next_action": "<done or next step>"\n'
        f"  }},\n"
        f'  "evidence_report": {{\n'
        f'    "patterns_checked": [],\n'
        f'    "files_checked": [],\n'
        f'    "commands_run": [],\n'
        f'    "key_outputs": [],\n'
        f'    "verbatim_outputs": [],\n'
        f'    "cross_layer_impacts": [],\n'
        f'    "open_gaps": []\n'
        f"  }},\n"
        f'  "consolidation_report": null\n'
        f"}}\n"
        f"```\n"
        f"\n"
        f"Required fields: agent_status (plan_status, agent_id, pending_steps, next_action), evidence_report\n"
        f"Evidence required fields: patterns_checked, files_checked, commands_run, key_outputs, verbatim_outputs, cross_layer_impacts, open_gaps"
    )
    return ValidationResult(
        is_valid=False,
        missing=all_missing,
        error_message=error_message,
    )


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
            task_info.get("agent_transcript_path", "")
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
