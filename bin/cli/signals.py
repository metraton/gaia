"""gaia signals -- the curated changes other sessions made, each with the command that opens it."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def register(subparsers) -> None:
    """Register the `signals` subcommand with the root parser."""
    p = subparsers.add_parser(
        "signals",
        help="Curated changes other sessions made (the rows behind a '~ changed:' line)",
        description=(
            "List the curated memory changes other sessions made, newest first, each with "
            "the command that opens the changed row. By default it starts where the last "
            "'~ changed:' line of this session started. Listing never moves the cursor."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  gaia signals\n"
            "  gaia signals --since 0 --limit 10\n"
            "  gaia signals --json\n"
        ),
    )
    p.add_argument("--since", type=int, default=None, metavar="ID",
                   help="List signals with an id above ID instead of the last announced range.")
    p.add_argument("--limit", type=int, default=50, help="Maximum rows to list (default: 50).")
    p.add_argument("--json", action="store_true", default=False, help="Emit the rows as JSON.")


def cmd_signals(args) -> int:
    """Dispatch handler for `gaia signals`."""
    from gaia.agent_identity import host_session_from_env
    from gaia.signals import recent

    rows = recent(host_session_from_env(), since=args.since, limit=args.limit)
    if getattr(args, "json", False):
        print(json.dumps(rows))
        return 0
    if not rows:
        print("No signals from other sessions.")
        return 0
    for row in rows:
        print(f"#{row['id']} {row['ts']} {row['label']} {row['op']} {row['ref']} -> {row['open']}")
    return 0
