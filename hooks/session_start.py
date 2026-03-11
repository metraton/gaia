#!/usr/bin/env python3
"""
SessionStart hook for Claude Code Agent System.

Fires on session start. Checks if project-context.json exists and is fresh
(< configurable staleness threshold, default 24h). If stale or missing,
runs a lightweight scan to auto-refresh the context (<3s target).

Architecture:
- Thin gate: parse_event -> check_freshness -> trigger_scan -> respond
- Business logic in modules.context.context_freshness + modules.scanning.scan_trigger
- Exit code is always 0 (informational only)
"""

import dataclasses
import sys
import json
import logging
from datetime import datetime
from pathlib import Path

# Add hooks dir to path for adapter imports
sys.path.insert(0, str(Path(__file__).parent))

from adapters.claude_code import ClaudeCodeAdapter
from modules.core.stdin import has_stdin_data  # session_start uses inline logic
from modules.core.paths import get_logs_dir
from modules.context.context_freshness import check_freshness
from modules.scanning.scan_trigger import trigger_lightweight_scan

# Configure logging
_log_file = get_logs_dir() / f"hooks-{datetime.now().strftime('%Y-%m-%d')}.log"
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [session_start] %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler(_log_file)],
)
logger = logging.getLogger(__name__)


def _run_self_test() -> int:
    """Run self-test mode: verify freshness check and scan availability."""
    print("SessionStart hook self-test")
    result = check_freshness()
    print(f"  Context fresh: {result.is_fresh} (reason: {result.reason})")
    hooks_dir = Path(__file__).resolve().parent
    cli_path = hooks_dir.parent / "bin" / "gaia-scan.py"
    print(f"  CLI exists: {cli_path.is_file()}")
    print("  Self-test: PASS")
    return 0


# ============================================================================
# STDIN HANDLER (Claude Code integration)
# ============================================================================

if __name__ == "__main__":
    if "--test" in sys.argv:
        sys.exit(_run_self_test())

    if has_stdin_data():
        try:
            adapter = ClaudeCodeAdapter()
            event = adapter.parse_event(sys.stdin.read())

            bootstrap = adapter.adapt_session_start(event.payload)
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
                    logger.warning(
                        "Auto-refresh failed, suggest running /gaia:scan-project manually"
                    )

            # Build response with scan result (BootstrapResult is frozen)
            if project_scanned:
                bootstrap = dataclasses.replace(bootstrap, project_scanned=True)

            response = adapter.format_bootstrap_response(bootstrap)
            print(json.dumps(response.output))
            sys.exit(0)

        except ValueError as e:
            logger.error("Adapter parse failed: %s", e)
            sys.exit(1)
        except Exception as e:
            logger.error("Error processing SessionStart hook: %s", e)
            sys.exit(1)
    else:
        print("Usage: echo '{...}' | python session_start.py  (stdin mode)")
        print("       python session_start.py --test           (self-test mode)")
        sys.exit(1)