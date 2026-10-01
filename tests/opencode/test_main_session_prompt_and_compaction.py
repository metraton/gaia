"""OpenCode's main session gets Claude Code's per-prompt and post-compaction context, and a child does not.

The bridge runs in process against a temporary HOME, GAIA_DATA_DIR and GAIA_DB;
the Bun driver asserts which sessions and messages the plugin asks Gaia for.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import copy_bootstrapped_db
from tests.fixtures.agent_ids import valid_agent_id

_ROOT = Path(__file__).resolve().parents[2]
for _path in (str(_ROOT), str(_ROOT / "hooks"), str(_ROOT / "opencode")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

MAIN_SESSION = "ses-main-prompt"
CHILD_SESSION = "ses-child-prompt"
DUE_NOTIFICATIONS = 2


@pytest.fixture
def isolated(tmp_path, monkeypatch, bootstrapped_db_template):
    data = tmp_path / "data"
    db = copy_bootstrapped_db(bootstrapped_db_template, data / "gaia.db")
    (tmp_path / "home").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GAIA_DATA_DIR", str(data))
    monkeypatch.setenv("GAIA_DB", str(db))
    monkeypatch.setenv("GAIA_HOST", "opencode")
    monkeypatch.chdir(tmp_path)
    return db


@pytest.fixture
def due_notifications(monkeypatch):
    workspaces: list = []

    def count(workspace=None, db_path=None):
        workspaces.append(workspace)
        return DUE_NOTIFICATIONS

    monkeypatch.setattr("gaia.store.reader.count_unread_notifications", count)
    return workspaces


def _stale_registration(session_id: str) -> None:
    from modules.session import session_registry

    session_registry.register_session(session_id)
    registry = session_registry._load_registry()
    registry["sessions"][session_id]["last_heartbeat"] = 0
    session_registry._save_registry(registry)


def _heartbeat(session_id: str) -> float:
    from modules.session import session_registry

    return session_registry._load_registry()["sessions"][session_id]["last_heartbeat"]


def test_prompt_submit_bridge_refreshes_the_heartbeat_and_returns_the_due_notifications_line(
    isolated, due_notifications,
):
    import bridge
    from gaia.project import current
    from modules.session.session_lifecycle import notifications_counter

    _stale_registration(MAIN_SESSION)

    response = bridge.handle({"event": "chat.prompt", "sessionID": MAIN_SESSION})

    assert response["action"] == "allow"
    assert _heartbeat(MAIN_SESSION) > 0
    assert f"{DUE_NOTIFICATIONS} notifications due" in response["additional_context"]
    assert due_notifications == [current() or None]
    assert response["additional_context"] == notifications_counter(current() or None)


def test_prompt_submit_bridge_returns_nothing_when_no_notification_is_due(isolated, monkeypatch):
    import bridge

    monkeypatch.setattr(
        "gaia.store.reader.count_unread_notifications", lambda workspace=None, db_path=None: 0,
    )

    response = bridge.handle({"event": "chat.prompt", "sessionID": MAIN_SESSION})

    assert response == {"action": "allow", "additional_context": ""}


def test_primary_compaction_bridge_returns_the_claude_code_compact_context(isolated):
    import bridge
    from modules.session.session_lifecycle import start_context

    response = bridge.handle({"event": "session.compacting", "sessionID": MAIN_SESSION, "main": True})

    context = response["updated_input"]["context"]
    assert response["action"] == "allow"
    assert context == [start_context("compact", [])]
    assert context[0]


def test_primary_compaction_is_not_injected_into_an_unbound_non_main_session(isolated):
    import bridge

    response = bridge.handle({"event": "session.compacting", "sessionID": CHILD_SESSION, "main": False})

    assert response == {"action": "allow"}


def test_primary_compaction_leaves_the_child_kernel_reinjection_as_is(isolated):
    import bridge
    from gaia.store.writer import (
        bind_harness_child_session,
        claim_dispatch_row,
        find_dispatch_row_by_harness_agent_id,
        insert_dispatched_handoff,
    )
    from modules.context.kernel_builder import build_dispatch_kernel

    agent_id = valid_agent_id("at7child")
    insert_dispatched_handoff(
        f"{agent_id}.t7cafe", agent_id, "me", session_id=None, db_path=isolated,
        agent_name="gaia-system", kind="investigation", dispatch_tool_use_id="call-t7",
    )
    claim_dispatch_row(dispatch_tool_use_id="call-t7", db_path=isolated)
    bind_harness_child_session(
        dispatch_tool_use_id="call-t7", harness_agent_id=CHILD_SESSION, db_path=isolated,
    )
    kernel = build_dispatch_kernel(find_dispatch_row_by_harness_agent_id(CHILD_SESSION, db_path=isolated))

    response = bridge.handle({"event": "session.compacting", "sessionID": CHILD_SESSION, "main": False})

    assert response == {"action": "allow", "updated_input": {"context": [kernel]}}


def test_prompt_submit_and_primary_compaction_plugin_ask_only_for_the_main_session():
    result = subprocess.run(
        ["bun", "test", str(_ROOT / "tests/opencode/main_session_prompt.test.ts")],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
