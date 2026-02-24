#!/usr/bin/env python3
"""
Subagent stop hook for Claude Code Agent System (with Episodic Memory)

Handles workflow metrics capture, anomaly detection, and episodic memory storage
when agents complete execution.

Responsibilities:
1. Capture workflow execution metrics (duration, exit code, agent)
2. Detect anomalies (slow execution, failures, consecutive failures)
3. Signal Gaia for analysis when anomalies are detected
4. Store workflow as episodic memory for future reference

Architecture:
- Metrics stored in .claude/project-context/workflow-episodic-memory/
- Episodes stored in .claude/project-context/episodic-memory/
- Anomaly signals trigger Gaia analysis
- Minimal footprint - no bundle creation

Integration:
- Executed automatically after agent tool completes
- Integrates with memory/episodic.py for context enrichment
"""

import os
import sys
import json
import logging
import re
import select
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional
import hashlib

# Configure structured logging with file handler (matching pre_tool_use.py pattern)
try:
    from modules.core.paths import get_logs_dir
    _log_dir = get_logs_dir()
except ImportError:
    _log_dir = Path.cwd() / ".claude" / "logs"
    _log_dir.mkdir(parents=True, exist_ok=True)

_log_file = _log_dir / f"hooks-{datetime.now().strftime('%Y-%m-%d')}.log"
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [subagent_stop] %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(_log_file),
    ]
)
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


def _find_claude_dir() -> Path:
    """Find the .claude directory by searching upward from current location"""
    current = Path.cwd()

    # If we're already in a .claude directory, go up one level
    if current.name == ".claude":
        return current

    # Look for .claude in current directory
    claude_dir = current / ".claude"
    if claude_dir.exists():
        return claude_dir

    # Search upward through parent directories
    for parent in current.parents:
        claude_dir = parent / ".claude"
        if claude_dir.exists():
            return claude_dir

    # Default fallback - create .claude in current directory
    logger.warning("No .claude directory found, creating in current directory")
    claude_dir = current / ".claude"
    claude_dir.mkdir(exist_ok=True)
    return claude_dir


def _get_or_create_session_id() -> str:
    """Get existing session ID or create new one"""
    session_id = os.environ.get("CLAUDE_SESSION_ID")
    if not session_id:
        timestamp = datetime.now().strftime("%H%M%S")
        hash_input = f"{timestamp}-{os.getpid()}"
        session_hash = hashlib.sha256(hash_input.encode()).hexdigest()[:8]
        session_id = f"session-{timestamp}-{session_hash}"
        os.environ["CLAUDE_SESSION_ID"] = session_id
        logger.debug(f"Generated new session_id: {session_id}")
    return session_id


def get_session_events() -> Dict[str, Any]:
    """
    Get critical events from active session context.

    Returns:
        Dict with categorized session events (commits, pushes, file_mods, speckit)
    """
    context_path = Path(".claude/session/active/context.json")

    if not context_path.exists():
        logger.debug("No session context found")
        return {}

    try:
        with open(context_path, 'r') as f:
            context = json.load(f)

        critical_events = context.get("critical_events", [])

        if not critical_events:
            return {}

        # Extract git commits
        commits = [
            {
                "hash": e.get("commit_hash", ""),
                "message": e.get("commit_message", ""),
                "timestamp": e.get("timestamp", "")
            }
            for e in critical_events
            if e.get("event_type") == "git_commit" and e.get("commit_hash")
        ]

        # Extract git pushes
        pushes = [
            {
                "branch": e.get("branch", ""),
                "timestamp": e.get("timestamp", "")
            }
            for e in critical_events
            if e.get("event_type") == "git_push" and e.get("branch")
        ]

        # Extract file modifications
        file_mods = [
            {
                "count": e.get("modification_count", 0),
                "timestamp": e.get("timestamp", "")
            }
            for e in critical_events
            if e.get("event_type") == "file_modifications"
        ]

        # Extract spec-kit milestones
        speckit = [
            {
                "command": e.get("command", ""),
                "timestamp": e.get("timestamp", "")
            }
            for e in critical_events
            if e.get("event_type") == "speckit_milestone"
        ]

        # Build result
        result = {}
        if commits:
            result["git_commits"] = commits
        if pushes:
            result["git_pushes"] = pushes
        if file_mods:
            result["file_modifications"] = file_mods
        if speckit:
            result["speckit_milestones"] = speckit

        if result:
            logger.info(f"Found {len(critical_events)} session events")

        return result

    except Exception as e:
        logger.warning(f"Failed to read session events: {e}")
        return {}


