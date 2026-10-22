"""An open gap that recurs across clean contract rows becomes one pending thread proposal (plan 94, T8)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
for _p in (_REPO_ROOT, _REPO_ROOT / "bin", _REPO_ROOT / "hooks"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

WORKSPACE = "me"
DISPATCH_PROJECT = "gaia (/repos/gaia)"
OTHER_PROJECT = "other (/repos/other)"
AGENT = "a" + "2" * 16
GAP = "comment lint is not wired into CI"
SMOKE_GAP = "the release workflow lacks a smoke test"


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
        for name, identity in (("gaia", "github.com/metraton/gaia"), ("other", "github.com/x/other")):
            con.execute(
                "INSERT INTO projects (workspace, name, path, project_identity) VALUES (?, ?, ?, ?)",
                (WORKSPACE, name, f"/repos/{name}", identity),
            )
        con.commit()
    finally:
        con.close()
    return db_path()


def _close(contract_id: str, *gaps: str, cut_reason=None, project=DISPATCH_PROJECT) -> int:
    from gaia.store.writer import finalize_agent_contract_handoff, insert_dispatched_handoff

    raw = json.dumps({
        "agent_status": {"agent_state": "COMPLETE", "agent_id": AGENT,
                         "pending_steps": [], "next_action": "done"},
        "evidence_report": {"open_gaps": list(gaps)},
    })
    insert_dispatched_handoff(contract_id, AGENT, WORKSPACE, dispatch_project=project)
    return finalize_agent_contract_handoff(
        contract_id=contract_id, agent_id=AGENT, workspace=WORKSPACE,
        agent_state="COMPLETE", raw_handoff_json=raw, cut_reason=cut_reason,
    )["handoff_id"]


def _threads() -> list:
    from gaia.store.proposals import list_proposals

    return list_proposals(status=None, kind="thread")


def _cli_threads() -> list:
    out = subprocess.run(
        [sys.executable, str(_REPO_ROOT / "bin" / "gaia"), "contract", "proposals", "list",
         "--kind", "thread", "--json"],
        capture_output=True, text=True,
    )
    assert out.returncode == 0, out.stderr
    data = json.loads(out.stdout)
    return data if isinstance(data, list) else data.get("proposals", data.get("items", []))


def test_a_gap_in_two_rows_is_one_pending_thread_listed_by_the_cli(db):
    first = _close(f"{AGENT}.r1", GAP, "an unrelated gap about release notes")
    assert _threads() == []

    second = _close(f"{AGENT}.r2", GAP)
    listed = _cli_threads()
    assert len(listed) == 1
    assert listed[0]["status"] == "pending"
    thread = _threads()[0]
    assert thread["handoff_id"] == second
    assert thread["payload"]["gap"] == GAP
    assert thread["payload"]["handoff_ids"] == [first, second]

    _close(f"{AGENT}.r3", GAP)
    assert len(_cli_threads()) == 1


def test_a_gap_seen_once_yields_no_thread(db):
    _close(f"{AGENT}.o1", SMOKE_GAP)
    assert _cli_threads() == []


def test_the_same_gap_worded_differently_still_recurs(db):
    _close(f"{AGENT}.w1", GAP)
    _close(f"{AGENT}.w2", "The CI does not wire the comment lint")
    _close(f"{AGENT}.w3", "comment-lint not wired in CI yet")
    assert len(_threads()) == 1


def test_distinct_gaps_sharing_most_words_are_not_one_recurring_gap(db):
    _close(f"{AGENT}.d1", GAP)
    _close(f"{AGENT}.d2", "comment lint wired into CI fails on Windows runners")
    _close(f"{AGENT}.d3", "comment lint wired into CI is too slow on large diffs")
    assert _threads() == []


def test_words_that_only_share_a_four_letter_prefix_are_different_gaps(db):
    _close(f"{AGENT}.e1", "proposal store missing index")
    _close(f"{AGENT}.e2", "property store missing index")
    _close(f"{AGENT}.e3", "proper storage misses indexes")
    assert _threads() == []


def test_rows_of_another_project_do_not_count(db):
    _close(f"{AGENT}.f1", SMOKE_GAP, project=OTHER_PROJECT)
    _close(f"{AGENT}.f2", SMOKE_GAP)
    assert _threads() == []


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
    for index in range(2):
        _close(f"{AGENT}.s{index}", SMOKE_GAP)

    threads = _threads()
    assert [thread["payload"]["gap"] for thread in threads] == [SMOKE_GAP]


def test_a_cut_row_neither_counts_nor_proposes(db):
    _close(f"{AGENT}.c1", GAP)
    cut = _close(f"{AGENT}.c2", GAP, cut_reason="reaped")
    assert _threads() == []

    _close(f"{AGENT}.c3", GAP)
    threads = _threads()
    assert len(threads) == 1
    assert cut not in threads[0]["payload"]["handoff_ids"]
