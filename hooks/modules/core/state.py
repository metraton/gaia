"""
Hook state management - Share state between pre and post hooks.

Uses a temporary file to pass information from pre_tool_use to post_tool_use,
since they run in separate processes.

State keying (concurrency fix)
------------------------------
Historically ALL pre-hook state was written to a single global file
(``.hooks_state.json``) resolved by cwd. Under concurrent subagents that one
file is clobbered: subagent A writes its ``consumed_approval_id``, subagent B
overwrites it before A's PostToolUse reads it, and A's terminal
(EXECUTED/FAILED) event is silently lost. That race is what produced the
EXECUTED-under-recording (261 executed vs 778 approved).

The fix keys each entry by ``(session_id, tool_use_id)`` -- the host stdin
carries a top-level snake_case ``tool_use_id`` in BOTH PreToolUse and
PostToolUse, and it MATCHES for the same tool call, so a keyed entry written at
PreToolUse is retrieved unambiguously at PostToolUse regardless of how many
other tool calls are in flight. Keyed entries live as individual files under
``.hooks_state/`` so the Stop-hook reconciliation can enumerate every dangling
entry for a session.

Safe fallback: when either id is absent the code degrades to the prior global
single-file behavior (never crashes), preserving back-compat for callers that
cannot supply a key.
"""

import json
import logging
import re
import time
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Iterator, Optional, Tuple
from dataclasses import dataclass, asdict, field

from adapters.host_session import read_host_session_id

from .paths import find_claude_dir

logger = logging.getLogger(__name__)

# Legacy single global state file -- used as the fallback when a per-call key
# (session_id + tool_use_id) is not available.
STATE_FILE_NAME = ".hooks_state.json"

# Directory holding per-(session_id, tool_use_id) keyed state files. Each tool
# call gets its own file so concurrent subagents never clobber one another and
# the Stop-hook reconciliation can list every dangling entry for a session.
STATE_DIR_NAME = ".hooks_state"


def get_session_id() -> str:
    """Return the current host session ID, defaulting to 'default'.

    Reads only the host session environment variable (via the adapter-owned
    ``read_host_session_id`` helper). Hook entry points that have the parsed
    stdin event in hand should prefer ``resolve_session_id(event_data)``
    because the host CLI does not always export the session env var into the
    hook subprocess; it does, however, always include ``session_id`` in the
    JSON event piped to stdin.
    """
    return read_host_session_id()


def resolve_session_id(event_data: Optional[Dict[str, Any]] = None) -> str:
    """Resolve the session id with stdin-event precedence.

    Order:
      1. ``event_data["session_id"]`` when present and non-empty.
      2. The host session environment variable (via the adapter helper).
      3. Literal ``"default"`` (matches ``get_session_id()`` for back-compat).

    Hook entry points should call this immediately after parsing stdin so
    downstream calls (``register_session``, ``touch_session``,
    ``unregister_session``) reach the registry with the real id. The host
    session env var is not guaranteed to be exported into the hook
    subprocess; the stdin event always carries ``session_id``.
    """
    if isinstance(event_data, dict):
        candidate = event_data.get("session_id")
        if isinstance(candidate, str) and candidate:
            return candidate
    return read_host_session_id()


@dataclass
class HookState:
    """
    State passed from pre-hook to post-hook.

    Attributes:
        tool_name: Name of the tool being executed
        command: Command being executed (for Bash)
        tier: Security tier assigned by pre-hook
        start_time: ISO timestamp when pre-hook ran
        session_id: Current session identifier
        tool_use_id: Host tool-call identifier for this invocation (used,
            together with session_id, to key the state file so concurrent
            tool calls do not clobber one another)
        pre_hook_result: Result from pre-hook validation
        metadata: Additional context data
    """
    tool_name: str = ""
    command: str = ""
    tier: str = "unknown"
    start_time: str = ""
    start_time_epoch: float = 0.0
    session_id: str = ""
    tool_use_id: str = ""
    pre_hook_result: str = "allowed"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "HookState":
        """Create from dictionary."""
        return cls(
            tool_name=data.get("tool_name", ""),
            command=data.get("command", ""),
            tier=data.get("tier", "unknown"),
            start_time=data.get("start_time", ""),
            start_time_epoch=float(data.get("start_time_epoch", 0.0)),
            session_id=data.get("session_id", ""),
            tool_use_id=data.get("tool_use_id", ""),
            pre_hook_result=data.get("pre_hook_result", "allowed"),
            metadata=data.get("metadata", {}),
        )


def _get_state_file_path() -> Path:
    """Get path to the legacy global (single) state file."""
    claude_dir = find_claude_dir()
    return claude_dir / STATE_FILE_NAME


def _get_state_dir() -> Path:
    """Get the directory holding per-(session, tool_use) keyed state files."""
    claude_dir = find_claude_dir()
    return claude_dir / STATE_DIR_NAME


def _sanitize_key_component(value: str) -> str:
    """Make an id safe to use as a filename component.

    session_id (a UUID) and tool_use_id (``toolu_...``) are already
    filename-safe in practice, but any stray character is collapsed to ``_``
    so a malformed id can never escape the state directory.
    """
    return re.sub(r"[^A-Za-z0-9_.-]", "_", value)


def _keyed_state_path(session_id: str, tool_use_id: str) -> Path:
    """Path to the keyed state file for one (session_id, tool_use_id) pair."""
    key = f"{_sanitize_key_component(session_id)}__{_sanitize_key_component(tool_use_id)}.json"
    return _get_state_dir() / key


