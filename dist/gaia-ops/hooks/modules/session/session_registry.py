"""
Session Registry — track active Claude sessions by CLAUDE_SESSION_ID.

Provides a user-scoped JSON registry at ~/.claude/session_registry.json that
records which sessions are currently alive. Liveness is heartbeat-only: hooks
that run inside a session call ``touch_session()`` to refresh
``last_heartbeat``; sessions whose heartbeat is stale beyond
``HEARTBEAT_TTL_SECONDS`` are treated as dead.

Why heartbeat-only:
    The previous design persisted ``pid`` and ``pid_create_time`` of the hook
    process. Hook processes are ephemeral — each event spawns a fresh Python
    interpreter that exits in milliseconds — so the persisted PID was always
    dead by the time another hook tried to use it for liveness. Claude Code
    does not expose its own parent PID to hooks (no CLAUDE_PARENT_PID), which
    rules out PID-based tracking entirely. Heartbeat freshness is the only
    signal hooks can produce that survives between events.

Storage format:
    {
        "sessions": {
            "<session_id>": {
                "started_at": "<ISO-8601 string>",
                "is_headless": <bool>,
                "last_heartbeat": <float epoch seconds>
            }
        }
    }

    Legacy entries with ``pid`` / ``pid_create_time`` fields are tolerated on
    read: they have no ``last_heartbeat``, so the freshness check treats them
    as dead immediately. That is the correct outcome — a registry written by
    the old code is by definition stale.

Concurrency:
    All writes are atomic via os.rename() after writing to a per-call .tmp
    file. Reads are best-effort; a corrupt or absent file returns an empty
    set.

Public API:
    register_session(session_id, started_at=None, is_headless=False) -> None
    unregister_session(session_id) -> None
    is_session_alive(session_id) -> bool
    touch_session(session_id) -> None
    get_live_sessions(include_headless=True) -> set[str]
    cleanup_stale_entries(grace_seconds=86400) -> int

Errors:
    SessionRegistryError — raised for expected failure modes (e.g., bad path).
"""

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

#: A session whose last_heartbeat is older than this is treated as dead by
#: get_live_sessions(). 30 minutes is generous enough to absorb a long-running
#: subagent (no user prompts in flight) while still catching real crashes
#: within a typical work session.
HEARTBEAT_TTL_SECONDS = 1800

#: touch_session() rate-limit. Calling on every hook event would thrash the
#: registry file; 30 seconds is enough resolution for a 30-minute TTL.
_HEARTBEAT_THROTTLE_SECONDS = 30


# ---------------------------------------------------------------------------
# Public exception
# ---------------------------------------------------------------------------

class SessionRegistryError(Exception):
    """Raised for expected failure modes in session registry operations."""


# ---------------------------------------------------------------------------
# Registry path
# ---------------------------------------------------------------------------

