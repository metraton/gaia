"""Plugin registry helpers for gaia hooks.

Gaia ships as a single unified plugin ("gaia"). There is no longer a
runtime mode distinction: every install runs the full orchestrator
surface. The former runtime mode-detection layer has been removed.

This module now exposes only ``has_plugin`` -- a direct registry membership
check used independently of any mode concept.
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
