"""A command set is judged at request as the run-time gate will judge it, and a step sealed elsewhere is told the line that runs it."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
for _path in (_ROOT, _ROOT / "hooks", _ROOT / "bin"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from gaia.approvals import core  # noqa: E402
from gaia.approvals import store as approval_store  # noqa: E402
from gaia.approvals.command_set import CommandSetValidationError, validate_request_set  # noqa: E402
from gaia.store import writer  # noqa: E402

SESSION = "ses-request-matches-gate"
AGENT_ID = "a0f1e2d3c4b5a6978"
AGENT_TYPE = "developer"
MERGE = "gh pr merge 41 --squash"


@pytest.fixture
def host(tmp_path, monkeypatch):
    from modules.core.paths import clear_path_cache

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("GAIA_DATA_DIR", str(data_dir))
    for name in ("GAIA_DB", "GAIA_DISPATCH_AGENT", "GAIA_HOST_SESSION_ID", "CLAUDE_CODE_SESSION_ID"):
        monkeypatch.delenv(name, raising=False)
    con = sqlite3.connect(data_dir / "gaia.db")
    con.executescript(writer._SCHEMA_PATH.read_text())
    con.commit()
    con.close()
    clear_path_cache()
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    monkeypatch.setattr("modules.core.state.find_claude_dir", lambda: claude_dir)
    for name in ("repo", "shell"):
        (tmp_path / name).mkdir()
    return {"tmp": str(tmp_path), "repo": str(tmp_path / "repo"), "shell": str(tmp_path / "shell"),
            "db": data_dir / "gaia.db", "data": data_dir}


def _items(commands, cwd):
    return [{"command": c, "cwd": cwd, "expect_exit": [], "does": "Runs a step.", "impact": "Lands."}
            for c in commands]


def _approve(commands, cwd):
    approval_id = core.request_command_set(
        _items(commands, cwd),
        what="Merge the PR.", question="Merge it?", session_id=SESSION, agent_id=AGENT_TYPE,
        rollback="Revert the merge.", verification="gh pr view 41", shared_state="Yes: main.",
    )
    core.record_presentation(approval_id, native_ref="toolu_q", session_id=SESSION, agent_id="gaia-orchestrator")
    assert core.decide(native_ref="toolu_q", option_key="approve", session_id=SESSION).status == "activated"
    return approval_id


def _event(name, command, tool_use_id, cwd, **fields):
    from adapters.claude_code import ClaudeCodeAdapter

    adapter = ClaudeCodeAdapter()
    return adapter, adapter.parse_event(json.dumps({
        "session_id": SESSION, "transcript_path": "/nonexistent/transcript.jsonl", "cwd": cwd,
        "permission_mode": "acceptEdits", "hook_event_name": name, "agent_id": AGENT_ID,
        "agent_type": AGENT_TYPE, "tool_name": "Bash", "tool_input": {"command": command},
        "tool_use_id": tool_use_id, **fields,
    }))


def _run(command, tool_use_id, cwd):
    adapter, event = _event("PreToolUse", command, tool_use_id, cwd)
    output = adapter.adapt_pre_tool_use(event).output
    decision = (output or {}).get("hookSpecificOutput", {}) if isinstance(output, dict) else {}
    verdict = decision.get("permissionDecision", "allow")
    if verdict == "allow":
        adapter, event = _event(
            "PostToolUse", command, tool_use_id, cwd, duration_ms=40,
            tool_response={"stdout": "", "stderr": "", "interrupted": False, "isImage": False},
        )
        adapter.adapt_post_tool_use(event)
    return verdict, decision.get("permissionDecisionReason") or ""


def _grant(db, approval_id):
    con = sqlite3.connect(db)
    try:
        return con.execute(
            "SELECT status, next_index FROM approval_grants WHERE approval_id = ?", (approval_id,),
        ).fetchone()
    finally:
        con.close()


def test_a_privileged_step_is_sealed_as_signed(host):
    sudo, merge = validate_request_set(["sudo -n true", MERGE], cwd=host["repo"])

    assert sudo["signed"] is True
    assert merge["signed"] is True


def test_an_own_scratch_removal_is_shown_free_and_the_set_runs_to_its_last_step(host, monkeypatch):
    entry = Path(host["data"]) / "scratch" / AGENT_ID
    (entry / "x").mkdir(parents=True)
    remove = f"rm -rf {entry}/x"
    with monkeypatch.context() as dispatched:
        dispatched.setenv("CLAUDE_CODE_SESSION_ID", SESSION)
        dispatched.setenv("GAIA_DISPATCH_AGENT", AGENT_TYPE)
        dispatched.setattr(
            writer, "find_dispatched_row_by_agent_name",
            lambda session, name, **_: {"agent_id": AGENT_ID} if (session, name) == (SESSION, AGENT_TYPE) else None,
        )
        approval_id = _approve([remove, MERGE], host["repo"])

    steps = json.loads(approval_store.get_by_id(approval_id)["payload_json"])["items"]
    assert [step.get("signed", True) for step in steps] == [False, True]
    assert _run(remove, "tu-1", host["repo"])[0] == "allow"
    assert _run(MERGE, "tu-2", host["repo"])[0] == "allow"
    assert _grant(host["db"], approval_id) == ("CONSUMED", 1)


def test_a_redirect_the_gate_refuses_is_refused_at_request_naming_the_floor(host):
    with pytest.raises(CommandSetValidationError, match="floor.shell-write"):
        validate_request_set([f"{MERGE} > {host['tmp']}/merge.log 2>&1"], cwd=host["repo"])


@pytest.mark.parametrize("decorated", ["nohup {merge} > {log}", "{merge} > {log} &"])
def test_a_step_the_host_runs_undecorated_is_refused_naming_the_form_it_runs(host, decorated):
    log = f"{host['tmp']}/merge.log"

    with pytest.raises(CommandSetValidationError, match=f"runs as '{MERGE} > {log}'"):
        validate_request_set([decorated.format(merge=MERGE, log=log)], cwd=host["repo"])


def test_a_step_run_from_another_folder_is_told_the_line_that_runs_it(host):
    approval_id = _approve([MERGE], host["repo"])
    line = f"cd {host['repo']} && {MERGE}"

    verdict, reason = _run(MERGE, "tu-1", host["shell"])
    assert verdict == "deny"
    assert f"`{line}`" in reason, reason
    assert _grant(host["db"], approval_id) == ("PENDING", 0)

    assert _run(line, "tu-2", host["shell"])[0] == "allow"
    assert _grant(host["db"], approval_id) == ("CONSUMED", 1)


def test_request_set_prints_the_line_for_each_step_sealed_elsewhere(host, monkeypatch, capsys):
    from cli.approvals import cmd_request_set

    monkeypatch.chdir(host["shell"])
    args = argparse.Namespace(
        command=["git status", MERGE, "gh pr close 41"], cwd=[host["shell"], host["repo"], host["shell"]],
        does=["Shows it.", "Merges it.", "Closes it."], impact=["None.", "Lands.", "Closes."],
        expect_exit=None, what="Merge the PR.", question="Merge it?", rollback="Revert the merge.",
        verification="gh pr view 41", shared_state="Yes: main.", rationale=None,
        agent_id=AGENT_TYPE, session_id=SESSION, json=False,
    )

    assert cmd_request_set(args) == 0
    printed = capsys.readouterr().out
    assert f"run it as: cd {host['repo']} && {MERGE}" in printed, printed
    assert printed.count("run it as:") == 1, printed
