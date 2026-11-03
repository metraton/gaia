#!/usr/bin/env python3
"""PostCompact hook — closes the session's compaction bracket; re-injection happens elsewhere.

The event input carries the ``compact_summary`` the compaction produced, which
this hook does not read. Claude Code does not accept ``hookSpecificOutput`` for
PostCompact (see pre_compact.py), so ``additionalContext`` cannot reach the
model from this event and the hook answers ``{}``.

The refresh is delivered by ``hooks/session_start.py``, which hooks.json wires
for ``startup|resume|clear|compact|fork`` and which injects context for every
one of them.
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

configure_hook_logging("post_compact")
logger = logging.getLogger(__name__)


def _handle_post_compact(event) -> None:
    """Log that compaction finished and close the compaction bracket."""
    logger.info(
        "PostCompact fired (event has no additionalContext support in "
        "Claude Code); compact-context refresh is delivered via "
        "SessionStart(source=compact) instead -- see session_start.py"
    )

    try:
        from modules.session.compaction_bracket import close_bracket

        close_bracket(event.session_id)
    except Exception:
        logger.warning("PostCompact: compaction bracket not closed", exc_info=True)
    print(json.dumps({}))
    sys.exit(0)


if __name__ == "__main__":
    run_hook(_handle_post_compact, hook_name="post_compact")
