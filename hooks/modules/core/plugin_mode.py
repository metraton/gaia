"""Plugin registry helpers for gaia hooks.

This module exposes only ``has_plugin``, a direct membership check against
``plugin-registry.json``.
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


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
