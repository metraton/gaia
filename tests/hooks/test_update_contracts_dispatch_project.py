"""An update_contracts write follows the project the dispatch names, not the session cwd.

Measured defect (plan 87 task 871): a dispatch opened from the workspace root
(``ws``) carried ``project=gaia``, whose project lives in ``me``. The close
wrote ``project_identity.gaia.workflow`` into ``ws``: ``gaia context project
gaia`` in ``me`` kept the old value and ``ws`` gained a stray partial entry.
"""

from __future__ import annotations

import json
import sqlite3

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
    _apply,
    _close_turn,
    _notice_contract_id,
    _project_view,
    _REPO_ROOT,
    _seed_project,
    _isolated_substrate,  # noqa: F401  (autouse fixture)
    db_path,  # noqa: F401  (fixture)
)
from adapters.claude_code import ClaudeCodeAdapter
from modules.context import context_writer

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
    assert WORKFLOW not in json.dumps(_project_view(db_path)), "the close only proposes"
    applied = _apply(_notice_contract_id())
    assert applied.returncode == 0, applied.stderr
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
                "user_facing_summary": "Work recorded for the test.",
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
    applied = _apply(_notice_contract_id())
    assert applied.returncode == 0, applied.stderr
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


def _row_envelope() -> dict:
    shown = _cli([str(_REPO_ROOT / "bin" / "gaia"), "contract", "view",
                  "--harness-id", HARNESS_AGENT_ID])
    assert shown.returncode == 0, shown.stderr
    return json.loads(shown.stdout)["envelope"]


@pytest.mark.parametrize("prompt", [
    "declare the project's workflow",
    "project=not-a-known-project declare the project's workflow",
])
def test_a_project_identity_key_lands_in_the_workspace_owning_that_project(
    db_path, monkeypatch, session_at_workspace_root, prompt,
):
    """The 2026-10-09 stray: no dispatch project to follow, so the write took ``ws``."""
    response = _close_turn(db_path, monkeypatch, AGENT, "NEEDS_VERIFICATION", prompt=prompt)

    assert response.exit_code == 0, response.output
    applied = _apply(_notice_contract_id())
    assert applied.returncode == 0, applied.stderr
    assert WORKFLOW in json.dumps(_project_view(db_path)), (
        f"`gaia context project {PROJECT}` of {DISPATCH_WORKSPACE} must read the workflow back"
    )
    assert WORKFLOW not in _stored_in(HOOK_CWD_WORKSPACE), (
        f"no stray {PROJECT} entry may be left in the session's workspace"
    )


BK_WORKSPACE = "century-inc"
BK_NAME = "branchkinect-platform"
BK_KEY = "branchkinect_platform"
BK_PATH = f"/repos/{BK_NAME}"


@pytest.fixture()
def branchkinect(db_path):
    """``projects.name`` is hyphenated; the owner's section keys are underscore slugs."""
    ClaudeCodeAdapter._maybe_birth_dispatched_row({"prompt": "bootstrap"}, AGENT, "sess-boot")
    con = sqlite3.connect(str(db_path))
    try:
        for name in (BK_WORKSPACE, HOOK_CWD_WORKSPACE):
            con.execute(
                "INSERT OR IGNORE INTO workspaces (name, identity, created_at) "
                "VALUES (?, ?, datetime('now'))", (name, name),
            )
        con.execute(
            "INSERT INTO projects (workspace, name, path) VALUES (?, ?, ?)",
            (BK_WORKSPACE, BK_NAME, BK_PATH),
        )
        con.commit()
    finally:
        con.close()
    seeded = context_writer.apply_update(
        {"contract": "project_identity",
         "payload": {BK_KEY: {"name": BK_NAME, "local_path": BK_PATH}}},
        "test-seed", workspace=BK_WORKSPACE, db_path=db_path,
    )
    assert seeded["success"], seeded


def _stored_keys(db_path, workspace: str) -> set:
    return context_writer._section_keys("project_identity", workspace, db_path)


def test_an_underscore_key_lands_in_the_workspace_owning_the_hyphenated_project(db_path, branchkinect):
    clause = [{"contract": "project_identity", "payload": {BK_KEY: {"workflow": WORKFLOW}}}]

    outcome = context_writer.apply_proposal(clause, HOOK_CWD_WORKSPACE, db_path)

    assert outcome["errors"] == []
    assert outcome["applied"] == [
        {"contract": "project_identity", "workspace": BK_WORKSPACE, "project": BK_PATH},
    ]
    assert WORKFLOW in json.dumps(
        json.loads(_stored_in(BK_WORKSPACE))["payload"][BK_KEY]
    )
    assert _stored_keys(db_path, HOOK_CWD_WORKSPACE) == set(), "no stray entry in the session's workspace"


