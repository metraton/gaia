#!/usr/bin/env python3
"""PostCompact hook — re-injects compact context after conversation compaction."""

import sys
import json
import logging
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from modules.core.hook_entry import run_hook
from modules.core.paths import get_logs_dir
from modules.context.compact_context_builder import build_compact_context

# Configure logging
_log_file = get_logs_dir() / f"hooks-{datetime.now().strftime('%Y-%m-%d')}.log"
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [post_compact] %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler(_log_file)],
)
logger = logging.getLogger(__name__)


def _handle_post_compact(event) -> None:
    """Re-inject compact context after compaction."""
    context = build_compact_context()

    logger.info("PostCompact: injecting %d chars of context", len(context))

    response = {
        "hookSpecificOutput": {
            "additionalContext": context,
        }
    }

    print(json.dumps(response))
    sys.exit(0)


if __name__ == "__main__":
    run_hook(_handle_post_compact, hook_name="post_compact")
