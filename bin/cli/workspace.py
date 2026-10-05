"""
gaia workspace -- Workspace identity and consolidate operations.

Subcommands:
  workspace current               Print the declared workspace containing cwd
  workspace declare <name> <path> Declare a workspace rooted at <path>
  workspace list                  List the declared workspaces and their roots
  workspace info                  Print structured info about the current workspace
  workspace retire <source> --into <target>
                                  Fold a workspace's rows in gaia.db into another
  workspace curate                Hide phantoms, drop stale aliases and dangling facets
  workspace merge <from> <to>     Preview/execute a merge of workspace FILES (--confirm to apply)

Patterns inspired by engram (MIT). No runtime dependency on engram.
"""

import argparse
import sys
from pathlib import Path

# Ensure the gaia package is importable when bin/gaia loads this plugin.
_PACKAGE_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_ROOT))


def _cmd_current(args) -> int:
    """Handle `gaia workspace current`: the declared workspace containing cwd, else exit 1."""
    from gaia.project import declared_workspace, not_declared_message

    workspace = declared_workspace()
    if workspace is None:
        print(not_declared_message(Path.cwd().resolve()), file=sys.stderr)
        return 1
    print(workspace)
    return 0


def _cmd_declare(args) -> int:
    """Handle `gaia workspace declare NAME PATH`."""
    from gaia.store.writer import WorkspaceDeclarationError, declare_workspace

    name = args.name.strip()
    root = Path(args.path).expanduser().resolve()
    if not name:
        print("gaia workspace declare: NAME cannot be empty", file=sys.stderr)
        return 2
    if not root.is_dir():
        print(f"gaia workspace declare: {root} is not a directory", file=sys.stderr)
        return 2
    dry_run = bool(getattr(args, "dry_run", False))
    try:
        report = declare_workspace(name, root, dry_run=dry_run)
    except WorkspaceDeclarationError as exc:
        print(f"gaia workspace declare: {exc}", file=sys.stderr)
        return 1
    outcome, alias = report["outcome"], report["dropped_alias"]
    if outcome == "noop":
        print(f"workspace {name!r} is already declared at {root}")
        return 0
    if dry_run:
        print(f"dry-run: workspace {name!r} would be {outcome} at {root}")
        if alias:
            print(f"dry-run: alias {name} -> {alias} would be dropped; {name} would mean itself")
        return 0
    print(f"workspace {name!r} declared at {root}")
    if alias:
        print(f"alias {name} -> {alias} dropped; {name} means itself again")
        print(f"  undo ledger: {report['ledger']} (gaia workspace retire --undo <ledger>)")
    print(f"Index its repositories with: gaia scan --workspace {name} {root}")
    return 0


def _render_curation(plan: dict) -> None:
    print(f"# workspace curate: {plan['mode']}")
    for item in plan["hide"]:
        history = ", ".join(f"{t}={n}" for t, n in item["history"].items())
        print(f"  hide {item['workspace']} (status retired)"
              + (f"; history kept under its name: {history}" if history else ""))
    for item in plan["retire"]:
        print(f"  retire {item['workspace']} into {item['into']} (owner found by {item['via']})")
        for table, counts in (item.get("tables") or {}).items():
            shown = {k: v for k, v in counts.items() if v}
            print(f"    {table:<28} " + "  ".join(f"{k}={v}" for k, v in shown.items()))
        for section in item.get("sections") or []:
            print(f"    section {section['section']}: keeps {section['wins']}'s version; "
                  f"{section['loses']}'s goes to the ledger")
        if item.get("refused"):
            print(f"    REFUSED: {item['refused']}")
        if item.get("ledger"):
            print(f"    ledger: {item['ledger']}")
    for item in plan["unresolved"]:
        owned = ", ".join(f"{t}={n}" for t, n in item["owned"].items())
        print(f"  left {item['workspace']} ({owned}): {item['reason']}")
    for alias in plan["drop_aliases"]:
        print(f"  drop alias {alias['alias']} -> {alias['target']}")
    for facet in plan["drop_facets"]:
        print(f"  drop {facet['scope']} facet {facet['key']} of "
              f"{facet['workspace']}/{facet['project']}")
    if plan["hidden_integrations"]:
        print(f"  {plan['hidden_integrations']} integration(s) with no version or install path: "
              "kept, left out of listings, not changed")
    if plan.get("backup"):
        print(f"  backup: {plan['backup']}")


