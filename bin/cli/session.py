"""
gaia session -- Inspect what a new session would receive at SessionStart.

Subcommands:
  session preview   Print build_session_context()'s output verbatim.

build_session_context() (hooks/modules/session/session_manifest.py) is a
pure read-only function: its only caller in production is the SessionStart
hook, so before this command existed the only way to see the effect of a
change to it was to close and reopen a session. `preview` calls the same
builder in-process, read-only, with no injection side effects (no session
bookkeeping, no telemetry) -- it exists purely to shorten the edit/verify
loop on the manifest builders.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure the gaia package (repo root) is importable regardless of cwd.
_PACKAGE_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_ROOT))

# The manifest builder's own modules resolve as `modules.*` with sibling
# top-level `adapters` imports (see hooks/session_start.py), which requires
# hooks/ itself -- not just the repo root -- on sys.path.
_HOOKS_DIR = _PACKAGE_ROOT / "hooks"
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))


def _cmd_preview(args) -> int:
    """Handle `gaia session preview`: render the SessionStart manifest now."""
    from modules.session.session_manifest import build_session_context

    text = build_session_context()
    print(text if text else "(empty -- every block returned nothing)")
    return 0


def cmd_session(args) -> int:
    """Top-level dispatcher for `gaia session [<action>]`."""
    func = getattr(args, "func", None)
    if func is None:
        args._session_parser.print_help()
        return 1
    return func(args) or 0


def register(subparsers):
    """Register the session subcommand with nested actions."""
    session_parser = subparsers.add_parser(
        "session",
        help="Inspect SessionStart injection content -- read-only",
        description=(
            "Inspect what a new session would receive at SessionStart.\n\n"
            "preview: print build_session_context()'s output verbatim, with\n"
            "  no injection side effects (no telemetry, no DB writes)."
        ),
    )
    # No `func=None` default here: an explicit default would shadow the
    # `cmd_<stem>` fallback that call-graph tooling (e.g. the package-
    # lifecycle-spawn invariant test) reads off `parser._defaults.get("func",
    # fallback)` for a chain with no subparser match of its own. Leaving the
    # key unset means the bare `gaia session` chain still resolves to
    # `cmd_session`, and `getattr(args, "func", None)` below is unaffected --
    # it already tolerates a missing attribute the same way it tolerates one
    # explicitly set to None.
    session_parser.set_defaults(_session_parser=session_parser)

    actions = session_parser.add_subparsers(dest="session_action", metavar="<action>")

    preview_p = actions.add_parser("preview", help="Print the SessionStart manifest text")
    preview_p.set_defaults(func=_cmd_preview)
