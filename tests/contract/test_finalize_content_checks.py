"""Finalize refuses a contract whose verification, excerpts or evidence ids the turn's tool record and evidence store refute."""

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

GITHUB_TOKEN = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
PYTEST = "python3 -m pytest tests/ -q"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "gaia_data"))
    monkeypatch.delenv("GAIA_DB", raising=False)
    monkeypatch.setenv("GAIA_WORKSPACE", "me")
    monkeypatch.setattr("gaia.store.writer.is_harness_session_bound", lambda _session: True)


@pytest.fixture(scope="module")
def cli():
    spec = importlib.util.spec_from_file_location("contract_cli_content_checks", REPO_ROOT / "bin" / "cli" / "contract.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _turn() -> tuple:
    from gaia.store import writer

    agent = "a" + uuid.uuid4().hex[:16]
    contract_id = f"{agent}.{uuid.uuid4().hex[:12]}"
    dispatch = f"toolu-dispatch-{uuid.uuid4().hex[:8]}"
    writer.insert_dispatched_handoff(contract_id, agent, "me", agent_name="developer", dispatch_tool_use_id=dispatch)
    writer.bind_harness_child_session(dispatch, agent)
    return agent, contract_id


def _bash(agent: str, command: str, stdout: str = "ok", exit_code: int = 0) -> None:
    adapter = ClaudeCodeAdapter()
    adapter.adapt_post_tool_use(adapter.parse_event(json.dumps({
        "hook_event_name": "PostToolUse", "session_id": "ses-parent", "cwd": "/repo",
        "tool_name": "Bash", "tool_input": {"command": command},
        "tool_response": {"stdout": stdout, "stderr": "", "interrupted": False, "exit_code": exit_code},
        "tool_use_id": f"toolu-{uuid.uuid4().hex[:8]}", "agent_id": agent, "agent_type": "developer",
    })))


def _draft(agent: str, verification: dict | None = None, verbatim: list | None = None) -> dict:
    return {
        "agent_status": {"agent_state": "NEEDS_VERIFICATION", "agent_id": agent, "pending_steps": [], "next_action": "verify"},
        "evidence_report": {
            "patterns_checked": [], "files_checked": [], "commands_run": [], "key_outputs": [],
            "verbatim_outputs": verbatim or [], "cross_layer_impacts": [], "open_gaps": [],
            "verification": verification or {"type": "none"},
        },
        "conclusion": "checked",
    }


def _command_check(command: str, **extra) -> dict:
    return {"type": "command", "command": command, "method": "ran it", "result": "pass", "details": "ok", **extra}


def _finalize(cli, contract_id: str, draft: dict, capsys) -> tuple:
    from gaia.contract.drafts import save_draft

    save_draft(contract_id, draft)
    capsys.readouterr()
    code = cli.cmd_finalize(argparse.Namespace(draft_id=contract_id, agent_id=None, json=True))
    return code, json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def _row(contract_id: str) -> tuple:
    from gaia.paths.resolver import db_path

    con = sqlite3.connect(db_path())
    try:
        state, raw = con.execute(
            "SELECT agent_state, raw_handoff_json FROM agent_contract_handoffs WHERE contract_id = ?", (contract_id,),
        ).fetchone()
    finally:
        con.close()
    return state, json.loads(raw) if raw else {}


def _evidence_id() -> int:
    from gaia.briefs.store import get_brief, upsert_brief
    from gaia.evidence.store import insert_evidence

    upsert_brief("me", "brief-content-checks", {"title": "t", "objective": "o"})
    brief_id = get_brief("me", "brief-content-checks")["id"]
    return insert_evidence("me", brief_id, "AC-1", type="text", text="5 passed")["id"]


def test_the_measured_bare_verdict_over_a_rejected_artifact_is_refused(cli, capsys):
    agent, contract_id = _turn()
    _bash(agent, PYTEST, stdout="1 failed, 4 passed")

    code, out = _finalize(cli, contract_id, _draft(agent, _command_check("pass")), capsys)

    assert code == 1 and out["reason"] == "content_unverified"
    assert "is a verdict, not a check" in out["errors"][0]
    assert _row(contract_id)[0] == "DISPATCHED"


def test_a_command_verification_naming_a_recorded_command_finalizes(cli, capsys):
    agent, contract_id = _turn()
    _bash(agent, PYTEST, stdout="5 passed")

    code, _ = _finalize(cli, contract_id, _draft(agent, _command_check(f"  {PYTEST} ")), capsys)

    assert code == 0
    assert _row(contract_id)[1]["content_checks"] == {"skipped": []}


@pytest.mark.parametrize("vtype", ["shell", "pytest", "test", "tests", "cmd", "none"])
def test_any_verification_carrying_a_command_is_checked_whatever_its_type_word(cli, capsys, vtype):
    agent, contract_id = _turn()
    _bash(agent, PYTEST, stdout="1 failed, 4 passed")

    code, out = _finalize(cli, contract_id, _draft(agent, {**_command_check("pass"), "type": vtype}), capsys)

    assert code == 1 and "is a verdict, not a check" in out["errors"][0]


def test_a_pass_backed_by_a_command_whose_last_run_failed_is_refused(cli, capsys):
    agent, contract_id = _turn()
    _bash(agent, PYTEST, stdout="5 passed")
    _bash(agent, PYTEST, stdout="1 failed, 4 passed", exit_code=1)

    code, out = _finalize(cli, contract_id, _draft(agent, _command_check(PYTEST)), capsys)

    assert code == 1 and "last exited 1" in out["errors"][0]


@pytest.mark.parametrize("result", ["passed", "ok", "success", "done", "true", "green", "yes", "Passed!"])
def test_every_pass_word_gets_the_exit_check_a_pass_gets(cli, capsys, result):
    agent, contract_id = _turn()
    _bash(agent, PYTEST, stdout="1 failed, 4 passed", exit_code=1)

    code, out = _finalize(cli, contract_id, _draft(agent, {**_command_check(PYTEST), "result": result}), capsys)

    assert code == 1 and "last exited 1" in out["errors"][0]


@pytest.mark.parametrize("result", ["fail", "failed", "false"])
def test_a_fail_word_over_a_failing_run_is_not_refused(cli, capsys, result):
    agent, contract_id = _turn()
    _bash(agent, PYTEST, stdout="1 failed, 4 passed", exit_code=1)

    code, _ = _finalize(cli, contract_id, _draft(agent, {**_command_check(PYTEST), "result": result}), capsys)

    assert code == 0


def test_a_declared_expect_exit_backs_a_pass_over_a_nonzero_exit(cli, capsys):
    agent, contract_id = _turn()
    _bash(agent, "grep -q secret config.yaml", stdout="", exit_code=1)

    code, _ = _finalize(cli, contract_id, _draft(agent, _command_check("grep -q secret config.yaml", expect_exit=1)), capsys)

    assert code == 0
    agent, contract_id = _turn()
    _bash(agent, f"echo {PYTEST}", stdout=PYTEST)

    code, out = _finalize(cli, contract_id, _draft(agent, _command_check(PYTEST)), capsys)

    assert code == 1 and "is not a command this turn ran" in out["errors"][0]


def test_an_existing_evidence_id_backs_a_command_verification(cli, capsys):
    agent, contract_id = _turn()

    code, _ = _finalize(cli, contract_id, _draft(agent, _command_check("pass", evidence_ids=[_evidence_id()])), capsys)

    assert code == 0


@pytest.mark.parametrize("cited", [[999999], ["x"], [True]])
def test_an_unknown_or_malformed_evidence_id_is_refused(cli, capsys, cited):
    agent, contract_id = _turn()
    _bash(agent, PYTEST)

    code, out = _finalize(cli, contract_id, _draft(agent, _command_check(PYTEST, evidence_ids=cited)), capsys)

    assert code == 1 and "evidence_ids" in out["errors"][0]


def test_an_unknown_evidence_id_on_a_verbatim_entry_is_refused(cli, capsys):
    agent, contract_id = _turn()

    code, out = _finalize(cli, contract_id, _draft(agent, verbatim=[{"evidence_id": 999999, "excerpt": "x"}]), capsys)

    assert code == 1 and "verbatim_outputs[0].evidence_id" in out["errors"][0]


def test_an_excerpt_absent_from_its_commands_recorded_output_is_refused(cli, capsys):
    agent, contract_id = _turn()
    _bash(agent, PYTEST, stdout="1 failed, 4 passed")

    verbatim = ["free text is not attributed", {"command": PYTEST, "excerpt": "5 passed"}]
    code, out = _finalize(cli, contract_id, _draft(agent, verbatim=verbatim), capsys)

    assert code == 1 and out["errors"] == [
        f"evidence_report.verbatim_outputs[1]: the excerpt is not in the recorded output of {PYTEST!r}",
    ]


@pytest.mark.parametrize("entry", [
    {"command": PYTEST, "output": "99 passed"},
    {"command": PYTEST, "excerpt": "4 passed", "output": "99 passed"},
])
def test_an_output_key_is_compared_as_an_excerpt(cli, capsys, entry):
    agent, contract_id = _turn()
    _bash(agent, PYTEST, stdout="1 failed, 4 passed", exit_code=1)

    code, out = _finalize(cli, contract_id, _draft(agent, verbatim=[entry]), capsys)

    assert code == 1 and out["errors"] == [
        f"evidence_report.verbatim_outputs[0]: the excerpt is not in the recorded output of {PYTEST!r}",
    ]


@pytest.mark.parametrize("entry", [f"{PYTEST} -> 99 passed", {"excerpt": "99 passed"}])
def test_a_quote_naming_no_command_is_stamped_uncompared(cli, capsys, entry):
    agent, contract_id = _turn()
    _bash(agent, PYTEST, stdout="1 failed, 4 passed", exit_code=1)

    code, _ = _finalize(cli, contract_id, _draft(agent, verbatim=[entry]), capsys)

    assert code == 0
    assert _row(contract_id)[1]["content_checks"]["skipped"] == [
        "evidence_report.verbatim_outputs[0] not compared: it names no command and cites no evidence_id",
    ]


def test_an_excerpt_passes_when_the_record_holds_no_output_and_the_skip_is_recorded(cli, capsys):
    agent, contract_id = _turn()
    _bash(agent, PYTEST, stdout="")

    code, _ = _finalize(cli, contract_id, _draft(agent, verbatim=[{"command": PYTEST, "excerpt": "5 passed"}]), capsys)

    assert code == 0
    assert "holds no output" in _row(contract_id)[1]["content_checks"]["skipped"][0]


def test_an_elided_excerpt_must_keep_its_fragments_in_recorded_order(cli, capsys):
    agent, contract_id = _turn()
    _bash(agent, "make check", stdout="step one\nstep two\n  step   three\n")
    in_order = {"command": "make check", "excerpt": "step one ... step three"}
    reversed_order = {"command": "make check", "excerpt": "step three ... step one"}

    code, out = _finalize(cli, contract_id, _draft(agent, verbatim=[in_order, reversed_order]), capsys)

    assert code == 1 and [error.split(":")[0] for error in out["errors"]] == ["evidence_report.verbatim_outputs[1]"]


def test_an_excerpt_attributed_to_a_command_the_turn_never_ran_is_refused(cli, capsys):
    agent, contract_id = _turn()
    _bash(agent, PYTEST, stdout="1 failed, 4 passed")

    code, out = _finalize(cli, contract_id, _draft(agent, verbatim=[{"command": "make test-all", "excerpt": "all 99 passed"}]), capsys)

    assert code == 1 and "'make test-all' is not a command this turn ran" in out["errors"][0]


@pytest.mark.parametrize("excerpt", ["...", "", "   ", "… ..."])
def test_an_excerpt_with_no_content_is_refused(cli, capsys, excerpt):
    agent, contract_id = _turn()
    _bash(agent, PYTEST, stdout="1 failed, 4 passed")

    code, out = _finalize(cli, contract_id, _draft(agent, verbatim=[{"command": PYTEST, "excerpt": excerpt}]), capsys)

    assert code == 1 and "the excerpt quotes nothing" in out["errors"][0]


def test_an_excerpt_matches_whole_tokens_only(cli, capsys):
    agent, contract_id = _turn()
    _bash(agent, PYTEST, stdout="1 failed, 15 passed")
    verbatim = [{"command": PYTEST, "excerpt": "5 passed"}, {"command": PYTEST, "excerpt": "15 passed"}]

    code, out = _finalize(cli, contract_id, _draft(agent, verbatim=verbatim), capsys)

    assert code == 1 and [error.split(":")[0] for error in out["errors"]] == ["evidence_report.verbatim_outputs[0]"]


def test_an_excerpt_past_a_truncated_record_is_skipped_with_its_reason(cli, capsys):
    agent, contract_id = _turn()
    _bash(agent, PYTEST, stdout="HEAD-MARK " + "x " * 2000 + "TAIL-MARK")
    verbatim = [{"command": PYTEST, "excerpt": "TAIL-MARK"}, {"command": PYTEST, "excerpt": "HEAD-MARK"}]

    code, _ = _finalize(cli, contract_id, _draft(agent, verbatim=verbatim), capsys)

    skipped = _row(contract_id)[1]["content_checks"]["skipped"]
    assert code == 0 and len(skipped) == 1 and skipped[0].startswith("evidence_report.verbatim_outputs[0]")
    assert "truncated" in skipped[0]


def test_an_excerpt_quoting_a_secret_matches_its_redacted_record(cli, capsys):
    agent, contract_id = _turn()
    _bash(agent, "env", stdout=f"TOKEN={GITHUB_TOKEN}\n")

    code, _ = _finalize(cli, contract_id, _draft(agent, verbatim=[{"command": "env", "excerpt": f"TOKEN={GITHUB_TOKEN}"}]), capsys)

    assert code == 0


def test_without_a_bound_record_a_runnable_command_is_skipped_and_a_verdict_refused(cli, capsys):
    from gaia.store import writer

    agent = "a" + uuid.uuid4().hex[:16]
    contract_id = f"{agent}.{uuid.uuid4().hex[:12]}"
    writer.insert_dispatched_handoff(contract_id, agent, "me", agent_name="developer", dispatch_tool_use_id="toolu-unbound")

    refused, _ = _finalize(cli, contract_id, _draft(agent, _command_check("pass")), capsys)
    accepted, _ = _finalize(cli, contract_id, _draft(agent, _command_check(PYTEST)), capsys)

    assert (refused, accepted) == (1, 0)
    assert "no tool record is bound" in _row(contract_id)[1]["content_checks"]["skipped"][0]


@pytest.mark.parametrize("command", ["PASS.", "pass ✓", "all tests pass", "exit 0", "Passed!"])
def test_without_a_bound_record_a_command_no_shell_could_run_is_refused(cli, capsys, command):
    from gaia.store import writer

    agent = "a" + uuid.uuid4().hex[:16]
    contract_id = f"{agent}.{uuid.uuid4().hex[:12]}"
    writer.insert_dispatched_handoff(contract_id, agent, "me", agent_name="developer", dispatch_tool_use_id=f"toolu-{agent}")

    code, out = _finalize(cli, contract_id, _draft(agent, _command_check(command)), capsys)

    assert code == 1 and "evidence_report.verification.command" in out["errors"][0]


def test_a_row_this_check_closed_is_checked_again_on_refinalize(cli, capsys):
    agent, contract_id = _turn()
    _bash(agent, PYTEST, stdout="5 passed")
    first, _ = _finalize(cli, contract_id, _draft(agent, _command_check(PYTEST)), capsys)
    forged = _draft(agent, _command_check("pass"), verbatim=[{"command": PYTEST, "excerpt": "99 passed"}])

    second, out = _finalize(cli, contract_id, forged, capsys)

    assert (first, second) == (0, 1) and len(out["errors"]) == 2
    assert _row(contract_id)[1]["evidence_report"]["verification"]["command"] == PYTEST


def test_a_row_finalized_before_the_check_is_not_revalidated(cli, capsys):
    from gaia.store.writer import finalize_agent_contract_handoff

    agent, contract_id = _turn()
    earlier = _draft(agent, _command_check("pass"))
    finalize_agent_contract_handoff(
        contract_id=contract_id, agent_id=agent, workspace="me", agent_state="NEEDS_VERIFICATION",
        raw_handoff_json=json.dumps(earlier),
    )

    code, _ = _finalize(cli, contract_id, earlier, capsys)

    state, stored = _row(contract_id)
    assert code == 0 and state == "NEEDS_VERIFICATION"
    assert stored["evidence_report"]["verification"]["command"] == "pass"
    assert "content_checks" not in stored
