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
    """Build first-run welcome message for the user."""
    plugin_name = f"GAIA {'Ops' if mode == 'ops' else 'Security'}"
    repo_url = "https://github.com/metraton/gaia-ops"
    tiers_info = (
        "Security tiers: T0 (read-only, auto-allowed) → T1 (validation) → "
        "T2 (simulation) → T3 (state change, requires approval) → "
        "T3-blocked (irreversible, permanently blocked)."
    )
    learn_more = (
        f"To learn more about security tiers, ask: 'explain the security tiers'. "
        f"Docs and source: {repo_url}"
    )

    if mode == "ops":
        return (
            f"FIRST-TIME SETUP: {plugin_name} is now active. "
            "Your permissions have been configured to allow GAIA to manage "
            "security classification and agent dispatch. "
            "To activate these permissions, close this session and reopen Claude Code. "
            "After that, GAIA will handle command classification automatically. "
            f"{tiers_info} "
            f"{learn_more} "
            "You must tell the user this message exactly."
        )
    return (
        f"FIRST-TIME SETUP: {plugin_name} is now active. "
        "Your permissions have been configured to delegate security decisions to GAIA. "
        "To activate these permissions, close this session and reopen Claude Code. "
        "After that, GAIA Security will classify every command by risk automatically. "
        f"{tiers_info} "
        f"{learn_more} "
        "You must tell the user this message exactly."
    )


if __name__ == "__main__":
    if not has_stdin_data():
        sys.exit(0)

    try:
        sys.stdin.read()
        from modules.core.plugin_setup import is_first_run
        first_run = is_first_run()

        # Ensure registry + permissions exist
        setup_msg = run_first_time_setup()
        identity = build_identity()
        mode = "ops" if "Orchestrator" in identity else "security"

        # First-time welcome: tell user what happened and what to do
        if first_run and setup_msg:
            welcome = _build_welcome(mode)
            identity += f"\n\n{welcome}"
            logger.info("First-run welcome appended for %s mode", mode)

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
