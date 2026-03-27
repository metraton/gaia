"""
Session ID generation and retrieval.

Provides:
    - get_or_create_session_id(): Get existing session ID or create new one
"""

import hashlib
import logging
import os
from datetime import datetime

logger = logging.getLogger(__name__)


def get_or_create_session_id() -> str:
    """Get existing session ID or create new one.

    Checks the CLAUDE_SESSION_ID env var first.  If absent, generates a
    new session ID from the current time and PID, stores it in the env var,
    and returns it.
    """
    session_id = os.environ.get("CLAUDE_SESSION_ID")
    if not session_id:
        timestamp = datetime.now().strftime("%H%M%S")
        hash_input = f"{timestamp}-{os.getpid()}"
        session_hash = hashlib.sha256(hash_input.encode()).hexdigest()[:8]
        session_id = f"session-{timestamp}-{session_hash}"
        os.environ["CLAUDE_SESSION_ID"] = session_id
        logger.debug(f"Generated new session_id: {session_id}")
    return session_id
