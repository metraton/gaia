"""
gaia paths -- Report canonical Gaia storage paths; materializes the layout if missing.

Subcommands:
  paths              Print all resolved paths (key=value):
                       data, db, snapshot, state, workspaces, logs, events,
                       cache, scratch, evidence, worktrees, tmp, rejected_turns
  paths data         Print only data_dir()
  paths db           Print only db_path()

All other canonical paths (snapshot, state, workspaces, logs, events, cache,
scratch, evidence, worktrees, tmp, rejected_turns) are printed by `gaia
paths` (no subcommand). Every one of them resolves under data_dir(), so a
`GAIA_DATA_DIR` override relocates all of them together -- including
`evidence`, whose root previously bypassed the resolver and stayed pinned
under the real ~/.gaia regardless of the override. Per-workspace metadata is
available via `gaia workspace info`.

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
        evidence_dir,
        logs_dir,
        rejected_turns_dir,
        scratch_dir,
        snapshot_dir,
        state_dir,
        tmp_dir,
        workspaces_dir,
        worktrees_dir,
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
    print(f"scratch={scratch_dir()}")
    print(f"evidence={evidence_dir()}")
    print(f"worktrees={worktrees_dir()}")
    print(f"tmp={tmp_dir()}")
    print(f"rejected_turns={rejected_turns_dir()}")
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
        help="Report canonical Gaia storage paths -- WRITES; materializes the layout if missing",
        description=(
            "Report resolved Gaia storage paths. WRITES: every subcommand\n"
            "calls ensure_layout() first, so the ~/.gaia layout (six\n"
            "directories, mode 0700) is materialized if missing.\n\n"
            "No subcommand: print all paths (data, db, snapshot, state,\n"
            "  workspaces, logs, events, cache, scratch, evidence, worktrees,\n"
            "  tmp, rejected_turns) as key=value pairs.\n"
            "data: print data_dir() only.\n"
            "db:   print db_path() only.\n\n"
            "Per-workspace metadata: gaia workspace info"
        ),
    )
    paths_parser.set_defaults(_paths_parser=paths_parser)

    actions = paths_parser.add_subparsers(dest="paths_action", metavar="<action>")

    data_p = actions.add_parser("data", help="Print data_dir() only")
    data_p.set_defaults(func=_cmd_data)

    db_p = actions.add_parser("db", help="Print db_path() only")
    db_p.set_defaults(func=_cmd_db)
