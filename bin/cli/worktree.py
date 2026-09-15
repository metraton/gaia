"""
gaia worktree -- host-agnostic door onto Gaia's agentic worktree machinery.

Wraps ``gaia.worktree.create_canonical_worktree`` (identity-locked creation),
``gaia.retention.worktree_collector.list_managed_worktrees`` /
``gaia.worktree.read_worktree_metadata`` (inspection), and
``gaia.retention.worktree_reclaim.reclaim_worktree`` (capture-before-recycle
release) behind one CLI verb, so a specialist can create, inspect, and free
its own isolated worktree without depending on any host feature (Claude
Code's ``worktree.bgIsolation`` or equivalent). Before this, the machinery
existed with no production call site -- see ``gaia/worktree.py``'s module
docstring for the identity contract this plugin's ``create`` action relies
on: the git lock's *reason*, not the directory name, carries
``contract_id``/``agent_id``, which is what lets the collector recognize a
worktree this verb created as its own.

Subcommands:
    gaia worktree create  --repo <path> --project <name>
                          --contract-id <id> --agent-id <id> [--branch <name>]
                          [--json]

    gaia worktree list    [--repo <path>] [--json]

    gaia worktree show    <path> [--json]

    gaia worktree release <path> --workspace <W> --brief <slug> --ac <ac_id>
                          [--repo <path>] [--task-id <id>]
                          [--created-by <agent>] [--json]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

# Ensure the gaia package (repo root) is importable regardless of cwd.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _err(msg: str, as_json: bool = False) -> int:
    if as_json:
        print(json.dumps({"error": msg}))
    else:
        print(f"Error: {msg}", file=sys.stderr)
    return 1


def _print_metadata(metadata, as_json: bool) -> None:
    if as_json:
        print(json.dumps(metadata.as_dict()))
        return
    d = metadata.as_dict()
    for key in ("path", "repo", "project", "contract_id", "agent_id", "branch", "commit", "lifecycle"):
        print(f"{key}={d[key]}")


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------

def _cmd_create(args) -> int:
    from gaia.worktree import WorktreePathError, create_canonical_worktree

    as_json = getattr(args, "json", False)
    repo = Path(args.repo).resolve() if args.repo else Path.cwd()

    try:
        metadata = create_canonical_worktree(
            repo,
            args.project,
            args.contract_id,
            args.agent_id,
            branch=args.branch,
        )
    except WorktreePathError as exc:
        return _err(f"worktree path error: {exc}", as_json)
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        return _err(f"git failed ({exc.returncode}): {stderr or exc}", as_json)

    _print_metadata(metadata, as_json)
    return 0


def _cmd_list(args) -> int:
    from gaia.retention.worktree_collector import list_managed_worktrees
    from gaia.worktree import read_worktree_metadata

    as_json = getattr(args, "json", False)
    repo = Path(args.repo).resolve() if args.repo else Path.cwd()

    try:
        entries = list_managed_worktrees(repo)
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        return _err(f"git failed ({exc.returncode}): {stderr or exc}", as_json)

    rows = []
    for entry in entries:
        metadata = read_worktree_metadata(entry["path"])
        if metadata is not None:
            rows.append(metadata.as_dict())
        else:
            rows.append({
                "path": str(entry["path"]),
                "repo": str(repo),
                "project": None,
                "contract_id": entry["contract_id"],
                "agent_id": entry["agent_id"],
                "branch": None,
                "commit": None,
                "lifecycle": "legacy",
            })

    if as_json:
        print(json.dumps(rows))
        return 0

    if not rows:
        print(f"No Gaia-managed worktrees found for {repo}.")
        return 0
    for row in rows:
        print(f"{row['path']}  contract_id={row['contract_id']}  agent_id={row['agent_id']}  lifecycle={row['lifecycle']}")
    return 0


def _cmd_show(args) -> int:
    from gaia.worktree import read_worktree_metadata

    as_json = getattr(args, "json", False)
    path = Path(args.path)

    metadata = read_worktree_metadata(path)
    if metadata is None:
        return _err(f"no Gaia identity found for worktree {path}", as_json)

    _print_metadata(metadata, as_json)
    return 0


def _cmd_release(args) -> int:
    from gaia.retention.worktree_reclaim import reclaim_worktree
    from gaia.worktree import read_worktree_metadata

    as_json = getattr(args, "json", False)
    worktree_path = Path(args.path).resolve()

    repo = Path(args.repo).resolve() if args.repo else None
    created_by = args.created_by
    if repo is None or created_by is None:
        metadata = read_worktree_metadata(worktree_path)
        if repo is None:
            if metadata is None:
                return _err(
                    f"--repo not given and no Gaia identity found for {worktree_path}",
                    as_json,
                )
            repo = Path(metadata.repo)
        if created_by is None and metadata is not None:
            created_by = metadata.agent_id

    try:
        result = reclaim_worktree(
            repo,
            worktree_path,
            workspace=args.workspace,
            brief_slug=args.brief,
            ac_id=args.ac,
            task_id=args.task_id,
            created_by_agent=created_by,
        )
    except Exception as exc:  # noqa: BLE001 -- surface any failure, never mask it
        return _err(f"release failed: {exc}", as_json)

    if as_json:
        print(json.dumps(result))
    else:
        print(f"status={result['status']}")
        print(f"recycled={result['recycled']}")
        print(f"captured={result['captured']}")
        if result.get("evidence_id") is not None:
            print(f"evidence_id={result['evidence_id']}")
        if result.get("reason"):
            print(f"reason={result['reason']}")
    return 0 if result.get("status") not in ("capture_failed", "deposit_failed", "removal_failed") else 1


# ---------------------------------------------------------------------------
# register (auto-discovered by bin/gaia)
# ---------------------------------------------------------------------------

def register(subparsers) -> None:
    """Register the `worktree` subcommand with the root parser."""
    wt_parser = subparsers.add_parser(
        "worktree",
        help="Create, inspect, and release a Gaia-managed agentic git worktree",
        description=(
            "Host-agnostic worktree lifecycle for a specialist's isolated repo "
            "work: create (identity-locked), list/show (inspect), release "
            "(capture-before-recycle)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    actions = wt_parser.add_subparsers(dest="worktree_action", metavar="<action>")

    # -- create ------------------------------------------------------------
    create_p = actions.add_parser(
        "create",
        help="Create and identity-lock a worktree under Gaia's central root",
        description=(
            "Create a worktree under ~/.gaia/worktrees (never inside the "
            "checkout), lock it with contract_id/agent_id in the git lock "
            "reason, and write the .gaia-worktree.json sidecar."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  gaia worktree create --repo . --project gaia "
            "--contract-id c1.abc --agent-id c1 --branch feature/x\n"
        ),
    )
    create_p.add_argument("--repo", default=None, metavar="PATH",
                          help="Repository to branch the worktree from. Default: cwd.")
    create_p.add_argument("--project", required=True, metavar="NAME",
                          help="Project name recorded in the worktree's metadata.")
    create_p.add_argument("--contract-id", required=True, dest="contract_id", metavar="ID",
                          help="Owning contract id, stamped into the git lock reason.")
    create_p.add_argument("--agent-id", required=True, dest="agent_id", metavar="ID",
                          help="Owning agent id, stamped into the git lock reason.")
    create_p.add_argument("--branch", default=None, metavar="NAME",
                          help="Create and check out a new branch in the worktree.")
    create_p.add_argument("--json", action="store_true", default=False,
                          help="Emit JSON output.")

    # -- list ----------------------------------------------------------------
    list_p = actions.add_parser(
        "list",
        help="List Gaia-managed worktrees for a repository",
        description=(
            "Enumerate every worktree the repository's git registry knows "
            "about that carries a Gaia-minted lock identity."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n  gaia worktree list --repo .\n",
    )
    list_p.add_argument("--repo", default=None, metavar="PATH",
                        help="Any member of the repository's worktree family. Default: cwd.")
    list_p.add_argument("--json", action="store_true", default=False,
                        help="Emit JSON output.")

    # -- show ------------------------------------------------------------------
    show_p = actions.add_parser(
        "show",
        help="Show one worktree's canonical identity",
        description="Read the .gaia-worktree.json sidecar, or fall back to the git lock reason.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n  gaia worktree show ~/.gaia/worktrees/<id>\n",
    )
    show_p.add_argument("path", metavar="PATH", help="Worktree directory.")
    show_p.add_argument("--json", action="store_true", default=False,
                        help="Emit JSON output.")

    # -- release -----------------------------------------------------------
    release_p = actions.add_parser(
        "release",
        help="Capture-then-recycle a worktree (never destroys uncaptured work)",
        description=(
            "Recycle a clean worktree (unlock + remove, unforced). A dirty "
            "worktree's full diff is deposited as evidence for the given "
            "brief/AC first, and the worktree is then left in place -- see "
            "gaia.retention.worktree_reclaim for why forced removal is "
            "deliberately withheld."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  gaia worktree release ~/.gaia/worktrees/<id> "
            "--workspace me --brief my-brief --ac AC-1\n"
        ),
    )
    release_p.add_argument("path", metavar="PATH", help="Worktree directory to release.")
    release_p.add_argument("--repo", default=None, metavar="PATH",
                           help="Owning repository. Default: read from the worktree's own metadata.")
    release_p.add_argument("--workspace", required=True, metavar="W",
                           help="Workspace identity for the evidence deposit, if one is needed.")
    release_p.add_argument("--brief", required=True, metavar="BRIEF",
                           help="Brief slug the captured diff is evidence for.")
    release_p.add_argument("--ac", required=True, dest="ac", metavar="AC_ID",
                           help="Acceptance-criteria id the captured diff is evidence for.")
    release_p.add_argument("--task-id", default=None, dest="task_id", metavar="TASK_ID",
                           help="Opaque task reference (optional).")
    release_p.add_argument("--created-by", default=None, dest="created_by", metavar="AGENT",
                           help="Agent slug attributed on the evidence row. Default: the worktree's own agent_id.")
    release_p.add_argument("--json", action="store_true", default=False,
                           help="Emit JSON output.")


def cmd_worktree(args) -> int:
    """Dispatch to the appropriate worktree subcommand. Called by bin/gaia."""
    action = getattr(args, "worktree_action", None)
    handlers = {
        "create": _cmd_create,
        "list": _cmd_list,
        "show": _cmd_show,
        "release": _cmd_release,
    }
    if action in handlers:
        return handlers[action](args)

    print("Usage: gaia worktree <create|list|show|release>", file=sys.stderr)
    return 0
