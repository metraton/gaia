"""
contract_ownership_guard.py -- PreToolUse Bash guard for contract ownership.

A subagent turn is bound to the contract row born for it: SubagentStart stamps
that row with the host's own agent id (``harness_agent_id``). Two attempts break
the binding and are otherwise discovered only when the stop gate rejects the
close:

    * ``gaia contract init`` from a turn that already owns a row. A resumed turn
      receives no kernel block, runs ``init``, and mints a rival draft.
    * ``gaia contract set|add|fill|finalize --draft-id X`` where X is stamped to
      another turn's harness id.

Both are refused at the attempt, naming the owner. The denial is categorical
(no approval_id): a grant cannot make a contract belong to another turn.

A turn with no stamped row -- the documented fallback, where the claim found
nothing to inject -- still gets its bare ``init``. The orchestrator is never
checked, and a failed lookup allows the command: this guard informs, the stop
gate remains the enforcement.

Public API:
    check(command, harness_agent_id) -> (allowed, reason)
"""

from __future__ import annotations

import logging
import os
from typing import Iterator, Optional, Tuple

from .mutative_verbs import _peel_leading_command_wrappers
from .shell_write_guard import _split_components, _tokenize

logger = logging.getLogger(__name__)

_WRITE_VERBS = frozenset({"set", "add", "fill", "finalize"})
_LOOKUP_LIMIT = 50


def _contract_calls(command: str) -> Iterator[Tuple[str, Optional[str]]]:
    """Yield ``(verb, draft_id)`` for each ``gaia contract <verb>`` component."""
    for component in _split_components(command or ""):
        if not component.strip():
            continue
        peeled, _ = _peel_leading_command_wrappers(component.strip())
        tokens = _tokenize(peeled)
        if len(tokens) < 3 or os.path.basename(tokens[0]) != "gaia":
            continue
        if tokens[1] != "contract":
            continue
        verb = tokens[2]
        if verb != "init" and verb not in _WRITE_VERBS:
            continue
        yield verb, _draft_id_of(tokens[3:])


def _draft_id_of(tokens: list) -> Optional[str]:
    for index, token in enumerate(tokens):
        if token == "--draft-id" and index + 1 < len(tokens):
            return tokens[index + 1].strip("'\"")
        if token.startswith("--draft-id="):
            return token.split("=", 1)[1].strip("'\"")
    return None


def _describe(row: dict) -> str:
    return f"{row.get('contract_id')} (state {row.get('agent_state')})"


def _own_row_message(own: dict) -> str:
    return (
        f"You already have a contract: {_describe(own)}. `gaia contract init` would "
        f"mint a second one the stop gate does not check. Continue it with "
        f"`gaia contract set|add|fill --draft-id {own['contract_id']}`; if it is "
        f"already closed, the first write opens a linked continuation."
    )


def _foreign_row_message(row: dict, own: Optional[dict]) -> str:
    owner = row.get("harness_agent_id")
    message = (
        f"Contract {_describe(row)} is adopted by another turn (host agent {owner}); "
        f"a write from this turn would corrupt its record."
    )
    if own is not None:
        return message + f" Your own contract is {own['contract_id']}; use --draft-id with it."
    return message + " Run `gaia contract init` for a contract of your own."


def check(command: str, harness_agent_id: str) -> Tuple[bool, Optional[str]]:
    """Refuse a contract open/write that belongs to another turn, naming the owner.

    ``harness_agent_id`` is the host's id for the calling subagent; empty means
    the orchestrator or a human, which are not checked.
    """
    if not harness_agent_id:
        return True, None
    calls = list(_contract_calls(command))
    if not calls:
        return True, None
    try:
        from gaia.store.writer import list_agent_contract_handoffs

        owned = list_agent_contract_handoffs(
            harness_agent_id=harness_agent_id, limit=_LOOKUP_LIMIT,
        )
        own = max(owned, key=lambda r: r.get("id") or 0) if owned else None
        for verb, draft_id in calls:
            if verb == "init":
                if own is not None:
                    return False, _own_row_message(own)
                continue
            if not draft_id:
                continue
            rows = list_agent_contract_handoffs(contract_id=draft_id, limit=1)
            owner = rows[0].get("harness_agent_id") if rows else None
            if owner and owner != harness_agent_id:
                return False, _foreign_row_message(rows[0], own)
    except Exception as exc:
        logger.debug("contract ownership lookup failed, allowing: %s", exc)
    return True, None
