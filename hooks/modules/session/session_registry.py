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
                "pid_create_time": <float or null>,
                "started_at": "<ISO-8601 string or null>"
            }
        }
    }

    pid_create_time is the process creation time (from /proc/<pid>/stat
    field 22 on Linux) used to disambiguate recycled PIDs during liveness
    checks. When the OS reuses a PID for a different process, the create
    time will differ and the session is treated as dead.

Concurrency:
    All writes are atomic via os.rename() after writing to a .tmp file.
    Reads are best-effort; a corrupt or absent file returns an empty set.

Public API:
    register_session(session_id, pid=None, started_at=None) -> None
    unregister_session(session_id) -> None
    is_session_alive(session_id) -> bool
    get_live_sessions() -> set[str]
    get_pid_create_time(pid) -> Optional[float]

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
# PID liveness helpers
# ---------------------------------------------------------------------------

def get_pid_create_time(pid: int) -> Optional[float]:
    """Return the creation time of a process, or None if unavailable.

    Reads /proc/<pid>/stat field 22 (starttime) on Linux. The value is in
    clock ticks since boot; the raw number is sufficient for equality
    comparison to detect PID recycling (we do not need wall-clock time).

    Args:
        pid: OS process ID to inspect. Non-positive values return None.

    Returns:
        Starttime as a float, or None when the process does not exist,
        /proc is not available, or parsing fails.
    """
    if not pid or pid <= 0:
        return None
    try:
        stat_path = Path("/proc") / str(pid) / "stat"
        text = stat_path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None
    # The comm field (field 2) may contain spaces and parentheses; parse
    # from the last ')' to avoid splitting on them.
    try:
        rparen = text.rindex(")")
    except ValueError:
        return None
    rest = text[rparen + 1 :].split()
    # After the closing paren the remaining fields are 3..N, so starttime
    # (field 22) is at index 22 - 3 = 19.
    if len(rest) < 20:
        return None
    try:
        return float(rest[19])
    except ValueError:
        return None


def _is_pid_alive(pid: Optional[int], pid_create_time: Optional[float]) -> bool:
    """Return True when pid names a live process with a matching create time.

    When pid is None we cannot verify liveness and fall back to True
    (presence-only behaviour — sessions registered without a pid remain
    live). When pid_create_time is provided we require it to match the
    current process create time; a mismatch indicates PID recycling and
    we treat the session as dead.

    Args:
        pid: Recorded process ID, may be None.
        pid_create_time: Recorded create time for the pid, may be None.

    Returns:
        True if the session should be considered alive.
    """
    if pid is None:
        return True
    current = get_pid_create_time(pid)
    if current is None:
        return False
    if pid_create_time is None:
        # Legacy entry without create time — trust the pid lookup.
        return True
    return current == pid_create_time


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
    provided, the current UTC time in ISO-8601 format is used. When pid
    is provided the process create time is captured alongside so that
    later liveness checks can detect PID recycling.

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

    pid_create_time: Optional[float] = None
    if pid is not None:
        pid_create_time = get_pid_create_time(pid)

    data = _load_registry()
    data["sessions"][session_id] = {
        "pid": pid,
        "pid_create_time": pid_create_time,
        "started_at": started_at,
    }
    _save_registry(data)
    logger.debug(
        "session_registry: registered session=%s pid=%s create_time=%s",
        session_id,
        pid,
        pid_create_time,
    )


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
    """Return the set of session IDs whose recorded pid is still alive.

    Entries registered without a pid are kept (presence-only liveness).
    Entries with a pid are filtered via _is_pid_alive so that PID
    recycling — OS reusing a PID for a different process — is detected
    by comparing the recorded create time with the current one.

    Returns:
        set[str] of session IDs considered live. Empty set when the
        registry is absent or corrupt (after logging a warning).
    """
    data = _load_registry()
    live: set = set()
    for session_id, entry in data["sessions"].items():
        if not isinstance(entry, dict):
            continue
        pid = entry.get("pid")
        pid_create_time = entry.get("pid_create_time")
        if _is_pid_alive(pid, pid_create_time):
            live.add(session_id)
    return live
