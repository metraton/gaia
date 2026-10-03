"""gaia now -- the machine's current local time, UTC offset and zone; it opens no database."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def register(subparsers) -> None:
    """Register the `now` subcommand with the root parser."""
    p = subparsers.add_parser(
        "now",
        help="Current local time, UTC offset and zone (read this before an absolute --at)",
        description="Print the machine's current local time, UTC offset and zone name.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "The zone falls back to the offset when no zone name resolves.\n\n"
            "Examples:\n"
            "  gaia now\n"
            "  gaia now --json\n"
        ),
    )
    p.add_argument("--json", action="store_true", default=False,
                   help="Emit local, offset, zone and utc as JSON.")


def cmd_now(args) -> int:
    """Dispatch handler for `gaia now`."""
    from gaia import notifications_time as clock

    instant = clock.now_utc()
    if getattr(args, "json", False):
        print(json.dumps(clock.clock_reading(instant)))
    else:
        print(clock.clock_line(instant))
    return 0
