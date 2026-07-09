#!/usr/bin/env python3
"""SessionStart hook — first-time setup + context injection (no auto-scan)."""

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
from modules.core.workspace_bootstrap import ensure_workspace_hooks_link
ensure_workspace_hooks_link()


# ---------------------------------------------------------------------------
# Headless detection
# ---------------------------------------------------------------------------

def _detect_headless(proc_root: Optional[Path] = None) -> bool:
    """Best-effort detection of headless / non-interactive sessions.

    Returns True when this Claude Code session is running without an
    interactive TUI. Sources, in order of confidence:

      1. Explicit env: CLAUDE_HEADLESS=1, CI=true, NONINTERACTIVE=1.
         These are the most reliable signals and the only ones the user
         can opt into deliberately.
      2. SDK CLI invocation: the parent process is `claude` invoked with
         a print/output flag (`-p`, `--print`, `--output-format json`).
         The SDK CLI does NOT set CLAUDE_HEADLESS, so without this fallback
         every `claude -p ...` call would register as interactive and
         pollute liveness tracking.
      3. Stdout is not a TTY. This is the weakest signal -- pipes happen
         in interactive sessions too -- so it is only used as a tertiary
         tiebreaker, never as a primary trigger.

    The /proc/<pid>/cmdline read is Linux-only. On other platforms the
    function silently falls through to the TTY check. Any unexpected error
    in the parent-cmdline probe is swallowed -- this hook must never block
    session start.

    Args:
        proc_root: Override for /proc (test injection). Defaults to /proc.
    """
    # (1) Explicit env signals.
    if os.environ.get("CLAUDE_HEADLESS") == "1":
        return True
    if os.environ.get("CI", "").lower() == "true":
        return True
    if os.environ.get("NONINTERACTIVE") == "1":
        return True

    # (2) Parent-process probe for SDK CLI invocations.
    if proc_root is None:
        proc_root = Path("/proc")
    try:
        if proc_root.exists():
            ppid = os.getppid()
            cmdline_path = proc_root / str(ppid) / "cmdline"
            if cmdline_path.exists():
                # /proc/<pid>/cmdline is NUL-separated, with a trailing NUL.
                raw = cmdline_path.read_bytes().decode("utf-8", errors="replace")
                argv = [a for a in raw.split("\x00") if a]
                if argv:
                    exe = Path(argv[0]).name.lower()
                    # Match the claude SDK CLI -- not the interactive TUI.
                    # Interactive `claude` has no -p/--print flag.
                    if "claude" in exe:
                        for arg in argv[1:]:
                            if arg in ("-p", "--print"):
                                return True
                            if arg.startswith("--output-format"):
                                return True
    except (OSError, ValueError, UnicodeDecodeError):
        # /proc missing (non-Linux), cmdline gone (race), or unparseable.
        # All non-fatal: fall through to TTY check.
        pass

    # (3) Tertiary: stdout not a TTY. Weak signal -- only return True if
    # explicitly non-tty AND the process likely lacks a controlling
    # terminal. We do NOT use this alone because piping stdout in an
    # interactive session is common.
    try:
        if not sys.stdout.isatty() and not sys.stdin.isatty():
            # Both pipes closed: very likely a headless invocation.
            return True
    except (AttributeError, ValueError):
        pass

    return False

from modules.core.stdin import has_stdin_data
from modules.core.logging_setup import configure_hook_logging
from modules.core.plugin_setup import run_first_time_setup
from modules.session.session_registry import register_session, SessionRegistryError

