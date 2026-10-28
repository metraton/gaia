"""A turn's context corrections: the finalize warning, the guarded auto-apply and the pending list."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sqlite3
import sys
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
for _path in (REPO_ROOT, REPO_ROOT / "hooks"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from adapters.claude_code import ClaudeCodeAdapter  # noqa: E402

IDENTITY = "github.com/metraton/gaia"
READ = "kubectl get deploy web -n bildwiz-demo-stage --context dev-gke"
GOAL = "project=gaia check deploy web in namespace bildwiz-demo-stage on context dev-gke"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "gaia_data"))
    monkeypatch.delenv("GAIA_DB", raising=False)
    monkeypatch.setenv("GAIA_WORKSPACE", "me")
    monkeypatch.setattr("gaia.store.writer.is_harness_session_bound", lambda _session: True)
    from gaia.store.writer import _connect

    con = _connect()
    try:
        con.execute("INSERT OR IGNORE INTO workspaces (name, identity) VALUES ('me', 'me')")
        con.execute("INSERT INTO projects (workspace, name, path, project_identity) "
                    "VALUES ('me', 'gaia', '/repos/gaia', ?)", (IDENTITY,))
        con.commit()
    finally:
        con.close()


@pytest.fixture(scope="module")
def cli():
    spec = importlib.util.spec_from_file_location("contract_cli_drift", REPO_ROOT / "bin" / "cli" / "contract.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _turn(project: str = "gaia", goal: str = GOAL) -> tuple:
    from gaia.store import writer

    agent = "a" + uuid.uuid4().hex[:16]
    contract_id = f"{agent}.{uuid.uuid4().hex[:12]}"
    dispatch = f"toolu-dispatch-{uuid.uuid4().hex[:8]}"
    writer.insert_dispatched_handoff(
        contract_id, agent, "me", agent_name="cloud-troubleshooter", dispatch_tool_use_id=dispatch,
        dispatch_prompt=goal, dispatch_project=project,
    )
    writer.bind_harness_child_session(dispatch, agent)
    return agent, contract_id


def _bash(agent: str, command: str) -> None:
    adapter = ClaudeCodeAdapter()
    adapter.adapt_post_tool_use(adapter.parse_event(json.dumps({
        "hook_event_name": "PostToolUse", "session_id": "ses-parent", "cwd": "/repo",
        "tool_name": "Bash", "tool_input": {"command": command},
        "tool_response": {"stdout": "web   1/1   registry/web:0.0.255", "stderr": "", "interrupted": False,
                          "exit_code": 0},
        "tool_use_id": f"toolu-{uuid.uuid4().hex[:8]}", "agent_id": agent, "agent_type": "cloud-troubleshooter",
    })))


def _correction(payload: dict, command: str = READ) -> dict:
    return {"contract": "application_services", "payload": payload, "drift": {
        "key_path": "web.image_tag", "old": "0.0.175", "new": "0.0.255",
        "command": command, "observed_at": "2026-10-28T18:00:00Z"}}


def _finalize(cli, capsys, agent: str, contract_id: str, *, prose: str = "", update=None) -> dict:
    from gaia.contract.drafts import save_draft

    draft = {
        "agent_status": {"agent_state": "NEEDS_VERIFICATION", "agent_id": agent, "pending_steps": [],
                         "next_action": "verify"},
        "evidence_report": {"patterns_checked": [], "files_checked": [], "commands_run": [], "key_outputs": [],
                            "verbatim_outputs": [], "cross_layer_impacts": [], "open_gaps": [],
                            "verification": {"type": "none"}},
        "conclusion": "checked", "report_prose": prose,
    }
    if update is not None:
        draft["update_contracts"] = update
    save_draft(contract_id, draft)
    capsys.readouterr()
    assert cli.cmd_finalize(argparse.Namespace(draft_id=contract_id, agent_id=None, json=True)) == 0
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def _proposal(contract_id: str) -> dict:
    from gaia.store.proposals import list_proposals

    (item,) = list_proposals(status=None, contract_id=contract_id)
    return item


def _stored_section() -> dict:
    from gaia.paths.resolver import db_path

    con = sqlite3.connect(db_path())
    try:
        row = con.execute("SELECT payload FROM project_context WHERE project_identity = ? AND "
                          "contract_name = 'application_services'", (IDENTITY,)).fetchone()
    finally:
        con.close()
    return json.loads(row[0]) if row else {}


def test_finalize_warns_when_prose_reports_stale_context_and_proposes_nothing(cli, capsys):
    agent, contract_id = _turn()

    out = _finalize(cli, capsys, agent, contract_id, prose="the injected cluster_details is stale")

    assert out["status"] == "finalized"
    assert "update_contracts is empty" in out["warnings"][0]


def test_finalize_does_not_warn_when_the_correction_is_proposed(cli, capsys):
    agent, contract_id = _turn()

    out = _finalize(cli, capsys, agent, contract_id, prose="cluster_details is stale",
                    update=[_correction({"web": {"image_tag": "0.0.255"}})])

    assert "warnings" not in out


def test_a_how_correction_read_by_a_t0_command_on_the_named_target_is_applied_and_signalled(cli, capsys):
    from gaia.signals import recent, render_line

    agent, contract_id = _turn()
    _bash(agent, READ)

    _finalize(cli, capsys, agent, contract_id, update=[_correction({"web": {"image_tag": "0.0.255"}})])

    item = _proposal(contract_id)
    assert (item["status"], item["decided_by"]) == ("accepted", f"auto:{agent}")
    assert READ in item["decision_note"]
    assert _stored_section() == {"web": {"image_tag": "0.0.255"}}
    rows = recent("another-session", since=0)
    assert render_line(rows) == f"~ changed: project context {IDENTITY} ×1 — gaia signals"
    assert rows[0]["open"].endswith(f"--project {IDENTITY} --section application_services")


@pytest.mark.parametrize("payload, ran, command, reason", [
    ({"web": {"namespace": "other"}}, READ, READ, "touches WHAT keys (web.namespace)"),
    ({"web": {"image_tag": "0.0.255"}}, None, READ, "no successful run"),
    ({"web": {"image_tag": "0.0.255"}}, "kubectl scale deploy web --replicas=0 -n bildwiz-demo-stage "
     "--context dev-gke", "kubectl scale deploy web --replicas=0 -n bildwiz-demo-stage --context dev-gke",
     "not a T0 read"),
    ({"web": {"image_tag": "0.0.255"}}, "kubectl get deploy web -n bildwiz-demo --context dev-gke",
     "kubectl get deploy web -n bildwiz-demo --context dev-gke", "targets bildwiz-demo, which the goal"),
], ids=["what-key", "not-in-record", "not-t0", "target-not-named"])
def test_a_correction_failing_one_guard_stays_pending_with_its_reason(cli, capsys, payload, ran, command, reason):
    agent, contract_id = _turn()
    if ran:
        _bash(agent, ran)

    _finalize(cli, capsys, agent, contract_id, update=[_correction(payload, command)])

    item = _proposal(contract_id)
    assert item["status"] == "pending"
    assert reason in item["decision_note"]
    assert _stored_section() == {}


def test_an_unresolved_dispatch_project_keeps_its_token_and_is_listed_with_the_drift(cli, capsys):
    agent, contract_id = _turn(project="bildwiz", goal="project=bildwiz check the demo")

    _finalize(cli, capsys, agent, contract_id, update=[_correction({"web": {"image_tag": "0.0.255"}})])

    assert _proposal(contract_id)["project"] == "bildwiz"
    sys.path.insert(0, str(REPO_ROOT / "bin"))
    from cli.contract import register

    parser = argparse.ArgumentParser()
    register(parser.add_subparsers(dest="subcommand"))
    args = parser.parse_args(["contract", "proposals", "list"])
    assert args.func(args) == 0
    listed = capsys.readouterr().out
    assert "project=bildwiz" in listed
    assert "drift web.image_tag: '0.0.175' -> '0.0.255'" in listed
    assert listed.strip().endswith("1 proposal(s): 1 context")
