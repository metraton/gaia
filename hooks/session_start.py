#!/usr/bin/env python3
"""SessionStart hook — checks project-context freshness and triggers auto-scan."""

import dataclasses
import sys
import json
import logging
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from modules.core.stdin import has_stdin_data
from modules.core.paths import get_logs_dir

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
        from modules.context.context_freshness import check_freshness
        result = check_freshness()
        print(f"Context fresh: {result.is_fresh} (reason: {result.reason})")
        sys.exit(0)

    if not has_stdin_data():
        sys.exit(0)

    try:
        # Read and parse stdin
        stdin_data = sys.stdin.read()
        raw = json.loads(stdin_data)

        # Import adapter only if we have valid input
        from adapters.claude_code import ClaudeCodeAdapter
        from modules.context.context_freshness import check_freshness
        from modules.scanning.scan_trigger import trigger_lightweight_scan

        adapter = ClaudeCodeAdapter()
        bootstrap = adapter.adapt_session_start(raw)
        logger.info(
            "SessionStart: type=%s, should_scan=%s, should_refresh=%s",
            bootstrap.session_type, bootstrap.should_scan, bootstrap.should_refresh,
        )

        freshness = check_freshness()
        project_scanned = False

        if freshness.is_fresh:
            logger.info("skipped: fresh")
        elif bootstrap.should_refresh:
            logger.info("triggered: %s -- running lightweight scan", freshness.reason)
            scan_ok = trigger_lightweight_scan(Path.cwd())
            if scan_ok:
                project_scanned = True
                logger.info("Auto-refresh completed successfully")
            else:
                logger.warning("Auto-refresh failed")

        if project_scanned:
            bootstrap = dataclasses.replace(bootstrap, project_scanned=True)

        response = adapter.format_bootstrap_response(bootstrap)
        print(json.dumps(response.output))
        sys.exit(0)

    except Exception as e:
        logger.error("SessionStart error (non-fatal): %s", e)
        # Exit 0 — never block session start
        print(json.dumps({}))
        sys.exit(0)
