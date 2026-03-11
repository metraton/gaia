"""
Workflow auditing: anomaly detection and Gaia analysis signaling.

Renamed from anomaly_detector.py and expanded with additional anomaly types.

Provides:
    - audit(): Full anomaly detection suite -> list of anomaly dicts
    - detect_anomalies(): Backward-compatible alias for audit()
    - signal_gaia_analysis(): Create flag file for Gaia analysis
"""

import json
import logging
import re
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .workflow_recorder import get_workflow_memory_dir

logger = logging.getLogger(__name__)


def audit(
    metrics: Dict[str, Any],
    agent_output: str = "",
    task_info: Optional[Dict[str, Any]] = None,
    rejected_sections: Optional[List[str]] = None,
) -> List[Dict[str, str]]:
    """
    Detect anomalies in workflow execution.

    Checks:
    - execution_failure: exit_code != 0
    - consecutive_failures: 3+ failures in a row for same agent
    - missing_evidence: COMPLETE but no EVIDENCE_REPORT
    - empty_evidence: EVIDENCE_REPORT exists but COMMANDS_RUN empty or all "not run"
    - skipped_verification: task has verify command in injected_context but not in commands_run
    - scope_escalation: rejected_sections exist (agent tried to write outside its scope)

    Args:
        metrics: Workflow metrics dict (from workflow_recorder.record()).
        agent_output: Complete agent output string (for evidence checks).
        task_info: Task metadata including injected_context (for verification checks).
        rejected_sections: List of context sections rejected by permission validation.

    Returns:
        List of anomaly descriptions
    """
    anomalies: List[Dict[str, str]] = []
    task_info = task_info or {}

    # --- existing: execution_failure ---
    if metrics.get("exit_code", 0) != 0:
        anomalies.append({
            "type": "execution_failure",
            "severity": "error",
            "message": f"Agent {metrics['agent']} failed with exit code {metrics['exit_code']}"
        })

    # --- existing: consecutive_failures ---
    try:
        workflow_memory_dir = get_workflow_memory_dir()
        metrics_file = workflow_memory_dir / "metrics.jsonl"

        if metrics_file.exists():
            with open(metrics_file) as f:
                recent = list(deque(f, maxlen=7))
            # Get last 5 metrics (excluding current which is the last line)
            last_5 = (
                [json.loads(line) for line in recent[:-1]][-5:]
                if len(recent) > 1
                else []
            )

            # Count recent failures for same agent
            agent = metrics["agent"]
            recent_failures = [
                m for m in last_5
                if m.get("agent") == agent and m.get("exit_code", 0) != 0
            ]

            # If current also failed and we have 2+ previous failures
            if metrics.get("exit_code", 0) != 0 and len(recent_failures) >= 2:
                anomalies.append({
                    "type": "consecutive_failures",
                    "severity": "critical",
                    "message": (
                        f"Agent {agent} has failed "
                        f"{len(recent_failures) + 1} times consecutively"
                    ),
                })
    except Exception as e:
        logger.debug(f"Could not check consecutive failures: {e}")

    # --- NEW: missing_evidence ---
    if agent_output:
        plan_status = metrics.get("plan_status", "")
        if "COMPLETE" in plan_status:
            has_evidence = bool(re.search(
                r"<!-- EVIDENCE_REPORT -->", agent_output
            ))
            if not has_evidence:
                anomalies.append({
                    "type": "missing_evidence",
                    "severity": "warning",
                    "message": (
                        f"Agent {metrics['agent']} completed but "
                        f"did not include EVIDENCE_REPORT block"
                    ),
                })

    # --- NEW: empty_evidence ---
    if agent_output:
        evidence_match = re.search(
            r"<!-- EVIDENCE_REPORT -->\s*(.*?)\s*<!-- /EVIDENCE_REPORT -->",
            agent_output,
            re.DOTALL,
        )
        if evidence_match:
            evidence_body = evidence_match.group(1)
            # Extract COMMANDS_RUN section
            commands_section = ""
            in_commands = False
            _FIELD_HEADERS = {
                "PATTERNS_CHECKED:", "FILES_CHECKED:", "COMMANDS_RUN:",
                "KEY_OUTPUTS:", "VERBATIM_OUTPUTS:", "CROSS_LAYER_IMPACTS:",
                "OPEN_GAPS:",
            }
            for line in evidence_body.splitlines():
                stripped = line.strip()
                if stripped == "COMMANDS_RUN:":
                    in_commands = True
                    continue
                if in_commands:
                    if stripped in _FIELD_HEADERS or stripped.startswith("<!-- "):
                        break
                    commands_section += stripped + "\n"

            # Check if commands section is empty or all "not run"
            if commands_section.strip():
                not_run_pattern = re.compile(
                    r"\b(not\s+run|not\s+executed|skipped|n/a|none)\b",
                    re.IGNORECASE,
                )
                lines = [
                    l.strip() for l in commands_section.splitlines()
                    if l.strip().startswith("- ")
                ]
                if lines and all(not_run_pattern.search(l) for l in lines):
                    anomalies.append({
                        "type": "empty_evidence",
                        "severity": "warning",
                        "message": (
                            f"Agent {metrics['agent']} has EVIDENCE_REPORT but "
                            f"all COMMANDS_RUN entries indicate 'not run'"
                        ),
                    })
            elif not commands_section.strip():
                # COMMANDS_RUN header exists but no content
                if "COMMANDS_RUN:" in evidence_body:
                    anomalies.append({
                        "type": "empty_evidence",
                        "severity": "warning",
                        "message": (
                            f"Agent {metrics['agent']} has EVIDENCE_REPORT but "
                            f"COMMANDS_RUN section is empty"
                        ),
                    })

    # --- NEW: skipped_verification ---
    injected = task_info.get("injected_context") or {}
    investigation_brief = injected.get("investigation_brief", {}) or {}
    required_checks = investigation_brief.get("required_checks", [])
    if required_checks and agent_output:
        # Extract commands that were actually run from evidence
        from ..agents.contract_validator import extract_commands_from_evidence
        commands_run = extract_commands_from_evidence(agent_output)

        # Check if any required check mentions a verify command
        for check in required_checks:
            if isinstance(check, str) and "verify" in check.lower():
                # If there are required verification checks but no commands were run at all
                if not commands_run:
                    anomalies.append({
                        "type": "skipped_verification",
                        "severity": "warning",
                        "message": (
                            f"Agent {metrics['agent']} has verification requirements "
                            f"in injected_context but ran no commands"
                        ),
                    })
                    break

    # --- NEW: scope_escalation ---
    if rejected_sections:
        anomalies.append({
            "type": "scope_escalation",
            "severity": "warning",
            "message": (
                f"Agent {metrics['agent']} attempted to write to "
                f"unauthorized sections: {', '.join(rejected_sections)}"
            ),
        })

    return anomalies


