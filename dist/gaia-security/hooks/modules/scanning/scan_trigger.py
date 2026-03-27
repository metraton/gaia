"""
Lightweight scan trigger for SessionStart hook.

Runs a subset of project scanners (e.g., tools + environment) to refresh
project-context.json without significant startup delay (<3s target).

Uses the scan engine directly (in-process) — no dependency on bin/gaia-scan.py.
Works in both npm and plugin mode since tools/scan/ is always available.

Public API:
    - trigger_lightweight_scan(project_root: Path, scanners: list) -> bool
"""

import logging
import sys
import time
from pathlib import Path
from typing import List

logger = logging.getLogger(__name__)


def trigger_lightweight_scan(
    project_root: Path,
    scanners: List[str] = None,
) -> bool:
    """Run a lightweight scan using the scan engine directly.

    Args:
        project_root: Working directory for the scan.
        scanners: List of scanner names to run. Defaults to
            ["tools", "environment"].

    Returns:
        True on success, False on failure. Designed to complete in <3s.
    """
    if scanners is None:
        scanners = ["tools", "environment"]

    # Ensure tools.scan is importable by adding plugin root to sys.path
    hooks_dir = Path(__file__).resolve().parents[2]  # hooks/
    plugin_root = hooks_dir.parent
    if str(plugin_root) not in sys.path:
        sys.path.insert(0, str(plugin_root))

    try:
        from tools.scan.config import ScanConfig
        from tools.scan.orchestrator import ScanOrchestrator
        from tools.scan.registry import ScannerRegistry
    except ImportError as e:
        logger.warning("Cannot import scan engine: %s", e)
        return False

    try:
        start = time.monotonic()

        config = ScanConfig(
            scanners=scanners,
            project_root=project_root,
        )
        registry = ScannerRegistry()
        orchestrator = ScanOrchestrator(registry=registry, config=config)
        output = orchestrator.run(project_root=project_root)
        elapsed = time.monotonic() - start

        if output.errors:
            logger.warning(
                "Lightweight scan completed with errors in %.1fs: %s",
                elapsed,
                output.errors[:3],
            )
            return False

        logger.info(
            "Lightweight scan completed in %.1fs (scanners: %s, sections: %d)",
            elapsed,
            ", ".join(scanners),
            output.sections_updated,
        )
        return True

    except Exception as e:
        logger.warning("Failed to run lightweight scan: %s", e)
        return False
