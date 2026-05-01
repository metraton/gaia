"""
gaia project -- Workspace identity and consolidate operations.

Subcommands:
  project current               Print current workspace identity (resolved from cwd)
  project info                  Print structured info about the current workspace
  project merge <from> <to>     Preview/execute workspace merge (--confirm to apply)

Patterns inspired by engram (MIT). No runtime dependency on engram.
"""

import sys
from pathlib import Path

# Ensure the gaia package is importable when bin/gaia loads this plugin.
_PACKAGE_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_ROOT))


def _cmd_current(args) -> int:
    """Handle `gaia project current`."""
    from gaia.project import current
    print(current())
    return 0


def _cmd_info(args) -> int:
    """Handle `gaia project info` -- structured info about the current workspace."""
    from gaia.paths import (
        cache_dir,
        data_dir,
        db_path,
        events_dir,
        logs_dir,
        snapshot_dir,
        state_dir,
        workspaces_dir,
    )
    from gaia.project import current

    cwd = Path.cwd()
    identity = current()

    print(f"identity={identity}")
    print(f"cwd={cwd}")
    print(f"data={data_dir()}")
    print(f"db={db_path()}")
    print(f"snapshot={snapshot_dir()}")
    print(f"state={state_dir()}")
    print(f"workspaces={workspaces_dir()}")
    print(f"workspace_dir={workspaces_dir() / identity}")
    print(f"logs={logs_dir()}")
    print(f"events={events_dir()}")
    print(f"cache={cache_dir()}")
    return 0


def _cmd_merge(args) -> int:
    """Handle `gaia project merge <from> <to> [--confirm] [--dry-run] [--report-duplicates]`.

    --dry-run            Execute the merge logic but do NOT commit any file
                         moves to disk. Reports what would happen identically
                         to preview mode (no --confirm) but the flag is
                         explicit and composable with other flags.

    --report-duplicates  List projects rows that share the same ``identity``
                         value (potential duplicates caused by the pre-fix
                         identity bug). Returns exit code 1 when duplicates
                         are found, 0 when clean.
    """
    from gaia.project import merge

    from_id = args.from_id
    to_id = args.to_id
    confirm = bool(getattr(args, "confirm", False))
    dry_run = bool(getattr(args, "dry_run", False))
    report_duplicates = bool(getattr(args, "report_duplicates", False))

    # --report-duplicates: query the DB and report identity collisions.
    if report_duplicates:
        return _report_duplicate_identities()

    # --dry-run forces preview mode (no confirm) regardless of other flags.
    if dry_run:
        confirm = False

    result = merge(from_id, to_id, confirm=confirm)

    if not confirm:
        # Preview / dry-run mode
        mode_label = "Dry-run" if dry_run else "Preview"
        if not result.preview and not result.conflicts:
            print(f"# No changes: source '{from_id}' has no files (or does not exist)")
            return 0
        print(f"# {mode_label}: merge '{from_id}' -> '{to_id}'")
        for rel, size in result.preview:
            print(f"move\t{rel}\t{size} bytes")
        for rel in result.conflicts:
            print(f"conflict\t{rel}")
        print()
        print(f"# {len(result.preview)} file(s) would move, {len(result.conflicts)} conflict(s)")
        if dry_run:
            print(f"# (dry-run: no files were moved)")
        else:
            print(f"# Re-run with --confirm to apply.")
        return 0

    # Confirmed mode
    print(f"# Merged '{from_id}' -> '{to_id}'")
    for rel in result.moved:
        print(f"moved\t{rel}")
    for rel in result.conflicts:
        print(f"conflict\t{rel}")
    print()
    print(f"# {len(result.moved)} file(s) moved, {len(result.conflicts)} conflict(s) skipped")
    return 1 if result.conflicts else 0


def _report_duplicate_identities() -> int:
    """Query the store and print projects rows with duplicate identity values.

    Returns:
        0 when no duplicates found (clean).
        1 when duplicates are found (actionable signal).
    """
    try:
        from gaia.store.writer import _connect
        con = _connect()
        rows = con.execute(
            """
            SELECT identity, COUNT(*) AS cnt, GROUP_CONCAT(name, ', ') AS names
            FROM projects
            WHERE identity IS NOT NULL AND identity != ''
            GROUP BY identity
            HAVING cnt > 1
            ORDER BY cnt DESC, identity
            """
        ).fetchall()
        con.close()
    except Exception as exc:
        print(f"# error: could not query store: {exc}", file=sys.stderr)
        return 2

    if not rows:
        print("# duplicates=0: all project identities are unique")
        return 0

    print(f"# duplicates={len(rows)}: projects sharing the same identity")
    print(f"# {'identity':<50} {'count':>5}  names")
    print(f"# {'-'*50} {'-----':>5}  -----")
    for row in rows:
        identity = row[0] or "(null)"
        cnt = row[1]
        names = row[2] or ""
        print(f"  {identity:<50} {cnt:>5}  {names}")
    return 1


def cmd_project(args) -> int:
    """Top-level dispatcher for `gaia project <action>`."""
    func = getattr(args, "func", None)
    if func is None:
        if hasattr(args, "_project_parser"):
            args._project_parser.print_help()
        else:
            print("Usage: gaia project <current|info|merge>", file=sys.stderr)
        return 0
    return func(args) or 0


def register(subparsers):
    """Register the project subcommand with nested actions."""
    proj_parser = subparsers.add_parser(
        "project",
        help="Workspace identity and consolidate operations",
    )
    proj_parser.set_defaults(_project_parser=proj_parser)

    actions = proj_parser.add_subparsers(dest="project_action", metavar="<action>")

    current_p = actions.add_parser("current", help="Print current workspace identity")
    current_p.set_defaults(func=_cmd_current)

    info_p = actions.add_parser("info", help="Print structured info about the current workspace")
    info_p.set_defaults(func=_cmd_info)

    merge_p = actions.add_parser("merge", help="Preview/execute workspace merge")
    merge_p.add_argument("from_id", help="Source workspace identity")
    merge_p.add_argument("to_id", help="Target workspace identity")
    merge_p.add_argument(
        "--confirm",
        action="store_true",
        default=False,
        help="Actually move files (default: preview only)",
    )
    merge_p.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=False,
        help="Simulate the merge without moving any files (explicit preview mode)",
    )
    merge_p.add_argument(
        "--report-duplicates",
        dest="report_duplicates",
        action="store_true",
        default=False,
        help="List projects with duplicate identity values; exit 1 if any found",
    )
    merge_p.set_defaults(func=_cmd_merge)