def _cmd_curate(args) -> int:
    """Handle `gaia workspace curate [--into NAME=TARGET] [--on-conflict keep-target] [--dry-run] [--yes] [--json]`."""
    import json

    from gaia.store.workspace_curation import (
        WorkspaceCurationError, apply_curation, plan_curation,
    )

    into = {}
    for value in args.into:
        name, sep, target = value.partition("=")
        if not sep:
            print(f"gaia workspace curate: --into {value!r}: expected NAME=TARGET", file=sys.stderr)
            return 2
        into[name.strip()] = target.strip()
    try:
        plan = plan_curation(into=into, on_conflict=args.on_conflict)
        if not (args.dry_run or plan["mode"] == "noop"):
            if not (args.yes or _confirmed("curate the workspace registry")):
                _render_curation(plan)
                return 1
            plan = apply_curation(into=into, on_conflict=args.on_conflict)
    except WorkspaceCurationError as exc:
        print(f"gaia workspace curate: {exc}; nothing was changed", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(plan, indent=2, default=str))
    else:
        _render_curation(plan)
    return 0


def _cmd_list(args) -> int:
    """Handle `gaia workspace list`: every declared workspace and its root."""
    from gaia.install_root import registered_roots

    for root, name in sorted(registered_roots().items(), key=lambda item: item[1]):
        print(f"{name}\t{root}")
    return 0


def _cmd_info(args) -> int:
    """Handle `gaia workspace info` -- structured info about the current workspace."""
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
    from gaia.project import declared_workspace, not_declared_message

    cwd = Path.cwd()
    declared = declared_workspace(cwd)
    identity = declared or "global"
    if declared is None:
        print(not_declared_message(cwd.resolve()), file=sys.stderr)

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
    """Handle `gaia workspace merge <from> <to> [--confirm] [--dry-run] [--report-duplicates]`.

    --dry-run            Execute the merge logic but do NOT commit any file
                         moves to disk. Reports what would happen identically
                         to preview mode (no --confirm) but the flag is
                         explicit and composable with other flags.

    --report-duplicates  List workspaces rows that share the same ``identity``
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


def _parse_on_conflict(values: list[str]) -> dict[str, str]:
    resolved = {}
    for value in values or []:
        table, sep, strategy = value.partition("=")
        if not sep:
            raise ValueError(f"--on-conflict {value!r}: expected TABLE=STRATEGY")
        resolved[table.strip()] = strategy.strip()
    return resolved


def _render_retire(report: dict) -> None:
    mode = report.get("mode")
    print(f"# workspace retire {report.get('source')} -> {report.get('target')}: {mode}")
    for table, counts in (report.get("tables") or {}).items():
        shown = {k: v for k, v in counts.items() if v}
        if shown:
            print(f"  {table:<28} " + "  ".join(f"{k}={v}" for k, v in shown.items()))
    for table, keys in (report.get("collisions") or {}).items():
        print(f"  COLLISION {table}: {', '.join(keys)}")
    for table, keys in (report.get("resolved") or {}).items():
        print(f"  resolved {table}: {', '.join(keys)}")
    for row in report.get("user_rows_to_adjudicate") or []:
        print(f"  user row with a project to adjudicate: {row['name']} "
              f"(initiative={row['initiative']}, project_ref={row['project_ref']})")
    history = {t: n for t, n in (report.get("history") or {}).items() if n}
    if history:
        print("  history kept under the source, read through the alias: "
              + ", ".join(f"{t}={n}" for t, n in history.items()))
    for table, n in (report.get("left_behind") or {}).items():
        print(f"  left under the source: {table}={n}")
    if report.get("release_root"):
        verb = "released" if mode in ("applied", "undone") else "to release"
        print(f"  source root {verb}: {report['release_root']}")
    if report.get("alias_restored"):
        verb = "restored" if mode == "undone" else "to restore"
        print(f"  alias {verb}: {report['source']} -> {report['target']}")
    if report.get("restore_root"):
        verb = "restored" if mode == "undone" else "to restore"
        print(f"  source root {verb}: {report['restore_root']}")
    for key in ("backup", "ledger"):
        if report.get(key):
            print(f"  {key}: {report[key]}")


def _cmd_retire(args) -> int:
    """Handle `gaia workspace retire <source> --into <target>` and `--undo LEDGER`."""
    import json

    from gaia.store.workspace_retire import (
        WorkspaceRetireError, apply_retire, plan_retire, undo_retire,
    )

    as_json = bool(args.json)

    def emit(report: dict) -> None:
        if as_json:
            print(json.dumps(report, indent=2, default=str))
        else:
            _render_retire(report)

    try:
        if args.undo:
            if args.dry_run or args.yes or _confirmed(f"undo the retire recorded in {args.undo}"):
                report = undo_retire(args.undo, dry_run=args.dry_run)
                emit(report)
                return 0
            return 1
        if not args.source or not args.into:
            print("gaia workspace retire: give <source> --into <target>, or --undo LEDGER",
                  file=sys.stderr)
            return 2
        on_conflict = _parse_on_conflict(args.on_conflict)
        plan = plan_retire(args.source, args.into, on_conflict=on_conflict)
        if args.dry_run or plan["mode"] == "noop":
            emit(plan)
            return 0
        if not (args.yes or _confirmed(f"retire workspace {args.source!r} into {args.into!r}")):
            emit(plan)
            return 1
        emit(apply_retire(args.source, args.into, on_conflict=on_conflict))
        return 0
    except (WorkspaceRetireError, ValueError) as exc:
        report = getattr(exc, "report", None) or {}
        report.setdefault("error", str(exc))
        if as_json:
            print(json.dumps(report, indent=2, default=str))
        else:
            if report.get("tables"):
                _render_retire(report)
            print(f"gaia workspace retire: {exc}", file=sys.stderr)
        return 2


def _confirmed(action: str) -> bool:
    try:
        answer = input(f"gaia workspace: about to {action}. The DB is backed up first. "
                       f"Type 'yes' to confirm: ")
    except (EOFError, OSError):
        answer = ""
    if answer.strip().lower() != "yes":
        print("Aborted (no confirmation; pass --yes).", file=sys.stderr)
        return False
    return True


def _report_duplicate_identities() -> int:
    """Query the store and print workspaces rows with duplicate identity values.

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
            FROM workspaces
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
        print("# duplicates=0: all workspace identities are unique")
        return 0

    print(f"# duplicates={len(rows)}: workspaces sharing the same identity")
    print(f"# {'identity':<50} {'count':>5}  names")
    print(f"# {'-'*50} {'-----':>5}  -----")
    for row in rows:
        identity = row[0] or "(null)"
        cnt = row[1]
        names = row[2] or ""
        print(f"  {identity:<50} {cnt:>5}  {names}")
    return 1


