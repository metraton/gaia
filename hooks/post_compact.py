#!/usr/bin/env python3
"""PostCompact hook — logging stub; real re-injection happens elsewhere.

PLATFORM LIMITATION: Claude Code's hook-output schema does not accept
``hookSpecificOutput.hookEventName == "PostCompact"`` (same discriminated
union as PreCompact -- see pre_compact.py's docstring for the full list of
accepted values), and the runtime's response-consumption switch has no
``"PostCompact"`` case either, so ``additionalContext`` is unreachable for
this event even when the JSON is otherwise well-formed. The previous
version of this hook built the compact-context refresh (agent roster +
active anomalies) and shipped it under this unsupported shape, so every
``/compact`` failed Claude Code's JSON validation with "(root): Invalid
input" and the refresh was silently dropped -- never delivered.

The real, valid delivery mechanism is ``SessionStart`` with
``source == "compact"``: Claude Code's SessionStart matcherMetadata lists
``compact`` as one of its accepted `source` values (alongside `startup`,
`resume`, `clear`, `fork`), and SessionStart's `hookSpecificOutput` DOES
support `additionalContext`. ``hooks/session_start.py`` is now wired for
``startup|resume|compact`` and builds the SAME compact-context refresh
(via ``modules.context.compact_context_builder.build_compact_context``)
when it fires with ``source == "compact"``. This file stays registered
for the ``PostCompact`` event as a harmless, schema-valid no-op (parallel
to pre_compact.py) purely for observability -- it no longer calls
``build_compact_context()`` itself, to avoid a second, discarded read of
the same DB queries on every compaction.
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

# Configure logging -- file handler only when GAIA_DEBUG is set; no
# hooks-*.log is written by default (see modules.core.logging_setup).
configure_hook_logging("post_compact")
logger = logging.getLogger(__name__)


def _handle_post_compact(event) -> None:
    """Log that compaction finished; the real refresh fires via SessionStart."""
    logger.info(
        "PostCompact fired (event has no additionalContext support in "
        "Claude Code); compact-context refresh is delivered via "
        "SessionStart(source=compact) instead -- see session_start.py"
    )

    # No hookSpecificOutput: PostCompact does not accept one. An empty
    # object is the schema-valid "nothing to report" response.
    print(json.dumps({}))
    sys.exit(0)


if __name__ == "__main__":
    run_hook(_handle_post_compact, hook_name="post_compact")
