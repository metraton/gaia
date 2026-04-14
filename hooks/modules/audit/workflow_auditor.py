"""
Workflow auditing: anomaly detection and Gaia analysis signaling.

Renamed from anomaly_detector.py and expanded with additional anomaly types.

Provides:
    - audit(): Full anomaly detection suite -> list of anomaly dicts
    - signal_gaia_analysis(): Create flag file for Gaia analysis
"""

import json
import logging
import re
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..agents.transcript_analyzer import TranscriptAnalysis
from .workflow_recorder import get_workflow_memory_dir

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Transcript-analysis check helpers (T009)
# ---------------------------------------------------------------------------


def _check_investigation_skip(
    analysis: TranscriptAnalysis,
) -> Optional[Dict[str, str]]:
    """Warning if the agent's first tool call was Bash (skipped investigation)."""
    if analysis.first_tool_name == "Bash":
        return {
            "type": "investigation_skip",
            "severity": "warning",
            "message": (
                "Agent's first tool call was Bash instead of a "
                "read-only investigation tool (Read/Glob/Grep)"
            ),
        }
    return None


def _check_context_ignored(
    analysis: TranscriptAnalysis,
) -> Optional[Dict[str, str]]:
    """Warning if the first tool call does not reference project-context paths."""
    if not analysis.tool_sequence:
        return None
    first_call = analysis.tool_sequence[0]
    args_str = json.dumps(first_call.arguments)
    # Look for any project-context path references
    context_indicators = [
        "project-context",
        ".claude/",
        "CLAUDE.md",
        "context-contracts",
    ]
    if not any(indicator in args_str for indicator in context_indicators):
        return {
            "type": "context_ignored",
            "severity": "warning",
            "message": (
                "First tool call does not reference any project-context "
                "paths — agent may have ignored injected context"
            ),
        }
    return None


def _check_context_update_missing(
    analysis: TranscriptAnalysis,
    agent_output: str,
) -> Optional[Dict[str, str]]:
    """Info if context-updater skill was injected but no CONTEXT_UPDATE emitted."""
    if "context-updater" in analysis.skills_injected:
        if "CONTEXT_UPDATE" not in agent_output:
            return {
                "type": "context_update_missing",
                "severity": "info",
                "message": (
                    "context-updater skill was injected but agent did not "
                    "emit a CONTEXT_UPDATE block"
                ),
            }
    return None


def _check_excessive_tool_calls(
    analysis: TranscriptAnalysis,
) -> Optional[Dict[str, str]]:
    """Warning if tool_call_count exceeds 75."""
    if analysis.tool_call_count > 75:
        return {
            "type": "excessive_tool_calls",
            "severity": "warning",
            "message": (
                f"Agent made {analysis.tool_call_count} tool calls "
                f"(threshold: 75) — may indicate inefficient exploration"
            ),
        }
    return None


def _check_token_budget(
    analysis: TranscriptAnalysis,
) -> Optional[Dict[str, str]]:
    """Info if cache_creation_tokens exceeds 200000."""
    if analysis.cache_creation_tokens > 200000:
        return {
            "type": "token_budget",
            "severity": "info",
            "message": (
                f"Cache creation tokens ({analysis.cache_creation_tokens}) "
                f"exceeded 200,000 — large context was created"
            ),
        }
    return None


def _check_pipe_retroactive(
    analysis: TranscriptAnalysis,
) -> List[Dict[str, str]]:
    """Warning per pipe command detected in transcript."""
    results: List[Dict[str, str]] = []
    for cmd in analysis.pipe_commands:
        # Truncate long commands for readability
        display_cmd = cmd[:120] + "..." if len(cmd) > 120 else cmd
        results.append({
            "type": "pipe_retroactive",
            "severity": "warning",
            "message": f"Pipe command detected in transcript: {display_cmd}",
        })
    return results


def _check_model_mismatch(
    analysis: TranscriptAnalysis,
    metrics: Dict[str, Any],
) -> Optional[Dict[str, str]]:
    """Info if transcript model differs from agent definition model."""
    definition_model = ""
    snapshot = metrics.get("default_skills_snapshot")
    if isinstance(snapshot, dict):
        definition_model = snapshot.get("model", "")
    if (
        analysis.model
        and definition_model
        and analysis.model != definition_model
    ):
        return {
            "type": "model_mismatch",
            "severity": "info",
            "message": (
                f"Transcript model ({analysis.model}) differs from "
                f"agent definition model ({definition_model})"
            ),
        }
    return None


def _check_skill_order(
    analysis: TranscriptAnalysis,
    metrics: Dict[str, Any],
) -> Optional[Dict[str, str]]:
    """Info if skills were injected in an unexpected order."""
    snapshot = metrics.get("default_skills_snapshot")
    if not isinstance(snapshot, dict):
        return None
    expected_skills = snapshot.get("skills", [])
    if not expected_skills or not analysis.skills_injected:
        return None
    # Check if the injected skills appear in the expected order
    # (only consider skills that are in the expected list)
    expected_set = set(expected_skills)
    actual_ordered = [s for s in analysis.skills_injected if s in expected_set]
    expected_ordered = [s for s in expected_skills if s in set(actual_ordered)]
    if actual_ordered and expected_ordered and actual_ordered != expected_ordered:
        return {
            "type": "skill_order",
            "severity": "info",
            "message": (
                f"Skills injected in unexpected order: "
                f"{actual_ordered} (expected: {expected_ordered})"
            ),
        }
    return None


