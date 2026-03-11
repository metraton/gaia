"""
Contract validation for agent output: structural checks, evidence parsing,
command extraction, PLAN_STATUS parsing, and exit code derivation.

Supports two contract formats:
    1. Legacy HTML-comment blocks (<!-- AGENT_STATUS -->, <!-- EVIDENCE_REPORT -->, etc.)
    2. JSON contract blocks (```json:contract ... ```)

When a ``json:contract`` fenced block is present, parse_contract() extracts and
returns the structured dict. The validate() function checks both formats,
preferring the JSON contract when available.

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

# Evidence report markers
_EVIDENCE_REPORT_RE = re.compile(
    r"<!-- EVIDENCE_REPORT -->\s*(.*?)\s*<!-- /EVIDENCE_REPORT -->", re.DOTALL
)
_AGENT_STATUS_RE = re.compile(
    r"<!-- AGENT_STATUS -->\s*(.*?)\s*<!-- /AGENT_STATUS -->", re.DOTALL
)
_CONSOLIDATION_REPORT_RE = re.compile(
    r"<!-- CONSOLIDATION_REPORT -->\s*(.*?)\s*<!-- /CONSOLIDATION_REPORT -->", re.DOTALL
)

# JSON contract block pattern
_JSON_CONTRACT_RE = re.compile(
    r'```json:contract\s*\n(.*?)```', re.DOTALL
)

# Required evidence fields
_EVIDENCE_REQUIRED_FIELDS = [
    "PATTERNS_CHECKED", "FILES_CHECKED", "COMMANDS_RUN", "KEY_OUTPUTS",
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
        "INVESTIGATING", "PLANNING", "PENDING_APPROVAL",
        "FIXING", "COMPLETE", "BLOCKED", "NEEDS_INPUT",
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
            f"Contract incomplete. Missing: {fields_str}. "
            f"Include: patterns_checked, files_checked, commands_run, key_outputs."
        )
        return ValidationResult(
            is_valid=False,
            missing=all_missing,
            error_message=error_message,
        )

    return ValidationResult(is_valid=True, missing=[], error_message="")


# ============================================================================
# Legacy HTML-comment block validation helpers
# ============================================================================

def _check_agent_status(agent_output: str) -> List[str]:
    """Check for AGENT_STATUS block with plan_status and agent_id.

    Returns list of missing field names (empty if all present).
    """
    missing = []
    match = None
    for m in _AGENT_STATUS_RE.finditer(agent_output):
        match = m
    if match is None:
        # Check for unstructured PLAN_STATUS as fallback
        if not re.search(r"PLAN_STATUS:\s*\S+", agent_output):
            missing.append("AGENT_STATUS")
            missing.append("PLAN_STATUS")
            missing.append("AGENT_ID")
            return missing
        # Has PLAN_STATUS but no structured block
        if not re.search(r"AGENT_ID:\s*\S+", agent_output):
            missing.append("AGENT_ID")
        return missing

    block_text = match.group(1)
    if not re.search(r"PLAN_STATUS:\s*\S+", block_text):
        missing.append("PLAN_STATUS")
    if not re.search(r"AGENT_ID:\s*\S+", block_text):
        missing.append("AGENT_ID")
    return missing


def _check_evidence_report(agent_output: str) -> List[str]:
    """Check for EVIDENCE_REPORT block with required fields.

    Returns list of missing field names (empty if all present).
    """
    missing = []
    match = None
    for m in _EVIDENCE_REPORT_RE.finditer(agent_output):
        match = m
    if match is None:
        missing.append("EVIDENCE_REPORT")
        return missing

    block_text = match.group(1)
    for field in _EVIDENCE_REQUIRED_FIELDS:
        # Check that field header exists
        pattern = re.compile(rf"^{field}:", re.MULTILINE)
        if not pattern.search(block_text):
            missing.append(field)
    return missing


def _check_consolidation_report(agent_output: str, task_info: Dict[str, Any]) -> List[str]:
    """Check for CONSOLIDATION_REPORT block when multi-surface task requires it.

    Only checks when injected_context indicates consolidation is required.
    Returns list of missing field names (empty if not required or all present).
    """
    if not requires_consolidation_report(task_info):
        return []

    missing = []
    match = None
    for m in _CONSOLIDATION_REPORT_RE.finditer(agent_output):
        match = m
    if match is None:
        missing.append("CONSOLIDATION_REPORT")
        return missing

    block_text = match.group(1)
    for field in _CONSOLIDATION_REQUIRED_FIELDS:
        pattern = re.compile(rf"^{field}:", re.MULTILINE)
        if not pattern.search(block_text):
            missing.append(field)
    return missing


# ============================================================================
# Main validation entry point
# ============================================================================

def validate(agent_output: str, task_info: Dict[str, Any]) -> ValidationResult:
    """Validate agent output against contract requirements.

    Tries JSON contract format first (``json:contract`` fenced block).
    Falls back to legacy HTML-comment block parsing if no JSON contract found.

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
    # Prefer JSON contract format when present
    contract = parse_contract(agent_output)
    if contract is not None:
        return _validate_from_json_contract(contract, task_info)

    # Fallback: legacy HTML-comment block parsing
    all_missing: List[str] = []

    # 1. Check AGENT_STATUS
    status_missing = _check_agent_status(agent_output)
    all_missing.extend(status_missing)

    # Only check evidence if we have a plan_status that requires it
    plan_status = extract_plan_status_from_output(agent_output)
    # APPROVED_EXECUTING does not require evidence
    statuses_requiring_evidence = {
        "INVESTIGATING", "PLANNING", "PENDING_APPROVAL",
        "FIXING", "COMPLETE", "BLOCKED", "NEEDS_INPUT",
    }
    if plan_status in statuses_requiring_evidence:
        # 2. Check EVIDENCE_REPORT
        evidence_missing = _check_evidence_report(agent_output)
        all_missing.extend(evidence_missing)

    # 3. Check CONSOLIDATION_REPORT (only when required)
    consolidation_missing = _check_consolidation_report(agent_output, task_info)
    all_missing.extend(consolidation_missing)

    if all_missing:
        fields_str = ", ".join(all_missing)
        error_message = (
            f"Contract incomplete. Missing: {fields_str}. "
            f"Include: patterns_checked, files_checked, commands_run, key_outputs."
        )
        return ValidationResult(
            is_valid=False,
            missing=all_missing,
            error_message=error_message,
        )

    return ValidationResult(is_valid=True, missing=[], error_message="")


