"""Plugin mode detection for gaia hooks.

Determines whether the installation is security-only or ops (full orchestrator).
Security mode: T3 operations use native Claude Code approval dialog (permissionDecision: ask).
Ops mode: T3 operations block with nonce for orchestrator agent approval flow.

Detection order:
1. plugin-registry.json in plugin data directory
2. GAIA_PLUGIN_MODE env var fallback
3. Default: "security" (most restrictive)
"""

import json
import logging
import os
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

VALID_MODES = ("security", "ops")
DEFAULT_MODE = "security"


@lru_cache(maxsize=1)
def get_plugin_mode() -> str:
    """Get the current plugin mode.

    Returns "security" or "ops".
    """
    # 1. Check plugin registry
    try:
        from .paths import get_plugin_data_dir
        registry_path = get_plugin_data_dir() / "plugin-registry.json"
        if registry_path.exists():
            registry = json.loads(registry_path.read_text())
            installed = [p.get("name", "") for p in registry.get("installed", [])]
            if "gaia-ops" in installed:
                return "ops"
            if "gaia-security" in installed:
                return "security"
    except Exception as e:
        logger.debug("Registry check failed (non-fatal): %s", e)

    # 2. Env var fallback
    mode = os.environ.get("GAIA_PLUGIN_MODE", "").lower()
    if mode in VALID_MODES:
        return mode

    # 3. Default: security (most restrictive)
    return DEFAULT_MODE


def has_plugin(name: str) -> bool:
    """Check if a specific plugin is installed."""
    try:
        from .paths import get_plugin_data_dir
        registry_path = get_plugin_data_dir() / "plugin-registry.json"
        if registry_path.exists():
            registry = json.loads(registry_path.read_text())
            return any(p.get("name") == name for p in registry.get("installed", []))
    except Exception:
        pass
    return False


def is_ops_mode() -> bool:
    """Convenience: check if running in ops mode."""
    return get_plugin_mode() == "ops"


def is_security_mode() -> bool:
    """Convenience: check if running in security-only mode."""
    return get_plugin_mode() == "security"


def clear_mode_cache():
    """Clear cached mode (for testing)."""
    get_plugin_mode.cache_clear()