def cmd_workspace(args) -> int:
    """Top-level dispatcher for `gaia workspace <action>`."""
    func = getattr(args, "func", None)
    if func is None:
        if hasattr(args, "_workspace_parser"):
            args._workspace_parser.print_help()
        else:
            print("Usage: gaia workspace <current|declare|list|info|retire|curate|merge>",
                  file=sys.stderr)
        return 0
    return func(args) or 0


def register(subparsers):
    """Register the workspace subcommand with nested actions."""
    ws_parser = subparsers.add_parser(
        "workspace",
        help="Workspace identity and consolidate operations",
    )
    ws_parser.set_defaults(_workspace_parser=ws_parser)

    actions = ws_parser.add_subparsers(dest="workspace_action", metavar="<action>")

    current_p = actions.add_parser(
        "current", help="Print the declared workspace containing the current directory"
    )
    current_p.set_defaults(func=_cmd_current)

    declare_p = actions.add_parser(
        "declare",
        help="Declare NAME as a workspace rooted at PATH",
        description=(
            "Record NAME as a workspace whose root is PATH. Only declared workspaces "
            "exist: a folder resolves to the declared root nearest above it. A name "
            "or a root already declared is never rebound."
        ),
        epilog="Example:\n  gaia workspace declare personal ~/code/personal\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    declare_p.add_argument("name", help="Workspace name")
    declare_p.add_argument("path", help="Workspace root directory")
    declare_p.add_argument(
        "--dry-run", dest="dry_run", action="store_true", default=False,
        help="Report the declaration and the retire alias it would drop, writing nothing",
    )
    declare_p.set_defaults(func=_cmd_declare)

    curate_p = actions.add_parser(
        "curate",
        help="Hide phantom workspaces, drop stale aliases and dangling facets (dry-run, backup)",
        description=(
            "Curate the workspace registry without deleting history. A workspace with no "
            "declared root is a phantom: one that owns nothing is marked retired and leaves "
            "every listing; one that owns rows, or has history and a declared owner, is "
            "retired into that owner (gaia workspace retire, with its alias and undo "
            "ledger). The owner is the --into value, the phantom's retire alias, the "
            "declared root holding its projects, the declared root holding a phantom named "
            "by a path, or the one declared workspace with a project of its name. Also drops "
            "retire aliases of declared names and worktree/copy facets whose folder is gone. "
            "Integrations with no version or install path are only counted: listings "
            "already leave them out. The batch is applied in one transaction after one "
            "curate backup, which every retire ledger points to. A retire that collides "
            "with the owner or with an earlier retire of the batch is skipped and listed "
            "as REFUSED; the rest still applies. With --on-conflict keep-target a "
            "context-contract section the phantom shares with its owner keeps the owner's "
            "version, and the phantom's is recorded whole in that retire's ledger."
        ),
        epilog=(
            "Examples:\n"
            "  gaia workspace curate --dry-run\n"
            "  gaia workspace curate --into bildwiz=aaxis --yes\n"
            "  gaia workspace curate --into bildwiz=aaxis --on-conflict keep-target --dry-run\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    curate_p.add_argument(
        "--into", action="append", default=[], metavar="NAME=TARGET",
        help="Retire phantom NAME into declared workspace TARGET. Repeatable.",
    )
    curate_p.add_argument(
        "--on-conflict", dest="on_conflict", choices=("keep-target",), default=None,
        help="Resolve colliding context-contract sections by keeping the owner's version",
    )
    curate_p.add_argument(
        "--dry-run", dest="dry_run", action="store_true", default=False,
        help="List every change, writing nothing",
    )
    curate_p.add_argument("--yes", action="store_true", default=False,
                          help="Skip the interactive confirmation")
    curate_p.add_argument("--json", action="store_true", default=False, help="JSON output")
    curate_p.set_defaults(func=_cmd_curate)

    list_p = actions.add_parser("list", help="List the declared workspaces and their roots")
    list_p.set_defaults(func=_cmd_list)

    info_p = actions.add_parser("info", help="Print structured info about the current workspace")
    info_p.set_defaults(func=_cmd_info)

    retire_p = actions.add_parser(
        "retire",
        help="Fold a workspace's rows in gaia.db into another (dry-run, backup, undo)",
        description=(
            "Retire <source> into <target>. Re-keys what the source owns -- projects "
            "and their scanner rows, briefs (with their plans), context contracts, "
            "integrations, scheduled tasks, notifications, memory and its links -- "
            "to the target; type=user memory goes to the _gaia_user scope and "
            "host-scoped memory to _gaia_host. History (episodes, episode_anomalies, "
            "harness_events, agent_contract_handoffs) is not rewritten: an alias "
            "source -> target is recorded, readers of the target include it, and "
            "--workspace <source> resolves to the target. Before writing it backs "
            "the database up with the sqlite backup API and writes an undo ledger "
            "beside the backup (<db dir>/backups). A collision on a unique name "
            "aborts without writing unless --on-conflict resolves it. Running it "
            "again once done does nothing."
        ),
        epilog=(
            "Examples:\n"
            "  gaia workspace retire me --into ws --dry-run\n"
            "  gaia workspace retire me --into ws --on-conflict integrations=keep-target --yes\n"
            "  gaia workspace retire --undo ~/.gaia/backups/gaia-pre-workspace-retire-...-ledger.json\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    retire_p.add_argument("source", nargs="?", help="Workspace to retire")
    retire_p.add_argument("--into", metavar="TARGET", help="Workspace that takes its rows")
    retire_p.add_argument(
        "--on-conflict", action="append", default=[], metavar="TABLE=STRATEGY",
        help="Resolve a name collision in memory, integrations or "
             "project_context_contracts: keep-target drops the source row, "
             "keep-source replaces the target row; both land in the undo ledger. "
             "Repeatable.",
    )
    retire_p.add_argument(
        "--undo", metavar="LEDGER",
        help="Put back what the retire recorded in LEDGER changed",
    )
    retire_p.add_argument(
        "--dry-run", dest="dry_run", action="store_true", default=False,
        help="Report per table what would move, collide or stay, writing nothing",
    )
    retire_p.add_argument(
        "--yes", action="store_true", default=False,
        help="Skip the interactive confirmation",
    )
    retire_p.add_argument("--json", action="store_true", default=False, help="JSON output")
    retire_p.set_defaults(func=_cmd_retire)

    merge_p = actions.add_parser(
        "merge",
        help="Preview/execute a merge of the files under ~/.gaia/workspaces "
             "(rows in gaia.db: use `workspace retire`)",
    )
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
        help="List workspaces with duplicate identity values; exit 1 if any found",
    )
    merge_p.set_defaults(func=_cmd_merge)
