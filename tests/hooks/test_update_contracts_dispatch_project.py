"""An update_contracts write follows the project the dispatch names, not the session cwd.

Measured defect (plan 87 task 871): a dispatch opened from the workspace root
(``ws``) carried ``project=gaia``, whose project lives in ``me``. The close
wrote ``project_identity.gaia.workflow`` into ``ws``: ``gaia context project
gaia`` in ``me`` kept the old value and ``ws`` gained a stray partial entry.
"""

from __future__ import annotations

import json

import pytest

from gaia.store.writer import (
    dispatch_row_for_identity,
    find_dispatched_row_by_agent_name,
    find_orphaned_dispatched_handoff,
    insert_dispatched_handoff,
    stamp_harness_agent_id,
)
from tests.fixtures.agent_ids import valid_agent_id
from tests.hooks.test_update_contracts_on_close import (
    DISPATCH_WORKSPACE,
    HARNESS_AGENT_ID,
    HOOK_CWD_WORKSPACE,
    PROJECT,
    SESSION_ID,
    WORKFLOW,
    _EVIDENCE_KEYS,
    _cli,
    _close_turn,
    _project_view,
    _REPO_ROOT,
    _seed_project,
    _isolated_substrate,  # noqa: F401  (autouse fixture)
    db_path,  # noqa: F401  (fixture)
)
from adapters.claude_code import ClaudeCodeAdapter

AGENT = "gaia-operator"


def _stored_in(workspace: str) -> str:
    """The workspace's project_identity as JSON; empty when it holds none (exit 1)."""
    shown = _cli([str(_REPO_ROOT / "bin" / "gaia"), "context", "get-contract",
                  "--section", "project_identity", "--workspace", workspace, "--json"])
    assert shown.returncode in (0, 1), shown.stderr
    return shown.stdout if shown.returncode == 0 else ""


@pytest.fixture()
def session_at_workspace_root(db_path, monkeypatch):
    """The dispatch is born in ``ws`` while the project is declared in ``me``."""
    monkeypatch.setenv("GAIA_WORKSPACE", HOOK_CWD_WORKSPACE)
    ClaudeCodeAdapter._maybe_birth_dispatched_row({"prompt": "bootstrap"}, AGENT, "sess-boot")
    _seed_project(db_path, AGENT)


def test_update_contracts_dispatch_project_lands_in_the_projects_workspace(
    db_path, monkeypatch, session_at_workspace_root,
):
    response = _close_turn(
        db_path, monkeypatch, AGENT, "NEEDS_VERIFICATION",
        prompt=f"project={PROJECT} declare the project's workflow",
    )

    assert response.exit_code == 0, response.output
    assert WORKFLOW in json.dumps(_project_view(db_path)), (
        f"the workflow must reach `gaia context project {PROJECT}` of {DISPATCH_WORKSPACE}"
    )
    assert WORKFLOW not in _stored_in(HOOK_CWD_WORKSPACE), (
        f"no stray {PROJECT} entry may be left in the session's workspace"
    )


