"""Gate 1008 (task 536, T8): the lifecycle transport set routed through
``bridge.handle`` -- the real boundary the plugin spawns -- with cero
"Unsupported OpenCode bridge event" responses for the conjunto, and the dead
``_EVENT_TYPES`` mappings (``session.created``, ``message.updated``) gone
rather than merely aparent coverage.

PENDING, plan 65 T11 (task 539, gate 1014): "Stop"/"PostToolUseFailure"/
"SessionEnd" (session.idle/error/deleted) are wired here to route to
``adapter.adapt_subagent_stop`` -- the real session-lifecycle close --
instead of the quality no-op (Stop) / bare acknowledgment
(PostToolUseFailure/SessionEnd) this gate originally certified. T11's own
turn found ``hooks/adapters/opencode.py`` and
``hooks/modules/agents/dispatch_lifecycle.py`` write-protected (T3_BLOCKED,
approval_ids P-531c6c5f8100e19efcc474787975a538 and
P-41c3d6a64ab0480896ac5ca079076574) and closed APPROVAL_REQUEST with the
close design sealed in its contract row rather than applying it unreviewed;
this file's assertions describe the CURRENT, reachable behavior only, and a
follow-up turn that applies the approved edit adds the close-behavior
assertions (``{"contract_valid": True, "closed": ...}`` replacing the bare
``{"action": "allow"}`` for idle/error/deleted, PostCompact unchanged) in the
SAME commit as that edit.

Isolation is explicit (``GAIA_DATA_DIR`` -> ``tmp_path``) even though no
route exercised here binds a dispatch row today: once T11's close lands,
``bridge.handle`` for these kinds reads (and, for a bound session, writes)
the store, and this fixture must already keep that off the developer's real
``~/.gaia`` rather than being retrofitted alongside the routing change.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

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


@pytest.fixture(autouse=True)
def _isolated_gaia_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))


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
