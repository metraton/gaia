"""gaia lint -- read-only checks over a change; `lint comments` is the pre-push comment limit."""

import json
import sys
from pathlib import Path

_PACKAGE_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_ROOT))


def _cmd_comments(args) -> int:
    from gaia.comment_lint import CommentLintError, measure

    try:
        report = measure(str(Path(args.repo).resolve()), head=args.head, base=args.base)
    except CommentLintError as exc:
        print(f"gaia lint comments: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report.to_dict(), indent=2) if args.json else report.render())
    return 1 if report.blocked else 0


def cmd_lint(args) -> int:
    """Top-level dispatcher for `gaia lint <check>`."""
    func = getattr(args, "func", None)
    if func is None:
        args._lint_parser.print_help()
        return 0
    return func(args)


def register(subparsers):
    """Register the lint subcommand with its checks."""
    lint = subparsers.add_parser(
        "lint",
        help="Read-only checks over a change (comments: the pre-push comment limit)",
        description="Read-only checks over the commits a branch carries.",
    )
    lint.set_defaults(_lint_parser=lint)
    checks = lint.add_subparsers(dest="lint_check", metavar="<check>")

    comments = checks.add_parser(
        "comments",
        help="Net comment lines vs max(2, 2%% of added code); exit 1 over the limit",
        description=(
            "Count NET comment and docstring lines the branch adds over its merge-base, in\n"
            "non-generated source files. One-line docstrings on public units are free.\n"
            "Limit: max(2, 2% of added code lines). Section banners and comment blocks of\n"
            "more than two added lines are always listed and fail the check."
        ),
    )
    comments.add_argument("--repo", default=".", help="Repository or worktree (default: cwd)")
    comments.add_argument("--head", default="HEAD", help="Change tip (default: HEAD)")
    comments.add_argument(
        "--base", default=None,
        help="Target ref to merge-base against (default: nearest remote branch not containing HEAD)",
    )
    comments.add_argument("--json", action="store_true", help="Emit JSON output")
    comments.set_defaults(func=_cmd_comments)
