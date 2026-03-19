"""Dynamic identity provider based on installed plugins."""

import logging
from ..core.plugin_mode import get_plugin_mode

logger = logging.getLogger(__name__)


def build_identity() -> str:
    """Build the identity context based on installed plugin mode."""
    mode = get_plugin_mode()

    if mode == "ops":
        from .ops_identity import build_ops_identity
        identity = build_ops_identity()
    else:
        from .security_identity import build_security_identity
        identity = build_security_identity()

    logger.info("Identity built for mode: %s (%d chars)", mode, len(identity))
    return identity
