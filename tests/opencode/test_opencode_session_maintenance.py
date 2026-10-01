"""OpenCode's session start runs Claude Code's start maintenance, and its sessions count as live everywhere the registry is read.

Driven through ``bridge.handle``, the boundary the plugin spawns, against a
private HOME (the session registry) and database. The birth block itself is
``test_session_birth.py``'s; here it is stubbed so only the maintenance runs.
"""

from __future__ import annotations

import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
for _path in (str(_ROOT), str(_ROOT / "hooks"), str(_ROOT / "opencode")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from gaia.store.writer import (
    bind_harness_child_session,
    claim_dispatch_row,
    insert_dispatched_handoff,
)
from tests.fixtures.agent_ids import valid_agent_id

WORKSPACE = "me"


@pytest.fixture
def db(tmp_path, monkeypatch):
    (tmp_path / "home").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("GAIA_DB", str(tmp_path / "data" / "gaia.db"))
    monkeypatch.setenv("GAIA_HOST", "opencode")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("modules.session.session_lifecycle.start_context", lambda source, alarms: "birth")
    return tmp_path / "data" / "gaia.db"


@pytest.fixture
def steps(db, monkeypatch):
    """Record the maintenance steps after registration, which the registry itself shows."""
    calls = []

    def record(name, result):
        def step(*args, **kwargs):
            calls.append((name, args))
            return result
        return step

    monkeypatch.setattr("modules.security.approval_grants.cleanup_expired_grants", record("grants", 0))
    monkeypatch.setattr("modules.session.db_backup.maybe_backup_db", record("backup", None))
    monkeypatch.setattr("modules.session.contract_drafts_gc.gc_contract_drafts", record("drafts", 0))
    monkeypatch.setattr("gaia.retention.worktree_collector.sweep_repo_worktrees", record("worktrees", []))
    return calls


def _send(event: str, session_id: str) -> dict:
    import bridge

    return bridge.handle({"event": event, "sessionID": session_id})


def _registry() -> dict:
    from modules.session.session_registry import _load_registry

    return _load_registry()["sessions"]


def _live() -> set:
    from modules.session.session_registry import get_live_sessions

    return get_live_sessions()


def _age_heartbeat(session_id: str, seconds: float) -> None:
    from modules.session.session_registry import _load_registry, _save_registry

    data = _load_registry()
    data["sessions"][session_id]["last_heartbeat"] = time.time() - seconds
    _save_registry(data)


def _contract(db, token: str, session_id: str) -> str:
    agent_id = valid_agent_id(f"a{token}")
    return insert_dispatched_handoff(
        f"{agent_id}.{token}cafe", agent_id, WORKSPACE, session_id=session_id,
        db_path=db, agent_name="gaia-system", kind="investigation",
        dispatch_tool_use_id=f"call-{token}",
    )["contract_id"]


def _bound_child(db, token: str, parent_session: str, child_session: str) -> str:
    contract_id = _contract(db, token, parent_session)
    claim_dispatch_row(dispatch_tool_use_id=f"call-{token}", db_path=db)
    bind_harness_child_session(
        dispatch_tool_use_id=f"call-{token}", harness_agent_id=child_session, db_path=db,
    )
    return contract_id


def _agent_state(db, contract_id: str) -> str:
    import sqlite3

    con = sqlite3.connect(str(db))
    try:
        return con.execute(
            "SELECT agent_state FROM agent_contract_handoffs WHERE contract_id = ?", (contract_id,),
        ).fetchone()[0]
    finally:
        con.close()


def test_session_maintenance_runs_the_claude_code_core_on_the_main_session_start(steps, monkeypatch, tmp_path):
    from modules.session import session_lifecycle

    starts = []
    core = session_lifecycle.run_start_maintenance
    monkeypatch.setattr(
        session_lifecycle, "run_start_maintenance", lambda start: starts.append(start) or core(start),
    )

    response = _send("chat.message", "ses-oc")

    assert response == {"action": "allow", "additional_context": "birth"}
    assert [start.session_id for start in starts] == ["ses-oc"]
    assert starts[0].workspace_dir == tmp_path
    assert "ses-oc" in _live()
    assert [name for name, _ in steps] == ["grants", "backup", "drafts", "worktrees"]
    assert steps[-1][1] == (tmp_path,)


def test_session_maintenance_is_idempotent_per_session_in_a_process_serving_several(steps):
    for session_id in ("ses-a", "ses-b", "ses-a"):
        _send("chat.message", session_id)

    assert set(_registry()) == {"ses-a", "ses-b"}
    assert _live() == {"ses-a", "ses-b"}
    assert [name for name, _ in steps].count("worktrees") == 3


def test_session_maintenance_turn_end_keeps_the_main_session_alive_and_a_killed_one_goes_stale(db):
    from modules.session.session_registry import HEARTBEAT_TTL_SECONDS

    _send("chat.message", "ses-oc")
    _age_heartbeat("ses-oc", HEARTBEAT_TTL_SECONDS - 60)

    _send("session.idle", "ses-oc")
    assert time.time() - _registry()["ses-oc"]["last_heartbeat"] < 60

    _age_heartbeat("ses-oc", HEARTBEAT_TTL_SECONDS + 1)
    assert "ses-oc" in _registry()
    assert "ses-oc" not in _live()


def test_session_maintenance_protects_each_hosts_live_worktrees_from_the_other(db):
    from gaia.retention.worktree_collector import worktree_collect_reason
    from modules.session.session_lifecycle import SessionStart, run_start_maintenance
    from modules.session.session_registry import HEARTBEAT_TTL_SECONDS

    _send("chat.message", "ses-oc")
    run_start_maintenance(SessionStart(
        session_id="cc-session", source="startup", is_headless=False,
        pinned_build=None, workspace_dir=Path.cwd(), plugin_channel=False,
    ))
    opencode_worktree = _contract(db, "0c0c0c", "ses-oc")
    claude_worktree = _contract(db, "cc1cc1", "cc-session")
    untouched_for_days = time.time() - 7 * 86400

    assert worktree_collect_reason(opencode_worktree, untouched_for_days, grace_hours=1) is None
    assert worktree_collect_reason(claude_worktree, untouched_for_days, grace_hours=1) is None

    _age_heartbeat("ses-oc", HEARTBEAT_TTL_SECONDS + 1)
    assert worktree_collect_reason(opencode_worktree, untouched_for_days, grace_hours=1)
    assert worktree_collect_reason(claude_worktree, untouched_for_days, grace_hours=1) is None

    _send("chat.message", "ses-oc")
    _age_heartbeat("cc-session", HEARTBEAT_TTL_SECONDS + 1)
    assert worktree_collect_reason(opencode_worktree, untouched_for_days, grace_hours=1) is None
    assert worktree_collect_reason(claude_worktree, untouched_for_days, grace_hours=1)


def test_session_maintenance_keeps_a_live_opencode_requesters_pending_out_of_orphaned(db):
    from gaia.approvals.reading import ORPHANED, PENDING, _ISO, decision_state, live_sessions

    _send("chat.message", "ses-oc")
    quiet_for_hours = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime(_ISO)
    approval = {"status": "pending", "session_id": "ses-oc", "created_at": quiet_for_hours}

    assert decision_state(approval, [], live=live_sessions()) == PENDING

    _send("session.deleted", "ses-oc")
    assert decision_state(approval, [], live=live_sessions()) == ORPHANED


def test_session_maintenance_deleting_the_main_unregisters_it_and_deleting_a_child_closes_only_its_row(db):
    _send("chat.message", "ses-main")
    child_row = _bound_child(db, "c41d00", "ses-main", "ses-child")

    assert _send("session.deleted", "ses-child")["closed"]["status"] == "closed"
    assert _agent_state(db, child_row) != "DISPATCHED"
    assert "ses-main" in _live()

    sibling_row = _bound_child(db, "51b100", "ses-main", "ses-sibling")
    assert _send("session.deleted", "ses-main") == {"contract_valid": True, "closed": {"status": "no_row"}}
    assert "ses-main" not in _registry()
    assert _agent_state(db, sibling_row) == "DISPATCHED"


def test_session_maintenance_deleting_a_main_session_with_an_open_approval_control_still_unregisters_it(db):
    plugin = _ROOT / "opencode" / "plugin.ts"
    probe = (
        f"import {{ forwardsPastOpenControls }} from {str(plugin)!r};"
        "console.log(JSON.stringify(['session.deleted', 'session.error', 'session.idle']"
        ".map(forwardsPastOpenControls)))"
    )
    result = subprocess.run(["bun", "-e", probe], cwd=_ROOT, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip().splitlines()[-1] == "[true,false,false]"

    _send("chat.message", "ses-main")
    _send("session.deleted", "ses-main")
    assert "ses-main" not in _registry()
