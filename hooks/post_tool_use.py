#!/usr/bin/env python3
"""
Post-tool use hook - Thin gate.

Architecture:
- Uses adapter layer to parse and process the full PostToolUse lifecycle
- All business logic lives in ClaudeCodeAdapter.adapt_post_tool_use()
- This file is stdin/stdout glue only
"""

import sys
import json
import logging
from pathlib import Path

_hooks_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(_hooks_dir))
_pkg_root = str(_hooks_dir.parent)
if _pkg_root not in sys.path:
    sys.path.insert(0, _pkg_root)

from modules.core.logging_setup import configure_hook_logging
from adapters.registry import get_adapter
from modules.core.hook_entry import run_hook

# Configure logging -- file handler only when GAIA_DEBUG is set; no
# hooks-*.log is written by default (see modules.core.logging_setup).
configure_hook_logging("post_tool_use")
logger = logging.getLogger(__name__)


def _handle_post_tool_use(event) -> None:
    """Process a PostToolUse event.

    Delegates all business logic to the adapter.

    Args:
        event: Parsed HookEvent from the adapter layer.
    """
    adapter = get_adapter()
    response = adapter.adapt_post_tool_use(event)

    if response.output:
        print(json.dumps(response.output))
    sys.exit(response.exit_code)


# ============================================================================
# STDIN HANDLER (Claude Code integration)
# ============================================================================

if __name__ == "__main__":
    run_hook(_handle_post_tool_use, hook_name="post_tool_use")
