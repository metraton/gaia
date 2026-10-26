"""The host's compaction window per session, read by SubagentStop to recognize the summarizer."""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Optional

from gaia.agent_identity import ORCHESTRATOR

TTL_SECONDS = 600
_KEY_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]")


def _marker(session_id: str) -> Path:
    from modules.core.paths import get_session_dir

    return get_session_dir() / f"{_KEY_SAFE_RE.sub('-', session_id)[:120]}.compacting"


def open_bracket(session_id: Optional[str]) -> None:
    """Record that the host started compacting ``session_id``."""
    if session_id:
        _marker(session_id).write_text(str(time.time()), encoding="utf-8")


def close_bracket(session_id: Optional[str]) -> None:
    """Record that the host finished compacting ``session_id``."""
    if session_id:
        _marker(session_id).unlink(missing_ok=True)


def in_bracket(session_id: Optional[str]) -> bool:
    """Whether ``session_id`` is inside a compaction window opened within the last TTL."""
    if not session_id:
        return False
    try:
        opened = float(_marker(session_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return 0 <= time.time() - opened <= TTL_SECONDS


def is_compaction_summarizer(agent_type: str, session_id: Optional[str]) -> bool:
    """Whether a SubagentStop is the host's summarizer: the main thread's type, mid-compaction."""
    return agent_type == ORCHESTRATOR and in_bracket(session_id)
