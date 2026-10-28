"""Drift duty and guarded auto-apply for the project-context corrections a turn proposes."""

from __future__ import annotations

import json
import re
import shlex
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, List, Optional

WHAT_ROOTS = ("environment", "cluster", "context", "project", "namespace")
WHAT_ABBREVIATIONS = frozenset({"env", "envs", "ns"})

_STALE = re.compile(r"\b(stale|outdated|out of date|contradict\w*|no longer (?:true|matches|accurate))\b",
                    re.IGNORECASE)
_WORD_BREAK = re.compile(r"[^0-9A-Za-z]+|(?<=[a-z0-9])(?=[A-Z])")
_KUBE_DIMENSIONS = (("context", ("--context",)), ("namespace", ("-n", "--namespace")))
_DIMENSIONS = {"kubectl": _KUBE_DIMENSIONS, "oc": _KUBE_DIMENSIONS, "gcloud": (("project", ("--project",)),)}
_KUBE_REPOINTING = ("--kubeconfig", "--cluster", "-s", "--server", "-A", "--all-namespaces")
_REPOINTING = {"kubectl": _KUBE_REPOINTING, "oc": _KUBE_REPOINTING, "gcloud": ("--configuration",)}


def _hooks_on_path() -> None:
    hooks_root = str(Path(__file__).resolve().parents[1] / "hooks")
    if hooks_root not in sys.path:
        sys.path.insert(0, hooks_root)


