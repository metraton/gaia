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
from pathlib import Path

_hooks_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(_hooks_dir))
_pkg_root = str(_hooks_dir.parent)
if _pkg_root not in sys.path:
    sys.path.insert(0, _pkg_root)

from modules.core.hook_entry import run_hook
from modules.core.logging_setup import configure_hook_logging
from modules.session.session_registry import unregister_session, SessionRegistryError

# Configure logging -- file handler only when GAIA_DEBUG is set; no
# hooks-*.log is written by default (see modules.core.logging_setup).
configure_hook_logging("session_end")
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
