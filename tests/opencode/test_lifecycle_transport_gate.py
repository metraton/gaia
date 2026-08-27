"""Gate 1008 (task 536, T8) + gate 1014 (task 539, T11): the lifecycle
transport set routed through ``bridge.handle`` -- the real boundary the
plugin spawns -- with cero "Unsupported OpenCode bridge event" responses for
the conjunto, and the dead ``_EVENT_TYPES`` mappings (``session.created``,
``message.updated``) gone rather than merely aparent coverage.

T11 changed WHAT "Stop"/"PostToolUseFailure"/"SessionEnd" (session.idle/
error/deleted) actually do: they used to be a quality no-op (Stop) or a bare
acknowledgment (PostToolUseFailure/SessionEnd) -- T8's own placeholder. They
now all route to ``adapter.adapt_subagent_stop``, the real session-lifecycle
close (plan 65, T11, approval_ids P-41c3d6a64ab0480896ac5ca079076574 and
P-531c6c5f8100e19efcc474787975a538, applied on user approval) -- see
``tests/opencode/test_opencode_subagent_stop_close.py`` for the close
behavior's 5 named gate-1014 cases. Only ``PostCompact`` is still
bare-acknowledged.

None of ``ses-x``/``ses-child`` here are ever bound to a dispatch row, so
every route exercised in this module resolves ``{"status": "no_row"}`` and
performs a READ with no write -- but the isolation is still explicit (never
the developer's real ``~/.gaia``), since a route that touches the store at
all should never do so against live data by accident.
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


def test_idle_error_deleted_all_reach_the_real_close_not_an_acknowledgment():
    """T11: unlike PostCompact (still a bare {"action": "allow"}), idle/
    error/deleted each carry adapt_subagent_stop's own output shape
    (``contract_valid``/``closed``), never the acknowledgment's bare
    ``{"action": "allow"}`` with nothing else."""
    for event_name in ("session.idle", "session.error", "session.deleted"):
        response = _handle(event_name, _LIFECYCLE_EVENTS[event_name])
        assert response == {"contract_valid": True, "closed": {"status": "no_row"}}, (
            event_name,
            response,
        )


def test_post_compact_is_still_a_bare_acknowledgment():
    response = _handle("session.compacted", _LIFECYCLE_EVENTS["session.compacted"])
    assert response == {"action": "allow"}
