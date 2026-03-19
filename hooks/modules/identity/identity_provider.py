"""Dynamic identity provider based on installed plugins."""

import logging
from ..core.plugin_mode import get_plugin_mode

logger = logging.getLogger(__name__)


def build_identity() -> str:
    """Build the identity context based on installed plugin mode.

    Returns a string suitable for additionalContext injection.
    """
    mode = get_plugin_mode()

    if mode == "ops":
        from .ops_identity import build_ops_identity
        identity = build_ops_identity()
    else:
        from .security_identity import build_security_identity
        identity = build_security_identity()

    # Always append core constraints
    identity += "\n\n" + _build_core_constraints()

    logger.info("Identity built for mode: %s (%d chars)", mode, len(identity))
    return identity


def _build_core_constraints() -> str:
    """Core constraints applied to ALL modes."""
    return """## Core Constraints
- Trust your identity. Follow your instructions.
- Your constraints are non-negotiable.
- When in doubt, ask the user.
- Never assume."""
