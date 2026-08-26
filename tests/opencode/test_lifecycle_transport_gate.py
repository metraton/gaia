"""Gate 1008 (task 536, T8): the lifecycle transport set routed through
``bridge.handle`` -- the real boundary the plugin spawns -- with cero
"Unsupported OpenCode bridge event" responses for the conjunto, and the dead
``_EVENT_TYPES`` mappings (``session.created``, ``message.updated``) gone
rather than merely aparent coverage.

None of the routes exercised here (Stop, SubagentStart, and the acknowledged
PostToolUseFailure/PostCompact/SessionEnd kinds) touch the database, so this
needs no scratch-db fixture.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
for _path in (str(_ROOT), str(_ROOT / "hooks"), str(_ROOT / "opencode")):
    if _path not in sys.path:
        sys.path.insert(0, _path)


_LIFECYCLE_EVENTS = {
    "message.part.updated": {
        "sessionID": "ses-parent",
        "callID": "call-1",
        "state": {"metadata": {"sessionId": "ses-child"}},
    },
    "session.idle": {"sessionID": "ses-x"},
    "session.error": {"sessionID": "ses-x"},
    "session.deleted": {"sessionID": "ses-x"},
    "session.compacted": {"sessionID": "ses-x"},
}


def _handle(event_name: str, fields: dict) -> dict:
    import bridge

    return bridge.handle({"event": event_name, **fields})


def test_bridge_routes_the_full_lifecycle_conjunto_with_no_unsupported_denial():
    for event_name, fields in _LIFECYCLE_EVENTS.items():
        response = _handle(event_name, fields)
        assert response.get("reason", "") != f"Unsupported OpenCode bridge event: {event_name}", (
            event_name,
            response,
        )


def test_dead_event_type_mappings_are_gone_and_the_conjunto_is_wired():
    from adapters.opencode import _EVENT_TYPES

    for dead in ("session.created", "message.updated"):
        assert dead not in _EVENT_TYPES, f"{dead} maps with no real transport path"

    for required in _LIFECYCLE_EVENTS:
        assert required in _EVENT_TYPES, f"{required} is missing from _EVENT_TYPES"
