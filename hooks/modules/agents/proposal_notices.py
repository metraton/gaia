"""The one-line notice that a finished turn proposed project facts.

A specialist never writes memory or project context; it proposes in
``memory_delta`` and the orchestrator, the only curator, decides. The notice
rides the dispatch's PostToolUse result line (``task_result_observer``), the
channel that reaches the orchestrator's context on both hosts. SubagentStop's
``systemMessage`` goes to the user's display and its ``additionalContext``
resumes the closing subagent, so neither carries it.
"""

from __future__ import annotations

from typing import Any, Optional

from .response_contract import parse_memory_delta


def memory_delta_notice(
    contract: Any, *, contract_id: Optional[str], dispatch_project: Optional[str],
) -> str:
    """The notice for a contract's ``memory_delta`` proposals, or "" when it carries none.

    ``dispatch_project`` is the born row's ``name (/abs/path)`` display; only
    the name is shown.
    """
    if not isinstance(contract, dict):
        return ""
    count = len(parse_memory_delta("", parsed_contract=contract).proposals)
    if not count:
        return ""
    project = (dispatch_project or "").partition(" (")[0]
    noun = "project fact" if count == 1 else "project facts"
    target = f" for {project}" if project else ""
    return f"{count} {noun} proposed{target} — contract {contract_id or 'unknown'}"
