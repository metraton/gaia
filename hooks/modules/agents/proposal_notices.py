"""The notice that a finished turn proposed project facts or project-context updates.

A specialist never writes memory or project context; it proposes in
``memory_delta`` and ``update_contracts`` and the orchestrator, the only
curator, decides. The notice rides the dispatch's PostToolUse result line
(``task_result_observer``), the channel that reaches the orchestrator's context
on both hosts. SubagentStop's ``systemMessage`` goes to the user's display and
its ``additionalContext`` resumes the closing subagent, so neither carries it.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from ..context.context_writer import proposed_entries
from .response_contract import parse_memory_delta

LIST_VERB = "gaia contract proposals list"


def _project_name(dispatch_project: Optional[str]) -> str:
    """The name in the born row's ``name (/abs/path)`` display."""
    return (dispatch_project or "").partition(" (")[0]


def _memory_delta_line(contract: dict, contract_id: str, project: str) -> str:
    count = len(parse_memory_delta("", parsed_contract=contract).proposals)
    if not count:
        return ""
    noun = "project fact" if count == 1 else "project facts"
    target = f" for {project}" if project else ""
    return f"{count} {noun} proposed{target} — contract {contract_id}"


def _update_contracts_line(contract: dict, contract_id: str, project: str) -> str:
    entries, normalized = proposed_entries(contract.get("update_contracts"))
    if not entries:
        return ""
    noun = "project-context update" if len(entries) == 1 else "project-context updates"
    target = f" for {project}" if project else ""
    note = f" ({normalized})" if normalized else ""
    return (
        f"{len(entries)} {noun} proposed{target} — contract {contract_id}{note}; "
        f"apply with `gaia context accept {contract_id}`"
    )


def _refused_line(contract: dict, contract_id: str) -> str:
    """The reasons SubagentStop stamped on the row for entries it did not record."""
    refused = contract.get("update_contracts_refused")
    if not isinstance(refused, list) or not refused:
        return ""
    return (
        f"update_contracts NOT recorded — contract {contract_id}: "
        + "; ".join(str(reason) for reason in refused)
    )


def proposal_notice(
    contract: Any, *, contract_id: Optional[str], dispatch_project: Optional[str],
) -> str:
    """One line per proposal kind and one for refusals, or "" when the contract carries none."""
    if not isinstance(contract, dict):
        return ""
    contract_id = contract_id or "unknown"
    lines = (
        *_proposed_lines(contract, contract_id, _project_name(dispatch_project)),
        _refused_line(contract, contract_id),
    )
    return "\n".join(line for line in lines if line)


def _proposed_lines(contract: dict, contract_id: str, project: str) -> list[str]:
    lines = (
        _memory_delta_line(contract, contract_id, project),
        _update_contracts_line(contract, contract_id, project),
    )
    return [line for line in lines if line]


def background_notice(row: dict) -> str:
    """The close's proposal notice under its conclusion line, claimed on the key the result line uses; "" if shown."""
    from gaia.conclusions import claim

    try:
        envelope = json.loads(row.get("raw_handoff_json") or "null")
        contract_id = str(row.get("contract_id") or "")
        project = row.get("dispatch_project")
        notice = proposal_notice(envelope, contract_id=contract_id, dispatch_project=project)
        if not notice or not claim(str(row.get("session_id") or ""), contract_id, notice):
            return ""
        recorded = _proposed_lines(envelope, contract_id, _project_name(project))
    except (TypeError, ValueError):
        return ""
    return f"{notice}\nsee them: `{LIST_VERB}`" if recorded else notice