def _resolve_state_path(
    session_id: Optional[str], tool_use_id: Optional[str]
) -> Tuple[Path, bool]:
    """Resolve which file backs this state operation.

    Returns ``(path, keyed)`` where ``keyed`` is True when BOTH ids are
    present (per-call file) and False when we fall back to the legacy global
    file (either id missing). The fallback is what preserves prior behavior
    and the existing back-compat callers/tests that pass no key.
    """
    if session_id and tool_use_id:
        return _keyed_state_path(session_id, tool_use_id), True
    return _get_state_file_path(), False


def save_hook_state(
    state: HookState,
    *,
    session_id: Optional[str] = None,
    tool_use_id: Optional[str] = None,
) -> bool:
    """
    Save hook state for post-hook to read.

    The state is written to a per-(session_id, tool_use_id) file when a key is
    available (explicit args take precedence, then the ids carried on ``state``
    itself), so concurrent tool calls never clobber one another. When no key is
    available it degrades to the legacy global file.

    Args:
        state: HookState to save
        session_id: Optional explicit session id for keying (defaults to
            ``state.session_id``)
        tool_use_id: Optional explicit tool-call id for keying (defaults to
            ``state.tool_use_id``)

    Returns:
        True if saved successfully
    """
    try:
        sid = session_id if session_id is not None else state.session_id
        tuid = tool_use_id if tool_use_id is not None else state.tool_use_id
        state_file, _keyed = _resolve_state_path(sid, tuid)
        state_file.parent.mkdir(parents=True, exist_ok=True)

        with open(state_file, "w") as f:
            json.dump(state.to_dict(), f)

        logger.debug(f"Saved hook state: {state.tool_name} / {state.tier}")
        return True

    except Exception as e:
        logger.warning(f"Could not save hook state: {e}")
        return False


def get_hook_state(
    *,
    session_id: Optional[str] = None,
    tool_use_id: Optional[str] = None,
) -> Optional[HookState]:
    """
    Get hook state saved by pre-hook.

    Reads the per-(session_id, tool_use_id) file when a key is given, else the
    legacy global file.

    Returns:
        HookState if found, None otherwise
    """
    try:
        state_file, _keyed = _resolve_state_path(session_id, tool_use_id)

        if not state_file.exists():
            logger.debug("No hook state file found")
            return None

        with open(state_file, "r") as f:
            data = json.load(f)

        return HookState.from_dict(data)

    except Exception as e:
        logger.warning(f"Could not read hook state: {e}")
        return None


def clear_hook_state(
    *,
    session_id: Optional[str] = None,
    tool_use_id: Optional[str] = None,
) -> bool:
    """
    Clear hook state after post-hook has processed it.

    Clears the per-(session_id, tool_use_id) file when a key is given, else the
    legacy global file.

    Returns:
        True if cleared successfully
    """
    try:
        state_file, _keyed = _resolve_state_path(session_id, tool_use_id)

        if state_file.exists():
            state_file.unlink()
            logger.debug("Cleared hook state")

        return True

    except Exception as e:
        logger.warning(f"Could not clear hook state: {e}")
        return False


def iter_dangling_states(session_id: str) -> Iterator[Tuple[str, HookState]]:
    """Yield ``(tool_use_id, HookState)`` for every keyed entry of a session.

    A keyed entry is "dangling" simply because it still exists: PostToolUse
    deletes its own entry on completion, so anything still present when the
    session's Stop hook fires never received a PostToolUse -- which, for a Bash
    command, means the command FAILED (PostToolUse does not fire on a non-zero
    Bash exit in the current host). The Stop-hook reconciliation uses this to
    close the audit cycle for those failed T3 commands.

    Best-effort: returns nothing if the state directory is absent or unreadable.
    """
    if not session_id:
        return
    try:
        state_dir = _get_state_dir()
        if not state_dir.is_dir():
            return
        prefix = f"{_sanitize_key_component(session_id)}__"
        for entry_file in sorted(state_dir.glob(f"{prefix}*.json")):
            try:
                with open(entry_file, "r") as f:
                    data = json.load(f)
            except Exception:
                continue
            state = HookState.from_dict(data)
            # Prefer the id stored inside the state; fall back to parsing it
            # out of the filename so a malformed entry is still cleanable.
            tuid = state.tool_use_id or entry_file.name[len(prefix):-len(".json")]
            yield tuid, state
    except Exception as e:
        logger.debug("Could not enumerate dangling hook states: %s", e)
        return


def create_pre_hook_state(
    tool_name: str,
    command: str = "",
    tier: str = "unknown",
    *,
    session_id: Optional[str] = None,
    tool_use_id: str = "",
    **metadata
) -> HookState:
    """
    Create a new hook state for pre-hook.

    Convenience function that sets common fields automatically.

    Args:
        tool_name: Name of the tool
        command: Command being executed
        tier: Security tier
        session_id: Explicit session id from the parsed stdin event. The host
            does not reliably export the session env var into the hook
            subprocess, so callers that have the stdin event in hand should
            pass its ``session_id`` here; when omitted we fall back to the
            env-based ``get_session_id()``.
        tool_use_id: Host tool-call id from the stdin event, used to key the
            state file so concurrent tool calls do not clobber one another.
        **metadata: Additional metadata

    Returns:
        New HookState instance
    """
    resolved_session = session_id if session_id else get_session_id()

    return HookState(
        tool_name=tool_name,
        command=command,
        tier=tier,
        start_time=datetime.now().isoformat(),
        start_time_epoch=time.time(),
        session_id=resolved_session,
        tool_use_id=tool_use_id,
        pre_hook_result="allowed",
        metadata=metadata,
    )
