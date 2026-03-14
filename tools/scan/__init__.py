"""
Scan Module: Modular project scanner for gaia-ops

This module provides a pluggable scanner system that detects project stack,
infrastructure, git configuration, tools, orchestration, and environment.
Each scanner is a pure function (filesystem path -> JSON sections) that runs
independently and in parallel. The system produces an agnostic project-context.json
schema that works for any project type.

Main components:
- BaseScanner: Abstract base class for scanner modules
- ScannerRegistry: Auto-discovery registry for scanner modules
- ScanOrchestrator: Parallel scanner execution and result aggregation
- ScanConfig: Scanner configuration and tool definitions
"""

import json as _json
from pathlib import Path as _Path


def _read_version() -> str:
    """Read version from package.json (single source of truth)."""
    try:
        pkg_path = _Path(__file__).resolve().parent.parent.parent / "package.json"
        with open(pkg_path) as f:
            return _json.load(f)["version"]
    except Exception:
        return "unknown"


__version__ = _read_version()

__all__ = [
    "__version__",
]
