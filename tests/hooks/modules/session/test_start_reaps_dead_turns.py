"""SessionStart reaps a dispatched turn whose host died, so no dead DISPATCHED row stays never_finalized."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[4]
for _path in (str(_ROOT), str(_ROOT / "hooks")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from gaia.store.writer import (  # noqa: E402
    agent_contract_handoff_state,
    insert_dispatched_handoff,
    write_harness_event_lean,
)
from tests.fixtures.agent_ids import valid_agent_id  # noqa: E402

LONG_AGO = "2026-01-01T00:00:00Z"


@pytest.fixture
def db(tmp_path, monkeypatch):
    (tmp_path / "home").mkdir()
    (tmp_path / "ledgers").mkdir()
    for name, value in (("HOME", tmp_path / "home"), ("GAIA_DATA_DIR", tmp_path / "data"),
                        ("GAIA_DB", tmp_path / "data" / "gaia.db"),
                        ("GAIA_OPENCODE_ATTESTATION_DIR", tmp_path / "ledgers")):
        monkeypatch.setenv(name, str(value))
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    monkeypatch.chdir(tmp_path)
    for target in ("modules.session.db_backup.maybe_backup_db",
                   "gaia.retention.worktree_collector.sweep_repo_worktrees"):
        monkeypatch.setattr(target, lambda *args, **kwargs: None)
    return tmp_path / "data" / "gaia.db"


def _dispatched(db, agent_hex: str, harness_agent_id: str, session_id: str = "ses-dead") -> str:
    agent_id = valid_agent_id(agent_hex)
    contract_id = f"{agent_id}.{agent_hex[:6]}"
    insert_dispatched_handoff(contract_id=contract_id, agent_id=agent_id, workspace="me",
                              session_id=session_id, db_path=db)
    con = sqlite3.connect(db)
    con.execute("UPDATE agent_contract_handoffs SET created_at = ?, claimed_at = ?, harness_agent_id = ? "
                "WHERE contract_id = ?", (LONG_AGO, LONG_AGO, harness_agent_id, contract_id))
    con.commit()
    con.close()
    return contract_id


def test_session_start_reaps_a_stale_dispatched_turn_and_spares_one_still_calling_tools(db):
    from modules.session.session_lifecycle import SessionStart, run_start_maintenance

    dead = _dispatched(db, "a1111dead", "harness-dead")
    working = _dispatched(db, "a2222live", "harness-live")
    write_harness_event_lean(event_type="tool.called", source="test",
                             meta={"harness_agent_id": "harness-live", "command": "ls"})

    run_start_maintenance(SessionStart(session_id="ses-new", source="startup", is_headless=True,
                                       pinned_build=None, workspace_dir=Path.cwd()))

    con = sqlite3.connect(db)
    rows = dict(con.execute("SELECT contract_id, cut_reason FROM agent_contract_handoffs").fetchall())
    con.close()
    assert agent_contract_handoff_state(dead, db_path=db) == "IN_PROGRESS" and rows[dead] == "reaped"
    assert agent_contract_handoff_state(working, db_path=db) == "DISPATCHED"


def test_session_start_spares_quiet_turns_of_the_compacting_session_and_of_a_live_one(db):
    from modules.session.session_lifecycle import SessionStart, run_start_maintenance
    from modules.session.session_registry import register_session

    own = _dispatched(db, "a3333own", "harness-own", session_id="ses-current")
    other = _dispatched(db, "a4444other", "harness-other", session_id="ses-other-window")
    dead = _dispatched(db, "a5555dead", "harness-gone")
    register_session(session_id="ses-other-window", is_headless=True, pinned_build=None)

    run_start_maintenance(SessionStart(session_id="ses-current", source="compact", is_headless=False,
                                       pinned_build=None, workspace_dir=Path.cwd()))

    assert agent_contract_handoff_state(own, db_path=db) == "DISPATCHED"
    assert agent_contract_handoff_state(other, db_path=db) == "DISPATCHED"
    assert agent_contract_handoff_state(dead, db_path=db) == "IN_PROGRESS"
