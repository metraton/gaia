"""An open gap that recurs across clean contract rows becomes one pending thread proposal (plan 94, T8)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
for _p in (_REPO_ROOT, _REPO_ROOT / "bin", _REPO_ROOT / "hooks"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

WORKSPACE = "me"
DISPATCH_PROJECT = "gaia (/repos/gaia)"
AGENT = "a" + "2" * 16
GAP = "comment lint is not wired into CI"


@pytest.fixture()
def db(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    for key in ("GAIA_DB", "GAIA_DISPATCH_AGENT", "GAIA_WORKSPACE", "GAIA_SESSION_ID"):
        monkeypatch.delenv(key, raising=False)
    from gaia.paths import db_path
    from gaia.store.writer import _connect

    con = _connect()
    try:
        con.execute("INSERT OR IGNORE INTO workspaces (name, identity) VALUES (?, ?)",
                    (WORKSPACE, WORKSPACE))
        con.execute(
            "INSERT INTO projects (workspace, name, path, project_identity) VALUES (?, ?, ?, ?)",
            (WORKSPACE, "gaia", "/repos/gaia", "github.com/metraton/gaia"),
        )
        con.commit()
    finally:
        con.close()
    return db_path()


def _close(contract_id: str, *gaps: str, cut_reason=None) -> int:
    from gaia.store.writer import finalize_agent_contract_handoff, insert_dispatched_handoff

    raw = json.dumps({
        "agent_status": {"agent_state": "COMPLETE", "agent_id": AGENT,
                         "pending_steps": [], "next_action": "done"},
        "evidence_report": {"open_gaps": list(gaps)},
    })
    insert_dispatched_handoff(contract_id, AGENT, WORKSPACE, dispatch_project=DISPATCH_PROJECT)
    return finalize_agent_contract_handoff(
        contract_id=contract_id, agent_id=AGENT, workspace=WORKSPACE,
        agent_state="COMPLETE", raw_handoff_json=raw, cut_reason=cut_reason,
    )["handoff_id"]


def _threads() -> list:
    from gaia.store.proposals import list_proposals

    return list_proposals(status=None, kind="thread")


def test_a_gap_in_two_rows_is_not_yet_a_thread_and_in_three_it_is(db):
    _close(f"{AGENT}.r1", GAP, "an unrelated gap about release notes")
    _close(f"{AGENT}.r2", GAP)
    assert _threads() == []

    third = _close(f"{AGENT}.r3", GAP)
    threads = _threads()
    assert len(threads) == 1
    assert threads[0]["status"] == "pending"
    assert threads[0]["handoff_id"] == third
    assert threads[0]["payload"]["gap"] == GAP
    assert len(threads[0]["payload"]["handoff_ids"]) == 3

    _close(f"{AGENT}.r4", GAP)
    assert len(_threads()) == 1


def test_the_same_gap_worded_differently_still_recurs(db):
    _close(f"{AGENT}.w1", GAP)
    _close(f"{AGENT}.w2", "The CI does not wire the comment lint")
    _close(f"{AGENT}.w3", "comment-lint not wired in CI yet")
    assert len(_threads()) == 1


def test_a_gap_a_thread_already_holds_is_not_proposed_again(db):
    from gaia.store.writer import _connect

    con = _connect()
    try:
        con.execute(
            "INSERT INTO memory (workspace, name, type, description, body, class, status) "
            "VALUES (?, ?, 'project', ?, ?, 'thread', 'closed')",
            (WORKSPACE, "thread_comment_lint_ci", "Thread: comment lint not wired into CI",
             "Closed when the CI job ran the comment lint."),
        )
        con.commit()
    finally:
        con.close()
    for index in range(3):
        _close(f"{AGENT}.t{index}", GAP)
    assert _threads() == []


def test_a_cut_row_neither_counts_nor_proposes(db):
    _close(f"{AGENT}.c1", GAP)
    _close(f"{AGENT}.c2", GAP)
    cut = _close(f"{AGENT}.c3", GAP, cut_reason="reaped")
    assert _threads() == []

    _close(f"{AGENT}.c4", GAP)
    threads = _threads()
    assert len(threads) == 1
    assert cut not in threads[0]["payload"]["handoff_ids"]
