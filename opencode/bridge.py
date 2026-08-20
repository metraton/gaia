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


def handle(raw: dict[str, object]) -> dict[str, object]:
    """Evaluate one OpenCode event and return a plugin-safe response."""
    os.environ["GAIA_HOST"] = "opencode"
    if raw.get("event") == _ATTEST_EVENT:
        return _attest(raw)
    from adapters.opencode import OpenCodeAdapter

    adapter = OpenCodeAdapter()
    event = adapter.parse_event(json.dumps(raw))
    if event.event_type.value == "PreToolUse":
        response = adapter.adapt_pre_tool_use(event)
    elif event.event_type.value == "PostToolUse":
        response = adapter.adapt_post_tool_use(event)
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
        output = handle(raw)
    except Exception as exc:  # The plugin must fail closed on bridge failures.
        output = _deny(f"Gaia OpenCode policy bridge failed: {exc}")
    print(json.dumps(output, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
