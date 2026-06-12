"""
Build task_info dict from Claude Code SubagentStop stdin payload.

Provides:
    - build_task_info_from_hook_data(): Map hook payload to task_info format
"""

import logging
import re
from typing import Any, Dict

from .contract_validator import extract_exit_code_from_output, extract_plan_status_from_output
from .transcript_reader import (
    extract_injected_context_payload_from_transcript,
    extract_task_description_from_transcript,
)

logger = logging.getLogger(__name__)


def build_task_info_from_hook_data(
    hook_data: Dict[str, Any],
    agent_output: str = "",
) -> Dict[str, Any]:
    """Build a task_info dict from the Claude Code SubagentStop stdin payload.

    Claude Code sends these fields for SubagentStop:
      - hook_event_name: "SubagentStop"
      - session_id: str
      - agent_type: str  (e.g. "cloud-troubleshooter")
      - agent_id: str
      - transcript_path: str  (session-level JSONL)
      - agent_transcript_path: str  (subagent JSONL)
      - last_assistant_message: str  (final agent response text, no I/O needed)
      - cwd: str
      - stop_hook_active: bool
      - permission_mode: str

    We map these to the task_info format expected by subagent_stop_hook().
    The exit_code is derived from the agent's AGENT_STATUS block.
    task_description is extracted from the first user message in the transcript.
    tier_real is parsed from the AGENT_STATUS block (not hardcoded T0).
    """
    exit_code = extract_exit_code_from_output(agent_output) if agent_output else 0
    plan_status = extract_plan_status_from_output(agent_output) if agent_output else ""

    # Extract tier from agent output (e.g. agents report tier in their context)
    # Look for explicit tier references in agent output: T0, T1, T2, T3
    tier_real = "T0"
    if agent_output:
        tier_match = re.search(r"\bT([0-3])\b", agent_output)
        if tier_match:
            tier_real = f"T{tier_match.group(1)}"

    # Extract real task description from the first user message in the transcript
    transcript_path = hook_data.get("agent_transcript_path", "")
    task_description = extract_task_description_from_transcript(transcript_path)
    injected_context = extract_injected_context_payload_from_transcript(transcript_path)
    agent_type = hook_data.get("agent_type", "") or "unknown"
    if not task_description:
        task_description = f"SubagentStop for {agent_type}"

    return {
        "task_id": hook_data.get("agent_id", "unknown"),
        "agent_id": hook_data.get("agent_id", "unknown"),
        "agent_transcript_path": transcript_path,
        "description": task_description,
        "agent": agent_type,
        "tier": tier_real,
        "tags": [agent_type] if agent_type != "unknown" else [],
        "exit_code": exit_code,
        "plan_status": plan_status,
        "injected_context": injected_context,
    }
