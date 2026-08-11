#!/usr/bin/env python3
"""
Pre-tool use hook - Thin Gate Architecture.

Entry point for Bash and Task/Agent tool validation. The hook is the primary
security gate: with Bash(*) in the settings.json allow list, all commands
reach this hook regardless of settings.json permissions.

Architecture:
- Uses adapter layer to parse and process the full PreToolUse lifecycle
- All business logic lives in ClaudeCodeAdapter.adapt_pre_tool_use()
- This file is stdin/stdout glue only

Two obligations belong to the glue itself, because nothing downstream can
discharge them. First, it WAITS for the payload rather than sampling for it:
the decision to run at all is made here, and a gate that skips itself because
the host's write was a few milliseconds late is the one failure no verdict can
describe. Second, EVERY exit records -- an exit that delivers no verdict is a
gate failure and leaves a fail-open trace, so no path out of this file is mute.

The former backward-compatible API (pre_tool_use_hook / _handle_* / main) was
retired: it diverged from the real path (no delegate-mode gate, no CLI-only
guard, no identity injection, no born-at-dispatch row) and its module-level
imports made every hook invocation pay for a lane only tests used. Tests
drive adapters.claude_code.ClaudeCodeAdapter.adapt_pre_tool_use directly
(see tests/fixtures/pretool_adapter.py).
"""
from __future__ import annotations

import os
import select
import sys
import json
import logging
from pathlib import Path
from typing import Any, Mapping, NoReturn, Optional

_hooks_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(_hooks_dir))
_pkg_root = str(_hooks_dir.parent)
if _pkg_root not in sys.path:
    sys.path.insert(0, _pkg_root)
from modules.core.hook_trace import record_hook_invocation
from modules.core.logging_setup import configure_hook_logging

# Adapter layer -- get_adapter() is the single construction point (registry),
# so this entry point never names the concrete host class.
from adapters.registry import get_adapter
from modules.core.stdin import has_stdin_data
from adapters.utils import warn_if_dual_channel

# Configure logging -- file handler only when GAIA_DEBUG is set (see
# modules.core.logging_setup); no hooks-*.log is written by default.
configure_hook_logging("pre_tool_use")
logger = logging.getLogger(__name__)


USAGE = (
    "Usage: echo '{\"hook_event_name\":\"PreToolUse\",\"tool_name\":\"Bash\","
    "\"tool_input\":{\"command\":\"ls\"}}' | python pre_tool_use.py"
)

# How long to wait for the host's payload before concluding there is none.
#
# The check this replaces was a zero-wait select(): if the write had not
# physically landed in the pipe at that instant, the gate declared it had no
# work and exited, and the tool call ran unvalidated. Nothing orders the host's
# write against this process's first read -- the race was only ever won by
# accident, because interpreter startup usually costs more than the write.
#
# Waiting is the remedy that fits the shape of the problem: the payload is
# almost always already buffered, so the wait costs nothing in the normal case
# and is paid only when the gate would otherwise have skipped itself. The
# deadline is what keeps the remedy from being worse than the defect -- a gate
# that blocked forever on a pipe nobody writes to would stop the session rather
# than the command. Overridable so an install on a slow host can widen it and
# tests can shrink it.
_STDIN_WAIT_SECONDS_DEFAULT = 2.0
_STDIN_WAIT_ENV = "GAIA_HOOK_STDIN_TIMEOUT"


def _stdin_wait_seconds() -> float:
    """Deadline for the payload to arrive, in seconds."""
    raw = os.environ.get(_STDIN_WAIT_ENV, "").strip()
    if raw:
        try:
            parsed = float(raw)
            if parsed >= 0:
                return parsed
        except ValueError:
            pass
    return _STDIN_WAIT_SECONDS_DEFAULT


def _await_stdin_data(timeout: float) -> bool:
    """Whether the host's payload is readable, waiting up to ``timeout`` for it.

    A closed pipe counts as readable and resolves immediately: that is an EOF,
    an answer rather than a wait. An interactive stdin is not a hook invocation
    at all and never waits.
    """
    if sys.stdin.isatty():
        return False
    try:
        readable, _, _ = select.select([sys.stdin], [], [], max(timeout, 0.0))
        return bool(readable)
    except Exception:
        return has_stdin_data()


def _trace(
    exit_code: int,
    *,
    payload: Optional[Mapping[str, Any]] = None,
    blocked: bool = False,
    extra: Optional[Mapping[str, Any]] = None,
) -> None:
    """Record that this hook ran, whatever it decided or failed to decide.

    Every exit records, including the ones that decide nothing: an invocation
    that leaves no line is indistinguishable offline from a hook that was never
    dispatched, which is precisely the confusion the silent exits caused.
    """
    record_hook_invocation(
        "pre_tool_use",
        payload=payload,
        exit_code=exit_code,
        blocked=blocked,
        extra=dict(extra) if extra else None,
    )


