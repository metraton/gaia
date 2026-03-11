"""Core context injection subsystem for project agents.

Handles:
- inject_project_context: main context injection into agent prompts
- should_inject_on_resume: determines if context is needed on resume
- check_pending_updates_threshold: warns when pending updates accumulate
- consume_anomaly_flag: reads and deletes anomaly signal flags
"""

import json
import logging
import os
import subprocess
from datetime import datetime
from pathlib import Path

from ..security.prompt_validator import classify_resume_prompt
from .contracts_loader import build_context_update_reminder

logger = logging.getLogger(__name__)


def should_inject_on_resume(parameters: dict) -> bool:
    """
    Determine if context should be injected on a resume operation.

    By default, resume operations skip context injection because the agent
    already has context from phase 1. However, in some cases we need fresh context:

    Rules:
    1. If prompt contains a nonce approval token -> NO inject (simple execution)
    2. If prompt has substantial new information (>100 words) -> YES inject
    3. If prompt mentions new resources/scope -> YES inject
    4. Default: NO inject (trust existing context)

    Args:
        parameters: Task tool parameters with 'prompt' key

    Returns:
        True if context should be injected, False otherwise
    """
    prompt = parameters.get("prompt", "")

    # Case 1: Any approval-related prompt - NO injection needed.
    classification = classify_resume_prompt(prompt)
    if classification != "standard":
        logger.debug("Resume with %s prompt - skipping context injection", classification)
        return False

    # Case 2: Substantial new information - YES inject
    # If user is providing a lot of new context, we should refresh
    word_count = len(prompt.split())
    if word_count > 100:
        logger.info(f"Resume with substantial new info ({word_count} words) - injecting context")
        return True

    # Case 3: New scope/resources mentioned - YES inject
    # These words indicate the user is expanding the task scope
    prompt_lower = prompt.lower()
    scope_expansion_indicators = [
        "also",
        "additionally",
        "another",
        "new ",
        "different",
        "and also",
        "as well as",
        "in addition",
        "plus",
        "moreover",
        "furthermore",
        "besides that"
    ]
    if any(indicator in prompt_lower for indicator in scope_expansion_indicators):
        logger.info("Resume with scope expansion - injecting context")
        return True

    # Default: Trust existing context
    logger.debug("Standard resume - skipping context injection")
    return False


def check_pending_updates_threshold() -> str:
    """
    Check if pending updates count exceeds threshold and return warning text.

    Returns warning string to inject into prompt, or empty string if below threshold.
    Must NEVER block or slow down context injection (target: <50ms).
    """
    try:
        threshold = int(os.environ.get("PENDING_UPDATE_THRESHOLD", "10"))

        # Fast path: try to read index directly (no module import)
        index_path = Path(".claude/project-context/pending-updates/pending-index.json")
        if not index_path.exists():
            return ""

        with open(index_path, 'r') as f:
            index_data = json.load(f)

        pending_count = index_data.get("pending_count", 0)
        if pending_count < threshold:
            return ""

        logger.info(f"Pending updates threshold reached: {pending_count} >= {threshold}")
        return (
            f"\n# Pending Context Updates Warning\n"
            f"There are {pending_count} pending context update suggestions awaiting review. "
            f"Run `gaia-review` or `python3 tools/review/review_engine.py list` to review them.\n\n"
        )

    except Exception as e:
        logger.debug(f"Pending updates check failed (non-fatal): {e}")
        return ""


def consume_anomaly_flag(enriched_prompt: str) -> str:
    """Read and delete the needs_analysis.flag if it exists, appending a warning.

    The flag is created by subagent_stop.py when workflow anomalies are
    detected.  Reading it once and deleting ensures the warning is shown
    exactly once.  Must not slow down context injection -- returns
    immediately if the file does not exist.

    TTL enforcement: flags older than 1 hour (by created_at or file mtime)
    are auto-expired and deleted without injecting a warning.
    """
    flag_path = Path(".claude/project-context/workflow-episodic-memory/signals/needs_analysis.flag")
    if not flag_path.exists():
        return enriched_prompt
    try:
        signal_data = json.loads(flag_path.read_text())

        # TTL check: auto-expire flags older than ttl_hours (default 1 hour)
        ttl_hours = signal_data.get("ttl_hours", 1)
        ttl_seconds = ttl_hours * 3600
        created_at = signal_data.get("created_at") or signal_data.get("timestamp")
        if created_at:
            created_dt = datetime.fromisoformat(created_at)
            age_seconds = (datetime.now() - created_dt).total_seconds()
            if age_seconds > ttl_seconds:
                flag_path.unlink()
                logger.info(
                    "Auto-expired anomaly flag (age: %.0fs, ttl: %ds)",
                    age_seconds, ttl_seconds,
                )
                return enriched_prompt
        else:
            # Fallback: check file modification time
            mtime = flag_path.stat().st_mtime
            age_seconds = datetime.now().timestamp() - mtime
            if age_seconds > ttl_seconds:
                flag_path.unlink()
                logger.info(
                    "Auto-expired anomaly flag by mtime (age: %.0fs, ttl: %ds)",
                    age_seconds, ttl_seconds,
                )
                return enriched_prompt

        anomalies = signal_data.get("anomalies", [])
        summary = "; ".join(a.get("message", "") for a in anomalies if a.get("message"))
        if summary:
            enriched_prompt += (
                f"\n# Anomaly Alert\n"
                f"Recent anomalies detected: {summary}. "
                f"Consider investigating with /gaia.\n"
            )
        flag_path.unlink()
        logger.info("Consumed anomaly flag and injected warning")
    except Exception as e:
        logger.debug(f"Failed to consume anomaly flag (non-fatal): {e}")
    return enriched_prompt


