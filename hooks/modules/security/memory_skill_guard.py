"""Orchestrator memory writes wait for Skill('memory') in the current session.

A compaction drops the skill's body from context, and the writes made without
it are the ones that land in the wrong home. The state lives in
``harness_events``: a Skill/skill PostToolUse from the main session appends
``memory.skill_loaded``, SessionStart(compact) appends ``memory.skill_dropped``,
and the newer of the two for the session decides. Subagents are decided by
``subagent_memory_write_guard`` alone.
"""

from __future__ import annotations

import logging
from typing import Optional, Tuple

from .subagent_memory_write_guard import is_memory_write_attempt

logger = logging.getLogger(__name__)

SKILL_LOADED_EVENT = "memory.skill_loaded"
SKILL_DROPPED_EVENT = "memory.skill_dropped"
SKILL_TOOL_NAMES = frozenset({"Skill", "skill"})
GUARDED_VERBS = frozenset({"add", "append", "reclassify", "link"})

REJECTION_MESSAGE = (
    "[MEMORY_SKILL_REQUIRED] A memory write needs Skill('memory') loaded in this "
    "session since its last compaction. Load Skill('memory'), then retry this "
    "identical command. Not approvable: there is no approval_id."
)


def is_memory_skill(tool_name: str, tool_input: object) -> bool:
    """True when the call loads the memory skill (Claude Code ``skill``, OpenCode ``name``)."""
    if tool_name not in SKILL_TOOL_NAMES or not isinstance(tool_input, dict):
        return False
    name = str(tool_input.get("skill") or tool_input.get("name") or "")
    return name.rsplit(":", 1)[-1] == "memory"


def record_skill_loaded(session_id: str) -> None:
    """Mark the memory skill loaded for ``session_id``; never raises."""
    _record(SKILL_LOADED_EVENT, session_id)


def record_skill_dropped(session_id: str) -> None:
    """Mark the memory skill dropped by a compaction of ``session_id``; never raises."""
    _record(SKILL_DROPPED_EVENT, session_id)


def _record(event_type: str, session_id: str) -> None:
    if not session_id:
        return
    try:
        from gaia.store.writer import write_harness_event

        write_harness_event(event_type=event_type, source="hook", meta={"session_id": session_id})
    except Exception as exc:
        logger.warning("memory skill state not recorded (%s): %s", event_type, exc)


def check(command: str, *, is_subagent: bool, session_id: str) -> Tuple[bool, Optional[str]]:
    """Refuse an orchestrator memory write made without the memory skill loaded.

    Fails open with a warning when the session state cannot be read: failing
    closed would stop all curation for as long as the database is unavailable.
    """
    if is_subagent or not is_memory_write_attempt(command, verbs=GUARDED_VERBS):
        return True, None
    try:
        from gaia.store.reader import latest_session_event_type

        latest = latest_session_event_type(session_id, (SKILL_LOADED_EVENT, SKILL_DROPPED_EVENT))
    except Exception as exc:
        logger.warning("memory skill guard failed open: session state unreadable (%s)", exc)
        return True, None
    if latest == SKILL_LOADED_EVENT:
        return True, None
    return False, REJECTION_MESSAGE
