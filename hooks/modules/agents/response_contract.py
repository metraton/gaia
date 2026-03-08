"""
Runtime validation for agent response contracts.

Validates the deterministic blocks returned by agents:
- EVIDENCE_REPORT
- CONSOLIDATION_REPORT (for multi-surface / cross-check tasks)
- AGENT_STATUS

Also persists repair state so the orchestrator can deterministically resume the
same agent when a response is structurally incomplete.
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


VALID_PLAN_STATUSES = {
    "INVESTIGATING",
    "PLANNING",
    "PENDING_APPROVAL",
    "APPROVED_EXECUTING",
    "FIXING",
    "COMPLETE",
    "BLOCKED",
    "NEEDS_INPUT",
}

EVIDENCE_REQUIRED_PLAN_STATUSES = VALID_PLAN_STATUSES - {"APPROVED_EXECUTING"}

EVIDENCE_FIELDS = [
    "PATTERNS_CHECKED",
    "FILES_CHECKED",
    "COMMANDS_RUN",
    "KEY_OUTPUTS",
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
RECOMMENDED_ACTION_RESUME_REPAIR = "resume_same_agent_contract_repair"
RECOMMENDED_ACTION_ESCALATE = "escalate_contract_repair"
DEFAULT_REPAIR_TTL_MINUTES = 30
MAX_REPAIR_ATTEMPTS = 2

_AGENT_ID_PATTERN = re.compile(r"^a[0-9a-f]{5,}$")

_AGENT_STATUS_BLOCK_RE = re.compile(
    r"<!-- AGENT_STATUS -->\s*(.*?)\s*<!-- /AGENT_STATUS -->", re.DOTALL
)
_EVIDENCE_REPORT_BLOCK_RE = re.compile(
    r"<!-- EVIDENCE_REPORT -->\s*(.*?)\s*<!-- /EVIDENCE_REPORT -->", re.DOTALL
)
_CONSOLIDATION_REPORT_BLOCK_RE = re.compile(
    r"<!-- CONSOLIDATION_REPORT -->\s*(.*?)\s*<!-- /CONSOLIDATION_REPORT -->",
    re.DOTALL,
)


def _format_field_list(items: list, indent: str = "") -> str:
    if not items:
        return f"{indent}- none"
    return "\n".join(f"{indent}- {item}" for item in items)


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
    evidence_required: bool
    consolidation_required: bool
    recommended_action: str
    agent_status: AgentStatusBlock
    evidence_report: EvidenceReportBlock
    consolidation_report: ConsolidationReportBlock

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


def _extract_last_marked_block(text: str, pattern: re.Pattern[str]) -> tuple[str, bool]:
    last = None
    for m in pattern.finditer(text):
        last = m
    if last is not None:
        return last.group(1).strip(), True
    return "", False


def _parse_key_value_lines(block_text: str) -> Dict[str, str]:
    parsed: Dict[str, str] = {}
    for line in block_text.splitlines():
        stripped = line.strip()
        if not stripped or ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        parsed[key.strip().upper()] = value.strip()
    return parsed


def _parse_bullet_field_block(
    block_text: str,
    fields: List[str],
) -> Dict[str, List[str]]:
    parsed: Dict[str, List[str]] = {field: [] for field in fields}
    current_field: Optional[str] = None
    for raw_line in block_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.endswith(":") and line[:-1] in fields:
            current_field = line[:-1]
            continue
        if line.startswith("- ") and current_field:
            parsed[current_field].append(line[2:].strip())
    return parsed


def parse_agent_status(agent_output: str) -> AgentStatusBlock:
    """Parse the last AGENT_STATUS block from agent output."""
    body, marker_present = _extract_last_marked_block(
        agent_output,
        _AGENT_STATUS_BLOCK_RE,
    )

    source = body if body else agent_output
    parsed = _parse_key_value_lines(source)

    return AgentStatusBlock(
        marker_present=marker_present,
        plan_status=parsed.get("PLAN_STATUS", "").upper().rstrip(".,;"),
        pending_steps=parsed.get("PENDING_STEPS", ""),
        next_action=parsed.get("NEXT_ACTION", ""),
        agent_id=parsed.get("AGENT_ID", ""),
    )


def parse_evidence_report(agent_output: str) -> EvidenceReportBlock:
    """Parse the last EVIDENCE_REPORT block from agent output."""
    body, marker_present = _extract_last_marked_block(
        agent_output,
        _EVIDENCE_REPORT_BLOCK_RE,
    )
    fields = _parse_bullet_field_block(body, EVIDENCE_FIELDS) if body else {field: [] for field in EVIDENCE_FIELDS}

    return EvidenceReportBlock(marker_present=marker_present, fields=fields)


def parse_consolidation_report(agent_output: str) -> ConsolidationReportBlock:
    """Parse the last CONSOLIDATION_REPORT block from agent output."""
    body, marker_present = _extract_last_marked_block(
        agent_output,
        _CONSOLIDATION_REPORT_BLOCK_RE,
    )
    parsed = _parse_key_value_lines(body) if body else {}
    fields = _parse_bullet_field_block(body, CONSOLIDATION_FIELDS) if body else {
        field: [] for field in CONSOLIDATION_FIELDS
    }

    return ConsolidationReportBlock(
        marker_present=marker_present,
        ownership_assessment=parsed.get("OWNERSHIP_ASSESSMENT", "").strip(),
        fields=fields,
    )


def _is_resume_agent_id(value: str) -> bool:
    return bool(_AGENT_ID_PATTERN.match(value or ""))


def _resolve_agent_id(task_info: dict) -> str:
    return str(task_info.get("agent_id", "") or task_info.get("task_id", ""))


def validate_response_contract(
    agent_output: str,
    *,
    task_agent_id: str = "",
    consolidation_required: bool = False,
) -> ResponseContractValidation:
    """Validate deterministic response blocks emitted by an agent."""
    status = parse_agent_status(agent_output)
    evidence = parse_evidence_report(agent_output)
    if consolidation_required:
        consolidation = parse_consolidation_report(agent_output)
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

    valid = not missing and not invalid
    recommended_action = RECOMMENDED_ACTION_NONE if valid else RECOMMENDED_ACTION_RESUME_REPAIR
    severity = "none" if valid else "hard"

    # If there is no actionable agent id, repair cannot be routed deterministically.
    if not valid and not effective_agent_id:
        recommended_action = RECOMMENDED_ACTION_ESCALATE

    return ResponseContractValidation(
        valid=valid,
        severity=severity,
        missing=missing,
        invalid=invalid,
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


def _is_expired(payload: dict) -> bool:
    created_at = payload.get("created_at_epoch", 0)
    ttl_minutes = int(payload.get("ttl_minutes", DEFAULT_REPAIR_TTL_MINUTES))
    if not created_at:
        return True
    return ((time.time() - created_at) / 60) > ttl_minutes


def load_pending_repair(session_id: Optional[str] = None) -> Optional[dict]:
    """Load the current pending response-contract repair request, if any."""
    session_id = session_id or _get_session_id()
    path = _get_contract_dir(session_id) / "pending-repair.json"
    payload = _read_json(path)
    if not payload:
        return None
    if payload.get("session_id") != session_id:
        return None
    if _is_expired(payload):
        path.unlink(missing_ok=True)
        return None
    return payload


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
        "agent_id": _resolve_agent_id(task_info),
        "task_id": task_info.get("task_id", ""),
        "validation": validation.to_dict(),
    }
    target.write_text(json.dumps(payload, indent=2))
    return target


def clear_pending_repair(agent_id: str = "") -> None:
    """Clear the pending repair file when a repaired response is accepted."""
    path = _get_contract_dir() / "pending-repair.json"
    existing = _read_json(path)
    if not existing:
        return
    if agent_id and existing.get("agent_id") and existing.get("agent_id") != agent_id:
        return
    path.unlink(missing_ok=True)


def save_pending_repair(task_info: Dict[str, object], validation: ResponseContractValidation) -> Optional[Path]:
    """Persist a pending repair request for an invalid response contract."""
    if validation.valid or validation.recommended_action != RECOMMENDED_ACTION_RESUME_REPAIR:
        return None

    agent_id = validation.agent_status.agent_id or _resolve_agent_id(task_info)
    if not _is_resume_agent_id(agent_id):
        return None

    session_id = _get_session_id()
    path = _get_contract_dir(session_id) / "pending-repair.json"
    existing = _read_json(path) or {}
    repair_attempts = int(existing.get("repair_attempts", 0)) if existing.get("agent_id") == agent_id else 0

    payload = {
        "timestamp": datetime.now().isoformat(),
        "created_at_epoch": time.time(),
        "ttl_minutes": DEFAULT_REPAIR_TTL_MINUTES,
        "session_id": session_id,
        "agent": task_info.get("agent", ""),
        "agent_id": agent_id,
        "task_id": task_info.get("task_id", ""),
        "missing": validation.missing,
        "invalid": validation.invalid,
        "repair_attempts": repair_attempts,
        "recommended_action": validation.recommended_action,
    }
    path.write_text(json.dumps(payload, indent=2))
    return path


def mark_repair_attempt(agent_id: str) -> Optional[dict]:
    """Increment repair-attempt count when the same agent is resumed for repair."""
    path = _get_contract_dir() / "pending-repair.json"
    payload = _read_json(path)
    if not payload or payload.get("agent_id") != agent_id:
        return None
    if _is_expired(payload):
        path.unlink(missing_ok=True)
        return None

    payload["repair_attempts"] = int(payload.get("repair_attempts", 0)) + 1
    payload["last_repair_attempt_at"] = datetime.now().isoformat()
    path.write_text(json.dumps(payload, indent=2))
    return payload


def build_repair_prompt(repair_payload: dict) -> str:
    """Build a deterministic repair instruction for the same agent."""
    missing = repair_payload.get("missing", [])
    invalid = repair_payload.get("invalid", [])

    consolidation_clause = ""
    if any("CONSOLIDATION" in m or "OWNERSHIP" in m for m in missing):
        consolidation_clause = ", CONSOLIDATION_REPORT (with OWNERSHIP_ASSESSMENT)"

    return (
        "Repair your previous response contract only.\n"
        "Do not restart the investigation unless strictly required to fill missing evidence.\n"
        f"Reissue a corrected response with complete EVIDENCE_REPORT{consolidation_clause}, optional CONTEXT_UPDATE if applicable, "
        "and a valid AGENT_STATUS block.\n\n"
        "Missing fields:\n"
        f"{_format_field_list(missing)}\n\n"
        "Invalid fields:\n"
        f"{_format_field_list(invalid)}\n"
    )


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
    "RECOMMENDED_ACTION_RESUME_REPAIR",
    "RECOMMENDED_ACTION_ESCALATE",
    "MAX_REPAIR_ATTEMPTS",
    "parse_agent_status",
    "parse_evidence_report",
    "parse_consolidation_report",
    "validate_response_contract",
    "save_validation_result",
    "save_pending_repair",
    "load_pending_repair",
    "load_last_validation",
    "clear_pending_repair",
    "mark_repair_attempt",
    "build_repair_prompt",
    "_format_field_list",
    "_resolve_agent_id",
    "clear_contract_dir_cache",
]
