"""The lifecycle events the plugin forwards reach a real route through ``bridge.handle``, the boundary the plugin spawns.

session.idle, session.error and session.deleted close the bound dispatch row
(``tests/opencode/test_opencode_subagent_stop_close.py`` covers the close);
only session.compacted is still a bare acknowledgment. No session here is
bound to a row, so every route resolves ``no_row`` without writing.
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


@pytest.fixture(autouse=True)
def _contain_bridge_host_mutation(monkeypatch):
    """Restore GAIA_HOST after each test: bridge.handle() writes it into os.environ with no revert, and these tests share the pytest worker."""
    monkeypatch.setenv("GAIA_HOST", "opencode")


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


def test_session_start_rides_chat_message_and_the_conjunto_is_wired():
    from adapters.opencode import _EVENT_TYPES
    from adapters.opencode_parity import PENDING_GAPS
    from adapters.types import HookEventType

    assert _EVENT_TYPES["chat.message"] is HookEventType.SESSION_START
    assert "session birth block" not in PENDING_GAPS

    for required in _LIFECYCLE_EVENTS:
        assert required in _EVENT_TYPES, f"{required} is missing from _EVENT_TYPES"


def test_idle_error_deleted_all_reach_the_real_close_not_an_acknowledgment():
    for event_name in ("session.idle", "session.error", "session.deleted"):
        response = _handle(event_name, _LIFECYCLE_EVENTS[event_name])
        assert response == {"contract_valid": True, "closed": {"status": "no_row"}}, (
            event_name,
            response,
        )


def test_post_compact_is_still_a_bare_acknowledgment():
    response = _handle("session.compacted", _LIFECYCLE_EVENTS["session.compacted"])
    assert response == {"action": "allow"}
