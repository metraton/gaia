"""
gaia session -- Inspect what a session receives, and record where it resumes.

Subcommands:
  session preview                Print the session birth block verbatim.
  session snapshot               Print one session's open contracts, pending
                                 signatures, active task and resume point.
  session resume-point set       Replace a session's resume point.

`preview` calls build_session_context() (hooks/modules/session/
session_manifest.py), the same assembler the SessionStart hook delivers, in
the current folder and without recording anything: the hook asks it to count
the user rows' injection, the preview does not, so it writes nothing to the
database. The notices only a real session start produces (a database upgrade
it just ran) are absent from it.
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
    """Handle `gaia session preview`: print the birth block for this folder, writing nothing."""
    from modules.session.session_manifest import build_session_context

    text = build_session_context()
    print(text if text else "(empty -- every block returned nothing)")
    return 0


def _cmd_snapshot(args) -> int:
    """Handle `gaia session snapshot`: print one session's snapshot, writing nothing."""
    import json

    from gaia.session_snapshot import build_snapshot, render_snapshot

    snapshot = build_snapshot(args.session_id)
    print(json.dumps(snapshot, indent=2) if args.json else render_snapshot(snapshot))
    return 0


def _cmd_resume_point_set(args) -> int:
    """Handle `gaia session resume-point set`: record the session's resume point."""
    from gaia.session_snapshot import write_resume_point

    try:
        path = write_resume_point(args.session_id, args.text)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"resume point written: {path}")
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
        help="Inspect what a session receives; record its resume point",
        description=(
            "Inspect what a session receives, and record where it resumes.\n\n"
            "preview: print the session birth block a session opened in this\n"
            "  folder would receive -- projects, environment, the user and their\n"
            "  preferences. Writes nothing: no telemetry, no database change.\n"
            "  Notices only a real session start produces (a database upgrade\n"
            "  it just ran) are not shown.\n\n"
            "snapshot: print one session's open contracts, pending signatures,\n"
            "  active brief/plan/task and resume point -- what a compaction\n"
            "  delivers. Writes nothing.\n\n"
            "resume-point set: replace the session's resume point (one small file\n"
            "  under the Gaia data directory; older ones are pruned)."
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

    preview_p = actions.add_parser("preview", help="Print the session birth block; writes nothing")
    preview_p.set_defaults(func=_cmd_preview)

    snapshot_p = actions.add_parser(
        "snapshot", help="Print one session's open contracts, signatures, active task and resume point; read-only"
    )
    snapshot_p.add_argument("--session-id", required=True, help="Session whose snapshot to print")
    snapshot_p.add_argument("--json", action="store_true", help="Emit JSON output")
    snapshot_p.set_defaults(func=_cmd_snapshot)

    resume_p = actions.add_parser("resume-point", help="Record where a session resumes after compaction")
    resume_actions = resume_p.add_subparsers(dest="resume_action", metavar="<action>", required=True)
    resume_set_p = resume_actions.add_parser("set", help="Replace the session's resume point")
    resume_set_p.add_argument("--session-id", required=True, help="Session the resume point belongs to")
    resume_set_p.add_argument("--text", required=True, help="Where the work stands and what comes next")
    resume_set_p.set_defaults(func=_cmd_resume_point_set)
