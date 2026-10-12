"""Tell a dispatched turn, once, that it is nearing its tool-call ceiling.

A dispatch prompt may carry ``ceiling=<N>`` (``extract_dispatch_binding``), the
tool calls the orchestrator budgets for the turn. Each host's PostToolUse
adapter counts the subagent's calls here, keyed by the harness id its contract
row was stamped with at SubagentStart, and on the call that reaches
:data:`NOTICE_PERCENT` of the ceiling returns a notice the model reads on its
next step. The orchestrator's own session has no harness id and is never
counted.

Context use is not a fallback: no Claude Code hook input carries it, and
OpenCode's ``tool.execute.after`` bridge payload does not either, so a dispatch
without the token gets no notice. Evidence lost anyway is recovered at close by
the SubagentStop path; this module only warns during the turn.
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

NOTICE_PERCENT = 80

_KEY_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]")


def notice_threshold(ceiling: int) -> int:
    """The call count at which the notice fires for ``ceiling``."""
    return max(1, ceiling * NOTICE_PERCENT // 100)


def _state_path(harness_agent_id: str) -> Path:
    from gaia.paths import state_dir

    directory = state_dir() / "call_ceiling"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{_KEY_SAFE_RE.sub('-', harness_agent_id)[:120]}.json"


def _read(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(path: Path, state: dict) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    os.replace(tmp, path)


def _dispatch_ceiling(harness_agent_id: str) -> Tuple[bool, Optional[int]]:
    """``(row_found, ceiling)`` read from the dispatch prompt of the turn's row."""
    from gaia.store.writer import find_dispatch_row_by_harness_agent_id
    from modules.agents.dispatch_binding import extract_dispatch_binding

    row = find_dispatch_row_by_harness_agent_id(harness_agent_id)
    if row is None:
        return False, None
    return True, extract_dispatch_binding({"prompt": row.get("dispatch_prompt")})["ceiling"]


def _notice(calls: int, ceiling: int) -> str:
    return (
        f"Tool-call ceiling: this is call {calls} of {ceiling}, "
        f"{max(0, ceiling - calls)} remain. Write your evidence lists now "
        "(files_checked, patterns_checked, commands_run, key_outputs, "
        "verbatim_outputs, open_gaps), then set agent_state and finalize your "
        "contract -- close partially if the work is not done, naming what "
        "remains in pending_steps."
    )


def reset(harness_agent_id: Optional[str]) -> None:
    """Drop the turn's count once SubagentStop accepts its close."""
    if not harness_agent_id:
        return
    try:
        _state_path(harness_agent_id).unlink(missing_ok=True)
    except Exception:
        logger.debug("call ceiling reset failed for %s", harness_agent_id, exc_info=True)


def record_call(harness_agent_id: Optional[str]) -> Optional[str]:
    """Count one tool call of the turn ``harness_agent_id`` names.

    Returns the notice on the call that reaches the threshold, else None; never
    raises. Two calls finishing at once can lose one increment, which shifts
    the notice by a call rather than repeating it.
    """
    if not harness_agent_id:
        return None
    try:
        path = _state_path(harness_agent_id)
        state = _read(path)
        if state.get("noticed") or ("ceiling" in state and not state["ceiling"]):
            return None
        state["calls"] = int(state.get("calls", 0)) + 1
        if "ceiling" not in state:
            found, ceiling = _dispatch_ceiling(harness_agent_id)
            if found:
                state["ceiling"] = ceiling
        ceiling = state.get("ceiling")
        state["noticed"] = bool(ceiling) and state["calls"] >= notice_threshold(ceiling)
        _write(path, state)
        return _notice(state["calls"], ceiling) if state["noticed"] else None
    except Exception:
        logger.warning("call ceiling count failed for %s", harness_agent_id, exc_info=True)
        return None