def _fail_open_exit(
    reason: str,
    detail: str,
    *,
    payload: Optional[Mapping[str, Any]] = None,
    cause: Optional[str] = None,
) -> NoReturn:
    """Deliver a gate failure to the user and the record, then exit.

    Every exit of this entry point that is not a delivered verdict is a gate
    failure: the hook returns without having decided. Routing them all through
    here is what keeps that from being silent, and what applies the one case
    where the operation is stopped instead -- a command the gate had already
    classified as mutating. See modules.security.fail_open. Imported lazily so
    an ordinary invocation, which never reaches this path, does not pay for the
    import.
    """
    from modules.security.fail_open import CAUSE_ERROR, decide_fail_open

    outcome = decide_fail_open(reason, detail, cause or CAUSE_ERROR)
    print(outcome.message, file=sys.stderr)
    print(outcome.message)
    _trace(
        outcome.exit_code,
        payload=payload,
        blocked=outcome.blocked,
        extra={"fail_open": reason},
    )
    sys.exit(outcome.exit_code)


def _exit_without_input() -> NoReturn:
    """Handle the case where no payload arrived before the deadline.

    Waiting narrows the race but cannot close it: the host may genuinely never
    write. That leaves the same situation as any other gate failure -- the
    operation proceeds and no verdict was reached -- so it degrades the same
    way, recorded and marked, rather than exiting mute. It stays NON-blocking
    on purpose: refusing every tool call whose payload went missing would brick
    the session, which the product posture rules out.

    An interactive stdin is the one exception, and not a gate failure at all:
    nobody dispatched a tool call, so a degradation notice would be a false
    alarm. It still records the invocation.
    """
    if sys.stdin.isatty():
        print(USAGE)
        _trace(1, extra={"no_input": "interactive"})
        sys.exit(1)

    from modules.security.fail_open import CAUSE_NO_INPUT

    _fail_open_exit(
        "stdin_payload_absent",
        f"no data on stdin after waiting {_stdin_wait_seconds()}s",
        cause=CAUSE_NO_INPUT,
    )


# ============================================================================
# STDIN HANDLER (Claude Code integration)
# ============================================================================

if __name__ == "__main__":
    if _await_stdin_data(_stdin_wait_seconds()):
        # Bound before the try so a failure AFTER a successful parse can still
        # attribute its trace line to the tool call it happened on.
        event = None
        try:
            adapter = get_adapter()
            warn_if_dual_channel()

            stdin_data = sys.stdin.read()

            try:
                event = adapter.parse_event(stdin_data)
            except ValueError as e:
                error_msg = str(e)
                logger.error(f"Adapter parse failed: {error_msg}")
                _fail_open_exit("event_parse_failed", f"{type(e).__name__}: {e}")

            response = adapter.adapt_pre_tool_use(event)

            # Always-on invocation trace. This entry point does not go through
            # modules.core.hook_entry.run_hook, so it records its own line --
            # and it is the one hook whose rejection is carried by the
            # permissionDecision rather than by the exit code, hence the
            # explicit `blocked` flag instead of the exit-code default.
            _decision = None
            if isinstance(response.output, dict):
                _decision = response.output.get("hookSpecificOutput", {}).get(
                    "permissionDecision"
                )
            _trace(
                response.exit_code,
                payload=getattr(event, "payload", None),
                blocked=_decision in ("block", "deny") or response.exit_code == 2,
                extra={"decision": _decision} if _decision else None,
            )

            if isinstance(response.output, dict) and response.output:
                hook_output = response.output.get("hookSpecificOutput", {})
                decision = hook_output.get("permissionDecision")
                if decision in ("block", "deny"):
                    reason = hook_output.get("permissionDecisionReason", "Command blocked by hook policy")
                    summary = reason.split('\n')[0]
                    print(f"BLOCKED: {summary}", file=sys.stderr)
                elif decision == "ask":
                    reason = hook_output.get("permissionDecisionReason", "")
                    summary = reason.split('\n')[0]
                    print(f"T3: {summary}", file=sys.stderr)
                print(json.dumps(response.output))
                sys.exit(response.exit_code)
            elif isinstance(response.output, str) and response.output:
                summary = response.output.split('\n')[0]
                label = "HOOK ERROR" if response.exit_code == 1 else "BLOCKED"
                print(f"{label}: {summary}", file=sys.stderr)
                print(response.output)
                sys.exit(response.exit_code)
            else:
                sys.exit(0)

        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON from stdin: {e}")
            _fail_open_exit(
                "invalid_stdin_json",
                f"{type(e).__name__}: {e}",
                payload=getattr(event, "payload", None),
            )
        except Exception as e:
            logger.error(f"Error processing hook: {e}", exc_info=True)
            _fail_open_exit(
                "unhandled_exception",
                f"{type(e).__name__}: {e}",
                payload=getattr(event, "payload", None),
            )
    else:
        _exit_without_input()