def _check_duplicate_tools(
    analysis: TranscriptAnalysis,
) -> Optional[Dict[str, str]]:
    """Info if duplicate tool calls were detected."""
    if analysis.duplicate_tool_calls:
        dup_summary = ", ".join(
            f"{d.tool_name}(x{len(d.indices)})"
            for d in analysis.duplicate_tool_calls
        )
        return {
            "type": "duplicate_tools",
            "severity": "info",
            "message": (
                f"Duplicate tool calls detected: {dup_summary}"
            ),
        }
    return None


def _check_token_explosion(
    analysis: TranscriptAnalysis,
) -> Optional[Dict[str, str]]:
    """Warning if total token consumption exceeds 10 million."""
    total = (
        analysis.input_tokens
        + analysis.cache_creation_tokens
        + analysis.output_tokens
    )
    if total > 10_000_000:
        return {
            "type": "token_explosion",
            "severity": "warning",
            "message": (
                f"Total token consumption ({total:,}) exceeded 10,000,000 "
                f"— possible runaway generation"
            ),
        }
    return None


def _check_cache_efficiency(
    analysis: TranscriptAnalysis,
) -> Optional[Dict[str, str]]:
    """Info if cache read ratio is below 60% with significant token volume."""
    total = (
        analysis.cache_read_tokens
        + analysis.cache_creation_tokens
        + analysis.input_tokens
    )
    if total > 1000:
        ratio = analysis.cache_read_tokens / total
        if ratio < 0.60:
            return {
                "type": "cache_efficiency",
                "severity": "info",
                "message": (
                    f"Cache read ratio is {ratio:.1%} (below 60%) "
                    f"with {total:,} total input tokens — cache may be underutilized"
                ),
            }
    return None


def _check_bash_permission_gate(
    analysis: TranscriptAnalysis,
    metrics: Dict[str, Any],
) -> Optional[Dict[str, str]]:
    """Warning if agent declares Bash but made 0 Bash calls.

    When the native Claude Code permission layer denies Bash before the
    hook fires, our hook never runs and no log entry is produced. The agent
    reports 'Permission to use Bash has been denied' but the failure is
    otherwise invisible.  A declared-Bash agent with 0 actual Bash calls is
    a reliable signal of this invisible gate.
    """
    snapshot = metrics.get("default_skills_snapshot") or {}
    declared_tools = snapshot.get("tools", [])
    if not isinstance(declared_tools, list):
        return None

    declared_lower = [t.lower() for t in declared_tools]
    if "bash" not in declared_lower:
        return None

    if len(analysis.bash_commands) == 0:
        agent_name = metrics.get("agent", "unknown")
        return {
            "type": "bash_permission_gate",
            "severity": "warning",
            "message": (
                f"WARNING: Agent {agent_name} declares Bash but made 0 Bash calls. "
                f"Possible native permission layer block."
            ),
        }
    return None


def _check_duplicate_write_storm(
    analysis: TranscriptAnalysis,
) -> Optional[Dict[str, str]]:
    """Warning if Write or Edit tool calls appear 3+ times with identical args."""
    for dup in analysis.duplicate_tool_calls:
        if dup.tool_name in ("Write", "Edit") and len(dup.indices) >= 3:
            return {
                "type": "duplicate_write_storm",
                "severity": "warning",
                "message": (
                    f"Duplicate {dup.tool_name} storm detected: "
                    f"{len(dup.indices)} identical calls at indices "
                    f"{dup.indices}"
                ),
            }
    return None


def _check_duration_outlier(
    analysis: TranscriptAnalysis,
) -> Optional[Dict[str, str]]:
    """Warning if agent execution exceeded 10 minutes."""
    if analysis.duration_ms is not None and analysis.duration_ms > 600_000:
        minutes = analysis.duration_ms / 60_000
        return {
            "type": "duration_outlier",
            "severity": "warning",
            "message": (
                f"Agent execution took {minutes:.1f} minutes "
                f"(threshold: 10 min) — may indicate stalled or inefficient work"
            ),
        }
    return None


def _check_tool_call_velocity(
    analysis: TranscriptAnalysis,
) -> Optional[Dict[str, str]]:
    """Warning if tool call rate exceeds 20 calls per minute."""
    if (
        analysis.duration_ms is not None
        and analysis.duration_ms > 0
        and (analysis.tool_call_count / (analysis.duration_ms / 60_000)) > 20
    ):
        velocity = analysis.tool_call_count / (analysis.duration_ms / 60_000)
        return {
            "type": "tool_call_velocity",
            "severity": "warning",
            "message": (
                f"Tool call velocity is {velocity:.1f} calls/min "
                f"(threshold: 20) — agent may be thrashing"
            ),
        }
    return None


