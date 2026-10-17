"""Confine gaia-verifier's file writes to its own scratch entry.

The verifier writes probes, never the artifact it judges: a Write or Edit from
it passes only when the resolved target lies in the Gaia scratch directory
under a name its contract owns -- the bare contract_id, the contract_id
followed by ``.``, or anything inside an entry so named. The contract_id is
resolved from the host's agent id through the dispatch row, never taken from
the tool input, so the verifier cannot name another turn's entry as its own.
"""

from __future__ import annotations

import os
from typing import Optional

VERIFIER_AGENT_TYPE = "gaia-verifier"


def _contract_id_for(harness_agent_id: str) -> str:
    from gaia.store.writer import find_dispatch_row_by_harness_agent_id

    row = find_dispatch_row_by_harness_agent_id(harness_agent_id)
    return str((row or {}).get("contract_id") or "")


def _owned_entry(target: str, scratch: str, contract_id: str) -> bool:
    try:
        if os.path.commonpath([scratch, target]) != scratch or target == scratch:
            return False
    except ValueError:
        return False
    entry = os.path.relpath(target, scratch).split(os.sep, 1)[0]
    return entry == contract_id or entry.startswith(contract_id + ".")


def check(file_path: str, *, agent_type: str, harness_agent_id: str) -> Optional[str]:
    """Return the refusal for a verifier write outside its scratch entry, else None."""
    if agent_type != VERIFIER_AGENT_TYPE:
        return None
    from gaia.paths.resolver import scratch_dir

    scratch = os.path.realpath(scratch_dir())
    contract_id = _contract_id_for(harness_agent_id) if harness_agent_id else ""
    target = os.path.realpath(os.path.expanduser(file_path))
    if contract_id and _owned_entry(target, scratch, contract_id):
        return None
    owner = contract_id or "<contract_id unresolved>"
    return (
        f"[VERIFIER_SCRATCH_ONLY] gaia-verifier writes only its own probes, under "
        f"{scratch}/{owner} or {scratch}/{owner}.<ext>; '{file_path}' is outside "
        "them. The artifact under verification stays as the producer left it: "
        "report the needed change and its owner instead."
    )