# Backward-compatible alias
detect_anomalies = audit


def signal_gaia_analysis(
    anomalies: List[Dict],
    metrics: Dict[str, Any],
) -> None:
    """
    Signal that Gaia analysis is needed.

    Creates a flag file that orchestrator can detect.
    """
    try:
        signals_dir = get_workflow_memory_dir() / "signals"
        signals_dir.mkdir(parents=True, exist_ok=True)

        signal_file = signals_dir / "needs_analysis.flag"

        signal_data = {
            "timestamp": datetime.now().isoformat(),
            "created_at": datetime.now().isoformat(),
            "ttl_hours": 1,
            "anomalies": anomalies,
            "metrics_summary": {
                "agent": metrics["agent"],
                "task_id": metrics["task_id"],
                "duration_ms": metrics.get("duration_ms"),
                "exit_code": metrics.get("exit_code"),
            },
            "suggested_action": "Invoke /gaia for system analysis",
        }

        with open(signal_file, "w") as f:
            json.dump(signal_data, f, indent=2)

        logger.info(f"Gaia analysis signal created: {signal_file}")

        # Also log to a permanent anomaly log
        anomaly_log = signals_dir.parent / "anomalies.jsonl"
        with open(anomaly_log, "a") as f:
            f.write(json.dumps({
                "timestamp": datetime.now().isoformat(),
                "anomalies": anomalies,
                "metrics": metrics,
            }) + "\n")

    except Exception as e:
        logger.warning(f"Could not create analysis signal: {e}")
