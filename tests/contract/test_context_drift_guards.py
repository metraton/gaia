"""The guards of the context-correction auto-apply: anything the close cannot prove stays pending with its reason."""

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
OTHER = "github.com/metraton/other"
NS = "bildwiz-demo-stage"
READ = f"kubectl get deploy web -n {NS} --context dev-gke"
GOAL = f"project=gaia check deploy web in namespace {NS} on context dev-gke"
SEED = {"web": {"namespace": NS, "cluster": "dev-gke", "image_tag": "0.0.175"}}
OUTPUT = "NAME  READY  IMAGE\nweb   1/1    registry/web:0.0.255"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "gaia_data"))
    monkeypatch.delenv("GAIA_DB", raising=False)
    monkeypatch.setenv("GAIA_WORKSPACE", "me")
    monkeypatch.setattr("gaia.store.writer.is_harness_session_bound", lambda _session: True)
    from gaia.paths.resolver import db_path
    from gaia.store.writer import _connect

    assert str(db_path()).startswith(str(tmp_path)), db_path()
    con = _connect()
    try:
        con.execute("INSERT OR IGNORE INTO workspaces (name, identity) VALUES ('me', 'me')")
        con.execute("INSERT INTO projects (workspace, name, path, project_identity) "
                    "VALUES ('me', 'gaia', '/repos/gaia', ?)", (IDENTITY,))
        con.execute("INSERT INTO projects (workspace, name, path, project_identity) "
                    "VALUES ('me', 'other', '/repos/other', ?)", (OTHER,))
        con.commit()
    finally:
        con.close()


