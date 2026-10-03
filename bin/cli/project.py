"""
gaia project -- operations on one project of a declared workspace.

Subcommands:
  project move <project> --into <workspace>
                  Move a project, with its briefs and its context entry, into
                  another declared workspace (--dry-run to preview)
"""

import argparse
import json
import sys
from pathlib import Path

_PACKAGE_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_ROOT))


def _render_move(report: dict) -> None:
    print(f"# project move {report['project']}: {report['from']} -> {report['to']} "
          f"({report['mode']})")
    if report["name_in_target"] != report["project"]:
        print(f"  name in {report['to']}: {report['name_in_target']}")
    for table, plan in report["tables"].items():
        moves, stays = plan["moves"], plan["stays"]
        line = f"  {table:<28} moves={moves if isinstance(moves, int) else len(moves)}"
        line += f"  stays={stays if isinstance(stays, int) else len(stays)}"
        if plan.get("read_through_project"):
            line += "  (read through the project, never re-keyed)"
        print(line)
        for label, names in (("moves", moves), ("stays", stays)):
            if isinstance(names, list) and names and table != "projects":
                print(f"    {label}: {', '.join(names)}")


def _cmd_move(args) -> int:
    """Handle `gaia project move <project> --into <workspace>`."""
    from gaia.store.project_move import ProjectMoveError, move_project

    try:
        report = move_project(
            args.project, args.into, from_workspace=args.from_workspace,
            dry_run=args.dry_run,
        )
    except ProjectMoveError as exc:
        if args.json:
            print(json.dumps({"error": str(exc), "candidates": exc.candidates}, indent=2))
        print(f"gaia project move: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        _render_move(report)
    return 0


def cmd_project(args) -> int:
    """Top-level dispatcher for `gaia project <action>`."""
    func = getattr(args, "func", None)
    if func is None:
        args._project_parser.print_help()
        return 0
    return func(args) or 0


def register(subparsers):
    """Register the project subcommand with nested actions."""
    project_parser = subparsers.add_parser("project", help="Operations on one project")
    project_parser.set_defaults(_project_parser=project_parser)
    actions = project_parser.add_subparsers(dest="project_action", metavar="<action>")

    move_p = actions.add_parser(
        "move",
        help="Move a project into another declared workspace",
        description=(
            "Move <project> into the declared workspace --into. Its projects row "
            "and scanner rows, the briefs created for it (gaia brief new "
            "--project) and its project_identity entry, declared workflow "
            "included, move in one transaction. Its memory is not re-keyed: it is "
            "read through the project from every workspace. Workspace-level "
            "briefs and the other context contracts stay. An undeclared target "
            "is refused."
        ),
        epilog=(
            "Examples:\n"
            "  gaia project move aos-iac --into aaxis --dry-run\n"
            "  gaia project move gaia --from ws --into me\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    move_p.add_argument("project", help="Project name, or its project_identity")
    move_p.add_argument("--into", required=True, metavar="WORKSPACE",
                        help="Declared workspace that takes the project")
    move_p.add_argument("--from", dest="from_workspace", default=None, metavar="WORKSPACE",
                        help="Workspace the project is in, when its name is ambiguous")
    move_p.add_argument("--dry-run", dest="dry_run", action="store_true", default=False,
                        help="Report per table what moves and what stays, writing nothing")
    move_p.add_argument("--json", action="store_true", default=False, help="JSON output")
    move_p.set_defaults(func=_cmd_move)
