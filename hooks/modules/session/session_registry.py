"""
Session Registry — track active Claude sessions by CLAUDE_SESSION_ID.

Provides a user-scoped JSON registry at ~/.claude/session_registry.json
that records which sessions are currently alive. This is the base
infrastructure for liveness filters (T12/T13).

Storage format:
    {
        "sessions": {
            "<session_id>": {
                "pid": <int or null>,
                "started_at": "<ISO-8601 string or null>"
            }
        }
    }

Concurrency:
    All writes are atomic via os.rename() after writing to a .tmp file.
    Reads are best-effort; a corrupt or absent file returns an empty set.

Public API:
    register_session(session_id, pid=None, started_at=None) -> None
    unregister_session(session_id) -> None
    is_session_alive(session_id) -> bool
    get_live_sessions() -> set[str]

Errors:
    SessionRegistryError — raised for expected failure modes (e.g., bad path).
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public exception
# ---------------------------------------------------------------------------

class SessionRegistryError(Exception):
    """Raised for expected failure modes in session registry operations."""


# ---------------------------------------------------------------------------
# Registry path
# ---------------------------------------------------------------------------

def _get_registry_path() -> Path:
    """Return the path to session_registry.json under ~/.claude/.

    Returns:
        Path to ~/.claude/session_registry.json
    """
    return Path.home() / ".claude" / "session_registry.json"


# ---------------------------------------------------------------------------
# Low-level I/O helpers
# ---------------------------------------------------------------------------

def _load_registry() -> dict:
    """Load the registry from disk.

    Returns:
        Registry dict with a "sessions" key. Returns {"sessions": {}} when
        the file is absent or corrupt (logs a warning on corrupt).
    """
    path = _get_registry_path()
    if not path.exists():
        return {"sessions": {}}
    try:
        text = path.read_text(encoding="utf-8")
        data = json.loads(text)
        if not isinstance(data, dict) or "sessions" not in data:
            raise ValueError("Missing 'sessions' key")
        if not isinstance(data["sessions"], dict):
            raise ValueError("'sessions' must be a dict")
        return data
    except Exception as exc:
        logger.warning(
            "session_registry: corrupt registry at %s (%s) — resetting to empty",
            path,
            exc,
        )
        return {"sessions": {}}


def _save_registry(data: dict) -> None:
    """Save the registry atomically using os.rename.

    Writes to a sibling .tmp file first, then renames to the target path
    so readers never see a partial write.

    Args:
        data: Registry dict to persist.

    Raises:
        SessionRegistryError: If the directory cannot be created or the
            write/rename fails.
    """
    path = _get_registry_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise SessionRegistryError(
            f"session_registry: cannot create directory {path.parent}: {exc}"
        ) from exc

    # Use a unique tmp suffix per call so concurrent writers don't stomp
    # on each other's tmp file before rename. os.rename is atomic on POSIX.
    tmp_path = path.with_suffix(f".tmp.{os.getpid()}.{os.urandom(4).hex()}")
    try:
        tmp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.rename(str(tmp_path), str(path))
    except OSError as exc:
        # Best-effort cleanup of the per-call tmp file
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
        raise SessionRegistryError(
            f"session_registry: write failed for {path}: {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def register_session(
    session_id: str,
    pid: Optional[int] = None,
    started_at: Optional[str] = None,
) -> None:
    """Register a session as active in the user-scoped registry.

    Creates or updates the entry for session_id. If started_at is not
    provided, the current UTC time in ISO-8601 format is used.

    Args:
        session_id: The CLAUDE_SESSION_ID for the session to register.
            Must be a non-empty string.
        pid: OS process ID of the hook process (optional but recommended
            for liveness checks in T12/T13).
        started_at: ISO-8601 timestamp string for when the session started.
            Defaults to datetime.now(timezone.utc).isoformat() if absent.

    Raises:
        SessionRegistryError: If session_id is empty or saving fails.
    """
    if not session_id:
        raise SessionRegistryError("register_session: session_id must be non-empty")

    if started_at is None:
        started_at = datetime.now(timezone.utc).isoformat()

    data = _load_registry()
    data["sessions"][session_id] = {
        "pid": pid,
        "started_at": started_at,
    }
    _save_registry(data)
    logger.debug("session_registry: registered session=%s pid=%s", session_id, pid)


def unregister_session(session_id: str) -> None:
    """Remove a session from the registry when it stops.

    Silently ignores the case where session_id is not found — this is
    normal during shutdown (hook may fire more than once or the entry
    may already have been cleaned up).

    Args:
        session_id: The CLAUDE_SESSION_ID to remove. Empty string is a
            no-op with a warning log.

    Raises:
        SessionRegistryError: If saving the updated registry fails.
    """
    if not session_id:
        logger.warning("unregister_session: called with empty session_id — no-op")
        return

    data = _load_registry()
    if session_id not in data["sessions"]:
        logger.debug(
            "session_registry: unregister called for unknown session=%s (no-op)",
            session_id,
        )
        return

    del data["sessions"][session_id]
    _save_registry(data)
    logger.debug("session_registry: unregistered session=%s", session_id)


def is_session_alive(session_id: str) -> bool:
    """Return True if session_id is present in the registry.

    This is a presence check only — it does not verify that the recorded
    PID is actually running. Full liveness probing (kill -0) is implemented
    in T12/T13.

    Args:
        session_id: The CLAUDE_SESSION_ID to check. Empty string always
            returns False.

    Returns:
        True if the session is in the registry, False otherwise.
    """
    if not session_id:
        return False
    data = _load_registry()
    return session_id in data["sessions"]


def get_live_sessions() -> set:
    """Return the set of all session IDs currently in the registry.

    Returns:
        set[str] of session IDs. Empty set when the registry is absent or
        corrupt (after logging a warning).
    """
    data = _load_registry()
    return set(data["sessions"].keys())
