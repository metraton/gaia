"""JSON bridge between the OpenCode plugin and Gaia's policy adapters.

The plugin owns OpenCode's native APIs and supplies immutable host identifiers.
This process only normalizes those facts, evaluates Gaia policy, and returns a
small JSON response; it never exposes the database to the plugin or an agent.
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


def _deny(reason: str) -> dict[str, object]:
    return {"action": "deny", "reason": reason}


def handle(raw: dict[str, object]) -> dict[str, object]:
    """Evaluate one OpenCode event and return a plugin-safe response."""
    os.environ["GAIA_HOST"] = "opencode"
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
