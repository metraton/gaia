#!/usr/bin/env python3
"""SessionStart hook — first-time setup, the declared-workspace notice, context injection.

It registers and scans no workspace; outside every declared one it says so and
names `gaia workspace declare <name> <path>`.

Every write happens only when the host executes this file: importing it -- as
doctor's importability check does -- links no hooks and records no manifest.
"""

import os
import sys
import json
import logging
from pathlib import Path
from typing import Optional

_hooks_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(_hooks_dir))
_pkg_root = str(_hooks_dir.parent)
if _pkg_root not in sys.path:
    sys.path.insert(0, _pkg_root)
from modules.core.plugin_setup import recorded_in_manifest
from modules.core.workspace_bootstrap import ensure_workspace_hooks_link


def _detect_headless(proc_root: Optional[Path] = None) -> bool:
    """Whether this session runs without an interactive TUI.

    Signals, in order: CLAUDE_HEADLESS=1, CI=true or NONINTERACTIVE=1; a parent
    ``claude`` process started with ``-p``/``--print`` or ``--output-format``
    (print mode sets no CLAUDE_HEADLESS); neither stdout nor stdin a TTY. The
    /proc read is Linux-only and a probe error falls through to the TTY check,
    so detection never blocks session start.

    Args:
        proc_root: Override for /proc (test injection). Defaults to /proc.
    """
    if os.environ.get("CLAUDE_HEADLESS") == "1":
        return True
    if os.environ.get("CI", "").lower() == "true":
        return True
    if os.environ.get("NONINTERACTIVE") == "1":
        return True

    if proc_root is None:
        proc_root = Path("/proc")
    try:
        if proc_root.exists():
            ppid = os.getppid()
            cmdline_path = proc_root / str(ppid) / "cmdline"
            if cmdline_path.exists():
                raw = cmdline_path.read_bytes().decode("utf-8", errors="replace")
                argv = [a for a in raw.split("\x00") if a]
                if argv:
                    exe = Path(argv[0]).name.lower()
                    if "claude" in exe:
                        for arg in argv[1:]:
                            if arg in ("-p", "--print"):
                                return True
                            if arg.startswith("--output-format"):
                                return True
    except (OSError, ValueError, UnicodeDecodeError):
        pass

    try:
        if not sys.stdout.isatty() and not sys.stdin.isatty():
            return True
    except (AttributeError, ValueError):
        pass

    return False

from modules.core.stdin import has_stdin_data
from modules.core.logging_setup import configure_hook_logging
from modules.session.session_lifecycle import SessionStart, start_session

configure_hook_logging("session_start")
logger = logging.getLogger(__name__)


def _pinned_build() -> Optional[dict]:
    """Return the content digest of the hooks tree this process runs, or None.

    Hashed from this file's own directory, which may no longer be where
    ``.claude/hooks`` points; doctor compares the digest to tell a restart is due.
    """
    try:
        from gaia.hooks_build import hooks_content_hash
        running_hooks_dir = Path(__file__).resolve().parent
        return {
            "hooks_path": str(running_hooks_dir),
            "hooks_hash": hooks_content_hash(running_hooks_dir),
        }
    except Exception as exc:
        logger.debug("pinned_build computation failed (non-fatal): %s", exc)
        return None


if __name__ == "__main__":
    with recorded_in_manifest():
        ensure_workspace_hooks_link()

    if not has_stdin_data():
        sys.exit(0)

    try:
        _raw_stdin = sys.stdin.read()
        try:
            event_data = json.loads(_raw_stdin) if _raw_stdin else {}
            if not isinstance(event_data, dict):
                event_data = {}
        except (json.JSONDecodeError, TypeError):
            event_data = {}

        from modules.core.state import resolve_session_id

        outcome = start_session(SessionStart(
            session_id=resolve_session_id(event_data),
            source=event_data.get("source", ""),
            is_headless=_detect_headless(),
            pinned_build=_pinned_build(),
            workspace_dir=Path.cwd(),
        ))

        response = {"session_type": "startup"}
        if outcome.setup_message:
            response["setup_message"] = outcome.setup_message
        if outcome.notices:
            response["systemMessage"] = "\n\n".join(outcome.notices.values())
        if outcome.context:
            response["hookSpecificOutput"] = {
                "hookEventName": "SessionStart",
                "additionalContext": outcome.context,
            }
            logger.info("SessionStart context injected (%d chars)", len(outcome.context))

        print(json.dumps(response))
        sys.exit(0)

    except Exception as e:
        logger.error("SessionStart error (non-fatal): %s", e)
        print(json.dumps({}))
        sys.exit(0)
