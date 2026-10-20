"""One-line notices that tell the orchestrator a specialist left it something to decide.

A specialist never writes memory or project context; it proposes in its
contract, and SubagentStop raises a notice so the orchestrator -- the only
curator -- reads the proposal instead of finding it by chance. Each notice is a
single line that names the contract to open.
"""

from __future__ import annotations

from typing import Any, Optional

from .response_contract import parse_memory_delta


def append_notice(message: Optional[str], notice: str) -> str:
    """``notice`` on its own line after ``message``, or alone when there is no message."""
    return f"{message}\n{notice}" if message else notice


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
