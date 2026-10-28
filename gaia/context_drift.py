"""Drift duty and guarded auto-apply for the project-context corrections a turn proposes."""

from __future__ import annotations

import json
import re
import shlex
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

WHAT_SECTIONS = frozenset({"project_identity", "environment"})
WHAT_KEYS = frozenset({
    "environment", "environments", "cluster", "clusters", "cluster_name", "context", "kube_context",
    "project", "project_id", "gcp_project", "namespace", "namespaces",
})

_STALE = re.compile(r"\b(stale|outdated|out of date|contradict\w*|no longer (?:true|matches|accurate))\b",
                    re.IGNORECASE)
_ANNOTATION = "[agent annotation] "
_NAMESPACE_FLAGS = ("-n", "--namespace")


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


def _paths(payload: dict, prefix: tuple = ()) -> list:
    paths = []
    for key, value in payload.items():
        path = prefix + (str(key),)
        paths.extend(_paths(value, path) if isinstance(value, dict) and value else [path])
    return paths


def what_paths(entry: dict) -> list:
    """The dotted key paths of the entry's payload that are WHAT, which only the orchestrator accepts."""
    paths = _paths(entry.get("payload") or {})
    if entry.get("contract") not in WHAT_SECTIONS:
        paths = [path for path in paths if WHAT_KEYS.intersection(path)]
    return [".".join(path) for path in paths]


def _recorded_exit(envelope: dict, command: str) -> Optional[str]:
    if not (envelope.get("harness_record") or {}).get("harness_agent_id"):
        return None
    code = None
    for entry in (envelope.get("evidence_report") or {}).get("commands_run") or []:
        if not isinstance(entry, str) or entry.startswith(_ANNOTATION):
            continue
        ran, marker, tail = entry.rpartition(" (exit ")
        if (ran if marker else entry).strip() == command.strip():
            code = tail.rstrip(")") if marker else None
    return code


def _named_targets(command: str) -> Optional[list]:
    from modules.security.targets import UNDETERMINED, UNREADABLE, command_targets

    try:
        words = shlex.split(command)
    except ValueError:
        return None
    names = [target.name for target in command_targets(command, ambient=False)]
    for index, word in enumerate(words[:-1]):
        if word in _NAMESPACE_FLAGS:
            names.append(words[index + 1])
    names += [word.split("=", 1)[1] for word in words if word.startswith("--namespace=")]
    if any(name in (UNDETERMINED, UNREADABLE) for name in names):
        return None
    return list(dict.fromkeys(names))


def _named_in(goal: str, name: str) -> bool:
    return re.search(rf"(?<![\w.-]){re.escape(name)}(?![\w.-])", goal) is not None


def auto_apply_refusal(entry: dict, envelope: dict, goal: str) -> Optional[str]:
    """Why the proposal stays pending for the orchestrator, or None when every auto-apply guard holds."""
    drift = entry.get("drift")
    command = drift.get("command") if isinstance(drift, dict) else None
    if not isinstance(command, str) or not command.strip():
        return "no drift.command names the read that observed the value"
    what = what_paths(entry)
    if what:
        return f"touches WHAT keys ({', '.join(what)})"
    if _recorded_exit(envelope, command) != "0":
        return "the turn record holds no successful run of drift.command"
    _hooks_on_path()
    from modules.security.tiers import SecurityTier, classify_command_tier

    if classify_command_tier(command) != SecurityTier.T0_READ_ONLY:
        return "drift.command is not a T0 read"
    targets = _named_targets(command)
    if not targets:
        return "drift.command names no explicit cluster, context, project or namespace"
    unnamed = [target for target in targets if not _named_in(goal, target)]
    if unnamed:
        return f"drift.command targets {', '.join(unnamed)}, which the goal did not name"
    return None


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
    from gaia.store.project_context import resolve_project
    from modules.context.context_writer import merge_into_section

    applied = []
    pending = con.execute(
        "SELECT id, payload FROM proposals WHERE handoff_id = ? AND kind = 'context' "
        "AND status = 'pending'", (handoff_id,),
    ).fetchall()
    for proposal_id, payload in pending:
        entry = json.loads(payload)
        refusal = auto_apply_refusal(entry, envelope, goal or "")
        owner = None if refusal else resolve_project(con, entry.get("project") or dispatch_project, workspace)
        if owner is None:
            if "drift" in entry:
                con.execute("UPDATE proposals SET decision_note = ? WHERE id = ?",
                            (f"not auto-applied: {refusal or 'its project does not resolve'}", proposal_id))
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