def capture_workflow_metrics(task_info: Dict[str, Any], agent_output: str, session_context: Dict[str, Any]) -> Dict[str, Any]:
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
    # The previous regex-based extraction almost never matched real output.
    duration_ms = None

    # Use exit_code from task_info (derived from AGENT_STATUS block) instead
    # of naive text matching which gives false positives on "No errors found".
    exit_code = task_info.get("exit_code", 0)

    # Approximate token count: 4 chars per token is a reliable heuristic for LLM output
    output_tokens_approx = len(agent_output) // 4

    metrics = {
        "timestamp": session_context["timestamp"],
        "session_id": session_context["session_id"],
        "task_id": task_info.get("task_id", "unknown"),
        "agent": task_info.get("agent", "unknown"),
        "tier": task_info.get("tier", "T0"),
        "duration_ms": duration_ms,
        "exit_code": exit_code,
        "plan_status": task_info.get("plan_status", ""),
        "output_length": len(agent_output),
        "output_tokens_approx": output_tokens_approx,
        "tags": task_info.get("tags", []),
        "prompt": task_info.get("description", ""),  # Store for episodic
    }

    # Save to workflow memory
    workflow_memory_dir = get_workflow_memory_dir()
    workflow_memory_dir.mkdir(parents=True, exist_ok=True)

    metrics_file = workflow_memory_dir / "metrics.jsonl"
    with open(metrics_file, "a") as f:
        f.write(json.dumps(metrics) + "\n")

    logger.debug(f"Captured workflow metrics: {metrics['agent']} (duration: {duration_ms}ms, exit: {exit_code})")

    return metrics


def detect_anomalies(metrics: Dict[str, Any]) -> List[Dict[str, str]]:
    """
    Detect anomalies in workflow execution.

    Checks:
    - Slow execution (> 120s)
    - Failed executions (exit_code != 0)
    - Consecutive failures (3+ in a row)

    Returns:
        List of anomaly descriptions
    """
    anomalies = []

    # Check exit code
    if metrics.get("exit_code", 0) != 0:
        anomalies.append({
            "type": "execution_failure",
            "severity": "error",
            "message": f"Agent {metrics['agent']} failed with exit code {metrics['exit_code']}"
        })

    # Check consecutive failures (read last N metrics)
    try:
        workflow_memory_dir = get_workflow_memory_dir()
        metrics_file = workflow_memory_dir / "metrics.jsonl"

        if metrics_file.exists():
            from collections import deque
            with open(metrics_file) as f:
                recent = list(deque(f, maxlen=7))
            # Get last 5 metrics (excluding current which is the last line)
            last_5 = [json.loads(line) for line in recent[:-1]][-5:] if len(recent) > 1 else []

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
                    "message": f"Agent {agent} has failed {len(recent_failures) + 1} times consecutively"
                })
    except Exception as e:
        logger.debug(f"Could not check consecutive failures: {e}")

    return anomalies


def signal_gaia_analysis(anomalies: List[Dict], metrics: Dict[str, Any]):
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
            "anomalies": anomalies,
            "metrics_summary": {
                "agent": metrics["agent"],
                "task_id": metrics["task_id"],
                "duration_ms": metrics.get("duration_ms"),
                "exit_code": metrics.get("exit_code")
            },
            "suggested_action": "Invoke /gaia for system analysis"
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
                "metrics": metrics
            }) + "\n")

    except Exception as e:
        logger.warning(f"Could not create analysis signal: {e}")


