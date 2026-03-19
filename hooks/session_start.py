#!/usr/bin/env python3
"""SessionStart hook — checks project-context freshness and triggers auto-scan."""

import sys
import json
import logging
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from modules.core.stdin import has_stdin_data
from modules.core.paths import get_logs_dir
from modules.context.context_freshness import check_freshness
from modules.scanning.scan_trigger import trigger_lightweight_scan

# Configure logging — file only, no stderr
_log_file = get_logs_dir() / f"hooks-{datetime.now().strftime('%Y-%m-%d')}.log"
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [session_start] %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler(_log_file)],
)
logger = logging.getLogger(__name__)


if __name__ == "__main__":
    if "--test" in sys.argv:
        result = check_freshness()
        print(f"Context fresh: {result.is_fresh} (reason: {result.reason})")
        sys.exit(0)

    if not has_stdin_data():
        sys.exit(0)

    try:
        # Read stdin (required) — don't need full adapter parse
        sys.stdin.read()

        # Check freshness and scan if needed
        freshness = check_freshness()
        project_scanned = False

        if freshness.is_fresh:
            logger.info("SessionStart: skipped (fresh)")
        else:
            logger.info("SessionStart: %s — running lightweight scan", freshness.reason)
            scan_ok = trigger_lightweight_scan(Path.cwd())
            if scan_ok:
                project_scanned = True
                logger.info("Auto-refresh completed successfully")
            else:
                logger.warning("Auto-refresh failed")

        response = {
            "session_type": "startup",
            "project_scanned": project_scanned,
            "context_fresh": freshness.is_fresh,
        }
        print(json.dumps(response))
        sys.exit(0)

    except Exception as e:
        logger.error("SessionStart error (non-fatal): %s", e)
        print(json.dumps({}))
        sys.exit(0)
