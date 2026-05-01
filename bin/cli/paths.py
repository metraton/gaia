"""
gaia paths -- Inspect canonical Gaia storage paths.

Subcommands:
  paths              Print all resolved paths (key=value)
  paths data         Print only data_dir()
  paths db           Print only db_path()

ensure_layout() is invoked before printing so that ~/.gaia/ (or the
GAIA_DATA_DIR override) is materialized on first use with mode 0700.

Patterns inspired by engram (MIT). No runtime dependency on engram.
"""

import sys
from pathlib import Path

# Ensure the gaia package (repo-rooted) is importable regardless of cwd.
# bin/gaia inserts _PACKAGE_ROOT (= /home/jorge/ws/me/gaia/) into sys.path.
# When invoked via the CLI dispatcher, the gaia/ package is directly under
# that root, so no extra path manipulation is needed here.

_PACKAGE_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_ROOT))


def _cmd_data(args) -> int:
    """Handle `gaia paths data`."""
    from gaia.paths import data_dir, ensure_layout
    ensure_layout()
    print(data_dir())
    return 0


def _cmd_db(args) -> int:
    """Handle `gaia paths db`."""
    from gaia.paths import db_path, ensure_layout
    ensure_layout()
    print(db_path())
    return 0


def _cmd_all(args) -> int:
    """Handle `gaia paths` (no sub-action) -- print all paths."""
    from gaia.paths import (
        cache_dir,
        data_dir,
        db_path,
        ensure_layout,
        events_dir,
        logs_dir,
        snapshot_dir,
        state_dir,
        workspaces_dir,
    )
    ensure_layout()
    print(f"data={data_dir()}")
    print(f"db={db_path()}")
    print(f"snapshot={snapshot_dir()}")
    print(f"state={state_dir()}")
    print(f"workspaces={workspaces_dir()}")
    print(f"logs={logs_dir()}")
    print(f"events={events_dir()}")
    print(f"cache={cache_dir()}")
    return 0


def cmd_paths(args) -> int:
    """Top-level dispatcher for `gaia paths [<action>]`."""
    func = getattr(args, "func", None)
    if func is None:
        return _cmd_all(args)
    return func(args) or 0


def register(subparsers):
    """Register the paths subcommand with nested actions."""
    paths_parser = subparsers.add_parser(
        "paths",
        help="Inspect canonical Gaia storage paths (data, db, snapshot, etc.)",
    )
    paths_parser.set_defaults(_paths_parser=paths_parser)

    actions = paths_parser.add_subparsers(dest="paths_action", metavar="<action>")

    data_p = actions.add_parser("data", help="Print data_dir() only")
    data_p.set_defaults(func=_cmd_data)

    db_p = actions.add_parser("db", help="Print db_path() only")
    db_p.set_defaults(func=_cmd_db)