def capture_episodic_memory(
    metrics: Dict[str, Any],
    anomalies: Optional[List[Dict[str, str]]] = None,
) -> Optional[str]:
    """
    Capture workflow as episodic memory.

    Args:
        metrics: Subagent metrics from workflow (includes plan_status, tier, task description)
        anomalies: Detected anomalies from detect_anomalies(), stored in episode context

    Returns:
        Episode ID if stored, None otherwise
    """
    try:
        import importlib.util

        # Find memory module
        candidates = [
            Path(__file__).parent.parent / "tools" / "memory" / "episodic.py",
            Path(".claude/tools/memory/episodic.py"),
        ]

        episodic_module = None
        for path in candidates:
            if path.exists():
                try:
                    spec = importlib.util.spec_from_file_location("episodic", path)
                    if spec and spec.loader:
                        episodic_module = importlib.util.module_from_spec(spec)
                        spec.loader.exec_module(episodic_module)
                        logger.debug(f"Loaded episodic module from {path}")
                        break
                except Exception as e:
                    logger.debug(f"Could not load episodic from {path}: {e}")
                    continue

        if not episodic_module:
            logger.debug("Episodic memory module not found - skipping episode capture")
            return None

        # Initialize memory
        memory = episodic_module.EpisodicMemory()

        # Use the real task description captured from the transcript.
        # metrics["prompt"] now holds the first user message (task description)
        # rather than the generic "SubagentStop for <agent>".
        prompt = metrics.get("prompt", "")
        if not prompt:
            prompt = f"Task for {metrics.get('agent', 'unknown')}"

        subagent_type = metrics.get("agent", "unknown")
        duration_seconds = metrics.get("duration_ms", 0) / 1000.0 if metrics.get("duration_ms") else None

        # Determine outcome: prefer plan_status string, fall back to exit_code
        plan_status = metrics.get("plan_status", "")
        exit_code = metrics.get("exit_code", 0)
        if plan_status:
            if "COMPLETE" in plan_status:
                outcome = "success"
                success = True
            elif "BLOCKED" in plan_status or "ERROR" in plan_status:
                outcome = "failed"
                success = False
            else:
                # INVESTIGATING, PLANNING, NEEDS_INPUT → partial
                outcome = "partial"
                success = None
        elif exit_code == 0:
            outcome = "success"
            success = True
        else:
            outcome = "failed"
            success = False

        # Tags from metrics
        tags = metrics.get("tags", [])
        if not tags:
            tags = [subagent_type]

        # Enrich with session events and anomalies
        session_events = get_session_events()
        context = {"metrics": metrics}
        if session_events:
            context["session_events"] = session_events
            logger.info(f"Enriched episode with session events: {list(session_events.keys())}")
        if anomalies:
            context["anomalies"] = anomalies
            logger.info(f"Episode has {len(anomalies)} anomaly/anomalies")

        # Store episode
        episode_id = memory.store_episode(
            prompt=prompt,
            clarifications={},
            enriched_prompt=prompt,
            context=context,
            tags=tags,
            outcome=outcome,
            success=success,
            duration_seconds=duration_seconds,
            commands_executed=[]
        )

        logger.info(f"Captured episode: {episode_id} (outcome: {outcome}, plan_status: {plan_status})")
        return episode_id

    except Exception as e:
        logger.debug(f"Failed to capture episodic memory: {e}")
        return None