# Configure logging -- file handler only when GAIA_DEBUG is set; no
# hooks-*.log is written by default (see modules.core.logging_setup).
configure_hook_logging("session_start")
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

        # Pin the build this session is actually running. The RUNNING
        # session_start hook IS the code Claude Code loaded for this session,
        # so ``__file__``'s resolved parent is the authoritative running-hooks
        # tree -- more precise than re-resolving the ``.claude/hooks`` symlink
        # (which could already point elsewhere if a repack raced this hook).
        # We snapshot its content digest so `gaia doctor` can later tell the
        # user whether the wired hooks still match what is running (ACTIVE) or
        # a `gaia dev` landed a newer build that needs a restart (STALE).
        # Fully best-effort: any failure leaves the marker absent (doctor
        # reports UNKNOWN), it never blocks session start.
        _pinned_build = None
        try:
            from gaia.hooks_build import hooks_content_hash
            _running_hooks_dir = Path(__file__).resolve().parent
            _pinned_build = {
                "hooks_path": str(_running_hooks_dir),
                "hooks_hash": hooks_content_hash(_running_hooks_dir),
            }
        except Exception as _pin_exc:
            logger.debug("pinned_build computation failed (non-fatal): %s", _pin_exc)

        # Register this session in the user-scoped session registry.
        # Heartbeat-only liveness: PID isn't tracked because the hook
        # process is ephemeral. Failures are non-fatal — a missing
        # registry entry must never block session start.
        try:
            if _sid and _sid != "default":
                _is_headless = _detect_headless()
                register_session(
                    session_id=_sid,
                    is_headless=_is_headless,
                    pinned_build=_pinned_build,
                )
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

        # TTL-sweep stale DB pending approvals (AC-2). This is pure hygiene:
        # pendings are no longer surfaced into the session context, but the DB
        # is still the canonical pending store (read on demand by `gaia
        # approvals` and by session-agnostic grant matching), so orphans that
        # aged past DEFAULT_PENDING_TTL_MINUTES must still be reaped. The sweep
        # also runs at SubagentStop (via approval_cleanup.cleanup()); running it
        # here too -- GLOBAL across sessions, same as the SubagentStop sweep --
        # transitions genuinely stale rows to 'expired' even when no subagent is
        # dispatched (or a session dies). force=True semantics do not apply here
        # (there is no throttle on this sweep); non-fatal like the grant cleanup
        # above.
        try:
            from modules.security.approval_cleanup import expire_db_pendings
            _expired_pendings = expire_db_pendings(agent_type="session_start", session_id=_sid)
            if _expired_pendings:
                logger.info(
                    "approval_cleanup: expired %d stale DB pending(s) at SessionStart",
                    _expired_pendings,
                )
        except Exception as _pend_exc:
            logger.debug("expire_db_pendings failed (non-fatal): %s", _pend_exc)

        # Throttled DB auto-backup (AC-7). The user DB (~/.gaia/gaia.db) is
        # precious; back it up automatically, but SessionStart fires many
        # times a day, so maybe_backup_db() snapshots at most once per 24h
        # (skips when the newest snapshot is younger than the window) and
        # rotates to keep only the last 5. Copy-based + additive: it never
        # moves, deletes, or writes the live DB. Non-fatal like the grant /
        # pending sweeps above -- any failure logs and continues.
        try:
            from modules.session.db_backup import maybe_backup_db
            _snap_path = maybe_backup_db()
            if _snap_path:
                logger.info("db_backup: created SessionStart snapshot %s", _snap_path)
        except Exception as _bak_exc:
            logger.debug("maybe_backup_db failed (non-fatal): %s", _bak_exc)

        # First-time setup: create project permissions if needed.
        # mark_done=False so UserPromptSubmit can detect first-run
        # and show the welcome message before marking initialized.
        setup_message = run_first_time_setup(mark_done=False)
        if setup_message:
            logger.info("First-time setup: %s", setup_message)

        # Note: SessionStart no longer triggers an automatic project scan.
        # Scanning is a separate, on-demand flow (`gaia scan`). Project context
        # injection (below, via build_session_context) is unaffected -- it reads
        # whatever the DB already holds, it does not scan.

        # Build the SessionStart manifest (Phase 4). Combines the Environment
        # block, projects index, agentic-loop resume, and workspace memory into
        # a one-shot additionalContext payload (pending approvals are no longer
        # surfaced). Fully fail-safe -- an empty manifest just
        # means no hookSpecificOutput in the response, which Claude Code
        # treats as "nothing to inject".
        additional_context = ""
        try:
            from modules.session.session_manifest import build_session_context
            additional_context = build_session_context()
        except Exception as _manifest_exc:
            logger.debug(
                "build_session_context failed (non-fatal): %s", _manifest_exc
            )

        response = {"session_type": "startup"}
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