@pytest.fixture(scope="module")
def cli():
    spec = importlib.util.spec_from_file_location("contract_cli_guards", REPO_ROOT / "bin" / "cli" / "contract.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _turn(goal: str = GOAL, bind: bool = True) -> tuple:
    from gaia.store import writer

    agent = "a" + uuid.uuid4().hex[:16]
    contract_id = f"{agent}.{uuid.uuid4().hex[:12]}"
    dispatch = f"toolu-dispatch-{uuid.uuid4().hex[:8]}"
    writer.insert_dispatched_handoff(
        contract_id, agent, "me", agent_name="cloud-troubleshooter", dispatch_tool_use_id=dispatch,
        dispatch_prompt=goal, dispatch_project="gaia",
    )
    if bind:
        writer.bind_harness_child_session(dispatch, agent)
    return agent, contract_id


def _bash(agent: str, command: str, stdout: str = OUTPUT) -> None:
    adapter = ClaudeCodeAdapter()
    adapter.adapt_post_tool_use(adapter.parse_event(json.dumps({
        "hook_event_name": "PostToolUse", "session_id": "ses-parent", "cwd": "/repo",
        "tool_name": "Bash", "tool_input": {"command": command},
        "tool_response": {"stdout": stdout, "stderr": "", "interrupted": False, "exit_code": 0},
        "tool_use_id": f"toolu-{uuid.uuid4().hex[:8]}", "agent_id": agent, "agent_type": "cloud-troubleshooter",
    })))


def _correction(payload, command: str = READ, contract: str = "application_services", project=None) -> dict:
    entry = {"contract": contract, "payload": payload, "drift": {
        "key_path": "web.image_tag", "old": "0.0.175", "new": "0.0.255",
        "command": command, "observed_at": "2026-10-28T18:00:00Z"}}
    if project:
        entry["project"] = project
    return entry


def _finalize(cli, capsys, contract_id: str, *, update=None, extra=None, commands=None, prose: str = "",
              gaps=None, state: str = "NEEDS_VERIFICATION", as_json: bool = True):
    from gaia.contract.drafts import save_draft

    draft = {
        "agent_status": {"agent_state": state, "agent_id": contract_id.split(".")[0],
                         "pending_steps": [], "next_action": "verify"},
        "evidence_report": {"patterns_checked": [], "files_checked": [], "commands_run": commands or [],
                            "key_outputs": [], "verbatim_outputs": [], "cross_layer_impacts": [],
                            "open_gaps": gaps or [], "verification": {"type": "none"}},
        "conclusion": "checked", "report_prose": prose,
    }
    if update is not None:
        draft["update_contracts"] = update
    draft.update(extra or {})
    save_draft(contract_id, draft)
    capsys.readouterr()
    code = cli.cmd_finalize(argparse.Namespace(draft_id=contract_id, agent_id=None, json=as_json))
    captured = capsys.readouterr()
    return code if as_json else (code, captured)


def _proposal(contract_id: str) -> dict:
    from gaia.store.proposals import list_proposals

    (item,) = list_proposals(status=None, contract_id=contract_id)
    return item


def _seed(identity: str = IDENTITY, contract: str = "application_services", payload=None) -> None:
    from gaia.paths.resolver import db_path
    from gaia.store.project_context import write_section

    con = sqlite3.connect(db_path())
    try:
        write_section(con, identity, contract, json.dumps(payload or SEED), "2026-10-01T00:00:00Z", "seed", None)
        con.commit()
    finally:
        con.close()


def _stored(identity: str = IDENTITY, contract: str = "application_services"):
    from gaia.paths.resolver import db_path

    con = sqlite3.connect(db_path())
    try:
        row = con.execute("SELECT payload FROM project_context WHERE project_identity = ? AND "
                          "contract_name = ?", (identity, contract)).fetchone()
    finally:
        con.close()
    return json.loads(row[0]) if row else None


def _assert_pending(contract_id: str, reason: str) -> None:
    item = _proposal(contract_id)
    assert item["status"] == "pending", item["decision_note"]
    assert item["decision_note"].startswith("not auto-applied: ")
    assert reason in item["decision_note"], item["decision_note"]


TARGET_CASES = {
    "ambient-context-explicit-namespace": (GOAL, f"kubectl get deploy web -n {NS}", "no explicit context"),
    "ambient-context-no-namespace": (GOAL, "kubectl get deploy web", "no explicit context"),
    "context-without-namespace": (GOAL, "kubectl get deploy web --context dev-gke", "no explicit namespace"),
    "gcloud-default-project": (GOAL, "gcloud run services describe web --region us-east1", "no explicit project"),
    "attached-namespace-flag": (GOAL, "kubectl get deploy web -nbildwiz-demo --context dev-gke",
                                "targets bildwiz-demo, which the goal"),
    "namespace-equals-short": (GOAL, "kubectl get deploy web -n=bildwiz-demo --context dev-gke",
                               "targets bildwiz-demo, which the goal"),
    "two-namespaces": (GOAL, f"kubectl get deploy web -n {NS} -n other --context dev-gke", "names 2 namespaces"),
    "context-in-other-kubeconfig-env": (GOAL, f"KUBECONFIG=/x/prod kubectl get deploy web -n {NS} --context dev-gke",
                                        "written before the program"),
    "context-in-other-kubeconfig-flag": (GOAL, f"kubectl --kubeconfig /x/prod get deploy web -n {NS} --context dev-gke",
                                         "--kubeconfig"),
    "partial-goal-names-longer": (f"project=gaia check web in {NS} on context dev-gke-2", READ,
                                  "targets dev-gke, which the goal"),
    "case-differs": (f"project=gaia check web in {NS.upper()} on context DEV-GKE", READ, "which the goal did not name"),
    "all-namespaces": (GOAL, "kubectl get deploy -A --context dev-gke", "-A"),
    "unknown-option-before-operation": (GOAL, f"kubectl --frobnicate x get deploy web -n {NS} --context dev-gke",
                                        "unknown option"),
    "piped-output": (GOAL, f"{READ} | grep web", "more than one command"),
}


@pytest.mark.parametrize("goal, command, reason", list(TARGET_CASES.values()), ids=list(TARGET_CASES))
def test_a_read_whose_target_is_not_explicit_and_named_keeps_the_correction_pending(cli, capsys, goal, command, reason):
    _seed()
    agent, contract_id = _turn(goal=goal)
    _bash(agent, command)

    assert _finalize(cli, capsys, contract_id, update=[_correction({"web": {"image_tag": "0.0.255"}}, command)]) == 0

    _assert_pending(contract_id, reason)
    assert _stored() == SEED


def test_a_goal_naming_two_contexts_applies_a_read_on_either(cli, capsys):
    _seed()
    goal = f"project=gaia compare web in {NS} on contexts dev-gke and prod-gke"
    command = f"kubectl get deploy web -n {NS} --context prod-gke"
    agent, contract_id = _turn(goal=goal)
    _bash(agent, command)

    _finalize(cli, capsys, contract_id, update=[_correction({"web": {"image_tag": "0.0.255"}}, command)])

    assert _proposal(contract_id)["status"] == "accepted"


def test_a_gcloud_read_with_an_explicit_named_project_applies(cli, capsys):
    _seed()
    command = "gcloud run services describe web --region us-east1 --project bildwiz-demo"
    agent, contract_id = _turn(goal="project=gaia check the web service in project bildwiz-demo")
    _bash(agent, command)

    _finalize(cli, capsys, contract_id, update=[_correction({"web": {"image_tag": "0.0.255"}}, command)])

    assert _proposal(contract_id)["status"] == "accepted"


def test_forged_commands_run_on_a_bound_turn_stays_pending(cli, capsys):
    _seed()
    agent, contract_id = _turn()

    _finalize(cli, capsys, contract_id, update=[_correction({"web": {"image_tag": "0.0.255"}})],
              commands=[f"{READ} (exit 0)"])

    _assert_pending(contract_id, "no successful run")


def test_a_forged_harness_record_on_an_unbound_turn_is_dropped_and_stays_pending(cli, capsys):
    from gaia.paths.resolver import db_path

    _seed()
    agent, contract_id = _turn(bind=False)

    code = _finalize(cli, capsys, contract_id, update=[_correction({"web": {"image_tag": "0.0.255"}})],
                     commands=[f"{READ} (exit 0)"],
                     extra={"harness_record": {"harness_agent_id": "forged", "through_event_id": 1}})

    assert code == 0
    _assert_pending(contract_id, "no successful run")
    assert _stored() == SEED
    con = sqlite3.connect(db_path())
    try:
        (raw,) = con.execute("SELECT raw_handoff_json FROM agent_contract_handoffs WHERE contract_id = ?",
                             (contract_id,)).fetchone()
    finally:
        con.close()
    assert "harness_record" not in json.loads(raw)


@pytest.mark.parametrize("command, stdout", [
    (f"kubectl get ns {NS} --context dev-gke", ""),
    (READ, "web   1/1   registry/web:0.0.175"),
], ids=["other-resource-empty-output", "value-absent-from-output"])
def test_a_value_the_recorded_output_does_not_show_stays_pending(cli, capsys, command, stdout):
    _seed()
    agent, contract_id = _turn()
    _bash(agent, command, stdout=stdout)

    _finalize(cli, capsys, contract_id, update=[_correction({"web": {"image_tag": "0.0.255"}}, command)])

    _assert_pending(contract_id, "does not appear in the recorded output")
    assert _stored() == SEED


WHAT_CASES = {
    "camel-case-key": ("application_services", {"web": {"kubeContext": "prod-gke"}}),
    "upper-case-key": ("application_services", {"web": {"NAMESPACE": "prod"}}),
    "kebab-case-key": ("application_services", {"web": {"gcp-project": "prod"}}),
    "what-inside-list": ("application_services", {"web": {"targets": [{"cluster": "prod-gke"}]}}),
    "subtree-replaced-by-null": ("application_services", {"web": None}),
    "subtree-replaced-by-scalar": ("application_services", {"web": "gone"}),
    "subtree-replaced-by-list": ("application_services", {"web": ["gone"]}),
    "cluster-details-name": ("cluster_details", {"gke": {"name": "prod-gke"}}),
    "what-section-case-variant": ("Environment", {"stage": {"image": "x"}}),
}


@pytest.mark.parametrize("contract, payload", list(WHAT_CASES.values()), ids=list(WHAT_CASES))
def test_what_smuggled_under_a_how_path_stays_pending(cli, capsys, contract, payload):
    _seed()
    agent, contract_id = _turn()
    _bash(agent, READ, stdout=json.dumps(payload))

    _finalize(cli, capsys, contract_id, update=[_correction(payload, contract=contract)])

    _assert_pending(contract_id, "WHAT")
    assert _stored() == SEED


def test_a_section_spelled_unlike_the_stored_one_stays_pending(cli, capsys):
    _seed()
    agent, contract_id = _turn()
    _bash(agent, READ)

    _finalize(cli, capsys, contract_id, update=[_correction({"web": {"image_tag": "0.0.255"}},
                                                            contract="Application-Services")])

    _assert_pending(contract_id, "stored as application_services")
    assert _stored() == SEED


def test_a_correction_naming_another_project_is_not_auto_applied_there(cli, capsys):
    _seed()
    _seed(identity=OTHER)
    agent, contract_id = _turn()
    _bash(agent, READ)

    _finalize(cli, capsys, contract_id, update=[_correction({"web": {"image_tag": "0.0.255"}}, project="other")])

    assert _stored(OTHER) == SEED
    _assert_pending(contract_id, "not the dispatch's project")


def test_every_failed_guard_is_listed(cli, capsys):
    _seed()
    command = f"kubectl get deploy web -n {NS}"
    agent, contract_id = _turn()
    _bash(agent, command, stdout="")

    _finalize(cli, capsys, contract_id, update=[_correction({"web": {"namespace": "other-ns"}}, command)])

    note = _proposal(contract_id)["decision_note"]
    for reason in ("WHAT keys (web.namespace)", "does not appear in the recorded output", "no explicit context"):
        assert reason in note, note


def test_auto_apply_records_command_and_observed_at_and_author(cli, capsys):
    _seed()
    agent, contract_id = _turn()
    _bash(agent, READ)

    _finalize(cli, capsys, contract_id, update=[_correction({"web": {"image_tag": "0.0.255"}})])

    item = _proposal(contract_id)
    assert item["decided_by"] == f"auto:{agent}"
    assert READ in item["decision_note"] and "2026-10-28T18:00:00Z" in item["decision_note"]
    assert _stored()["web"] == {"namespace": NS, "cluster": "dev-gke", "image_tag": "0.0.255"}


def test_failed_guard_is_shown_in_the_pending_list(cli, capsys):
    sys.path.insert(0, str(REPO_ROOT / "bin"))
    from cli.contract import register

    _seed()
    agent, contract_id = _turn()
    _finalize(cli, capsys, contract_id, update=[_correction({"web": {"image_tag": "0.0.255"}})])
    parser = argparse.ArgumentParser()
    register(parser.add_subparsers(dest="subcommand"))
    args = parser.parse_args(["contract", "proposals", "list"])

    assert args.func(args) == 0
    assert "[not auto-applied: the turn record holds no successful run of drift.command]" in capsys.readouterr().out


GCP_GOAL = GOAL + " in gcp project dev-proj"
ROUND_TWO_PENDING = {
    "jsonpath-literal-prints-the-value": ({"web": {"image_tag": "0.0.999"}}, f"{READ} -o jsonpath=0.0.999",
                                          "0.0.999", "prints with"),
    "go-template-literal": ({"web": {"image_tag": "0.0.999"}}, f"{READ} -o go-template=0.0.999", "0.0.999",
                            "prints with"),
    "custom-columns-header": ({"web": {"image_tag": "0.0.999"}}, f"{READ} -o custom-columns=0.0.999:.metadata.name",
                              "0.0.999\nweb", "prints with"),
    "jsonpath-split-value": ({"web": {"image_tag": "0.0.999"}}, READ + ' -o jsonpath={"0.0.9"}{"99"}', "0.0.999",
                             "prints with"),
    "gcloud-custom-format": ({"web": {"image_tag": "0.0.255"}},
                             "gcloud run services describe web --project dev-proj --format='value(0.0.255)'", OUTPUT,
                             "prints with"),
    "substring-1.2-in-1.23": ({"web": {"image_tag": "1.2"}}, READ, "web   1/1    registry/web:1.23", "1.2"),
    "token-inside-longer-version": ({"web": {"image_tag": "0.0.25"}}, READ, OUTPUT, "does not appear"),
    "digit-0-in-any-version": ({"web": {"port": 0}}, READ, OUTPUT, "too short or numeric"),
    "redaction-placeholder": ({"web": {"db_password": "[REDACTED]"}}, READ, "DB_PASSWORD=hunter2secretvalue",
                              "redacted or truncated"),
    "unicode-lookalike-namespace-key": ({"web": {"namеspace": "prod"}}, READ, "prod", "WHAT"),
    "fullwidth-namespace-key": ({"web": {"ｎamespace": "prod-ns"}}, READ, "prod-ns", "WHAT"),
    "abbrev-kubeCtx": ({"web": {"kubeCtx": "prod-gke"}}, READ, "prod-gke", "WHAT"),
    "abbrev-proj-id": ({"web": {"proj_id": "prod-proj"}}, READ, "prod-proj", "WHAT"),
    "abbrev-cl": ({"web": {"cl": "prod-gke"}}, READ, "prod-gke", "WHAT"),
    "two-commands-semicolon": ({"web": {"image_tag": "0.0.255"}}, f"{READ}; kubectl get deploy web", OUTPUT,
                               "not auto-applied"),
    "two-commands-newline": ({"web": {"image_tag": "0.0.255"}}, f"{READ}\nkubectl get deploy web", OUTPUT,
                             "not auto-applied"),
    "two-commands-and": ({"web": {"image_tag": "0.0.255"}}, f"kubectl get deploy web && {READ}", OUTPUT,
                         "not auto-applied"),
    "substituted-context": ({"web": {"image_tag": "0.0.255"}},
                            f"kubectl get deploy web -n {NS} --context $(echo dev-gke)", OUTPUT, "not auto-applied"),
    "env-wrapper-kubeconfig": ({"web": {"image_tag": "0.0.255"}}, f"env KUBECONFIG=/x/prod {READ}", OUTPUT,
                               "written before the program"),
    "gcloud-flags-file": ({"web": {"image_tag": "0.0.255"}},
                          "gcloud run services describe web --project dev-proj --region r --flags-file=/x/f.yaml",
                          OUTPUT, "--flags-file"),
    "kubectl-filename": ({"web": {"image_tag": "0.0.255"}}, f"{READ} -f /x/prod.yaml", OUTPUT, "-f"),
}


def _round_two(cli, capsys, payload, command, stdout) -> dict:
    _seed()
    agent, contract_id = _turn(goal=GCP_GOAL)
    _bash(agent, command, stdout=stdout)
    assert _finalize(cli, capsys, contract_id, update=[_correction(payload, command)]) == 0
    return _proposal(contract_id)


@pytest.mark.parametrize("payload, command, stdout, reason", list(ROUND_TWO_PENDING.values()),
                         ids=list(ROUND_TWO_PENDING))
def test_a_round_two_probe_stays_pending(cli, capsys, payload, command, stdout, reason):
    item = _round_two(cli, capsys, payload, command, stdout)

    assert item["status"] == "pending", item["decision_note"]
    assert reason in item["decision_note"], item["decision_note"]
    assert _stored() == SEED


@pytest.mark.xfail(strict=True, reason="accepted residual: a WHAT value under a HOW key name needs key-path marking")
def test_a_cluster_name_stored_under_a_how_key_stays_pending(cli, capsys):
    item = _round_two(cli, capsys, {"web": {"runs_on": "prod-gke"}}, READ, "prod-gke")

    assert item["status"] == "pending", item["decision_note"]


ROUND_TWO_APPLIES = {
    "gcloud-project-equals": "gcloud run services describe web --project=dev-proj --region us-east1",
    "gcloud-project-space": "gcloud run services describe web --project dev-proj --region us-east1",
    "gcloud-project-before-operation": "gcloud --project=dev-proj run services describe web --region us-east1",
    "gcloud-format-json": "gcloud run services describe web --project dev-proj --format json",
    "kubectl-context-equals": f"kubectl get deploy web --namespace={NS} --context=dev-gke",
    "kubectl-flags-before-operation": f"kubectl --context dev-gke -n {NS} get deploy web",
    "kubectl-output-yaml": f"{READ} -o yaml",
    "kubectl-output-wide-attached": f"{READ} -owide",
}


@pytest.mark.parametrize("command", list(ROUND_TWO_APPLIES.values()), ids=list(ROUND_TWO_APPLIES))
def test_an_explicit_spelling_with_a_plain_output_format_applies(cli, capsys, command):
    item = _round_two(cli, capsys, {"web": {"image_tag": "0.0.255"}}, command, OUTPUT)

    assert item["status"] == "accepted", item["decision_note"]


@pytest.mark.parametrize("state, where", [("BLOCKED", "prose"), ("NEEDS_INPUT", "gaps")])
def test_stale_warning_never_refuses_and_prints_to_stderr(cli, capsys, state, where):
    agent, contract_id = _turn()
    kwargs = {"prose": "the injected context is outdated"} if where == "prose" else {
        "gaps": ["cluster_details no longer matches the cluster"]}

    code, captured = _finalize(cli, capsys, contract_id, state=state, update=[], as_json=False, **kwargs)

    assert code == 0
    assert "Warning: " in captured.err and "update_contracts is empty" in captured.err
