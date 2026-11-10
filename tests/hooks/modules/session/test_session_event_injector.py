"""A dispatch carries no recent-events digest: the events file is one per machine, so its rows may be another session's."""

import json
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parents[4] / "hooks"
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))

from modules.session.session_event_injector import build_session_events  # noqa: E402

_ANOTHER_SESSIONS_COMMIT = {
    "event_type": "git_commit",
    "timestamp": "2026-08-06T13:11:00Z",
    "commit_hash": "a44822fdeadbeef",
    "commit_message": "fix(cert-manager): another client's commit",
}
_EVENT_THAT_RENDERS_NO_LINE = {
    "event_type": "git_commit",
    "timestamp": "2026-11-09T22:10:00Z",
    "commit_hash": "",
    "commit_message": "",
}


@pytest.mark.parametrize("events", [[_ANOTHER_SESSIONS_COMMIT], [_EVENT_THAT_RENDERS_NO_LINE]])
def test_a_dispatch_carries_nothing_from_the_machine_wide_events_file(tmp_path, monkeypatch, events):
    from modules.core import paths as core_paths

    session_dir = tmp_path / "session"
    session_dir.mkdir()
    (session_dir / "context.json").write_text(json.dumps({"critical_events": events}), encoding="utf-8")
    monkeypatch.setattr(core_paths, "get_session_dir", lambda: session_dir)

    assert build_session_events({"subagent_type": "developer"}) is None