def _get_registry_path() -> Path:
    """Return the path to session_registry.json under ~/.claude/."""
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
    """
    path = _get_registry_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise SessionRegistryError(
            f"session_registry: cannot create directory {path.parent}: {exc}"
        ) from exc

    # Per-call tmp suffix so concurrent writers don't stomp on each other's
    # tmp file before rename. os.rename is atomic on POSIX.
    tmp_path = path.with_suffix(f".tmp.{os.getpid()}.{os.urandom(4).hex()}")
    try:
        tmp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.rename(str(tmp_path), str(path))
    except OSError as exc:
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
    started_at: Optional[str] = None,
    is_headless: bool = False,
) -> None:
    """Register a session as active in the user-scoped registry.

    Heartbeat-only liveness: PID is not tracked. The new entry's
    ``last_heartbeat`` is initialised to the current time so the session is
    immediately considered live by get_live_sessions().

    Args:
        session_id: The CLAUDE_SESSION_ID for the session to register.
            Must be a non-empty string.
        started_at: ISO-8601 timestamp for session start. Defaults to now
            (UTC) when not provided.
        is_headless: True when this session has no live human watching
            (CI, cron, ``claude --headless``). Headless sessions can be
            filtered out via ``get_live_sessions(include_headless=False)``
            so their pending approvals surface to interactive sessions.

    Raises:
        SessionRegistryError: If session_id is empty or saving fails.
    """
    if not session_id:
        raise SessionRegistryError("register_session: session_id must be non-empty")

    if started_at is None:
        started_at = datetime.now(timezone.utc).isoformat()

    data = _load_registry()
    data["sessions"][session_id] = {
        "started_at": started_at,
        "is_headless": bool(is_headless),
        "last_heartbeat": time.time(),
    }
    _save_registry(data)
    logger.debug(
        "session_registry: registered session=%s headless=%s",
        session_id,
        is_headless,
    )


def unregister_session(session_id: str) -> None:
    """Remove a session from the registry when it stops.

    Silently ignores the case where session_id is not found — this is
    normal during shutdown (hook may fire more than once or the entry
    may already have been cleaned up).
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

    Presence-only check; does not consult heartbeat freshness. Callers
    that need liveness should use ``get_live_sessions()``.
    """
    if not session_id:
        return False
    data = _load_registry()
    return session_id in data["sessions"]


def touch_session(session_id: str) -> None:
    """Refresh ``last_heartbeat`` for ``session_id``.

    Throttled: a no-op when the heartbeat was refreshed less than
    ``_HEARTBEAT_THROTTLE_SECONDS`` ago, to avoid thrashing the registry
    file under bursty hook traffic. Also a no-op when the session is not
    registered — touch must never create entries (only register_session
    does), because that would resurrect sessions that should have been
    cleaned up.

    Args:
        session_id: The CLAUDE_SESSION_ID to refresh. Empty/missing is a
            no-op. Failures are swallowed and logged at debug; this is a
            best-effort liveness signal and must never break the calling
            hook.
    """
    if not session_id:
        return
    try:
        data = _load_registry()
        entry = data["sessions"].get(session_id)
        if entry is None or not isinstance(entry, dict):
            return
        now = time.time()
        last = entry.get("last_heartbeat", 0) or 0
        if now - last < _HEARTBEAT_THROTTLE_SECONDS:
            return
        entry["last_heartbeat"] = now
        _save_registry(data)
    except Exception as exc:  # noqa: BLE001 — deliberate: never break callers
        logger.debug("touch_session(%s) failed (non-fatal): %s", session_id, exc)


def get_live_sessions(include_headless: bool = True) -> set:
    """Return session IDs whose ``last_heartbeat`` is within the TTL.

    Args:
        include_headless: When False, sessions registered with
            ``is_headless=True`` are excluded from the result. The
            [ACTIONABLE] pending-approval injection path uses
            ``include_headless=False`` so headless sessions don't suppress
            their own pendings — nobody is watching them live, so their
            pendings must surface to interactive sessions that can act on
            them.

    Returns:
        set[str] of session IDs considered live. Entries with no
        ``last_heartbeat`` (e.g., legacy PID-only entries written by the
        previous schema) are filtered out by the freshness check.
    """
    data = _load_registry()
    now = time.time()
    live: set = set()
    for session_id, entry in data["sessions"].items():
        if not isinstance(entry, dict):
            continue
        if not include_headless and entry.get("is_headless"):
            continue
        heartbeat = entry.get("last_heartbeat", 0) or 0
        if heartbeat and (now - heartbeat) < HEARTBEAT_TTL_SECONDS:
            live.add(session_id)
    return live


def cleanup_stale_entries(grace_seconds: int = 86400) -> int:
    """Remove registry entries whose ``last_heartbeat`` is older than ``grace_seconds``.

    Intended to be called opportunistically (e.g., from SessionStart) to
    keep the registry small. A grace window is used instead of the live-TTL
    so a session that briefly went inactive — closed laptop, suspended VM —
    isn't garbage-collected before it has a chance to send another
    heartbeat. Default of 24h matches the typical "I'm done for the day"
    boundary; tune as needed.

    Args:
        grace_seconds: Heartbeat age (seconds) beyond which an entry is
            removed. Entries with no ``last_heartbeat`` (legacy schema)
            are removed unconditionally — they cannot be live.

    Returns:
        Number of entries removed.
    """
    data = _load_registry()
    now = time.time()
    to_delete = []
    for session_id, entry in data["sessions"].items():
        if not isinstance(entry, dict):
            # Junk entry — sweep it out.
            to_delete.append(session_id)
            continue
        heartbeat = entry.get("last_heartbeat", 0) or 0
        if heartbeat == 0 or (now - heartbeat) >= grace_seconds:
            to_delete.append(session_id)

    if to_delete:
        for session_id in to_delete:
            del data["sessions"][session_id]
        _save_registry(data)
    return len(to_delete)
