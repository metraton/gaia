"""A turn's update_contracts is applied, or visibly refused, whatever state it closes in.

Measured defect (handoff row 25793): a gaia-operator turn closed
NEEDS_VERIFICATION with a project_identity proposal for project ``gaia`` of
workspace ``me``. The proposal was merged, but into the workspace the hook
process's cwd resolved to (``ws``), so ``gaia context project gaia`` in ``me``
never showed it. Here the dispatch is born in ``me`` and the close runs where
the cwd-derived workspace is ``ws``, which is exactly that split.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_HOOKS_DIR = str(_REPO_ROOT / "hooks")
for _p in (_HOOKS_DIR, str(_REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from adapters.claude_code import ClaudeCodeAdapter  # noqa: E402
from gaia.store.writer import stamp_harness_agent_id  # noqa: E402
from modules.context import context_writer  # noqa: E402
from tests.fixtures.agent_ids import valid_agent_id  # noqa: E402

DISPATCH_WORKSPACE = "me"
HOOK_CWD_WORKSPACE = "ws"
PROJECT = "gaia"
SESSION_ID = "sess-update-contracts-on-close"
HARNESS_AGENT_ID = valid_agent_id("update-contracts-on-close-harness")
WORKFLOW = "Each task in its own worktree; integrate through the PR."

_EVIDENCE_KEYS = (
    "patterns_checked", "files_checked", "commands_run", "key_outputs",
    "verbatim_outputs", "cross_layer_impacts", "open_gaps",
)


@pytest.fixture(autouse=True)
def _isolated_substrate(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "gaia-data"))
    monkeypatch.delenv("GAIA_DB", raising=False)
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    monkeypatch.delenv("GAIA_DISPATCH_WORKSPACE", raising=False)
    monkeypatch.delenv("GAIA_CONTRACT_FULL_VERDICT_GATE", raising=False)
    monkeypatch.setenv("GAIA_WORKSPACE", DISPATCH_WORKSPACE)
    monkeypatch.setattr(context_writer, "_permissions_cache", {})
    yield


@pytest.fixture()
def db_path(tmp_path) -> Path:
    return tmp_path / "gaia-data" / "gaia.db"


def _cli(argv):
    env = dict(os.environ, PYTHONPATH=str(_REPO_ROOT))
    return subprocess.run(
        [sys.executable, *argv], capture_output=True, text=True, env=env,
    )


def _seed_project(db_path: Path, writer_agent: str) -> None:
    con = sqlite3.connect(str(db_path))
    try:
        con.execute(
            "INSERT OR IGNORE INTO workspaces (name, identity, created_at) "
            "VALUES (?, ?, datetime('now'))",
            (DISPATCH_WORKSPACE, DISPATCH_WORKSPACE),
        )
        con.execute(
            "INSERT INTO projects (workspace, name, path) VALUES (?, ?, ?)",
            (DISPATCH_WORKSPACE, PROJECT, f"/repos/{PROJECT}"),
        )
        con.execute(
            "INSERT INTO agent_contract_permissions "
            "(agent_name, contract_name, can_read, can_write) VALUES (?, ?, 1, 1)",
            (writer_agent, "project_identity"),
        )
        con.commit()
    finally:
        con.close()
    seeded = context_writer.apply_update(
        {
            "contract": "project_identity",
            "payload": {PROJECT: {"name": PROJECT, "local_path": f"/repos/{PROJECT}"}},
        },
        "test-seed",
        workspace=DISPATCH_WORKSPACE,
        db_path=db_path,
    )
    assert seeded["success"], seeded


def _close_turn(db_path: Path, monkeypatch, agent: str, state: str):
    """Birth, fill and finalize a turn carrying the 25793 proposal, then run
    SubagentStop from a cwd that resolves to another workspace."""
    identity = ClaudeCodeAdapter._maybe_birth_dispatched_row(
        {"prompt": "declare the project's workflow"}, agent, SESSION_ID,
    )
    assert identity is not None
    stamp_harness_agent_id(identity["contract_id"], HARNESS_AGENT_ID, db_path=db_path)

    envelope = {
        "agent_status": {
            "agent_id": identity["agent_id"],
            "agent_state": state,
            "pending_steps": [],
            "next_action": "done",
        },
        "evidence_report": dict(
            {k: [] for k in _EVIDENCE_KEYS},
            verification={"method": "test", "result": "pass", "details": "ok"},
        ),
        "update_contracts": [
            {"contract": "project_identity", "payload": {PROJECT: {"workflow": WORKFLOW}}},
        ],
    }
    contract_cli = str(_REPO_ROOT / "bin" / "cli" / "contract.py")
    filled = _cli([contract_cli, "fill", "--draft-id", identity["contract_id"],
                   "--json", json.dumps(envelope)])
    assert filled.returncode == 0, filled.stderr
    finalized = _cli([contract_cli, "finalize", "--draft-id", identity["contract_id"],
                      "--session-id", SESSION_ID, "--json"])
    assert finalized.returncode == 0, finalized.stderr

    monkeypatch.setenv("GAIA_WORKSPACE", HOOK_CWD_WORKSPACE)
    adapter = ClaudeCodeAdapter()
    event = adapter.parse_event(json.dumps({
        "hook_event_name": "SubagentStop",
        "session_id": SESSION_ID,
        "agent_type": agent,
        "agent_id": HARNESS_AGENT_ID,
        "agent_transcript_path": "",
        "last_assistant_message": f"{state}\ncontract_id: {identity['contract_id']}",
        "stop_reason": "end_turn",
        "cwd": "/tmp",
    }))
    return adapter.adapt_subagent_stop(event)


def _project_view(db_path: Path) -> dict:
    shown = _cli([str(_REPO_ROOT / "bin" / "gaia"), "context", "project", PROJECT,
                  "--workspace", DISPATCH_WORKSPACE, "--json"])
    assert shown.returncode == 0, shown.stderr
    return json.loads(shown.stdout)


@pytest.mark.parametrize("state", ["NEEDS_VERIFICATION", "COMPLETE"])
def test_update_contracts_lands_in_the_dispatch_workspace(db_path, monkeypatch, state):
    agent = "gaia-operator"
    ClaudeCodeAdapter._maybe_birth_dispatched_row({"prompt": "bootstrap"}, agent, "sess-boot")
    _seed_project(db_path, agent)

    response = _close_turn(db_path, monkeypatch, agent, state)

    assert response.exit_code == 0, response.output
    assert WORKFLOW in json.dumps(_project_view(db_path)), (
        f"a {state} close must leave the proposed workflow visible in "
        f"`gaia context project {PROJECT}` of workspace {DISPATCH_WORKSPACE}"
    )


@pytest.mark.parametrize("state", ["NEEDS_VERIFICATION", "COMPLETE"])
def test_a_refused_update_contracts_is_named_on_the_close(db_path, monkeypatch, state):
    ClaudeCodeAdapter._maybe_birth_dispatched_row({"prompt": "bootstrap"}, "gaia-operator", "sess-boot")
    _seed_project(db_path, "gaia-operator")

    response = _close_turn(db_path, monkeypatch, "gaia-planner", state)

    assert WORKFLOW not in json.dumps(_project_view(db_path))
    refused = response.output.get("update_contracts_refused") or []
    assert any("project_identity" in message for message in refused), response.output
    assert "project_identity" in (response.output.get("systemMessage") or ""), (
        "an unapplied proposal must reach the orchestrator, not only the hook log"
    )
