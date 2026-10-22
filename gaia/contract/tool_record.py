"""A turn's commands_run and files_checked, rebuilt at finalize from the tool calls the harness recorded for it."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from gaia.contract import read_spool

TOOL_CALLED = "tool.called"
ANNOTATION_MARK = "[agent annotation] "

# These calls are how the turn wrote its record, not evidence it gathered.
CONTRACT_BOOKKEEPING = re.compile(r"^\s*gaia\s+contract\b")


def _command_entry(record: Dict[str, Any]) -> str:
    code = record.get("exit_code")
    return record["command"] if code is None else f"{record['command']} (exit {code})"


def harness_evidence(
    records: Iterable[Dict[str, Any]], agent_commands: Iterable[Any], agent_files: Iterable[Any],
    spooled_files: Iterable[str] = (),
) -> Dict[str, List[str]]:
    """The recorded commands and paths, the spooled paths, then the agent's own entries the record does not hold, marked as annotations."""
    commands: List[str] = []
    files: List[str] = []
    ran = set()
    for record in records:
        command = record.get("command")
        path = record.get("path")
        if isinstance(command, str) and command and not CONTRACT_BOOKKEEPING.match(command):
            ran.add(command)
            commands.append(_command_entry(record))
        elif isinstance(path, str) and path and path not in files:
            files.append(path)
    files.extend(path for path in dict.fromkeys(spooled_files) if path not in files)
    recorded_commands = ran | set(commands)
    for entry in agent_commands:
        if isinstance(entry, str) and entry and entry not in recorded_commands:
            commands.append(entry if entry.startswith(ANNOTATION_MARK) else ANNOTATION_MARK + entry)
    for entry in agent_files:
        if isinstance(entry, str) and entry and entry not in files:
            files.append(entry if entry.startswith(ANNOTATION_MARK) else ANNOTATION_MARK + entry)
    return {"commands_run": commands, "files_checked": files}


def stamp_harness_fields(
    envelope: Dict[str, Any], contract_id: str, *, worktree_scope: str, db_path: Optional[Path] = None,
) -> None:
    """Overwrite agent_id, worktree_scope and, for a harness-bound turn, the evidence lists with what the harness holds."""
    from gaia.store.writer import tool_records_for_contract

    found = tool_records_for_contract(contract_id, db_path=db_path)
    status = envelope.setdefault("agent_status", {})
    status["agent_id"] = found["agent_id"] or contract_id.split(".", 1)[0]
    envelope["worktree_scope"] = worktree_scope
    if not found["harness_agent_id"]:
        return
    report = envelope.setdefault("evidence_report", {})
    report.update(harness_evidence(
        found["records"], report.get("commands_run") or [], report.get("files_checked") or [],
        read_spool.read(found["harness_agent_id"]),
    ))
    envelope["harness_record"] = {
        "harness_agent_id": found["harness_agent_id"],
        "through_event_id": found["through_event_id"],
    }


def commit_harness_record(envelope: Dict[str, Any]) -> None:
    """Delete the read spool the envelope was stamped from; called once the row that holds its paths is written."""
    key = (envelope.get("harness_record") or {}).get("harness_agent_id")
    if key:
        read_spool.discard(key)


def recover_spooled_files(
    envelope: Dict[str, Any], contract_id: str, *, db_path: Optional[Path] = None,
) -> Optional[str]:
    """Add the paths a cut turn spooled to the envelope's files_checked; returns the harness id whose spool to discard once the row is written."""
    from gaia.store.writer import tool_records_for_contract

    key = tool_records_for_contract(contract_id, db_path=db_path)["harness_agent_id"]
    if not key:
        return None
    report = envelope.get("evidence_report")
    if not isinstance(report, dict):
        report = envelope["evidence_report"] = {}
    files = report.get("files_checked")
    files = list(files) if isinstance(files, list) else []
    report["files_checked"] = files + [path for path in read_spool.read(key) if path not in files]
    return key
