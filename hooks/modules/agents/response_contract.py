"""
Runtime validation for agent response contracts.

Validates the structured JSON contract block returned by agents
(``json:contract`` fenced blocks parsed by ``contract_validator.parse_contract``).

Validated sections:
- agent_status  (plan_status, agent_id, pending_steps, next_action)
- evidence_report  (patterns_checked, files_checked, commands_run, key_outputs, ...)
- consolidation_report  (ownership_assessment, confirmed_findings, ...)

"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from ..core.paths import get_session_dir
from ..core.state import get_session_id
from .contract_validator import parse_contract


VALID_PLAN_STATUSES = {
    "IN_PROGRESS",
    "APPROVAL_REQUEST",
    "COMPLETE",
    "BLOCKED",
    "NEEDS_INPUT",
}

# Evidence is required for ALL valid states -- no exclusions.
EVIDENCE_REQUIRED_PLAN_STATUSES = VALID_PLAN_STATUSES

EVIDENCE_FIELDS = [
    "PATTERNS_CHECKED",
    "FILES_CHECKED",
    "COMMANDS_RUN",
    "KEY_OUTPUTS",
    "VERBATIM_OUTPUTS",
    "CROSS_LAYER_IMPACTS",
    "OPEN_GAPS",
]
VALID_OWNERSHIP_ASSESSMENTS = {
    "owned_here",
    "cross_surface_dependency",
    "not_my_surface",
}
# Bullet-list fields only; OWNERSHIP_ASSESSMENT is validated separately as a key-value enum.
CONSOLIDATION_FIELDS = [
    "CONFIRMED_FINDINGS",
    "SUSPECTED_FINDINGS",
    "CONFLICTS",
    "OPEN_GAPS",
    "NEXT_BEST_AGENT",
]

RECOMMENDED_ACTION_NONE = "none"

# Statuses that should carry an approval_request block
APPROVAL_REQUEST_STATUSES = {"APPROVAL_REQUEST"}

APPROVAL_REQUEST_REQUIRED_FIELDS = [
    "operation",
    "exact_content",
    "scope",
    "risk_level",
    "rollback",
    "verification",
]

VALID_RISK_LEVELS = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}

_NONCE_HEX_PATTERN = re.compile(r"^[a-f0-9]{32}$")

_AGENT_ID_PATTERN = re.compile(r"^a[0-9a-f]{5,}$")


@dataclass(frozen=True)
class AgentStatusBlock:
    marker_present: bool
    plan_status: str
    pending_steps: str
    next_action: str
    agent_id: str


@dataclass(frozen=True)
class EvidenceReportBlock:
    marker_present: bool
    fields: Dict[str, List[str]]


@dataclass(frozen=True)
class ConsolidationReportBlock:
    marker_present: bool
    ownership_assessment: str
    fields: Dict[str, List[str]]


@dataclass(frozen=True)
class ResponseContractValidation:
    valid: bool
    severity: str
    missing: List[str]
    invalid: List[str]
    warnings: List[str]
    evidence_required: bool
    consolidation_required: bool
    recommended_action: str
    agent_status: AgentStatusBlock
    evidence_report: EvidenceReportBlock
    consolidation_report: ConsolidationReportBlock

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


# ============================================================================
# JSON contract -> dataclass extraction helpers
# ============================================================================

def _get_str(d: dict, key: str) -> str:
    """Get a string value from a dict, trying both lower-case and UPPER-CASE keys."""
    return str(d.get(key, "") or d.get(key.upper(), "") or "")


def _get_list(d: dict, key: str) -> List[str]:
    """Get a list-of-strings value, trying both lower-case and UPPER-CASE keys.

    If the value is a list of dicts (e.g. commands_run entries with {command, result}),
    each dict is serialised to a readable string.
    """
    val = d.get(key) or d.get(key.upper()) or []
    if not isinstance(val, list):
        return [str(val)] if val else []
    result: List[str] = []
    for item in val:
        if isinstance(item, dict):
            # e.g. {"command": "ls", "result": "ok"} -> "`ls` -> ok"
            cmd = item.get("command", item.get("cmd", ""))
            res = item.get("result", item.get("output", ""))
            result.append(f"`{cmd}` -> {res}" if cmd else str(item))
        else:
            result.append(str(item))
    return result


def _extract_agent_status(contract: dict) -> AgentStatusBlock:
    """Build an AgentStatusBlock from the parsed JSON contract dict."""
    agent_status = contract.get("agent_status")
    if not agent_status or not isinstance(agent_status, dict):
        return AgentStatusBlock(
            marker_present=False,
            plan_status="",
            pending_steps="",
            next_action="",
            agent_id="",
        )

    plan_status = _get_str(agent_status, "plan_status").upper().rstrip(".,;")
    pending_steps = _get_str(agent_status, "pending_steps")
    next_action = _get_str(agent_status, "next_action")
    agent_id = _get_str(agent_status, "agent_id")

    return AgentStatusBlock(
        marker_present=True,
        plan_status=plan_status,
        pending_steps=pending_steps,
        next_action=next_action,
        agent_id=agent_id,
    )


def _extract_evidence_report(contract: dict) -> EvidenceReportBlock:
    """Build an EvidenceReportBlock from the parsed JSON contract dict."""
    evidence = contract.get("evidence_report")
    if not evidence or not isinstance(evidence, dict):
        return EvidenceReportBlock(
            marker_present=False,
            fields={field: [] for field in EVIDENCE_FIELDS},
        )

    fields: Dict[str, List[str]] = {}
    for field_name in EVIDENCE_FIELDS:
        key_lower = field_name.lower()
        values = _get_list(evidence, key_lower)
        fields[field_name] = values

    return EvidenceReportBlock(marker_present=True, fields=fields)


def _extract_consolidation_report(contract: dict) -> ConsolidationReportBlock:
    """Build a ConsolidationReportBlock from the parsed JSON contract dict."""
    consolidation = contract.get("consolidation_report")
    if not consolidation or not isinstance(consolidation, dict):
        return ConsolidationReportBlock(
            marker_present=False,
            ownership_assessment="",
            fields={field: [] for field in CONSOLIDATION_FIELDS},
        )

    ownership = _get_str(consolidation, "ownership_assessment")

    fields: Dict[str, List[str]] = {}
    for field_name in CONSOLIDATION_FIELDS:
        key_lower = field_name.lower()
        values = _get_list(consolidation, key_lower)
        fields[field_name] = values

    return ConsolidationReportBlock(
        marker_present=True,
        ownership_assessment=ownership,
        fields=fields,
    )


# ============================================================================
# Public parse helpers (operate on agent_output string via parse_contract)
# ============================================================================

def parse_agent_status(agent_output: str, parsed_contract: Optional[dict] = None) -> AgentStatusBlock:
    """Parse agent_status from agent output using the json:contract block."""
    contract = parsed_contract if parsed_contract is not None else parse_contract(agent_output)
    if contract is None:
        return AgentStatusBlock(
            marker_present=False, plan_status="", pending_steps="",
            next_action="", agent_id="",
        )
    return _extract_agent_status(contract)


def parse_evidence_report(agent_output: str, parsed_contract: Optional[dict] = None) -> EvidenceReportBlock:
    """Parse evidence_report from agent output using the json:contract block."""
    contract = parsed_contract if parsed_contract is not None else parse_contract(agent_output)
    if contract is None:
        return EvidenceReportBlock(
            marker_present=False,
            fields={field: [] for field in EVIDENCE_FIELDS},
        )
    return _extract_evidence_report(contract)


def parse_consolidation_report(agent_output: str, parsed_contract: Optional[dict] = None) -> ConsolidationReportBlock:
    """Parse consolidation_report from agent output using the json:contract block."""
    contract = parsed_contract if parsed_contract is not None else parse_contract(agent_output)
    if contract is None:
        return ConsolidationReportBlock(
            marker_present=False, ownership_assessment="",
            fields={field: [] for field in CONSOLIDATION_FIELDS},
        )
    return _extract_consolidation_report(contract)


def _is_resume_agent_id(value: str) -> bool:
    return bool(_AGENT_ID_PATTERN.match(value or ""))


def resolve_agent_id(task_info: dict) -> str:
    """Extract the agent ID from a task_info dict.

    Falls back to ``task_id`` when ``agent_id`` is absent.
    """
    return str(task_info.get("agent_id", "") or task_info.get("task_id", ""))


def validate_response_contract(
    agent_output: str,
    *,
    task_agent_id: str = "",
    consolidation_required: bool = False,
    parsed_contract: Optional[dict] = None,
) -> ResponseContractValidation:
    """Validate deterministic response blocks emitted by an agent.

    Args:
        agent_output: Raw agent output text.
        task_agent_id: Agent ID from task_info, used as fallback.
        consolidation_required: Whether a CONSOLIDATION_REPORT is required.
        parsed_contract: Pre-parsed dict from parse_contract(). If provided,
            avoids re-parsing agent_output. If None, parse_contract() is
            called internally.
    """
    contract = parsed_contract if parsed_contract is not None else parse_contract(agent_output)

    if contract is None:
        # No json:contract block found -- everything is missing.
        empty_evidence = EvidenceReportBlock(
            marker_present=False,
            fields={field: [] for field in EVIDENCE_FIELDS},
        )
        empty_consolidation = ConsolidationReportBlock(
            marker_present=False, ownership_assessment="",
            fields={field: [] for field in CONSOLIDATION_FIELDS},
        )
        empty_status = AgentStatusBlock(
            marker_present=False, plan_status="", pending_steps="",
            next_action="", agent_id="",
        )
        missing = ["AGENT_STATUS", "PLAN_STATUS", "PENDING_STEPS", "NEXT_ACTION", "AGENT_ID"]
        recommended_action = "escalate_contract_repair" if not task_agent_id else "resume_same_agent_contract_repair"
        if not _is_resume_agent_id(task_agent_id):
            recommended_action = "escalate_contract_repair"
        return ResponseContractValidation(
            valid=False,
            severity="hard",
            missing=missing,
            invalid=[],
            warnings=[],
            evidence_required=False,
            consolidation_required=consolidation_required,
            recommended_action=recommended_action,
            agent_status=empty_status,
            evidence_report=empty_evidence,
            consolidation_report=empty_consolidation,
        )

    status = _extract_agent_status(contract)
    evidence = _extract_evidence_report(contract)
    if consolidation_required:
        consolidation = _extract_consolidation_report(contract)
    else:
        consolidation = ConsolidationReportBlock(
            marker_present=False, ownership_assessment="",
            fields={field: [] for field in CONSOLIDATION_FIELDS}
        )

    missing: List[str] = []
    invalid: List[str] = []

    if not status.marker_present:
        missing.append("AGENT_STATUS")

    if not status.plan_status:
        missing.append("PLAN_STATUS")
    elif status.plan_status not in VALID_PLAN_STATUSES:
        invalid.append(f"PLAN_STATUS:{status.plan_status}")

    if not status.pending_steps:
        missing.append("PENDING_STEPS")
    if not status.next_action:
        missing.append("NEXT_ACTION")
    if not status.agent_id:
        missing.append("AGENT_ID")

    effective_agent_id = status.agent_id if _is_resume_agent_id(status.agent_id) else task_agent_id
    if not _is_resume_agent_id(effective_agent_id):
        effective_agent_id = ""
    evidence_required = status.plan_status in EVIDENCE_REQUIRED_PLAN_STATUSES
    if evidence_required:
        if not evidence.marker_present:
            missing.append("EVIDENCE_REPORT")
        for field in EVIDENCE_FIELDS:
            if not evidence.fields.get(field, []):
                missing.append(field)

    if consolidation_required:
        if not consolidation.marker_present:
            missing.append("CONSOLIDATION_REPORT")
        if not consolidation.ownership_assessment:
            missing.append("OWNERSHIP_ASSESSMENT")
        elif consolidation.ownership_assessment not in VALID_OWNERSHIP_ASSESSMENTS:
            invalid.append(f"OWNERSHIP_ASSESSMENT:{consolidation.ownership_assessment}")
        for field in CONSOLIDATION_FIELDS:
            if not consolidation.fields.get(field, []):
                missing.append(field)

    # ------------------------------------------------------------------
    # Approval request validation (advisory -- warnings only, not blocking)
    # ------------------------------------------------------------------
    warnings: List[str] = []
    if status.plan_status in APPROVAL_REQUEST_STATUSES:
        approval_req = contract.get("approval_request")
        if not approval_req or not isinstance(approval_req, dict):
            warnings.append("APPROVAL_REQUEST_MISSING")
        else:
            for field in APPROVAL_REQUEST_REQUIRED_FIELDS:
                if not approval_req.get(field):
                    warnings.append(f"APPROVAL_REQUEST_FIELD_MISSING:{field}")
            risk = str(approval_req.get("risk_level", "")).upper()
            if risk and risk not in VALID_RISK_LEVELS:
                warnings.append(f"APPROVAL_REQUEST_INVALID_RISK_LEVEL:{risk}")
            # Check for approval_id when status is APPROVAL_REQUEST
            if status.plan_status == "APPROVAL_REQUEST":
                pass  # approval_id presence is advisory, not enforced

    valid = not missing and not invalid
    recommended_action = RECOMMENDED_ACTION_NONE if valid else "resume_same_agent_contract_repair"
    severity = "none" if valid else "hard"

    # If there is no actionable agent id, repair cannot be routed deterministically.
    if not valid and not effective_agent_id:
        recommended_action = "escalate_contract_repair"

    return ResponseContractValidation(
        valid=valid,
        severity=severity,
        missing=missing,
        invalid=invalid,
        warnings=warnings,
        evidence_required=evidence_required,
        consolidation_required=consolidation_required,
        recommended_action=recommended_action,
        agent_status=status,
        evidence_report=evidence,
        consolidation_report=consolidation,
    )


def _get_session_id() -> str:
    return get_session_id()


_contract_dir_cache: Dict[str, Path] = {}


def clear_contract_dir_cache() -> None:
    """Clear the cached contract directory path (useful for testing)."""
    _contract_dir_cache.clear()


def _get_contract_dir(session_id: Optional[str] = None) -> Path:
    session_id = session_id or _get_session_id()
    cached = _contract_dir_cache.get(session_id)
    if cached is not None and cached.is_dir():
        return cached
    path = get_session_dir() / "response-contract" / session_id
    path.mkdir(parents=True, exist_ok=True)
    _contract_dir_cache[session_id] = path
    return path


def _read_json(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def load_last_validation(session_id: Optional[str] = None) -> Optional[dict]:
    """Load the last response-contract validation result, if any."""
    session_id = session_id or _get_session_id()
    path = _get_contract_dir(session_id) / "last-result.json"
    payload = _read_json(path)
    if not payload:
        return None
    if payload.get("session_id") != session_id:
        return None
    return payload


def save_validation_result(task_info: Dict[str, object], validation: ResponseContractValidation) -> Path:
    """Persist the last validation result for observability and orchestration."""
    session_id = _get_session_id()
    target = _get_contract_dir(session_id) / "last-result.json"
    payload = {
        "timestamp": datetime.now().isoformat(),
        "created_at_epoch": time.time(),
        "session_id": session_id,
        "agent": task_info.get("agent", ""),
        "agent_id": resolve_agent_id(task_info),
        "task_id": task_info.get("task_id", ""),
        "validation": validation.to_dict(),
    }
    target.write_text(json.dumps(payload, indent=2))
    return target


__all__ = [
    "AgentStatusBlock",
    "EvidenceReportBlock",
    "ConsolidationReportBlock",
    "ResponseContractValidation",
    "VALID_PLAN_STATUSES",
    "EVIDENCE_REQUIRED_PLAN_STATUSES",
    "EVIDENCE_FIELDS",
    "VALID_OWNERSHIP_ASSESSMENTS",
    "CONSOLIDATION_FIELDS",
    "RECOMMENDED_ACTION_NONE",
    "APPROVAL_REQUEST_STATUSES",
    "APPROVAL_REQUEST_REQUIRED_FIELDS",
    "VALID_RISK_LEVELS",
    "parse_agent_status",
    "parse_evidence_report",
    "parse_consolidation_report",
    "validate_response_contract",
    "save_validation_result",
    "load_last_validation",
    "resolve_agent_id",
    "clear_contract_dir_cache",
]