def audit(
    metrics: Dict[str, Any],
    agent_output: str = "",
    task_info: Optional[Dict[str, Any]] = None,
    rejected_sections: Optional[List[str]] = None,
    transcript_analysis: Optional[TranscriptAnalysis] = None,
) -> List[Dict[str, str]]:
    """
    Detect anomalies in workflow execution.

    Checks:
    - execution_failure: exit_code != 0
    - consecutive_failures: 3+ failures in a row for same agent
    - missing_evidence: COMPLETE but no evidence in json:contract block
    - empty_evidence: json:contract evidence exists but commands_run empty or all "not run"
    - skipped_verification: task has verify command in injected_context but not in commands_run
    - scope_escalation: rejected_sections exist (agent tried to write outside its scope)

    Transcript-analysis checks (only when transcript_analysis is provided):
    - investigation_skip: first tool was Bash
    - context_ignored: first tool call has no project-context paths
    - context_update_missing: context-updater injected but no CONTEXT_UPDATE emitted
    - excessive_tool_calls: tool_call_count > 75
    - token_budget: cache_creation_tokens > 200000
    - token_explosion: total tokens (input+cache_creation+output) > 10M
    - cache_efficiency: cache read ratio < 60% with significant volume
    - duplicate_write_storm: Write/Edit tool with 3+ identical calls
    - duration_outlier: duration_ms > 600,000 (10 min)
    - tool_call_velocity: > 20 tool calls per minute
    - pipe_retroactive: pipe commands found in transcript
    - model_mismatch: transcript model != agent definition model
    - skill_order: skills injected in unexpected order
    - duplicate_tools: duplicate tool calls detected
    - bash_permission_gate: agent declares Bash but made 0 calls (native layer block)

    Args:
        metrics: Workflow metrics dict (from workflow_recorder.record()).
        agent_output: Complete agent output string (for evidence checks).
        task_info: Task metadata including injected_context (for verification checks).
        rejected_sections: List of context sections rejected by permission validation.
        transcript_analysis: Optional TranscriptAnalysis from transcript_analyzer.
            When None (default), transcript-based checks are skipped for backward
            compatibility.

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
            from ..agents.contract_validator import parse_contract
            contract = parse_contract(agent_output)
            has_evidence = (
                contract is not None
                and isinstance(contract.get("evidence_report"), dict)
                and bool(contract["evidence_report"])
            )
            if not has_evidence:
                anomalies.append({
                    "type": "missing_evidence",
                    "severity": "warning",
                    "message": (
                        f"Agent {metrics['agent']} completed but "
                        f"did not include evidence in json:contract block"
                    ),
                })

    # --- NEW: empty_evidence ---
    if agent_output:
        from ..agents.contract_validator import parse_contract
        contract = parse_contract(agent_output)
        if contract is not None:
            evidence = contract.get("evidence_report")
            if isinstance(evidence, dict):
                commands_run = evidence.get("commands_run", [])
                if isinstance(commands_run, list):
                    not_run_pattern = re.compile(
                        r"\b(not\s+run|not\s+executed|skipped|n/a|none)\b",
                        re.IGNORECASE,
                    )
                    if not commands_run:
                        # commands_run key exists but is empty list
                        anomalies.append({
                            "type": "empty_evidence",
                            "severity": "warning",
                            "message": (
                                f"Agent {metrics['agent']} has evidence in "
                                f"json:contract but commands_run is empty"
                            ),
                        })
                    elif all(
                        isinstance(c, str) and not_run_pattern.search(c)
                        for c in commands_run
                    ):
                        anomalies.append({
                            "type": "empty_evidence",
                            "severity": "warning",
                            "message": (
                                f"Agent {metrics['agent']} has evidence in "
                                f"json:contract but all commands_run entries "
                                f"indicate 'not run'"
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

    # --- Transcript-analysis checks (T009) ---
    if transcript_analysis is not None:
        for check_fn in (
            _check_investigation_skip,
            _check_context_ignored,
            _check_excessive_tool_calls,
            _check_token_budget,
            _check_duplicate_tools,
            _check_token_explosion,
            _check_cache_efficiency,
            _check_duplicate_write_storm,
            _check_duration_outlier,
            _check_tool_call_velocity,
        ):
            result = check_fn(transcript_analysis)
            if result is not None:
                anomalies.append(result)

        # Checks that need agent_output
        result = _check_context_update_missing(transcript_analysis, agent_output)
        if result is not None:
            anomalies.append(result)

        # Checks that need metrics
        for check_fn_m in (_check_model_mismatch, _check_skill_order, _check_bash_permission_gate):
            result = check_fn_m(transcript_analysis, metrics)
            if result is not None:
                anomalies.append(result)

        # Pipe check returns a list (one per pipe command)
        anomalies.extend(_check_pipe_retroactive(transcript_analysis))

    return anomalies



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
