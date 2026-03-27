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
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))

from modules.core.paths import get_logs_dir
from adapters.claude_code import ClaudeCodeAdapter
from modules.core.hook_entry import run_hook

# Configure logging
log_file = get_logs_dir() / f"hooks-{datetime.now().strftime('%Y-%m-%d')}.log"
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [post_tool_use] %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler(log_file)],
)
logger = logging.getLogger(__name__)


def _handle_post_tool_use(event) -> None:
    """Process a PostToolUse event.

    Delegates all business logic to the adapter.

    Args:
        event: Parsed HookEvent from the adapter layer.
    """
    adapter = ClaudeCodeAdapter()
    response = adapter.adapt_post_tool_use(event)

    if response.output:
        print(json.dumps(response.output))
    sys.exit(response.exit_code)


# ============================================================================
# STDIN HANDLER (Claude Code integration)
# ============================================================================

if __name__ == "__main__":
    run_hook(_handle_post_tool_use, hook_name="post_tool_use")
