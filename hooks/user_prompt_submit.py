#!/usr/bin/env python3
"""UserPromptSubmit hook — injects routing recommendations, first-run welcome, and agentic-loop resume context."""

import sys
import json
import logging
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from modules.core.paths import get_logs_dir
from modules.core.stdin import has_stdin_data
from modules.core.plugin_setup import run_first_time_setup
from modules.core.plugin_mode import get_plugin_mode

# Configure logging — file only, no stderr
_log_file = get_logs_dir() / f"hooks-{datetime.now().strftime('%Y-%m-%d')}.log"
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [user_prompt_submit] %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler(_log_file)],
)
logger = logging.getLogger(__name__)


def _extract_user_prompt(raw_input: str) -> str:
    """Extract user prompt text from stdin event.

    The UserPromptSubmit event is JSON with the user's message.
    Try known field names; return empty string if extraction fails.
    """
    try:
        event = json.loads(raw_input)
        # Try known field names from Claude Code hook events
        for field in ("user_message", "prompt", "message", "content"):
            if field in event and isinstance(event[field], str):
                return event[field]
        # Check nested hookEventInput
        hook_input = event.get("hookEventInput", {})
        if isinstance(hook_input, dict):
            for field in ("user_message", "prompt", "message", "content"):
                if field in hook_input and isinstance(hook_input[field], str):
                    return hook_input[field]
    except (json.JSONDecodeError, TypeError, AttributeError):
        pass
    return ""


def _build_routing_recommendation(prompt_text: str) -> str:
    """Run surface classification and format as a routing recommendation block.

    Returns empty string if classification fails or produces no active surfaces.
    This is advisory — never raises exceptions.
    """
    try:
        # Import surface_router from tools/context
        tools_dir = Path(__file__).resolve().parent.parent / "tools" / "context"
        if str(tools_dir) not in sys.path:
            sys.path.insert(0, str(tools_dir))

        from surface_router import classify_surfaces

        routing = classify_surfaces(prompt_text)

        active_surfaces = routing.get("active_surfaces", [])
        if not active_surfaces:
            logger.info("Surface routing: no active surfaces for prompt")
            return ""

        agents = routing.get("recommended_agents", [])
        dispatch_mode = routing.get("dispatch_mode", "single_surface")
        confidence = routing.get("confidence", 0.0)
        matched_signals = routing.get("matched_signals", {})

        # Flatten matched signals into a single list for display
        all_signals = []
        for surface_signals in matched_signals.values():
            all_signals.extend(surface_signals)

        lines = [
            "\n\n## Surface Routing Recommendation",
            f"- Recommended agents: {agents}",
            f"- Dispatch mode: {dispatch_mode}",
            f"- Confidence: {confidence}",
            f"- Matched signals: {json.dumps(all_signals)}",
        ]

        logger.info(
            "Surface routing: agents=%s mode=%s confidence=%.2f signals=%s",
            agents, dispatch_mode, confidence, all_signals,
        )
        return "\n".join(lines)

    except Exception as e:
        logger.warning("Surface routing failed (advisory, skipping): %s", e)
        return ""


def _build_welcome(mode: str) -> str:
    """Build first-run welcome message for the user.

    This message is injected as additionalContext so Claude receives it,
    not the user directly.  The framing explains WHY the user needs to
    know — Claude should naturally relay it because it is actionable
    and the user cannot use the plugin fully without restarting.
    """
    plugin_name = f"GAIA {'Ops' if mode == 'ops' else 'Security'}"

    return (
        f"{plugin_name} was just installed for the first time in this project. "
        f"The plugin detected the installation and configured security permissions, "
        f"but these permissions only take effect after restarting Claude Code. "
        f"Until the user closes this session and reopens Claude Code, "
        f"{plugin_name} cannot protect their commands — destructive operations "
        f"will not be blocked and mutative operations will not require approval. "
        f"The user needs to know this because they just installed {plugin_name} "
        f"expecting it to work, but it will not be active until they restart. "
        f"Tell the user that {plugin_name} is installed, that they need to close "
        f"and reopen Claude Code to activate it, and that until they do, "
        f"security protections are not active."
    )


if __name__ == "__main__":
    if not has_stdin_data():
        sys.exit(0)

    try:
        raw_input = sys.stdin.read()

        # Check first-run BEFORE setup (SessionStart does setup with
        # mark_done=False so the marker doesn't exist yet on first run).
        from modules.core.plugin_setup import is_first_run, mark_initialized
        first_run = is_first_run()

        # Ensure registry + permissions exist (idempotent, no mark).
        setup_msg = run_first_time_setup(mark_done=False)
        mode = get_plugin_mode()

        # Build additionalContext: welcome + agentic-loop + routing.
        # Identity now lives in agents/gaia-orchestrator.md (agent definition).
        context_parts = []

        # First-time welcome: the marker does not exist yet because
        # neither SessionStart nor this call marked it.
        if first_run:
            welcome = _build_welcome(mode)
            context_parts.append(welcome)
            mark_initialized()  # Mark AFTER building the welcome
            logger.info("First-run welcome prepended for %s mode", mode)

        # Detect active agentic-loop and inject resume context (ops mode only).
        # Lightweight: checks file existence + small JSON read, fully fail-safe.
        if mode == "ops":
            try:
                from modules.context.agentic_loop_detector import build_resume_context
                loop_context = build_resume_context()
                if loop_context:
                    context_parts.append(loop_context)
                    logger.info("Agentic loop resume context injected")
            except Exception as e:
                logger.debug("Agentic loop detection skipped (non-fatal): %s", e)

        # Append deterministic surface routing recommendation (ops mode only)
        if mode == "ops":
            prompt_text = _extract_user_prompt(raw_input)

            # NOTE: Approval activation moved to ElicitationResult hook.
            # AskUserQuestion responses trigger ElicitationResult, not
            # UserPromptSubmit, so approval detection lives there now.

            if prompt_text:
                routing_block = _build_routing_recommendation(prompt_text)
                if routing_block:
                    context_parts.append(routing_block)
            else:
                logger.info("Could not extract user prompt from stdin, skipping routing")

        additional_context = "\n\n".join(context_parts)
        logger.info("Context injected: %s mode (%d chars)", mode, len(additional_context))

        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": additional_context,
            }
        }))
        sys.exit(0)

    except Exception as e:
        logger.error("Error in user_prompt_submit: %s", e, exc_info=True)
        sys.exit(0)