def _close_resumed_turn(db_path, monkeypatch, prompt):
    """Close a turn, resume it, and close the continuation carrying the proposal.

    The harness stamps the same agent id on a resumption, so SubagentStop
    resolves the continuation row, not the born one.
    """
    identity = ClaudeCodeAdapter._maybe_birth_dispatched_row({"prompt": prompt}, AGENT, SESSION_ID)
    assert identity is not None
    stamp_harness_agent_id(identity["contract_id"], HARNESS_AGENT_ID, db_path=db_path)

    contract_cli = str(_REPO_ROOT / "bin" / "cli" / "contract.py")

    def envelope(**extra) -> dict:
        return dict(
            {
                "agent_status": {
                    "agent_id": identity["agent_id"],
                    "agent_state": "NEEDS_VERIFICATION",
                    "pending_steps": [],
                    "next_action": "done",
                },
                "evidence_report": dict(
                    {k: [] for k in _EVIDENCE_KEYS},
                    verification={"method": "test", "result": "pass", "details": "ok"},
                ),
            },
            **extra,
        )

    first = _cli([contract_cli, "fill", "--draft-id", identity["contract_id"],
                  "--json", json.dumps(envelope())])
    assert first.returncode == 0, first.stderr
    closed = _cli([contract_cli, "finalize", "--draft-id", identity["contract_id"],
                   "--session-id", SESSION_ID, "--json"])
    assert closed.returncode == 0, closed.stderr

    proposal = [{"contract": "project_identity", "payload": {PROJECT: {"workflow": WORKFLOW}}}]
    resumed = _cli([contract_cli, "fill", "--draft-id", identity["contract_id"],
                    "--json", json.dumps(envelope(update_contracts=proposal))])
    assert resumed.returncode == 0, resumed.stderr
    link_id = json.loads(resumed.stdout)["draft_id"]
    assert link_id != identity["contract_id"], "the resumed write must open a continuation"
    finalized = _cli([contract_cli, "finalize", "--draft-id", link_id,
                      "--session-id", SESSION_ID, "--json"])
    assert finalized.returncode == 0, finalized.stderr

    monkeypatch.setenv("GAIA_WORKSPACE", HOOK_CWD_WORKSPACE)
    adapter = ClaudeCodeAdapter()
    event = adapter.parse_event(json.dumps({
        "hook_event_name": "SubagentStop",
        "session_id": SESSION_ID,
        "agent_type": AGENT,
        "agent_id": HARNESS_AGENT_ID,
        "agent_transcript_path": "",
        "last_assistant_message": f"NEEDS_VERIFICATION\ncontract_id: {link_id}",
        "stop_reason": "end_turn",
        "cwd": "/tmp",
    }))
    return adapter.adapt_subagent_stop(event)


def test_update_contracts_from_a_resumed_turn_lands_in_the_projects_workspace(
    db_path, monkeypatch, session_at_workspace_root,
):
    response = _close_resumed_turn(
        db_path, monkeypatch, f"project={PROJECT} declare the project's workflow",
    )

    assert response.exit_code == 0, response.output
    assert WORKFLOW in json.dumps(_project_view(db_path)), (
        f"a continuation inherits the project the dispatch named, so the workflow "
        f"must reach `gaia context project {PROJECT}` of {DISPATCH_WORKSPACE}"
    )
    assert WORKFLOW not in _stored_in(HOOK_CWD_WORKSPACE)


def test_every_dispatch_row_lane_returns_the_project_and_workspace(tmp_path):
    db = tmp_path / "lanes.db"
    agent_id = valid_agent_id("lane-project")
    insert_dispatched_handoff(
        contract_id=f"{agent_id}.lane",
        agent_id=agent_id,
        workspace=DISPATCH_WORKSPACE,
        session_id=SESSION_ID,
        kind="task_execution",
        agent_name=AGENT,
        dispatch_prompt_id="prompt-lane",
        dispatch_project=f"{PROJECT} (/repos/{PROJECT})",
        db_path=db,
    )

    rows = {
        "identity": dispatch_row_for_identity(SESSION_ID, agent_id, db_path=db),
        "orphan": find_orphaned_dispatched_handoff(SESSION_ID, [agent_id], db_path=db),
        "name": find_dispatched_row_by_agent_name(SESSION_ID, AGENT, db_path=db),
    }

    for lane, row in rows.items():
        assert row is not None, lane
        assert row["workspace"] == DISPATCH_WORKSPACE, lane
        assert row["dispatch_project"] == f"{PROJECT} (/repos/{PROJECT})", lane


def test_update_contracts_dispatch_project_absent_keeps_the_born_workspace(
    db_path, monkeypatch, session_at_workspace_root,
):
    response = _close_turn(db_path, monkeypatch, AGENT, "NEEDS_VERIFICATION")

    assert response.exit_code == 0, response.output
    assert WORKFLOW in _stored_in(HOOK_CWD_WORKSPACE)
    assert WORKFLOW not in json.dumps(_project_view(db_path))


def test_update_contracts_dispatch_project_unknown_keeps_the_born_workspace(
    db_path, monkeypatch, session_at_workspace_root,
):
    response = _close_turn(
        db_path, monkeypatch, AGENT, "NEEDS_VERIFICATION",
        prompt="project=not-a-known-project declare the project's workflow",
    )

    assert response.exit_code == 0, response.output
    assert WORKFLOW in _stored_in(HOOK_CWD_WORKSPACE)
    assert WORKFLOW not in json.dumps(_project_view(db_path))
