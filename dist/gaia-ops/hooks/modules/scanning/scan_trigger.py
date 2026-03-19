"""
Lightweight scan trigger for SessionStart hook.

Runs a subset of project scanners (e.g., tools + environment) to refresh
project-context.json without significant startup delay (<3s target).

Public API:
    - trigger_lightweight_scan(project_root: Path, scanners: list) -> bool
"""

import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import List

logger = logging.getLogger(__name__)

# Maximum time (seconds) we allow the lightweight scan subprocess to run
_SCAN_TIMEOUT = 10


def trigger_lightweight_scan(
    project_root: Path,
    scanners: List[str] = None,
) -> bool:
    """Run a lightweight scan via the CLI.

    Args:
        project_root: Working directory for the scan subprocess.
        scanners: List of scanner names to run. Defaults to
            ["tools", "environment"].

    Returns:
        True on success, False on failure. Designed to complete in <3s.
    """
    if scanners is None:
        scanners = ["tools", "environment"]

    scanners_csv = ",".join(scanners)

    # Resolve the CLI script path
    hooks_dir = Path(__file__).resolve().parents[2]  # hooks/
    plugin_root = hooks_dir.parent
    cli_path = plugin_root / "bin" / "gaia-scan.py"

    if not cli_path.is_file():
        logger.warning(
            "gaia-scan.py not found at %s, skipping auto-refresh", cli_path
        )
        return False

    env = os.environ.copy()
    # Ensure tools.scan is importable
    python_path = env.get("PYTHONPATH", "")
    if str(plugin_root) not in python_path:
        env["PYTHONPATH"] = (
            f"{plugin_root}:{python_path}" if python_path else str(plugin_root)
        )

    try:
        start = time.monotonic()
        result = subprocess.run(
            [sys.executable, str(cli_path), "--scanners", scanners_csv],
            capture_output=True,
            text=True,
            timeout=_SCAN_TIMEOUT,
            cwd=str(project_root),
            env=env,
        )
        elapsed = time.monotonic() - start

        if result.returncode == 0:
            logger.info(
                "Lightweight scan completed in %.1fs (scanners: %s)",
                elapsed,
                scanners_csv,
            )
            return True
        else:
            logger.warning(
                "Lightweight scan failed (exit %d, %.1fs): %s",
                result.returncode,
                elapsed,
                result.stderr[:500],
            )
            return False

    except subprocess.TimeoutExpired:
        logger.warning("Lightweight scan timed out after %ds", _SCAN_TIMEOUT)
        return False
    except Exception as e:
        logger.warning("Failed to run lightweight scan: %s", e)
        return False
