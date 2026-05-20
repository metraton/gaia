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
        # Parse the stdin event so we can recover session_id from it.
        # Claude Code always includes session_id in the JSON event piped
        # to the hook; CLAUDE_SESSION_ID is *not* guaranteed in the hook
        # subprocess env. Reading from the event is the reliable source.
        _raw_stdin = sys.stdin.read()
        try:
            event_data = json.loads(_raw_stdin) if _raw_stdin else {}
            if not isinstance(event_data, dict):
                event_data = {}
        except (json.JSONDecodeError, TypeError):
            event_data = {}

        from modules.core.state import resolve_session_id
        _sid = resolve_session_id(event_data)

        # Register this session in the user-scoped session registry.
        # Heartbeat-only liveness: PID isn't tracked because the hook
        # process is ephemeral. Failures are non-fatal — a missing
        # registry entry must never block session start.
        try:
            if _sid and _sid != "default":
                _is_headless = (
                    os.environ.get("CLAUDE_HEADLESS") == "1"
                    or os.environ.get("CI") == "true"
                )
                register_session(session_id=_sid, is_headless=_is_headless)
        except SessionRegistryError as _reg_exc:
            logger.warning("session_registry register failed (non-fatal): %s", _reg_exc)

        # Opportunistic GC of entries whose heartbeat is older than 24h.
        # Cheap (one JSON read/write) and keeps the registry from growing
        # unbounded across crashed/orphan sessions.
        try:
            from modules.session.session_registry import cleanup_stale_entries
            _removed = cleanup_stale_entries()
            if _removed:
                logger.info("session_registry: cleaned %d stale entries", _removed)
        except Exception as _gc_exc:
            logger.debug("cleanup_stale_entries failed (non-fatal): %s", _gc_exc)

        # Flush expired approval artefacts (grants, pending files, orphan
        # pending-index files). force=True bypasses the 60s throttle used by
        # pre_tool_use; SessionStart fires once per session, so users
        # should not have to wait for the throttle window before stale
        # approvals disappear.
        try:
            from modules.security.approval_grants import cleanup_expired_grants
            _cleaned = cleanup_expired_grants(force=True)
            if _cleaned:
                logger.info(
                    "approval_grants: cleaned %d expired/orphan files at SessionStart",
                    _cleaned,
                )
        except Exception as _ag_exc:
            logger.debug("cleanup_expired_grants failed (non-fatal): %s", _ag_exc)

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

        # Build the SessionStart manifest (Phase 4). Combines the Environment
        # block, agentic-loop resume, and pending approvals into a one-shot
        # additionalContext payload. Fully fail-safe -- an empty manifest just
        # means no hookSpecificOutput in the response, which Claude Code
        # treats as "nothing to inject".
        additional_context = ""
        try:
            from modules.session.session_manifest import build_session_context
            from modules.core.plugin_mode import get_plugin_mode
            additional_context = build_session_context(get_plugin_mode())
        except Exception as _manifest_exc:
            logger.debug(
                "build_session_context failed (non-fatal): %s", _manifest_exc
            )

        response = {"session_type": "startup", "project_scanned": project_scanned}
        if setup_message:
            response["setup_message"] = setup_message
        if additional_context:
            response["hookSpecificOutput"] = {
                "hookEventName": "SessionStart",
                "additionalContext": additional_context,
            }
            logger.info(
                "SessionStart context injected (%d chars)",
                len(additional_context),
            )

        print(json.dumps(response))
        sys.exit(0)

    except Exception as e:
        logger.error("SessionStart error (non-fatal): %s", e)
        print(json.dumps({}))
        sys.exit(0)
