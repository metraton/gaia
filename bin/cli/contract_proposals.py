"""``gaia contract proposals list|accept|dismiss|backfill``: the orchestrator decides what turns proposed."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional


def _print(payload: dict, text: str, as_json: bool, *, error: bool = False) -> None:
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(text, file=sys.stderr if error else sys.stdout)


def _refused(verb: str, as_json: bool) -> Optional[int]:
    from gaia.agent_identity import ORCHESTRATOR, dispatch_agent_from_env

    agent = dispatch_agent_from_env().strip()
    if not agent or agent == ORCHESTRATOR:
        return None
    message = (f"gaia contract proposals {verb}: refused for {agent!r}. A subagent proposes in "
               "its contract row; only the orchestrator accepts or dismisses.")
    _print({"status": "refused", "error": message}, message, as_json, error=True)
    return 1


def _summary(item: dict) -> str:
    payload = item["payload"]
    if item["kind"] == "memory":
        operation = payload.get("operation", "?")
        body = payload.get(operation) or {}
        return f"{operation} {body.get('slug') or body.get('name') or '(no slug)'}: {body.get('description', '')}"
    return f"{payload.get('contract')} keys {', '.join(sorted(payload.get('payload') or {}))}"


def _cmd_list(args) -> int:
    from gaia.store.proposals import list_proposals

    status = None if args.all else "pending"
    items = list_proposals(status=status, kind=args.kind, contract_id=args.contract_id)
    if args.json:
        print(json.dumps({"proposals": items, "count": len(items)}, indent=2))
        return 0
    for item in items:
        print(f"#{item['id']} {item['status']} {item['kind']} "
              f"project={item['project'] or '(none)'} contract={item['contract_id']} "
              f"{_summary(item)}")
    print(f"{len(items)} proposal(s)")
    return 0


def _resolve_project(reference: str) -> Optional[dict]:
    from gaia.store.project_context import resolve_project
    from gaia.store.writer import _connect

    con = _connect()
    try:
        return resolve_project(con, reference)
    finally:
        con.close()


def _accept_memory(item: dict, owner: Optional[dict], workspace: Optional[str]) -> tuple:
    payload = item["payload"]
    if payload.get("operation") != "create":
        return False, (f"operation {payload.get('operation')!r} is applied with "
                       f"`gaia memory {payload.get('operation')}`; dismiss this proposal after it")
    create = payload["create"]
    slug, mem_type = create.get("slug"), create.get("type")
    if not slug or not mem_type:
        return False, "the proposal names no slug or type; add it with `gaia memory add` and dismiss"
    argv = ["memory", "add", "--name", slug, "--type", mem_type, "--body", create["body"],
            "--description", create["description"]]
    if create.get("class"):
        argv += ["--class", create["class"]]
    if mem_type != "user":
        if owner is None:
            return False, "the proposal has no project: pass --project <identity or name>"
        argv += ["--project-ref", owner["identity"], "--workspace", workspace or owner["workspace"]]
    bin_dir = str(Path(__file__).resolve().parents[1])
    if bin_dir not in sys.path:
        sys.path.insert(0, bin_dir)
    from cli import memory

    parser = argparse.ArgumentParser(prog="gaia")
    memory.register(parser.add_subparsers(dest="subcommand"))
    memory_args = parser.parse_args(argv)
    if memory_args.func(memory_args) != 0:
        return False, f"gaia memory add refused {slug}; the proposal stays pending"
    return True, f"memory {slug} written"


def _accept_context(item: dict, owner: Optional[dict], workspace: Optional[str]) -> tuple:
    hooks_dir = str(Path(__file__).resolve().parents[2] / "hooks")
    if hooks_dir not in sys.path:
        sys.path.insert(0, hooks_dir)
    from gaia.project import current
    from modules.context.context_writer import apply_proposal

    project = owner["identity"] if owner else item["project"]
    outcome = apply_proposal([item["payload"]], workspace or current(), project=project,
                             proposed_by=item["contract_id"], proposer_agent=item["agent_id"])
    if outcome["errors"]:
        return False, "; ".join(outcome["errors"])
    return True, ", ".join(f"{a['contract']} -> {a['project']}" for a in outcome["applied"])


def _reject(proposal_id: int, state: str, as_json: bool) -> int:
    _print({"status": "rejected", "error": state}, f"proposal {proposal_id}: {state}", as_json,
           error=True)
    return 1


def _cmd_accept(args) -> int:
    """Claim the proposal, then write it; a lost claim writes nothing and a failed write reopens it."""
    from gaia.store import proposals as store

    if (refused := _refused("accept", args.json)) is not None:
        return refused
    item = store.get_proposal(args.proposal_id)
    if item is None or item["status"] != "pending":
        return _reject(args.proposal_id,
                       "no such proposal" if item is None else f"already {item['status']}",
                       args.json)
    owner = None
    reference = args.project or item["project"]
    if reference:
        owner = _resolve_project(reference)
        if owner is None:
            message = f"project {reference!r} is not a known active project (see `gaia context project`)"
            _print({"status": "rejected", "error": message}, message, args.json, error=True)
            return 1
    if not store.decide_proposal(item["id"], "accepted", decided_by="orchestrator",
                                 project=owner["identity"] if owner else None):
        return _reject(item["id"], "decided by another call meanwhile; nothing written",
                       args.json)
    if item["kind"] == "memory":
        ok, message = _accept_memory(item, owner, args.workspace)
    elif item["kind"] == "context":
        ok, message = _accept_context(item, owner, args.workspace)
    else:
        ok, message = False, f"no accept path for {item['kind']!r} proposals yet; dismiss it or wait"
    if not ok:
        store.reopen_proposal(item["id"], project=item["project"])
        _print({"status": "rejected", "error": message}, f"refused: {message}", args.json,
               error=True)
        return 1
    store.note_decision(item["id"], message)
    _print({"status": "accepted", "id": item["id"], "detail": message},
           f"accepted #{item['id']}: {message}", args.json)
    return 0


def _cmd_dismiss(args) -> int:
    from gaia.store.proposals import decide_proposal

    if (refused := _refused("dismiss", args.json)) is not None:
        return refused
    if not decide_proposal(args.proposal_id, "dismissed", decided_by="orchestrator",
                           note=args.reason):
        message = f"proposal {args.proposal_id} is not pending"
        _print({"status": "rejected", "error": message}, message, args.json, error=True)
        return 1
    _print({"status": "dismissed", "id": args.proposal_id}, f"dismissed #{args.proposal_id}",
           args.json)
    return 0


def _cmd_backfill(args) -> int:
    from gaia.store.proposals import backfill_proposals

    report = backfill_proposals(since_days=args.since_days, apply=args.apply)
    print(json.dumps(report, indent=2) if args.json else
          " ".join(f"{key}={value}" for key, value in report.items()))
    return 0


def register_proposals(sub) -> None:
    """Add the ``proposals`` group to ``gaia contract``."""
    group = sub.add_parser("proposals", help="List, accept or dismiss what closed turns proposed")
    verbs = group.add_subparsers(dest="proposals_cmd", metavar="VERB", required=True)

    p_list = verbs.add_parser("list", help="Pending proposals, oldest first")
    p_list.add_argument("--all", action="store_true", help="Include accepted and dismissed")
    p_list.add_argument("--kind", choices=("memory", "context", "thread"))
    p_list.add_argument("--contract-id", dest="contract_id")
    p_list.add_argument("--json", action="store_true")
    p_list.set_defaults(func=_cmd_list)

    p_accept = verbs.add_parser("accept", help="Write a proposal and record the decision")
    p_accept.add_argument("proposal_id", type=int)
    p_accept.add_argument("--project", help="Project identity or name; required when it has none")
    p_accept.add_argument("--workspace", help="Workspace to write under; default: the project's")
    p_accept.add_argument("--json", action="store_true")
    p_accept.set_defaults(func=_cmd_accept)

    p_dismiss = verbs.add_parser("dismiss", help="Drop a proposal from the pending list")
    p_dismiss.add_argument("proposal_id", type=int)
    p_dismiss.add_argument("--reason")
    p_dismiss.add_argument("--json", action="store_true")
    p_dismiss.set_defaults(func=_cmd_dismiss)

    p_backfill = verbs.add_parser(
        "backfill", help="Record proposals of rows closed before the store existed (dry run "
                         "unless --apply; the user's call on a live database)")
    p_backfill.add_argument("--since-days", dest="since_days", type=int, default=30)
    p_backfill.add_argument("--apply", action="store_true")
    p_backfill.add_argument("--json", action="store_true")
    p_backfill.set_defaults(func=_cmd_backfill)
