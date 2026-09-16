"""JSON bridge between the OpenCode plugin and Gaia's policy adapters.

The plugin owns OpenCode's native APIs and supplies the session, call and tool
identifiers it holds. What identifies the host run itself is not among them: it
is derived here, from this process's own lineage, because a value the plugin
sent would be a value the plugin's own caller could name. This process
normalizes what it is given, evaluates Gaia policy, and returns a small JSON
response; it never exposes the database to the plugin or an agent.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


_ROOT = Path(__file__).resolve().parent.parent
_HOOKS = _ROOT / "hooks"
for _path in (str(_ROOT), str(_HOOKS)):
    if _path not in sys.path:
        sys.path.insert(0, _path)


_ATTEST_EVENT = "identity.attest"

# Must stay equal to plugin.ts's UNCORRELATED_PERMISSION_EVENT: the two halves
# of this adapter exchange the name by value, and a rename on one side silently
# stops the audit rather than failing.
_UNCORRELATED_PERMISSION_EVENT = "permission.uncorrelated"

# The lane named here is permission.ask, not decision_audit's
# LANE_OPENCODE_PERMISSION ("opencode.permission_replied"): what is recorded is
# a request refused before any user reply existed, so reusing the reply lane's
# name would misreport where the decision was lost.
PERMISSION_ASK_LANE = "opencode.permission_ask"


def _deny(reason: str) -> dict[str, object]:
    return {"action": "deny", "reason": reason}


def _attest(raw: dict[str, object]) -> dict[str, object]:
    """Mint one identity claim inside this Gaia-side process.

    The plugin asks for a claim and never composes one: the token is a nonce
    this process generates and records, so what the plugin later presents is
    resolvable against host state rather than derived from a tool argument.

    The ledger the claim is written to is named by ``host_run_id``, which reads
    the process that started this one. Nothing in the request selects it: a
    caller that invokes this bridge itself mints under its own launcher and
    cannot reach the namespace the legitimate host run resolves against.
    """
    from modules.security.host_attestation import (
        AttestationDenied,
        host_run_id,
        issue,
    )

    try:
        issued = issue(
            host_run=host_run_id(),
            session_id=str(raw.get("sessionID") or raw.get("session_id") or ""),
            role=str(raw.get("role") or ""),
            issuer=str(raw.get("issuer") or ""),
            parent_attestation=raw.get("parentAttestation")
            or raw.get("parent_attestation"),
        )
    except AttestationDenied as exc:
        return _deny(f"Gaia refused to attest this OpenCode identity: {exc}")
    return {
        "action": "allow",
        "attestation": issued.token,
        "granted_by": issued.granted_by,
        "delegation_depth": issued.depth,
    }


# PostCompact has no per-host adapter method on ANY host yet -- Claude Code's
# own compact hook (hooks/pre_compact.py, hooks/post_compact.py) returns the
# same schema-valid empty acknowledgment without adapter dispatch. Routed
# here rather than denied, so a genuinely open lifecycle point is not
# misreported as this bridge's "Unsupported" placeholder.
#
# PostToolUseFailure (session.error) and SessionEnd (session.deleted) used
# to be acknowledged here too, alongside PostCompact -- until plan 65 T11
# gave them a real close to perform (see the "Stop"/"PostToolUseFailure"/
# "SessionEnd" branch below): a dispatched child's row must be promoted or
# cut on ANY of idle/error/deleted, not only idle.
_ACKNOWLEDGED_EVENT_KINDS = {"PostCompact"}


def _ack() -> dict[str, object]:
    return {"action": "allow"}


def _record_uncorrelated_permission(raw: dict[str, object]) -> dict[str, object]:
    """Record one host permission request that matched no Gaia verdict.

    The plugin has already denied it by the time this runs, so nothing here can
    change the outcome and the acknowledgment is unconditional -- an audit write
    that failed must not be reported to the plugin as a policy answer.

    Two denials share this channel. Without a ``cause`` the request carried no
    binding to any session Gaia ruled on (REASON_NO_SESSION_BINDING). With one,
    the plugin tried to present ``approvalID`` and ``gaia approvals
    opencode-present`` refused; the cause it returned is recorded verbatim
    under REASON_PRESENTATION_FAILED so the refusal is queryable by approval.
    """
    from gaia.approvals.decision_audit import (
        REASON_NO_SESSION_BINDING,
        REASON_PRESENTATION_FAILED,
        record_decision_not_activated,
    )

    call_id = str(raw.get("callID") or "")
    cause = str(raw.get("cause") or "")
    record_decision_not_activated(
        reason=REASON_PRESENTATION_FAILED if cause else REASON_NO_SESSION_BINDING,
        lane=PERMISSION_ASK_LANE,
        session_id=str(raw.get("sessionID") or ""),
        approval_id=str(raw.get("approvalID") or "") or None,
        detail=cause or "host permission request correlated to no Gaia verdict",
        details={"call_id": call_id},
    )
    return _ack()


def handle(raw: dict[str, object], *, shell_env_transport: bool = False) -> dict[str, object]:
    """Evaluate one OpenCode event and return a plugin-safe response."""
    os.environ["GAIA_HOST"] = "opencode"
    if raw.get("event") == _ATTEST_EVENT:
        return _attest(raw)
    if raw.get("event") == _UNCORRELATED_PERMISSION_EVENT:
        return _record_uncorrelated_permission(raw)
    from adapters.opencode import OpenCodeAdapter

    adapter = OpenCodeAdapter()
    event = adapter.parse_event(json.dumps(raw))
    kind = event.event_type.value

    if kind == "PreToolUse":
        if shell_env_transport:
            handler = getattr(adapter, "_adapt_pre_tool_use_with_shell_env", None)
            if handler is None:
                return _deny("Gaia shell-env transport requires the matching policy adapter")
            response = handler(event)
        else:
            response = adapter.adapt_pre_tool_use(event)
    elif kind == "PostToolUse":
        response = adapter.adapt_post_tool_use(event)
    elif kind in ("Stop", "PostToolUseFailure", "SessionEnd"):
        # session.idle/error/deleted (plan 65, T11): the ONE real close for a
        # dispatched child's row, regardless of which of the three signals
        # arrives first -- resolve_close (dispatch_lifecycle) is idempotent,
        # so a later signal for the same session is a harmless no-op.
        response = adapter.adapt_subagent_stop(event)
    elif kind == "SubagentStart":
        response = adapter.format_context_response(adapter.adapt_subagent_start(event.payload))
    elif kind == "PreCompact":
        # session.compacting (plan 65, T12): the one compaction signal that
        # can still inject, dispatched here rather than folded into
        # _ACKNOWLEDGED_EVENT_KINDS because -- unlike PostCompact -- it now
        # has a real per-host adapter method.
        response = adapter.adapt_pre_compact(event)
    elif kind in _ACKNOWLEDGED_EVENT_KINDS:
        return _ack()
    else:
        return _deny(f"Unsupported OpenCode bridge event: {raw.get('event', '')}")

    if not isinstance(response.output, dict):
        return _deny(str(response.output))
    return dict(response.output)


def main() -> int:
    """Read one JSON request from stdin and emit one JSON response."""
    try:
        raw = json.load(sys.stdin)
        if not isinstance(raw, dict):
            raise ValueError("OpenCode bridge input must be a JSON object")
        if sys.argv[1:] not in ([], ["--shell-env-v1"]):
            raise ValueError("Unsupported Gaia bridge transport arguments")
        output = handle(raw, shell_env_transport=sys.argv[1:] == ["--shell-env-v1"])
    except Exception as exc:  # The plugin must fail closed on bridge failures.
        output = _deny(f"Gaia OpenCode policy bridge failed: {exc}")
    print(json.dumps(output, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
