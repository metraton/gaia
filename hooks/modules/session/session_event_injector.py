"""The recent-events digest a dispatch used to carry, retired.

The events file (``<data home>/session/active/context.json``) is one per
machine, written by every session, so a digest built from it could hand a
subagent another session's commit message.
"""
from __future__ import annotations


def build_session_events(parameters: dict) -> None:
    """No digest for any dispatch; kept as the call site ``tool_policy._task_verdict`` still imports."""
    return None
