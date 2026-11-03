#!/usr/bin/env python3
"""PreCompact hook — opens the compaction bracket, writes the session ledger, and returns an empty response.

Claude Code neither validates nor consumes ``hookSpecificOutput`` for
PreCompact, so this hook has no way to inject model context (see
``_handle_pre_compact``). It writes the session ledger (``gaia.session_ledger``)
that a compact, resume or fork SessionStart renders instead.

All errors are caught and logged — this hook never blocks compaction.
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

from modules.core.hook_entry import run_hook
from modules.core.logging_setup import configure_hook_logging

configure_hook_logging("pre_compact")
logger = logging.getLogger(__name__)


def _handle_pre_compact(event) -> None:
    """Open the compaction bracket, write the session ledger, answer ``{}``.

    Claude Code's hook-output schema has no ``hookSpecificOutput`` shape for
    PreCompact, so a ``hookEventName == "PreCompact"`` payload fails its JSON
    validation and ``additionalContext`` could not be applied anyway. The
    post-compaction refresh happens at SessionStart instead.
    """
    logger.info("PreCompact: no deliverable payload for this event, returning {}")
    try:
        from modules.session.compaction_bracket import open_bracket

        open_bracket(event.session_id)
    except Exception:
        logger.warning("PreCompact: compaction bracket not recorded", exc_info=True)
    try:
        from gaia.session_ledger import write_ledger

        write_ledger(event.session_id)
    except Exception:
        logger.warning("PreCompact: session ledger not written", exc_info=True)

    print(json.dumps({}))
    sys.exit(0)


if __name__ == "__main__":
    run_hook(_handle_pre_compact, hook_name="pre_compact")
