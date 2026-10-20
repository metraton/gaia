"""The notice that a finished turn proposed project facts or project-context updates.

A specialist never writes memory or project context; it proposes in
``memory_delta`` and ``update_contracts`` and the orchestrator, the only
curator, decides. The notice rides the dispatch's PostToolUse result line
(``task_result_observer``), the channel that reaches the orchestrator's context
on both hosts. SubagentStop's ``systemMessage`` goes to the user's display and
its ``additionalContext`` resumes the closing subagent, so neither carries it.
"""

from __future__ import annotations

from typing import Any, Optional

from ..context.context_writer import proposed_entries
from .response_contract import parse_memory_delta


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


def proposal_notice(
    contract: Any, *, contract_id: Optional[str], dispatch_project: Optional[str],
) -> str:
    """One line per kind of proposal the contract carries, or "" when it carries none."""
    if not isinstance(contract, dict):
        return ""
    contract_id = contract_id or "unknown"
    project = _project_name(dispatch_project)
    lines = (
        _memory_delta_line(contract, contract_id, project),
        _update_contracts_line(contract, contract_id, project),
    )
    return "\n".join(line for line in lines if line)
