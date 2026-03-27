"""Plugin mode detection for gaia hooks.

Determines whether the installation is security-only or ops (full orchestrator).
Security mode: T3 operations use native Claude Code approval dialog (permissionDecision: ask).
Ops mode: T3 operations block with nonce for orchestrator agent approval flow.

Detection order:
1. plugin-registry.json in plugin data directory
2. NPM package name detection (gaia-ops vs gaia-security)
3. GAIA_PLUGIN_MODE env var fallback
4. Default: "security" (most restrictive)
"""
from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

VALID_MODES = ("security", "ops")
DEFAULT_MODE = "security"

# Map NPM package names to plugin modes
_NPM_PACKAGE_MODE = {
    "gaia-ops": "ops",
    "gaia-security": "security",
}


def _detect_mode_from_npm_package() -> str | None:
    """Detect plugin mode from the NPM package name.

    When installed via npm, this module lives at a path like:
      .../node_modules/@jaguilar87/gaia-ops/hooks/modules/core/plugin_mode.py

    The package directory name (gaia-ops or gaia-security) determines the mode.
    Also checks .claude/ symlinks as a secondary signal for npm installs.

    Returns the mode string or None if not detectable.
    """
    # Primary: check our own file path for node_modules package name
    module_path = Path(__file__).resolve()
    parts = module_path.parts
    for i, part in enumerate(parts):
        if part == "node_modules" and i + 2 < len(parts):
            # Could be @scope/package-name or just package-name
            pkg_name = parts[i + 1]
            if pkg_name.startswith("@") and i + 2 < len(parts):
                pkg_name = parts[i + 2]
            mode = _NPM_PACKAGE_MODE.get(pkg_name)
            if mode:
                logger.debug("Detected mode '%s' from npm package path: %s", mode, pkg_name)
                return mode

    # Secondary: check if .claude/agents symlink points to a gaia package
    try:
        from .paths import find_claude_dir
        claude_dir = find_claude_dir()
        agents_link = claude_dir / "agents"
        if agents_link.is_symlink():
            target = str(agents_link.resolve())
            for pkg_name, mode in _NPM_PACKAGE_MODE.items():
                if pkg_name in target:
                    logger.debug("Detected mode '%s' from .claude/agents symlink target", mode)
                    return mode
    except Exception:
        pass

    return None


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

    # 2. CLAUDE_PLUGIN_ROOT + plugin.json (--plugin-dir mode)
    plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT", "")
    if plugin_root:
        try:
            pjson = Path(plugin_root) / ".claude-plugin" / "plugin.json"
            if pjson.exists():
                pdata = json.loads(pjson.read_text())
                pname = pdata.get("name", "")
                mode = _NPM_PACKAGE_MODE.get(pname)
                if mode:
                    logger.debug("Detected mode '%s' from plugin.json name: %s", mode, pname)
                    return mode
        except Exception as e:
            logger.debug("Plugin.json check failed (non-fatal): %s", e)

    # 3. NPM package name detection
    npm_mode = _detect_mode_from_npm_package()
    if npm_mode:
        return npm_mode

    # 4. Env var fallback
    mode = os.environ.get("GAIA_PLUGIN_MODE", "").lower()
    if mode in VALID_MODES:
        return mode

    # 5. Default: security (most restrictive)
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
