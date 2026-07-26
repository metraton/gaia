"""Always-on, one-line-per-invocation trace of the hook pipeline.

``configure_hook_logging`` gates Python's own hook logging behind ``GAIA_DEBUG``
and attaches a ``NullHandler`` otherwise -- correct for verbose debug prose, but
it leaves the default installation with no record whatsoever of which hooks ran.
Diagnosing a hook that silently never fired then depends entirely on harness
transcripts, which are not part of Gaia's substrate.

This module writes the minimum that makes "did this hook run?" answerable
offline: one JSON object per line in ``<logs>/hook-trace.jsonl`` carrying the
timestamp, hook name, agent, session, exit code, and whether the invocation
rejected the operation. It is deliberately NOT the audit log (``modules.audit``)
and NOT the episodic event stream (``harness_events``) -- it records the
INVOCATION, including the ones that decide nothing.

Gotchas:
- Silent by contract. Every failure is swallowed: a trace that cannot be
  written must never change what a hook does.
- Bounded by size, not by time. The file rotates to a single ``.1`` sibling
  once it exceeds ``GAIA_HOOK_TRACE_MAX_BYTES`` (default 2 MiB), so total
  on-disk cost is capped at twice that regardless of session volume.
- Opt out with ``GAIA_HOOK_TRACE=0`` (accepts the usual falsy spellings).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

TRACE_FILENAME = "hook-trace.jsonl"

_DEFAULT_MAX_BYTES = 2 * 1024 * 1024
_FALSE_VALUES = {"0", "false", "no", "off"}

# Exit code the hook contract reserves for "operation rejected".
_BLOCKING_EXIT_CODE = 2


def trace_enabled() -> bool:
    """Whether tracing is on (default yes; ``GAIA_HOOK_TRACE=0`` disables)."""
    return os.environ.get("GAIA_HOOK_TRACE", "").strip().lower() not in _FALSE_VALUES


def _max_bytes() -> int:
    """Rotation threshold, overridable for tests and constrained installs."""
    raw = os.environ.get("GAIA_HOOK_TRACE_MAX_BYTES", "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    return _DEFAULT_MAX_BYTES


def trace_path() -> Path:
    """Absolute path of the trace file."""
    from .paths import get_logs_dir

    return get_logs_dir() / TRACE_FILENAME


def _rotate_if_needed(path: Path) -> None:
    """Roll the trace over to a single ``.1`` backup once it exceeds the cap."""
    try:
        if path.exists() and path.stat().st_size >= _max_bytes():
            os.replace(path, path.with_suffix(path.suffix + ".1"))
    except OSError:
        pass


def _extract_identity(payload: Optional[Mapping[str, Any]]) -> dict:
    """Pull the session/agent/tool coordinates out of a raw hook payload."""
    if not isinstance(payload, Mapping):
        return {}
    tool_input = payload.get("tool_input")
    agent = None
    if isinstance(tool_input, Mapping):
        agent = tool_input.get("subagent_type")
    if not agent:
        agent = payload.get("agent_type") or payload.get("subagent_type")
    fields = {
        "session": payload.get("session_id"),
        "agent": agent,
        "tool": payload.get("tool_name"),
        "event": payload.get("hook_event_name"),
    }
    return {k: v for k, v in fields.items() if v}


def record_hook_invocation(
    hook_name: str,
    *,
    payload: Optional[Mapping[str, Any]] = None,
    exit_code: int = 0,
    blocked: Optional[bool] = None,
    extra: Optional[Mapping[str, Any]] = None,
) -> None:
    """Append one trace line for a hook invocation. Never raises.

    Args:
        hook_name: Entry point that ran (e.g. "post_tool_use").
        payload:   Raw hook stdin payload, mined for session/agent/tool.
        exit_code: Exit code the hook returned.
        blocked:   Whether the invocation rejected the operation. When None it
            is derived from the exit code (2 == rejection).
        extra:     Additional scalar fields to merge into the line.
    """
    if not trace_enabled():
        return
    try:
        record = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "hook": hook_name,
            "exit_code": exit_code,
            "blocked": bool(exit_code == _BLOCKING_EXIT_CODE) if blocked is None else bool(blocked),
        }
        record.update(_extract_identity(payload))
        if extra:
            record.update(dict(extra))

        path = trace_path()
        _rotate_if_needed(path)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, default=str, separators=(",", ":")) + "\n")
    except Exception:
        pass


__all__ = [
    "TRACE_FILENAME",
    "record_hook_invocation",
    "trace_enabled",
    "trace_path",
]
