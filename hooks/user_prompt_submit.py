#!/usr/bin/env python3
"""UserPromptSubmit hook — injects dynamic identity based on installed plugins."""

import sys
import json
import logging
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from modules.core.paths import get_logs_dir
from modules.core.stdin import has_stdin_data
from modules.core.plugin_setup import run_first_time_setup
from modules.identity.identity_provider import build_identity

# Configure logging — file only, no stderr
_log_file = get_logs_dir() / f"hooks-{datetime.now().strftime('%Y-%m-%d')}.log"
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [user_prompt_submit] %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler(_log_file)],
)
logger = logging.getLogger(__name__)


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
        sys.stdin.read()

        # Check first-run BEFORE setup (SessionStart does setup with
        # mark_done=False so the marker doesn't exist yet on first run).
        from modules.core.plugin_setup import is_first_run, mark_initialized
        first_run = is_first_run()

        # Ensure registry + permissions exist (idempotent, no mark).
        setup_msg = run_first_time_setup(mark_done=False)
        identity = build_identity()
        mode = "ops" if "Orchestrator" in identity else "security"

        # First-time welcome: the marker does not exist yet because
        # neither SessionStart nor this call marked it.
        if first_run:
            welcome = _build_welcome(mode)
            identity = f"{welcome}\n\n{identity}"
            mark_initialized()  # Mark AFTER building the welcome
            logger.info("First-run welcome prepended for %s mode", mode)

        logger.info("Identity injected: %s mode (%d chars)", mode, len(identity))

        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": identity,
            }
        }))
        sys.exit(0)

    except Exception as e:
        logger.error("Error in user_prompt_submit: %s", e, exc_info=True)
        sys.exit(0)
