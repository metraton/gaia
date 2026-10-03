#!/usr/bin/env python3
"""
SessionEnd hook for Claude Code Agent System.

Fires when a Claude Code session terminates. Unregisters the session from
the user-scoped session registry so that T12/T13 liveness filters stop
considering it live.

It reads CLAUDE_SESSION_ID and hands it to session_lifecycle.end_session;
a registry failure never blocks shutdown.
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
from modules.session.session_lifecycle import end_session

# Configure logging -- file handler only when GAIA_DEBUG is set; no
# hooks-*.log is written by default (see modules.core.logging_setup).
configure_hook_logging("session_end")
logger = logging.getLogger(__name__)


def _handle_session_end(event) -> None:
    """Unregister the session CLAUDE_SESSION_ID names and answer with an empty response.

    Args:
        event: Parsed HookEvent from the adapter layer.
    """
    end_session(os.environ.get("CLAUDE_SESSION_ID"))
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
