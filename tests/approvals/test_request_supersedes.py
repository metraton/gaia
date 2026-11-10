"""A new request retires what it replaces: one command never has two live authorities."""

from __future__ import annotations

import sqlite3

import pytest

from gaia.approvals import core, store
from gaia.approvals.reading import read_ids
from gaia.store import writer

#: T3 whatever the repository holds, so every one is a signed item.
A, B, C = "gh pr merge 41 --squash", "gh pr merge 42 --squash", "gh pr merge 43 --squash"
SESSION = "ses-requester"
AGENT = "gaia-system"
PHRASES = {
    "what": "Merge the pull requests.", "question": "Merge them?",
    "rollback": "Revert the merge commits.", "verification": "gh pr view shows them merged.",
    "shared_state": "Yes: the main branch.",
}


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A scratch store; returns the directory the requests are sealed in."""
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("GAIA_DB", raising=False)
    con = sqlite3.connect(tmp_path / "gaia.db")
    con.executescript(writer._SCHEMA_PATH.read_text())
    con.commit()
    con.close()
    work = tmp_path / "repo"
    work.mkdir()
    return str(work)


def _request(commands: list[str], cwd: str, *, agent: str = AGENT) -> str:
    items = [
        {"command": command, "cwd": cwd, "expect_exit": [],
         "does": "Merges one pull request.", "impact": "main receives the change."}
        for command in commands
    ]
    return core.request_command_set(items, session_id=SESSION, agent_id=agent, **PHRASES)


def _approve(approval_id: str) -> None:
    ref = f"toolu_{approval_id}"
    core.record_presentation(approval_id, native_ref=ref, session_id="ses-orch", agent_id="orchestrator")
    assert core.decide(native_ref=ref, option_key="approve", session_id="ses-orch").status == "activated"


def _reserve(command: str, cwd: str, call: str, *, agent: str = AGENT) -> dict | None:
    return core.match_command(command, cwd=cwd, session_id=SESSION, agent_id=agent, tool_use_id=call)


def _run(command: str, cwd: str, call: str) -> None:
    reserved = _reserve(command, cwd, call)
    assert reserved is not None
    assert core.close_call(
        reserved["approval_id"], command=command, session_id=SESSION, tool_use_id=call,
        exit_code=0, reserved=True, terminal_event="PostToolUse",
    ) == "executed"


def _status(approval_id: str) -> tuple[str, str | None]:
    """The request's decision and, when it armed one, its grant's status."""
    con = store._open_db()
    try:
        decision = con.execute("SELECT status FROM approvals WHERE id=?", (approval_id,)).fetchone()[0]
        grant = con.execute(
            "SELECT status FROM approval_grants WHERE approval_id=?", (approval_id,)
        ).fetchone()
    finally:
        con.close()
    return decision, grant[0] if grant else None


def test_a_new_request_withdraws_the_older_phrased_request_holding_one_of_its_commands(repo):
    older = _request([A], repo)

    newer = _request([A, B], repo)

    assert _status(older) == ("revoked", None)
    assert _status(newer) == ("pending", None)
    assert core.replaced_by(newer) == [older]


def test_a_new_request_revokes_the_stalled_set_and_lets_its_tail_run_under_the_new_signature(repo):
    stalled = _request([A, B, C], repo)
    _approve(stalled)
    _run(A, repo, "call-a")

    newer = _request([C], repo)

    assert _status(stalled) == ("approved", "REVOKED")
    assert core.replaced_by(newer) == [stalled]
    assert read_ids([stalled])[stalled]["reason"] == core.REPLACED_REASON
    assert _reserve(B, repo, "call-b") is None
    assert _reserve(C, repo, "call-c-early") is None
    _approve(newer)
    assert _reserve(C, repo, "call-c")["approval_id"] == newer


def test_a_new_request_revokes_the_single_command_grant_for_the_same_bytes(repo):
    from modules.tools.bash_validator import _build_sealed_payload

    payload = _build_sealed_payload(
        A, verb="merge", category="MUTATIVE", agent_type=AGENT, session_id=SESSION, cwd=repo,
    )
    single = store.insert_requested(payload, agent_id=AGENT, session_id=SESSION)
    assert store.activate_approval_atomically(single, approver_session="ses-user", agent_id=AGENT).success

    newer = _request([A], repo)

    assert _status(single) == ("approved", "REVOKED")
    assert core.replaced_by(newer) == [single]


def test_a_request_leaves_alone_what_it_does_not_replace(repo, tmp_path):
    foreign_pending = _request([A], repo, agent="developer")
    foreign_grant = _request([A, B], repo, agent="gitops-operator")
    _approve(foreign_grant)
    elsewhere = _request([A], str(tmp_path))
    consumed_here = _request([A, C], repo)
    _approve(consumed_here)
    _run(A, repo, "call-a")

    _request([A], repo)

    assert _status(foreign_pending) == ("pending", None)
    assert _status(foreign_grant) == ("approved", "PENDING")
    assert _status(elsewhere) == ("pending", None)
    assert _status(consumed_here) == ("approved", "PENDING")


def test_repeating_a_request_reuses_it_instead_of_withdrawing_it(repo):
    first = _request([A, B], repo)

    assert _request([A, B], repo) == first
    assert _status(first) == ("pending", None)
