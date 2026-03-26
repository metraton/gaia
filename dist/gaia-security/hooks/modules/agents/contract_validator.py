"""
Contract validation for agent output: structural checks, evidence parsing,
command extraction, PLAN_STATUS parsing, and exit code derivation.

Only the ``json:contract`` fenced-block format is supported.  Legacy
HTML-comment blocks (``<!-- AGENT_STATUS -->``, etc.) are **not** parsed here.

Provides:
    - parse_contract(): Extract structured dict from json:contract fenced block
    - validate(): Check agent output against contract requirements -> ValidationResult
    - extract_commands_from_evidence(): Parse COMMANDS_RUN field
    - requires_consolidation_report(): Check if consolidation is needed
    - extract_plan_status_from_output(): Extract PLAN_STATUS string
    - extract_exit_code_from_output(): Derive exit code from PLAN_STATUS
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
# JSON contract parser
# ============================================================================

def parse_contract(agent_output: str) -> Optional[dict]:
    """Extract structured contract dict from a ``json:contract`` fenced block.

    Searches for the first occurrence of a fenced code block tagged
    ``json:contract`` and attempts to parse its contents as JSON.

    Args:
        agent_output: Complete output from agent execution.

    Returns:
        Parsed dict if a valid json:contract block is found, None otherwise.
    """
    m = re.search(r'```json:contract\s*\n(.*?)```', agent_output, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


# ============================================================================
# JSON contract validation helpers
# ============================================================================

def _validate_from_json_contract(contract: dict, task_info: Dict[str, Any]) -> ValidationResult:
    """Validate agent output using the parsed JSON contract dict.

    Checks that the contract dict contains the required keys:
    - agent_status with plan_status and agent_id
    - evidence_report with required fields (when plan_status requires it)
    - consolidation_report (when multi-surface task requires it)

    Args:
        contract: Parsed dict from parse_contract().
        task_info: Task metadata including injected_context for multi-surface detection.

    Returns:
        ValidationResult with is_valid, missing fields list, and error_message.
    """
    all_missing: List[str] = []

    # 1. Check agent_status
    agent_status = contract.get("agent_status")
    if not agent_status or not isinstance(agent_status, dict):
        all_missing.extend(["AGENT_STATUS", "PLAN_STATUS", "AGENT_ID"])
    else:
        if not agent_status.get("plan_status"):
            all_missing.append("PLAN_STATUS")
        if not agent_status.get("agent_id"):
            all_missing.append("AGENT_ID")

    # Determine plan_status for evidence check
    plan_status = ""
    if agent_status and isinstance(agent_status, dict):
        plan_status = str(agent_status.get("plan_status", "")).upper()

    statuses_requiring_evidence = {
        "IN_PROGRESS", "REVIEW", "AWAITING_APPROVAL",
        "COMPLETE", "BLOCKED", "NEEDS_INPUT",
    }

    if plan_status in statuses_requiring_evidence:
        # 2. Check evidence_report
        evidence = contract.get("evidence_report")
        if not evidence or not isinstance(evidence, dict):
            all_missing.append("EVIDENCE_REPORT")
        else:
            for field in _EVIDENCE_REQUIRED_FIELDS:
                # Accept both lower-case keys (JSON style) and upper-case (legacy)
                key_lower = field.lower()
                if not evidence.get(key_lower) and not evidence.get(field):
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

    if all_missing:
        fields_str = ", ".join(all_missing)
        error_message = (
            f"Contract incomplete. Missing: {fields_str}.\n"
            f"\n"
            f"Repair: reissue your response ending with a json:contract block:\n"
            f"\n"
            f"```json:contract\n"
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

    return ValidationResult(is_valid=True, missing=[], error_message="")


# ============================================================================
# Main validation entry point
# ============================================================================

def validate(agent_output: str, task_info: Dict[str, Any]) -> ValidationResult:
    """Validate agent output against contract requirements.

    Only the ``json:contract`` fenced-block format is supported.

    Checks:
    1. AGENT_STATUS block with plan_status and agent_id
    2. EVIDENCE_REPORT with required fields (when plan_status requires it)
    3. CONSOLIDATION_REPORT (when multi-surface task requires it)

    Args:
        agent_output: Complete output from agent execution.
        task_info: Task metadata including injected_context for multi-surface detection.

    Returns:
        ValidationResult with is_valid, missing fields list, and error_message.
    """
    contract = parse_contract(agent_output)
    if contract is not None:
        return _validate_from_json_contract(contract, task_info)

    # No json:contract block found -- report everything as missing.
    all_missing = ["AGENT_STATUS", "PLAN_STATUS", "AGENT_ID"]
    fields_str = ", ".join(all_missing)
    error_message = (
        f"Contract incomplete. Missing: {fields_str}. "
        f"No json:contract fenced block found.\n"
        f"\n"
        f"Repair: your response MUST end with a json:contract block:\n"
        f"\n"
        f"```json:contract\n"
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

    Only the ``json:contract`` fenced-block format is supported.
    Extracts from ``evidence_report.commands_run`` list entries.

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

    Checks injected_context for investigation_brief.consolidation_required,
    investigation_brief.cross_check_required, or surface_routing.multi_surface.

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

    investigation_brief = payload.get("investigation_brief", {}) or {}
    surface_routing = payload.get("surface_routing", {}) or {}
    return bool(
        investigation_brief.get("consolidation_required")
        or investigation_brief.get("cross_check_required")
        or surface_routing.get("multi_surface")
    )


def extract_plan_status_from_output(agent_output: str) -> str:
    """Extract the PLAN_STATUS string from agent output.

    Only the ``json:contract`` fenced-block format is supported.

    Returns the raw status string (e.g. "COMPLETE", "BLOCKED", "NEEDS_INPUT")
    or empty string if not found.
    """
    contract = parse_contract(agent_output)
    if contract is None:
        return ""

    agent_status = contract.get("agent_status", {}) or {}
    plan_status = agent_status.get("plan_status", "")
    if plan_status:
        return str(plan_status).upper().rstrip(".,;")
    return ""


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
        evidence_report: The ``evidence_report`` dict from the agent's json:contract.

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

_NONCE_PATTERN = re.compile(r"NONCE:[a-f0-9]{32}")


def validate_awaiting_approval_has_nonce(
    transcript_text: str,
    plan_status: str,
) -> Optional[Dict[str, Any]]:
    """Detect when an agent returns AWAITING_APPROVAL without a real hook nonce.

    If plan_status is AWAITING_APPROVAL, a hook should have blocked a T3 command
    and emitted a ``NONCE:<32-hex>`` token.  If no such token appears in the
    agent's transcript/output, the agent likely over-escalated.

    Args:
        transcript_text: The full agent transcript or output text.
        plan_status: The agent's reported plan_status string.

    Returns:
        An anomaly dict (severity: info) when the check triggers, None otherwise.
    """
    if plan_status.upper() != "AWAITING_APPROVAL":
        return None

    if _NONCE_PATTERN.search(transcript_text):
        return None

    return {
        "type": "awaiting_approval_missing_nonce",
        "severity": "info",
        "detail": (
            "Agent returned AWAITING_APPROVAL without a hook nonce. "
            "This is normal for plan-first workflows; it becomes a concern "
            "only if the agent never proceeds to execution."
        ),
    }


# ============================================================================
# Approval request validation
# ============================================================================

_APPROVAL_STATUSES = {"REVIEW", "AWAITING_APPROVAL"}

_APPROVAL_REQUIRED_FIELDS = [
    "operation", "exact_content", "scope", "risk_level", "rollback", "verification",
]

_VALID_RISK_LEVELS = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}

_NONCE_HEX_RE = re.compile(r"^[a-f0-9]{32}$")


def validate_approval_request(
    contract: dict,
    plan_status: str,
) -> Optional[Dict[str, Any]]:
    """Validate the approval_request block when plan_status is REVIEW or AWAITING_APPROVAL.

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

    # For AWAITING_APPROVAL, also check nonce
    nonce_issue = None
    if plan_status.upper() == "AWAITING_APPROVAL":
        nonce_val = str(approval_req.get("nonce", ""))
        if not nonce_val:
            nonce_issue = "nonce field missing"
        elif not _NONCE_HEX_RE.match(nonce_val):
            nonce_issue = f"nonce format invalid: {nonce_val}"

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
