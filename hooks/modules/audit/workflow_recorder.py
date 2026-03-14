"""
Workflow metrics capture and persistence.

Renamed from metrics_recorder.py for clarity.

Provides:
    - get_workflow_memory_dir(): Resolve workflow memory directory
    - record(): Build metrics dict, write to JSONL
    - capture_workflow_metrics(): Backward-compatible alias for record()
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..context.context_injector import build_context_telemetry_snapshot

logger = logging.getLogger(__name__)


def get_workflow_memory_dir() -> Path:
    """
    Get workflow memory directory path.

    Supports override via WORKFLOW_MEMORY_BASE_PATH env var for testing.
    In production, uses .claude/project-context/workflow-episodic-memory relative to CWD.
    """
    base_path = os.environ.get("WORKFLOW_MEMORY_BASE_PATH")
    if base_path:
        return Path(base_path) / "project-context" / "workflow-episodic-memory"
    return Path(".claude/project-context/workflow-episodic-memory")


def _append_jsonl(path: Path, payload: Dict[str, Any]) -> None:
    """Append one JSON record per line."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(payload) + "\n")


def _parse_frontmatter(text: str) -> Dict[str, Any]:
    """Parse simple markdown frontmatter without external dependencies."""
    if not text.startswith("---"):
        return {}

    try:
        end = text.index("---", 3)
    except ValueError:
        return {}

    frontmatter = text[3:end]
    result: Dict[str, Any] = {}
    current_key: Optional[str] = None
    current_list: Optional[List[str]] = None

    for line in frontmatter.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        if stripped.startswith("- ") and current_key and current_list is not None:
            current_list.append(stripped[2:].strip())
            continue

        if ":" in stripped:
            if current_key and current_list is not None:
                result[current_key] = current_list

            key, _, value = stripped.partition(":")
            key = key.strip()
            value = value.strip()

            if value:
                result[key] = value
                current_key = key
                current_list = None
            else:
                current_key = key
                current_list = []

    if current_key and current_list is not None:
        result[current_key] = current_list

    return result


def _parse_tools_field(value: Any) -> List[str]:
    """Normalize frontmatter tools into a list."""
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str) and item.strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return []


