"""Whether the main session loaded Skill('human-register') since its last compaction, kept as ``memory_skill_guard`` keeps the memory skill's."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

SKILL_LOADED_EVENT = "human_register.skill_loaded"
SKILL_DROPPED_EVENT = "human_register.skill_dropped"
REGISTER_SKILL_NAMES = frozenset({"human-register", "gaia:human-register"})


def is_register_skill(tool_name: str, tool_input: object) -> bool:
    """True when the call loads the human-register skill (Claude Code ``skill``, OpenCode ``name``)."""
    from modules.security.memory_skill_guard import SKILL_TOOL_NAMES

    if tool_name not in SKILL_TOOL_NAMES or not isinstance(tool_input, dict):
        return False
    return str(tool_input.get("skill") or tool_input.get("name") or "") in REGISTER_SKILL_NAMES


def record_loaded(session_id: str) -> None:
    """Mark the skill loaded for ``session_id``; never raises."""
    _record(SKILL_LOADED_EVENT, session_id)


def record_dropped(session_id: str) -> None:
    """Mark the skill dropped by a compaction of ``session_id``; never raises."""
    _record(SKILL_DROPPED_EVENT, session_id)


def _record(event_type: str, session_id: str) -> None:
    if not session_id:
        return
    try:
        from gaia.store.writer import write_harness_event

        write_harness_event(event_type=event_type, source="hook", meta={"session_id": session_id})
    except Exception as exc:
        logger.warning("human-register skill state not recorded (%s): %s", event_type, exc)


def loaded(session_id: str) -> bool:
    """Whether the newest mark for ``session_id`` is a load; False when unread, so the reminder shows."""
    try:
        from gaia.store.reader import latest_session_event_type

        latest = latest_session_event_type(session_id, (SKILL_LOADED_EVENT, SKILL_DROPPED_EVENT))
    except Exception as exc:
        logger.debug("human-register skill state unreadable (%s)", exc)
        return False
    return latest == SKILL_LOADED_EVENT
