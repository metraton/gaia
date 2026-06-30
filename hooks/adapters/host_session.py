"""
Host Session ID Access for Gaia-Ops.

Adapter-owned utility that encapsulates how the host CLI exposes the current
session identifier via the environment. The host-specific env var name lives
ONLY here (inside ``hooks/adapters/``); business logic modules call these
helpers instead of reading the environment directly, so the core stays
agnostic to the host CLI's session-propagation convention.

Mirrors the ``channel.py`` pattern: a small standalone module under
``adapters/`` that reads a host env var and is imported by business logic,
with no dependency on the heavier ``ClaudeCodeAdapter`` (avoids any
circular-import or instantiation concern in low-level modules).
"""

import hashlib
import logging
import os
from datetime import datetime

logger = logging.getLogger(__name__)

# The environment variable the host CLI (Claude Code) uses to advertise the
# current session id. Confined to this adapter module by design.
_HOST_SESSION_ENV_VAR = "CLAUDE_SESSION_ID"


def read_host_session_id(default: str = "default") -> str:
    """Return the host session id from the environment, or ``default``.

    The host CLI does not guarantee the session env var is exported into a
    hook subprocess. Callers that have the parsed stdin event in hand should
    prefer the event's ``session_id`` and use this only as a fallback.
    """
    return os.environ.get(_HOST_SESSION_ENV_VAR, default)


def get_or_create_host_session_id() -> str:
    """Return the host session id, generating and storing one if absent.

    Checks the host session env var first. If absent, generates a new id from
    the current time and PID, stores it back into the env var (so subsequent
    reads in the same process are stable), and returns it.
    """
    session_id = os.environ.get(_HOST_SESSION_ENV_VAR)
    if not session_id:
        timestamp = datetime.now().strftime("%H%M%S")
        hash_input = f"{timestamp}-{os.getpid()}"
        session_hash = hashlib.sha256(hash_input.encode()).hexdigest()[:8]
        session_id = f"session-{timestamp}-{session_hash}"
        os.environ[_HOST_SESSION_ENV_VAR] = session_id
        logger.debug("Generated new session_id: %s", session_id)
    return session_id