def _texts(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _texts(item)


def stale_context_warning(envelope: Any) -> Optional[str]:
    """The finalize warning for a row whose prose or gaps report stale context while update_contracts is empty."""
    if not isinstance(envelope, dict) or envelope.get("update_contracts"):
        return None
    report = envelope.get("evidence_report") or {}
    for text in _texts([envelope.get("report_prose"), report.get("open_gaps")]):
        match = _STALE.search(text)
        if match:
            return (
                f"report_prose/open_gaps say {match.group(0)!r} but update_contracts is empty. A "
                "context value this turn observed to differ is proposed there with a drift object "
                "(key_path, old, new, command, observed_at), a contradicted memory row in "
                "memory_delta (agent-protocol principle 3). The close goes ahead."
            )
    return None


def _words(name: Any) -> list:
    return [word.casefold() for word in _WORD_BREAK.split(str(name)) if word]


def is_what_key(name: Any) -> bool:
    """Whether a key or section name, split at case, hyphen and underscore changes, names an environment, cluster, context, project or namespace."""
    return any(word in WHAT_ABBREVIATIONS or any(root in word for root in WHAT_ROOTS) for word in _words(name))


def _what_within(value: Any, path: str) -> list:
    if isinstance(value, dict):
        found = []
        for key, item in value.items():
            child = f"{path}.{key}"
            found += ([child] if is_what_key(key) else []) + _what_within(item, child)
        return found
    if isinstance(value, list):
        return [found for index, item in enumerate(value) for found in _what_within(item, f"{path}[{index}]")]
    return []


def _touched(delta: dict, stored: Any, prefix: str = "") -> tuple:
    what: list = []
    replaced: list = []
    for key, value in delta.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        current = stored.get(key) if isinstance(stored, dict) else None
        if is_what_key(key):
            what.append(path)
        if isinstance(value, dict):
            inner_what, inner_replaced = _touched(value, current, path)
            what += inner_what
            replaced += inner_replaced
            continue
        what += _what_within(value, path) + _what_within(current, path)
        if value is None or isinstance(value, list) or isinstance(current, (dict, list)):
            replaced.append(path)
    return list(dict.fromkeys(what)), replaced


def _leaves(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        for item in value.values():
            yield from _leaves(item)
    elif isinstance(value, list):
        for item in value:
            yield from _leaves(item)
    else:
        yield value if isinstance(value, str) else json.dumps(value)


def _named_in(goal: str, name: str) -> bool:
    return re.search(rf"(?<![\w.-]){re.escape(name)}(?![\w.-])", goal) is not None


def _section_refusals(entry: dict, sections: dict) -> List[str]:
    from modules.security.targets import DECLARATION_SECTION

    contract, payload = str(entry.get("contract") or ""), entry.get("payload")
    if not isinstance(payload, dict) or not payload:
        return ["its payload is not a non-empty object"]
    if is_what_key(contract) or "_".join(_words(contract)) == DECLARATION_SECTION:
        return [f"touches WHAT section {contract}"]
    spelled = [name for name in sections if _words(name) == _words(contract) and name != contract]
    if spelled:
        return [f"section {contract} is stored as {spelled[0]}"]
    stored = sections[contract]["payload"] if contract in sections else None
    if contract in sections and not isinstance(stored, dict):
        return [f"replaces the stored section {contract} whole"]
    what, replaced = _touched(payload, stored)
    refusals = [f"touches WHAT keys ({', '.join(what)})"] if what else []
    return refusals + ([f"deletes or replaces stored values at {', '.join(replaced)}"] if replaced else [])


def _output_refusal(command: str, payload: Any, records: list) -> Optional[str]:
    runs = [record for record in records if isinstance(record.get("command"), str)
            and record["command"].strip() == command.strip() and record.get("exit_code") in (0, "0")]
    if not runs:
        return "the turn record holds no successful run of drift.command"
    excerpt = runs[-1].get("excerpt") or ""
    missing = [value for value in dict.fromkeys(_leaves(payload)) if not value or value not in excerpt]
    if missing:
        return f"value {', '.join(map(repr, missing))} does not appear in the recorded output of drift.command"
    return None


def _target_refusal(command: str, goal: str) -> Optional[str]:
    from modules.security.canonical_path import command_name
    from modules.security.targets import (
        UNDETERMINED, UNREADABLE, _attached_short, _flag_values, _literal, _operation, _split, command_targets,
    )
    from modules.tools.shell_parser import parse_command

    if len(parse_command(command)) != 1:
        return "drift.command runs more than one command"
    split = _split(command)
    if split is None or not split[1]:
        return "drift.command cannot be parsed"
    _, words, prefix = split
    if prefix:
        return f"drift.command has {shlex.join(prefix)} written before the program"
    program, args = command_name(words[0]), words[1:]
    if program not in _DIMENSIONS:
        return f"drift.command runs {program}, whose target is not modelled"
    options = args[:args.index("--")] if "--" in args else args
    repointing = frozenset(_REPOINTING[program])
    used = [word for word in options if word.partition("=")[0] in repointing or _attached_short(word, repointing)]
    if used:
        return f"drift.command passes {', '.join(used)}"
    operation, _, known = _operation(words)
    if operation is None or not known:
        return "drift.command has an unknown option before its operation"
    names = []
    for dimension, flags in _DIMENSIONS[program]:
        values = set(map(_literal, _flag_values(args, frozenset(flags))))
        if not values:
            return f"drift.command names no explicit {dimension}"
        if len(values) > 1:
            return f"drift.command names {len(values)} {dimension}s"
        if values & {UNDETERMINED, UNREADABLE}:
            return f"drift.command computes its {dimension}"
        names += values
    targets = command_targets(command, ambient=False)
    if any(target.kind != "flag" or target.name in (UNDETERMINED, UNREADABLE) for target in targets):
        return "drift.command reaches a target that is not determinate"
    unnamed = [name for name in dict.fromkeys(names + [t.name for t in targets]) if not _named_in(goal, name)]
    if unnamed:
        return f"drift.command targets {', '.join(unnamed)}, which the goal did not name"
    return None


def auto_apply_refusals(
    con: sqlite3.Connection, entry: dict, owner: Optional[dict], workspace: Optional[str], sections: dict,
    records: list, goal: str,
) -> List[str]:
    """Every reason the proposal stays pending for the orchestrator; empty when each auto-apply guard holds."""
    from gaia.store.project_context import resolve_project

    drift = entry.get("drift")
    command = drift.get("command") if isinstance(drift, dict) else None
    if not isinstance(command, str) or not command.strip():
        return ["no drift.command names the read that observed the value"]
    _hooks_on_path()
    from modules.security.tiers import SecurityTier, classify_command_tier

    refusals = []
    named = entry.get("project")
    if owner is None:
        refusals.append("its project does not resolve")
    elif named and (resolve_project(con, named, workspace) or {}).get("identity") != owner["identity"]:
        refusals.append(f"names project {named}, not the dispatch's project")
    refusals += _section_refusals(entry, sections)
    refusals.append(_output_refusal(command, entry.get("payload"), records))
    if classify_command_tier(command) != SecurityTier.T0_READ_ONLY:
        refusals.append("drift.command is not a T0 read")
    refusals.append(_target_refusal(command, goal))
    return [refusal for refusal in refusals if refusal]


def _evidence(entry: dict) -> str:
    drift = entry["drift"]
    return (f"{entry['contract']} keys {', '.join(sorted(entry['payload']))}; read by "
            f"{drift['command']!r} at {drift.get('observed_at') or '(no observed_at)'}")


def auto_apply(con: sqlite3.Connection, handoff_id: int, envelope: Any) -> list:
    """Apply the close's pending context proposals whose guards all hold, in the caller's transaction; return their ids."""
    row = con.execute(
        "SELECT dispatch_prompt, dispatch_project, workspace, agent_id, contract_id "
        "FROM agent_contract_handoffs WHERE id = ?", (handoff_id,),
    ).fetchone()
    if row is None or not isinstance(envelope, dict):
        return []
    goal, dispatch_project, workspace, agent_id, contract_id = row
    _hooks_on_path()
    from gaia.store.project_context import project_sections, resolve_project
    from gaia.store.writer import tool_records_on
    from modules.context.context_writer import merge_into_section

    owner = resolve_project(con, dispatch_project, workspace)
    records = tool_records_on(con, contract_id)["records"]
    applied = []
    pending = con.execute(
        "SELECT id, payload FROM proposals WHERE handoff_id = ? AND kind = 'context' "
        "AND status = 'pending'", (handoff_id,),
    ).fetchall()
    for proposal_id, payload in pending:
        entry = json.loads(payload)
        sections = project_sections(con, owner) if owner else {}
        refusals = auto_apply_refusals(con, entry, owner, workspace, sections, records, goal or "")
        if refusals:
            if "drift" in entry:
                con.execute("UPDATE proposals SET decision_note = ? WHERE id = ?",
                            (f"not auto-applied: {'; '.join(refusals)}", proposal_id))
            continue
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        merge_into_section(con, owner, entry["contract"], entry["payload"], now, agent_id, contract_id)
        con.execute(
            "UPDATE proposals SET status = 'accepted', project = ?, decided_at = ?, decided_by = ?, "
            "decision_note = ? WHERE id = ?",
            (owner["identity"], now, f"auto:{agent_id}", f"auto-applied {_evidence(entry)}", proposal_id),
        )
        con.execute(
            "INSERT INTO signals (subject, source, workspace, ref, op) VALUES (?, 'project_context', ?, ?, 'update')",
            (f"context:{owner['identity']}", owner["workspace"], entry["contract"]),
        )
        applied.append(proposal_id)
    return applied
