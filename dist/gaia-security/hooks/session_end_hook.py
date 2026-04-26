#!/usr/bin/env python3
"""
SessionEnd hook for Claude Code Agent System.

Fires when a Claude Code session terminates. Unregisters the session from
the user-scoped session registry so that T12/T13 liveness filters stop
considering it live.

Architecture:
- Reads SessionEnd event via the shared run_hook() entrypoint
- Reads CLAUDE_SESSION_ID from environment
- Calls session_registry.unregister_session() guarded by SessionRegistryError
- Failures are non-fatal: a missing registry entry must never block shutdown
- Returns an empty JSON response and exits 0
"""

import os
import sys
import json
import logging
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from modules.core.hook_entry import run_hook
from modules.core.paths import get_logs_dir
from modules.session.session_registry import unregister_session, SessionRegistryError

# Configure logging — file only
_log_file = get_logs_dir() / f"hooks-{datetime.now().strftime('%Y-%m-%d')}.log"
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [session_end] %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler(_log_file)],
)
logger = logging.getLogger(__name__)


def _handle_session_end(event) -> None:
    """Process a SessionEnd event.

    Unregisters the session from the session registry. Non-fatal: "session
    not found" is already a silent no-op inside the registry; SessionRegistryError
    here only signals I/O failure, which is expected in shutdown race conditions.

    Args:
        event: Parsed HookEvent from the adapter layer.
    """
    try:
        _sid = os.environ.get("CLAUDE_SESSION_ID")
        if _sid:
            unregister_session(session_id=_sid)
            logger.info("SessionEnd: unregistered session %s", _sid)
    except SessionRegistryError as _reg_exc:
        logger.debug("session_registry unregister failed (non-fatal): %s", _reg_exc)

    print(json.dumps({}))
    sys.exit(0)


# ============================================================================
# STDIN HANDLER (Claude Code integration)
# ============================================================================

def main() -> None:
    """Module-level entrypoint used by tests and by the ``__main__`` block.

    Delegates to ``run_hook()`` exactly like the inline ``__main__`` body
    would, but via a named function so tests can import this module and
    invoke the handler without spawning a subprocess.
    """
    run_hook(_handle_session_end, hook_name="session_end_hook")


if __name__ == "__main__":
    main()
