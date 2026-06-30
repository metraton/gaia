"""
Session ID generation and retrieval.

Provides:
    - get_or_create_session_id(): Get existing session ID or create new one
"""

import logging

from adapters.host_session import get_or_create_host_session_id

logger = logging.getLogger(__name__)


def get_or_create_session_id() -> str:
    """Get existing session ID or create new one.

    Delegates to the adapter-owned host-session helper, which reads the host
    session env var first and, if absent, generates a new session id from the
    current time and PID, stores it back, and returns it.
    """
    return get_or_create_host_session_id()