def _process_context_updates(agent_output: str, task_info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Process CONTEXT_UPDATE blocks from agent output via context_writer.

    Loads the context_writer module dynamically and calls process_agent_output()
    to apply progressive enrichment to project-context.json.

    This function MUST NOT break the existing hook flow -- all errors are caught
    and logged, returning None on failure.

    Args:
        agent_output: Complete output from agent execution
        task_info: Task metadata (agent, description, task_id)

    Returns:
        Result dict from context_writer.process_agent_output, or None on error
    """
    try:
        import importlib.util

        # Load context_writer module
        writer_path = Path(__file__).parent / "modules" / "context" / "context_writer.py"
        if not writer_path.exists():
            logger.debug("context_writer module not found, skipping context updates")
            return None

        spec = importlib.util.spec_from_file_location("context_writer", writer_path)
        if not spec or not spec.loader:
            return None
        writer_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(writer_mod)

        # Find project-context.json via _find_claude_dir
        claude_dir = _find_claude_dir()
        context_path = claude_dir / "project-context" / "project-context.json"

        if not context_path.exists():
            logger.debug("project-context.json not found at %s, skipping context updates", context_path)
            return None

        # Determine config_dir (inside .claude directory)
        config_dir = claude_dir / "config"

        # Build task_info dict for context_writer
        agent_type = task_info.get("agent", "unknown")
        task_info_for_writer = {
            "agent_type": agent_type,
            "context_path": str(context_path),
            "config_dir": str(config_dir),
        }

        result = writer_mod.process_agent_output(agent_output, task_info_for_writer)

        if result and result.get("updated"):
            logger.info(
                "Context updated by %s: sections=%s",
                agent_type, result.get("sections_updated", []),
            )
        if result and result.get("rejected"):
            logger.debug(
                "Context sections rejected for %s: %s",
                agent_type, result.get("rejected", []),
            )

        return result

    except Exception as e:
        logger.debug("Context update processing failed (non-fatal): %s", e)
        return None


def _consume_approval_file(agent_type: str) -> bool:
    """
    Delete .claude/approvals/pending.json if it exists and matches agent_type.

    The orchestrator writes this file when resuming an approved T3 operation.
    Consuming it here confirms the approval was used and prevents reuse.

    Returns:
        True if a matching approval file was consumed, False otherwise.
    """
    try:
        approval_path = Path(".claude/approvals/pending.json")
        if not approval_path.exists():
            return False

        data = json.loads(approval_path.read_text())
        if data.get("agent") == agent_type:
            approval_path.unlink()
            logger.info(
                "Consumed approval for agent '%s' (operation: %s)",
                agent_type,
                data.get("operation", "unknown"),
            )
            return True

        # File exists but for a different agent — leave it alone
        logger.debug(
            "Approval file exists for agent '%s', not '%s' — leaving intact",
            data.get("agent"),
            agent_type,
        )
        return False
    except Exception as e:
        logger.debug("Failed to consume approval file (non-fatal): %s", e)
        return False


def subagent_stop_hook(task_info: Dict[str, Any], agent_output: str) -> Dict[str, Any]:
    """
    Main subagent stop hook - captures metrics, detects anomalies, stores episodes.

    Args:
        task_info: Task information including ID, description, agent, etc.
        agent_output: Complete output from agent execution

    Returns:
        Success confirmation with metrics info
    """
    try:
        session_id = _get_or_create_session_id()
        agent_type = task_info.get("agent", "unknown")

        # Step 0: Consume approval file if present for this agent
        _consume_approval_file(agent_type)

        # Create session context for metrics
        session_context = {
            "timestamp": datetime.now().isoformat(),
            "session_id": session_id,
            "task_id": task_info.get('task_id', 'unknown'),
            "agent": agent_type,
        }

        # Step 1: Capture workflow metrics
        workflow_metrics = capture_workflow_metrics(task_info, agent_output, session_context)

        # Step 2: Check for anomalies
        anomalies = detect_anomalies(workflow_metrics)

        if anomalies:
            logger.warning(f"{len(anomalies)} anomalies detected in workflow")
            signal_gaia_analysis(anomalies, workflow_metrics)

        # Step 3: Capture as episodic memory (include anomalies for context)
        episode_id = capture_episodic_memory(workflow_metrics, anomalies=anomalies if anomalies else None)

        # Step 4: Process context updates (progressive enrichment)
        context_update_result = _process_context_updates(agent_output, task_info)

        return {
            "success": True,
            "session_id": session_id,
            "status": "metrics_captured",
            "metrics_captured": True,
            "anomalies_detected": len(anomalies) if anomalies else 0,
            "episode_id": episode_id,
            "context_updated": context_update_result.get("updated", False) if context_update_result else False,
        }

    except Exception as e:
        logger.error(f"Error in subagent_stop_hook: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "status": "partial_update"
        }


def _read_transcript(transcript_path: str) -> str:
    """Read agent transcript from file path provided by Claude Code.

    Claude Code provides ``agent_transcript_path`` pointing to a JSONL file.
    Each line has the structure:
        {"type": "assistant", "message": {"role": "assistant", "content": [...]}, ...}
    The role/content are nested inside the ``message`` field.

    Falls back to empty string on any error so the hook never crashes.
    """
    try:
        # Expand ~ to home directory (Claude Code may use ~ in paths)
        path = Path(transcript_path).expanduser()
        logger.debug("Reading transcript from: %s", path)

        if not path.exists():
            logger.warning("Transcript file not found: %s", path)
            return ""

        lines = path.read_text().strip().splitlines()

        text_parts: List[str] = []
        for line in lines:
            if not line.strip():
                continue
            try:
                entry = json.loads(line)

                # Claude Code transcript format: content is inside entry["message"]
                msg = entry.get("message", entry)  # fallback to entry itself for simple format
                role = msg.get("role", "")
                if role != "assistant":
                    continue

                content = msg.get("content", "")
                if isinstance(content, str):
                    text_parts.append(content)
                elif isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            text_parts.append(block.get("text", ""))
                        elif isinstance(block, str):
                            text_parts.append(block)
            except (json.JSONDecodeError, TypeError):
                continue

        result = "\n".join(text_parts)
        logger.debug("Extracted %d text parts, total length: %d chars", len(text_parts), len(result))
        return result

    except Exception as e:
        logger.debug("Failed to read transcript from %s: %s", transcript_path, e)
        return ""


def _extract_task_description_from_transcript(transcript_path: str) -> str:
    """Read the first user message from the subagent transcript JSONL.

    Claude Code's agent_transcript_path contains the full subagent conversation.
    The first ``role: "user"`` entry is the task prompt sent by the orchestrator —
    which is the most meaningful description of what the agent was asked to do.

    Returns empty string on any error so the hook never crashes.
    """
    if not transcript_path:
        return ""
    try:
        path = Path(transcript_path).expanduser()
        if not path.exists():
            return ""
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    # Format: {"type": "user", "message": {"role": "user", "content": ...}}
                    msg = entry.get("message", entry)
                    if msg.get("role") != "user":
                        continue
                    content = msg.get("content", "")
                    if isinstance(content, str):
                        text = content.strip()
                        # Pattern 2: pre_tool_use injected project context before the real prompt.
                        # The injected block ends with "\n\n---\n\n# User Task\n\n" followed by
                        # the actual task description sent by the orchestrator.
                        if text.startswith("# Project Context (Auto-Injected)"):
                            sep_full = "\n\n---\n\n# User Task\n\n"
                            sep_bare = "\n\n---\n\n"
                            pos = text.find(sep_full)
                            if pos != -1:
                                text = text[pos + len(sep_full):].strip()
                            else:
                                pos = text.find(sep_bare)
                                if pos != -1:
                                    text = text[pos + len(sep_bare):].strip()
                                else:
                                    text = ""  # Cannot extract real prompt
                        # Pattern 1: content is already the clean orchestrator prompt — no change
                    elif isinstance(content, list):
                        # content blocks: [{"type": "text", "text": "..."}]
                        text = " ".join(
                            b.get("text", "") for b in content
                            if isinstance(b, dict) and b.get("type") == "text"
                        ).strip()
                    else:
                        continue
                    if text:
                        # Truncate to 500 chars — enough context, not too much
                        return text[:500]
                except (json.JSONDecodeError, TypeError):
                    continue
    except Exception as e:
        logger.debug("Failed to extract task description from transcript: %s", e)
    return ""


def _extract_plan_status_from_output(agent_output: str) -> str:
    """Extract the PLAN_STATUS string from the last AGENT_STATUS block.

    Returns the raw status string (e.g. "COMPLETE", "BLOCKED", "NEEDS_INPUT")
    or empty string if not found.
    """
    status_match = None
    for m in re.finditer(r"PLAN_STATUS:\s*(\S+)", agent_output):
        status_match = m
    if status_match:
        return status_match.group(1).upper().rstrip(".,;")
    return ""


def _extract_exit_code_from_output(agent_output: str) -> int:
    """Derive exit code from the LAST AGENT_STATUS block in agent output.

    Looks for PLAN_STATUS in the final assistant message.  If the status
    contains COMPLETE -> 0, BLOCKED or ERROR -> 1.  Falls back to 0 when
    no AGENT_STATUS is found (optimistic default).
    """
    status_value = _extract_plan_status_from_output(agent_output)
    if status_value:
        if "COMPLETE" in status_value:
            return 0
        if "BLOCKED" in status_value or "ERROR" in status_value:
            return 1
    return 0


def _build_task_info_from_hook_data(hook_data: Dict[str, Any], agent_output: str = "") -> Dict[str, Any]:
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
    exit_code = _extract_exit_code_from_output(agent_output) if agent_output else 0
    plan_status = _extract_plan_status_from_output(agent_output) if agent_output else ""

    # Extract tier from agent output (e.g. agents report tier in their context)
    # Look for explicit tier references in agent output: T0, T1, T2, T3
    tier_real = "T0"
    if agent_output:
        tier_match = re.search(r"\bT([0-3])\b", agent_output)
        if tier_match:
            tier_real = f"T{tier_match.group(1)}"

    # Extract real task description from the first user message in the transcript
    transcript_path = hook_data.get("agent_transcript_path", "")
    task_description = _extract_task_description_from_transcript(transcript_path)
    agent_type = hook_data.get("agent_type", "unknown")
    if not task_description:
        task_description = f"SubagentStop for {agent_type}"

    return {
        "task_id": hook_data.get("agent_id", "unknown"),
        "description": task_description,
        "agent": agent_type,
        "tier": tier_real,
        "tags": [],
        "exit_code": exit_code,
        "plan_status": plan_status,
    }


def main():
    """CLI interface for testing metrics capture"""

    if len(sys.argv) < 2:
        print("Usage: python subagent_stop.py --test")
        sys.exit(1)

    if sys.argv[1] == "--test":
        test_task_info = {
            "task_id": "T006",
            "description": "Terraform plan for infrastructure",
            "agent": "terraform-architect",
            "tier": "T1",
            "tags": ["#terraform", "#infrastructure"],
        }

        test_output = """
# Terraform Architect Execution Log

## Task: T006 - Terraform plan for infrastructure

### Results:
- Configuration validated successfully
- Plan generated with 12 resources
- Duration: 45000 ms
"""

        result = subagent_stop_hook(test_task_info, test_output)

        if result["success"]:
            print("Test completed successfully!")
            print(f"Session ID: {result['session_id']}")
            print(f"Anomalies: {result['anomalies_detected']}")
            print(f"Episode ID: {result.get('episode_id', 'none')}")
        else:
            print(f"Test failed: {result.get('error', 'Unknown error')}")

    else:
        print("Unknown command. Use --test to run test.")


def has_stdin_data() -> bool:
    """Check if there's data available on stdin."""
    if sys.stdin.isatty():
        return False
    try:
        readable, _, _ = select.select([sys.stdin], [], [], 0)
        return bool(readable)
    except Exception:
        return not sys.stdin.isatty()


# ============================================================================
# STDIN HANDLER (Claude Code integration)
# ============================================================================

if __name__ == "__main__":
    # Check if running from CLI with arguments
    if len(sys.argv) > 1:
        main()
    elif has_stdin_data():
        try:
            stdin_data = sys.stdin.read()
            if not stdin_data.strip():
                print("Error: Empty stdin data")
                sys.exit(1)

            hook_data = json.loads(stdin_data)

            logger.info(f"Hook event: {hook_data.get('hook_event_name')}, agent: {hook_data.get('agent_type', 'unknown')}")

            # Use last_assistant_message directly — no transcript I/O needed for AGENT_STATUS parsing.
            # Falls back to reading the transcript if last_assistant_message is absent (older Claude Code).
            agent_output = hook_data.get("last_assistant_message", "")
            if not agent_output:
                transcript_path = hook_data.get("agent_transcript_path", "")
                agent_output = _read_transcript(transcript_path) if transcript_path else ""
                logger.info(f"Agent output: {len(agent_output)} chars from transcript (fallback)")
            else:
                logger.info(f"Agent output: {len(agent_output)} chars from last_assistant_message")

            # Build task_info from Claude Code SubagentStop payload
            # (needs agent_output to derive exit_code from AGENT_STATUS)
            task_info = _build_task_info_from_hook_data(hook_data, agent_output)

            # Run the full processing chain
            result = subagent_stop_hook(task_info, agent_output)

            # Output result as JSON for Claude Code
            print(json.dumps(result))
            sys.exit(0)

        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON from stdin: {e}")
            sys.exit(1)
        except Exception as e:
            logger.error(f"Error processing hook: {e}", exc_info=True)
            sys.exit(1)
    else:
        # No args and no stdin - show usage
        print("Usage: python subagent_stop.py --test")
        print("       echo '{...}' | python subagent_stop.py  (stdin mode)")
        sys.exit(1)
