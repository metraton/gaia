#!/usr/bin/env python3
"""UserPromptSubmit hook — injects dynamic identity based on installed plugins."""

import sys
import json
import logging
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from modules.core.hook_entry import run_hook
from modules.core.paths import get_logs_dir
from modules.identity.identity_provider import build_identity

# Configure logging
_log_file = get_logs_dir() / f"hooks-{datetime.now().strftime('%Y-%m-%d')}.log"
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [user_prompt_submit] %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler(_log_file)],
)
logger = logging.getLogger(__name__)


def _handle_user_prompt_submit(event) -> None:
    """Inject identity context on each user prompt."""
    identity = build_identity()

    response = {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": identity,
        }
    }

    print(json.dumps(response))
    sys.exit(0)


if __name__ == "__main__":
    run_hook(_handle_user_prompt_submit, hook_name="user_prompt_submit")