def inject_project_context(
    parameters: dict,
    project_agents: list,
    hooks_dir: Path = None,
) -> dict:
    """
    Inject project context for project agents.

    Automatically provisions context from project-context.json for agents that need it.
    This makes the orchestrator lightweight - it only routes, the hook injects context.

    Args:
        parameters: Original Task tool parameters
        project_agents: List of valid project agent names.
        hooks_dir: Path to the hooks directory (for fallback paths).
            Defaults to Path(__file__).parent.parent.parent if None.

    Returns:
        Modified parameters with context injected into prompt
    """
    if hooks_dir is None:
        hooks_dir = Path(__file__).parent.parent.parent

    subagent_type = parameters.get("subagent_type", "")

    # Only inject for project agents (not for generic agents like Explore, general-purpose, etc.)
    if subagent_type not in project_agents:
        logger.debug(f"Skipping context injection for non-project agent: {subagent_type}")
        return parameters

    # Conditional context injection for resume operations (Phase 4 enhancement)
    # By default, skip injection for resume (context from phase 1)
    # But inject if: new info (>100 words), scope expansion, or new resources
    if parameters.get("resume"):
        if should_inject_on_resume(parameters):
            logger.info(f"Resume with new context detected, injecting for: {parameters.get('resume')}")
            # Continue to context injection below
        else:
            logger.debug(f"Standard resume, skipping context injection: {parameters.get('resume')}")
            return parameters

    prompt = parameters.get("prompt", "")
    if not prompt:
        logger.warning(f"No prompt provided for {subagent_type}, skipping context injection")
        return parameters

    try:
        # Find context_provider.py
        context_provider_paths = [
            Path(".claude/tools/context/context_provider.py"),
            Path("node_modules/@jaguilar87/gaia-ops/tools/context/context_provider.py"),
            hooks_dir.parent / "tools" / "context" / "context_provider.py"
        ]

        context_provider = None
        for path in context_provider_paths:
            if path.exists():
                context_provider = path
                break

        if not context_provider:
            logger.warning("context_provider.py not found, skipping context injection")
            return parameters

        # Execute context_provider.py to get filtered context
        logger.info(f"Injecting context for {subagent_type}...")
        result = subprocess.run(
            ["python3", str(context_provider), subagent_type, prompt],
            capture_output=True,
            text=True,
            timeout=15,
            cwd=os.getcwd()
        )

        if result.returncode != 0:
            logger.error(f"context_provider.py failed: {result.stderr}")
            # Don't block - let agent proceed without context
            return parameters

        # Parse context JSON
        try:
            context_payload = json.loads(result.stdout)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse context JSON: {e}")
            return parameters

        # Check pending update count (non-blocking, fast path)
        pending_warning = check_pending_updates_threshold()

        # Build context update reminder for empty writable sections
        update_reminder = build_context_update_reminder(
            subagent_type, project_agents, hooks_dir
        )

        # Inject context into prompt (skills are injected natively by Claude Code
        # via the 'skills:' field in the agent's frontmatter)
        enriched_prompt = f"""# Project Context (Auto-Injected)

{json.dumps(context_payload, indent=2)}

{pending_warning}---
{update_reminder}
# User Task

{prompt}
"""

        # Modify parameters
        parameters["prompt"] = enriched_prompt

        # Add metadata for TaskValidator to know the original user task
        # This prevents T3 keyword detection in injected context
        parameters["_original_user_task"] = prompt

        # Check for anomaly signal flag created by subagent_stop.py
        enriched_prompt = consume_anomaly_flag(enriched_prompt)
        parameters["prompt"] = enriched_prompt

        sections_count = len(context_payload.get("contract", {}))
        rules_count = context_payload.get("metadata", {}).get("rules_count", 0)

        logger.info(
            f"Context injected for {subagent_type} "
            f"(sections={sections_count}, rules={rules_count})"
        )

        return parameters

    except subprocess.TimeoutExpired:
        logger.error("context_provider.py timed out (15s)")
        return parameters
    except Exception as e:
        logger.error(f"Error injecting context: {e}", exc_info=True)
        return parameters
