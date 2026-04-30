#!/usr/bin/env python3
"""SessionStart hook — first-time setup + project scan (ops only)."""

import os
import sys
import json
import logging
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from modules.core.workspace_bootstrap import ensure_workspace_hooks_link
ensure_workspace_hooks_link()

from modules.core.stdin import has_stdin_data
from modules.core.paths import get_logs_dir
from modules.core.plugin_mode import is_ops_mode
from modules.core.plugin_setup import run_first_time_setup
from modules.session.session_registry import register_session, SessionRegistryError

# Configure logging — file only
_log_file = get_logs_dir() / f"hooks-{datetime.now().strftime('%Y-%m-%d')}.log"
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [session_start] %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler(_log_file)],
)
logger = logging.getLogger(__name__)


if __name__ == "__main__":
    if not has_stdin_data():
        sys.exit(0)

    try:
        sys.stdin.read()

        # Register this session in the user-scoped session registry.
        # Base infrastructure for T12/T13 liveness filter. Failures are
        # non-fatal: a missing registry entry must never block session start.
        try:
            _sid = os.environ.get("CLAUDE_SESSION_ID")
            if _sid:
                register_session(session_id=_sid, pid=os.getpid())
        except SessionRegistryError as _reg_exc:
            logger.warning("session_registry register failed (non-fatal): %s", _reg_exc)

        # First-time setup: create project permissions if needed.
        # mark_done=False so UserPromptSubmit can detect first-run
        # and show the welcome message before marking initialized.
        setup_message = run_first_time_setup(mark_done=False)
        if setup_message:
            logger.info("First-time setup: %s", setup_message)

        # Project scan: only in ops mode
        project_scanned = False
        if is_ops_mode():
            from modules.context.context_freshness import check_freshness
            from modules.scanning.scan_trigger import trigger_lightweight_scan

            freshness = check_freshness()
            if freshness.is_fresh:
                logger.info("SessionStart: skipped scan (fresh)")
            else:
                logger.info("SessionStart: %s — running lightweight scan", freshness.reason)
                scan_ok = trigger_lightweight_scan(Path.cwd())
                if scan_ok:
                    project_scanned = True
                    logger.info("Auto-refresh completed successfully")
                else:
                    logger.warning("Auto-refresh failed")

        response = {"session_type": "startup", "project_scanned": project_scanned}
        if setup_message:
            response["setup_message"] = setup_message

        print(json.dumps(response))
        sys.exit(0)

    except Exception as e:
        logger.error("SessionStart error (non-fatal): %s", e)
        print(json.dumps({}))
        sys.exit(0)
