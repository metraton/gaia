"""Shared path resolution utilities for the context module."""

from __future__ import annotations

from pathlib import Path


def resolve_config_dir() -> Path:
    """Resolve config directory from installed project or package checkout."""
    installed_path = Path(".claude/config")
    if installed_path.is_dir():
        return installed_path

    # context/ -> tools/ -> gaia-ops/
    script_dir = Path(__file__).parent.parent.parent
    package_path = script_dir / "config"
    if package_path.is_dir():
        return package_path

    return Path(".claude/config")
