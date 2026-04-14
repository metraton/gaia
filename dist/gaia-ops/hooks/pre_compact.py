#!/usr/bin/env python3
"""PreCompact hook — injects agentic-loop checkpoint instructions before context compaction.

When an agentic-loop is active, the agent needs to save its state before
compaction wipes context.  This hook detects the loop and injects a prompt
telling the agent to write continue.md + update state.json + worklog.md.

If no loop is active, this hook is a no-op (returns empty additionalContext).
All errors are caught and logged — this hook never blocks compaction.
"""

import sys
import json
import logging
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from modules.core.hook_entry import run_hook
from modules.core.paths import get_logs_dir
from modules.core.plugin_mode import is_ops_mode

# Configure logging
_log_file = get_logs_dir() / f"hooks-{datetime.now().strftime('%Y-%m-%d')}.log"
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [pre_compact] %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler(_log_file)],
)
logger = logging.getLogger(__name__)


def _handle_pre_compact(event) -> None:
    """Inject agentic-loop checkpoint instructions before compaction."""
    context = ""

    if is_ops_mode():
        try:
            from modules.context.agentic_loop_detector import build_precompact_prompt
            context = build_precompact_prompt()
            if context:
                logger.info("PreCompact: injecting agentic-loop checkpoint prompt (%d chars)", len(context))
            else:
                logger.info("PreCompact: no active agentic loop, skipping")
        except Exception as e:
            logger.debug("PreCompact: agentic-loop detection failed (non-fatal): %s", e)

    response = {
        "hookSpecificOutput": {
            "additionalContext": context,
        }
    }

    print(json.dumps(response))
    sys.exit(0)


if __name__ == "__main__":
    run_hook(_handle_pre_compact, hook_name="pre_compact")