def test_a_hyphenated_spelling_updates_the_existing_underscore_entry(db_path, branchkinect):
    clause = [{"contract": "project_identity", "payload": {BK_NAME: {"workflow": WORKFLOW}}}]

    outcome = context_writer.apply_proposal(clause, HOOK_CWD_WORKSPACE, db_path)

    assert outcome["errors"] == []
    assert _stored_keys(db_path, BK_WORKSPACE) == {BK_KEY}, "no second entry beside the slug"


def test_a_key_no_workspace_owns_is_refused_and_written_nowhere(db_path, branchkinect):
    clause = [{"contract": "project_identity", "payload": {
        "no_such_project": {"workflow": WORKFLOW}, BK_KEY: {"workflow": WORKFLOW},
    }}]

    outcome = context_writer.apply_proposal(clause, HOOK_CWD_WORKSPACE, db_path)

    assert outcome["applied"] == [
        {"contract": "project_identity", "workspace": BK_WORKSPACE, "project": BK_PATH},
    ]
    assert len(outcome["errors"]) == 1
    assert "no workspace owns no_such_project" in outcome["errors"][0]
    assert "Repair:" in outcome["errors"][0]
    assert _stored_keys(db_path, HOOK_CWD_WORKSPACE) == set()
    assert "no_such_project" not in _stored_keys(db_path, BK_WORKSPACE)


def test_the_close_refuses_an_unowned_key_visibly_and_records_the_owned_one(
    db_path, monkeypatch, branchkinect,
):
    result = context_writer.process_update_contracts(
        {"update_contracts": [{"contract": "project_identity", "payload": {
            BK_KEY: {"workflow": WORKFLOW}, "brand_new": {"name": "brand_new"},
        }}]},
        {"agent": AGENT, "db_path": db_path, "workspace": HOOK_CWD_WORKSPACE},
    )

    assert result["proposed"] == [{
        "contract": "project_identity", "workspace": BK_WORKSPACE, "project": BK_PATH,
        "keys": [BK_KEY],
    }]
    assert any("no workspace owns brand_new" in e for e in result["errors"])
    assert _stored_keys(db_path, HOOK_CWD_WORKSPACE) == set()
    assert WORKFLOW not in json.dumps(_stored_in(BK_WORKSPACE))


def test_the_underscore_slug_resolves_in_gaia_context_project(db_path, branchkinect):
    shown = _cli([str(_REPO_ROOT / "bin" / "gaia"), "context", "project", BK_KEY, "--json"])

    assert shown.returncode == 0, shown.stderr
    assert json.loads(shown.stdout)["project"]["workspace"] == BK_WORKSPACE


def test_a_section_keyed_object_is_recorded_not_dropped(db_path, branchkinect):
    """The 3 cluster_details proposals of the last 14 days arrived as ``{section: payload}``."""
    task_info = {"agent": AGENT, "db_path": db_path, "workspace": HOOK_CWD_WORKSPACE,
                 "project": BK_NAME}

    result = context_writer.process_update_contracts(
        {"update_contracts": {"cluster_details": {"gke": {"version": "1.30"}}}}, task_info,
    )

    assert result["errors"] == []
    assert result["proposed"] == [{
        "contract": "cluster_details", "workspace": BK_WORKSPACE, "project": BK_PATH,
        "keys": ["gke"],
    }]
    assert "{section: payload}" in result["normalized"]
    assert context_writer._section_keys("cluster_details", HOOK_CWD_WORKSPACE, db_path) == set()


@pytest.mark.parametrize("clause", [
    {"cluster_details": "not an object"},
    {"cluster_details": {"a": 1}, "contract": "x"},
    "cluster_details",
])
def test_an_unreadable_clause_is_refused_with_a_repair(db_path, branchkinect, clause):
    result = context_writer.process_update_contracts(
        {"update_contracts": clause},
        {"agent": AGENT, "db_path": db_path, "workspace": HOOK_CWD_WORKSPACE},
    )

    assert result["proposed"] == []
    assert len(result["errors"]) == 1 and "Repair:" in result["errors"][0]
