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


if __name__ == "__main__":
    if not has_stdin_data():
        sys.exit(0)

    try:
        sys.stdin.read()
        # Ensure registry + permissions exist (fallback if SessionStart didn't fire)
        setup_msg = run_first_time_setup()
        identity = build_identity()

        # If setup wrote new permissions, tell user to reload
        if setup_msg:
            identity += f"\n\nIMPORTANT: {setup_msg}"
            logger.info("Setup message appended: %s", setup_msg)

        logger.info("Identity injected: %s mode (%d chars)", "ops" if "Orchestrator" in identity else "security", len(identity))

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