def _resolve_agent_file(agent_type: str) -> Optional[Path]:
    """Resolve the markdown definition for a project agent."""
    if not agent_type:
        return None

    candidates = [
        Path(".claude/agents") / f"{agent_type}.md",
        Path(__file__).resolve().parents[3] / "agents" / f"{agent_type}.md",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def load_agent_runtime_profile(agent_type: str) -> Dict[str, Any]:
    """Load runtime-default metadata from an agent definition file."""
    agent_file = _resolve_agent_file(agent_type)
    if not agent_file:
        return {
            "agent": agent_type or "unknown",
            "model": "",
            "tools": [],
            "skills": [],
            "skills_count": 0,
        }

    frontmatter = _parse_frontmatter(agent_file.read_text())
    skills = frontmatter.get("skills", [])
    if not isinstance(skills, list):
        skills = []

    return {
        "agent": agent_type or "unknown",
        "model": frontmatter.get("model", ""),
        "tools": _parse_tools_field(frontmatter.get("tools", [])),
        "skills": skills,
        "skills_count": len(skills),
    }


def record_agent_skill_snapshot(
    agent_type: str,
    session_context: Optional[Dict[str, Any]] = None,
    task_description: str = "",
) -> Dict[str, Any]:
    """Persist a historical snapshot of an agent's runtime defaults."""
    session_context = session_context or {}
    profile = load_agent_runtime_profile(agent_type)
    snapshot = {
        "timestamp": session_context.get("timestamp", datetime.now().isoformat()),
        "session_id": session_context.get("session_id", ""),
        "agent": profile.get("agent", agent_type or "unknown"),
        "task_description": task_description[:200],
        "model": profile.get("model", ""),
        "tools": profile.get("tools", []),
        "skills": profile.get("skills", []),
        "skills_count": profile.get("skills_count", 0),
    }

    try:
        _append_jsonl(get_workflow_memory_dir() / "agent-skills.jsonl", snapshot)
    except Exception as exc:
        logger.debug("Could not persist agent skill snapshot: %s", exc)

    return snapshot


def record(
    task_info: Dict[str, Any],
    agent_output: str,
    session_context: Dict[str, Any],
    commands_executed: Optional[List[str]] = None,
    context_update_result: Optional[Dict[str, Any]] = None,
    anchor_hits: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Capture workflow execution metrics for analysis.

    Args:
        task_info: Task metadata
        agent_output: Output from agent execution
        session_context: Current session context

    Returns:
        Dict with duration, exit_code, agent, tier, etc.
    """
    # Duration cannot be reliably measured from within this hook because
    # it fires only at agent completion (no start timestamp available).
    duration_ms = None

    # Use exit_code from task_info (derived from AGENT_STATUS block) instead
    # of naive text matching which gives false positives on "No errors found".
    exit_code = task_info.get("exit_code", 0)

    # Approximate token count: 4 chars per token is a reliable heuristic for LLM output
    output_tokens_approx = len(agent_output) // 4

    commands_executed = commands_executed or []
    context_update_result = context_update_result or {}
    context_snapshot = build_context_telemetry_snapshot(
        task_info.get("injected_context") or {}
    )
    default_skills_snapshot = load_agent_runtime_profile(task_info.get("agent", "unknown"))

    metrics = {
        "timestamp": session_context["timestamp"],
        "session_id": session_context["session_id"],
        "task_id": task_info.get("task_id", "unknown"),
        "agent_id": task_info.get("agent_id", "unknown"),
        "agent": task_info.get("agent", "unknown"),
        "tier": task_info.get("tier", "T0"),
        "duration_ms": duration_ms,
        "exit_code": exit_code,
        "plan_status": task_info.get("plan_status", ""),
        "output_length": len(agent_output),
        "output_tokens_approx": output_tokens_approx,
        "tags": task_info.get("tags", []),
        "prompt": task_info.get("description", ""),  # Store for episodic
        "commands_executed": commands_executed,
        "commands_executed_count": len(commands_executed),
        "context_snapshot": context_snapshot,
        "context_updated": bool(context_update_result.get("updated", False)),
        "context_sections_updated": context_update_result.get("sections_updated", []),
        "context_rejected_sections": context_update_result.get("rejected", []),
        "default_skills_snapshot": default_skills_snapshot,
        "context_anchor_hits": anchor_hits,
        "context_anchor_hit_rate": anchor_hits.get("hit_rate") if anchor_hits else None,
    }

    run_snapshot = {
        "timestamp": metrics["timestamp"],
        "session_id": metrics["session_id"],
        "task_id": metrics["task_id"],
        "agent_id": metrics["agent_id"],
        "agent": metrics["agent"],
        "tier": metrics["tier"],
        "plan_status": metrics["plan_status"],
        "context_snapshot": context_snapshot,
        "context_updated": metrics["context_updated"],
        "context_sections_updated": metrics["context_sections_updated"],
        "context_rejected_sections": metrics["context_rejected_sections"],
        "default_skills_snapshot": default_skills_snapshot,
        "context_anchor_hits": anchor_hits,
        "context_anchor_hit_rate": anchor_hits.get("hit_rate") if anchor_hits else None,
    }

    try:
        workflow_memory_dir = get_workflow_memory_dir()
        workflow_memory_dir.mkdir(parents=True, exist_ok=True)
        _append_jsonl(workflow_memory_dir / "run-snapshots.jsonl", run_snapshot)
    except Exception as exc:
        logger.debug("Could not persist run telemetry snapshot: %s", exc)

    # Save to workflow memory (gated behind env var; default: no write)
    if os.environ.get("GAIA_WRITE_WORKFLOW_METRICS") == "1":
        workflow_memory_dir = get_workflow_memory_dir()
        workflow_memory_dir.mkdir(parents=True, exist_ok=True)

        metrics_file = workflow_memory_dir / "metrics.jsonl"
        with open(metrics_file, "a") as f:
            f.write(json.dumps(metrics) + "\n")

    logger.debug(
        "Captured workflow metrics: %s (duration: %sms, exit: %s, commands: %s)",
        metrics["agent"], duration_ms, exit_code, len(commands_executed),
    )

    return metrics


# Backward-compatible alias
capture_workflow_metrics = record
