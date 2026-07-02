#!/usr/bin/env python3
"""UserPromptSubmit hook — injects routing recommendations, first-run welcome, and agentic-loop resume context."""

import os
import sys
import json
import logging
from datetime import datetime
from pathlib import Path

_hooks_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(_hooks_dir))
_pkg_root = str(_hooks_dir.parent)
if _pkg_root not in sys.path:
    sys.path.insert(0, _pkg_root)

from modules.core.paths import get_logs_dir
from modules.core.stdin import has_stdin_data
from modules.core.plugin_setup import run_first_time_setup

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


def _detect_install_method() -> str:
    """Detect how Gaia was installed: "npm" or "plugin".

    The install method -- NOT the runtime mode -- decides how Gaia's security
    hooks activate, so the first-run welcome can give accurate guidance:

    - npm/pnpm install: setup_project_hooks() merges the hooks into
      .claude/settings.local.json. Claude Code's settings file-watcher applies
      changes to that file automatically, so protection takes effect without a
      restart.
    - plugin install (marketplace / --plugin-dir): Claude Code reads hooks from
      the plugin's own hooks.json, which is only re-read on /reload-plugins or
      a restart.

    Detection, most reliable first:
      1. plugin-registry.json "source" -- the determination persisted by
         ensure_plugin_registry() at first setup ("npm-mode" / "plugin-mode").
         run_first_time_setup() runs before the welcome, so the registry
         normally already exists here.
      2. CLAUDE_PLUGIN_ROOT env var -- set by Claude Code only when launching
         from a plugin root, so its presence means a plugin install.
      3. node_modules in this module's resolved path -- npm/pnpm layout.
      4. Default "plugin": the safer guidance, since "/reload-plugins (or
         restart)" is harmless in both cases, whereas telling an npm user it
         "applies automatically" would be wrong if it does not.
    """
    # 1. Persisted determination from the registry.
    try:
        from modules.core.paths import get_plugin_data_dir
        registry_path = get_plugin_data_dir() / "plugin-registry.json"
        if registry_path.exists():
            source = json.loads(registry_path.read_text()).get("source", "")
            if source == "npm-mode":
                return "npm"
            if source == "plugin-mode":
                return "plugin"
    except Exception as _reg_exc:
        logger.debug("install-method registry read failed (non-fatal): %s", _reg_exc)

    # 2. CLAUDE_PLUGIN_ROOT -> plugin install.
    if os.environ.get("CLAUDE_PLUGIN_ROOT", "").strip():
        return "plugin"

    # 3. node_modules in module path -> npm/pnpm install.
    if "node_modules" in Path(__file__).resolve().parts:
        return "npm"

    # 4. Conservative default.
    return "plugin"


def _build_welcome() -> str:
    """Build the first-run welcome message for the user.

    This message is injected as additionalContext so Claude receives it,
    not the user directly.  The framing explains WHY the user needs to
    know — Claude should naturally relay it because it is actionable.

    The activation guidance branches on the INSTALL METHOD (see
    _detect_install_method), because that -- not the runtime mode -- is what
    determines whether the security hooks activate on their own or need a
    reload/restart. The product is a single unified plugin, always named
    "Gaia" to the user (the ops/security runtime mode is an internal detail,
    not a product name).
    """
    if _detect_install_method() == "npm":
        return (
            "Gaia was just installed for the first time in this project. "
            "Gaia configured its security protections — destructive-command "
            "blocking and approval gating for mutative operations — by merging "
            "them into .claude/settings.local.json. Because this is an npm/pnpm "
            "install, Claude Code's settings watcher applies those changes "
            "automatically, so protection takes effect without restarting. "
            "Tell the user that Gaia is installed and its protections apply on "
            "their own; they can confirm by running /hooks and checking that "
            "Gaia's entries are listed. Only if /hooks does not show Gaia's "
            "entries after a few seconds should the user restart Claude Code as "
            "a fallback."
        )

    # Plugin install (or unknown -> conservative reload guidance).
    return (
        "Gaia was just installed for the first time in this project. "
        "Gaia configured its security protections — destructive-command "
        "blocking and approval gating for mutative operations — but because "
        "this is a plugin install, Claude Code only picks them up after "
        "reloading plugins. Until then, Gaia cannot protect the user's "
        "commands: destructive operations will not be blocked and mutative "
        "operations will not require approval. Tell the user that Gaia is "
        "installed and that they need to run /reload-plugins (or restart "
        "Claude Code) to activate its security protections, and that until "
        "they do, protections are not active."
    )


if __name__ == "__main__":
    if not has_stdin_data():
        sys.exit(0)

    try:
        raw_input = sys.stdin.read()

        # Parse the event JSON once so subsequent helpers can read fields
        # (session_id, prompt). Defensive: an unreadable payload becomes
        # an empty dict so the rest of the hook still runs.
        try:
            event_data = json.loads(raw_input) if raw_input else {}
            if not isinstance(event_data, dict):
                event_data = {}
        except (json.JSONDecodeError, TypeError):
            event_data = {}

        # Refresh liveness heartbeat for this session. Throttled inside
        # touch_session(), fully non-fatal. The session_id must come from
        # the stdin event because CLAUDE_SESSION_ID is not guaranteed to
        # be exported into the hook subprocess.
        try:
            from modules.session.session_registry import touch_session
            from modules.core.state import resolve_session_id
            touch_session(resolve_session_id(event_data))
        except Exception as _hb_exc:
            logger.debug("touch_session failed (non-fatal): %s", _hb_exc)

        # Check first-run BEFORE setup (SessionStart does setup with
        # mark_done=False so the marker doesn't exist yet on first run).
        from modules.core.plugin_setup import is_first_run, mark_initialized
        first_run = is_first_run()

        # Ensure registry + permissions exist (idempotent, no mark).
        setup_msg = run_first_time_setup(mark_done=False)

        # Build additionalContext: welcome + routing.
        # Identity now lives in agents/gaia-orchestrator.md (agent definition).
        # Agentic-loop resume and pending approvals moved to SessionStart
        # via session_manifest (Phase 4) -- they are session-scoped, not
        # turn-scoped, so re-evaluating on every prompt added noise without
        # changing the answer.
        context_parts = []

        # First-time welcome: the marker does not exist yet because
        # neither SessionStart nor this call marked it.
        if first_run:
            welcome = _build_welcome()
            context_parts.append(welcome)
            mark_initialized()  # Mark AFTER building the welcome
            logger.info("First-run welcome prepended")

        # Append deterministic surface routing recommendation.
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
        logger.info("Context injected (%d chars)", len(additional_context))

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