# ============================================================================
# Functions absorbed from evidence_parser.py (backward compatible)
# ============================================================================

def extract_commands_from_evidence(agent_output: str) -> List[str]:
    """Extract command strings from the EVIDENCE_REPORT COMMANDS_RUN field.

    Supports both JSON contract format and legacy HTML-comment blocks.

    For JSON contract format, extracts from evidence_report.commands_run list.
    For legacy format, parses lines between ``COMMANDS_RUN:`` and the next field
    header (a line ending with ``:`` that matches a known evidence field name).
    Each bullet line (``- `cmd` -> result`` or ``- cmd``) is extracted as a
    command string.

    Commands whose result indicates they were NOT actually run (e.g. "not run",
    "skipped", "n/a", "not executed") are excluded from the returned list.

    Returns a list of command strings (without surrounding backticks).
    """
    # Try JSON contract format first
    contract = parse_contract(agent_output)
    if contract is not None:
        evidence = contract.get("evidence_report", {}) or {}
        commands_run = evidence.get("commands_run", [])
        if isinstance(commands_run, list):
            commands = []
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

    # Legacy HTML-comment block parsing
    commands: List[str] = []
    in_commands_section = False
    # Known field headers that end the COMMANDS_RUN section
    _FIELD_HEADERS = {
        "PATTERNS_CHECKED:", "FILES_CHECKED:", "COMMANDS_RUN:",
        "KEY_OUTPUTS:", "VERBATIM_OUTPUTS:", "CROSS_LAYER_IMPACTS:", "OPEN_GAPS:",
    }

    for raw_line in agent_output.splitlines():
        line = raw_line.strip()
        if line == "COMMANDS_RUN:":
            in_commands_section = True
            continue
        if in_commands_section:
            # Stop at the next field header or block boundary
            if line in _FIELD_HEADERS or line.startswith("<!-- "):
                break
            if line.startswith("- "):
                entry = line[2:].strip()
                # Extract command from backtick-quoted format: `cmd` -> result
                cmd = ""
                if entry.startswith("`"):
                    end_tick = entry.find("`", 1)
                    if end_tick > 1:
                        cmd = entry[1:end_tick]
                    else:
                        # Single backtick without closing -- take the whole entry
                        cmd = entry.lstrip("`").strip()
                elif entry and entry.lower() not in _LITERAL_NONE_COMMANDS:
                    cmd = entry

                if not cmd or cmd.lower() in _LITERAL_NONE_COMMANDS:
                    continue

                # Check if the rest of the line (after the command) indicates
                # the command was not actually run
                remainder = entry[entry.find(cmd) + len(cmd):]
                if _NOT_RUN_INDICATORS.search(remainder):
                    continue

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

    Tries JSON contract format first, falls back to regex on the last
    AGENT_STATUS block.

    Returns the raw status string (e.g. "COMPLETE", "BLOCKED", "NEEDS_INPUT")
    or empty string if not found.
    """
    # Try JSON contract format first
    contract = parse_contract(agent_output)
    if contract is not None:
        agent_status = contract.get("agent_status", {}) or {}
        plan_status = agent_status.get("plan_status", "")
        if plan_status:
            return str(plan_status).upper().rstrip(".,;")

    # Legacy regex parsing
    status_match = None
    for m in re.finditer(r"PLAN_STATUS:\s*(\S+)", agent_output):
        status_match = m
    if status_match:
        return status_match.group(1).upper().rstrip(".,;")
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


# Aliases for shorter import names
extract_plan_status = extract_plan_status_from_output
extract_exit_code = extract_exit_code_from_output
