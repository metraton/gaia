#!/usr/bin/env python3
"""PostCompact hook — re-injects compact context after conversation compaction."""

import sys
import json
import logging
from pathlib import Path

_hooks_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(_hooks_dir))
_pkg_root = str(_hooks_dir.parent)
if _pkg_root not in sys.path:
    sys.path.insert(0, _pkg_root)

from modules.core.hook_entry import run_hook
from modules.core.logging_setup import configure_hook_logging
from modules.context.compact_context_builder import build_compact_context

# Configure logging -- file handler only when GAIA_DEBUG is set; no
# hooks-*.log is written by default (see modules.core.logging_setup).
configure_hook_logging("post_compact")
logger = logging.getLogger(__name__)


def _handle_post_compact(event) -> None:
    """Re-inject compact context after compaction."""
    context = build_compact_context()

    logger.info("PostCompact: injecting %d chars of context", len(context))

    response = {
        "hookSpecificOutput": {
            "hookEventName": "PostCompact",
            "additionalContext": context,
        }
    }

    print(json.dumps(response))
    sys.exit(0)


if __name__ == "__main__":
    run_hook(_handle_post_compact, hook_name="post_compact")
