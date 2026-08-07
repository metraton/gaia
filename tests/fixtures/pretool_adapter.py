"""Drive the REAL PreToolUse path in tests: adapter.adapt_pre_tool_use.

The retired backward-compat API of hooks/pre_tool_use.py diverged from
production (no delegate-mode gate, no CLI-only guard, no identity injection,
no born-at-dispatch row). Tests migrate here and exercise the same code the
stdin entry point runs.

Two identity notes that shape every caller:

* A payload WITHOUT agent_id/agent_type classifies as ORCHESTRATOR
  (delegate mode), whose Bash lane is restricted to the trusted gaia CLI --
  a generic command would be denied by gaia_cli_only_guard before tier
  classification. Tests measuring TIER behavior therefore run as a SUBAGENT
  (agent_id present), which is also who runs domain commands in production.
* A subagent's blocked T3 produces a "deny" decision carrying an approval_id
  (DB pending created by the hook), not the native "ask" dialog.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

_REPO_ROOT = Path(__file__).resolve().parents[2]
_HOOKS_DIR = str(_REPO_ROOT / "hooks")
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

# A stable, shape-valid harness agent id marking the payload as a subagent.
SUBAGENT_AGENT_ID = "a1b2c3d4e5f60718"


def run_pre_tool_use(
    tool_name: str,
    tool_input: Dict[str, Any],
    *,
    session_id: str = "sess-pretool-fixture",
    agent_id: Optional[str] = None,
    agent_type: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
):
    """Run one PreToolUse event through the real adapter; returns HookResponse."""
    from adapters.registry import get_adapter

    payload: Dict[str, Any] = {
        "hook_event_name": "PreToolUse",
        "tool_name": tool_name,
        "tool_input": tool_input,
        "session_id": session_id,
    }
    if agent_id:
        payload["agent_id"] = agent_id
    if agent_type:
        payload["agent_type"] = agent_type
    if extra:
        payload.update(extra)

    adapter = get_adapter()
    event = adapter.parse_event(json.dumps(payload))
    return adapter.adapt_pre_tool_use(event)


def run_subagent_bash(
    command: str,
    *,
    session_id: str = "sess-pretool-fixture",
    agent_type: str = "developer",
    extra: Optional[Dict[str, Any]] = None,
):
    """Run a Bash command as a dispatched subagent (the tier-classifying lane)."""
    return run_pre_tool_use(
        "Bash",
        {"command": command},
        session_id=session_id,
        agent_id=SUBAGENT_AGENT_ID,
        agent_type=agent_type,
        extra=extra,
    )


def compat_shape(response):
    """Map a HookResponse onto the retired API's return shape.

    None  -> allowed without modification
    str   -> blocked (error message)
    dict  -> decision payload (allow+updatedInput, ask, or deny)

    Keeps migrated assertions readable; new tests should assert on the
    HookResponse directly.
    """
    out = response.output
    if isinstance(out, dict):
        return out or None
    if isinstance(out, str):
        return out or None
    return None
