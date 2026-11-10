"""A background command keeps its output off the host spool, and a turn does not close while one it started runs.

Every probe runs a real hook entry point on a payload shaped like the 2026-11-10
outage: gaia-operator's ``run_in_background`` ssh ``dir /s`` (task ``bksxoirex``)
that looped on a Windows junction and outlived its turn.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOKS = REPO_ROOT / "hooks"
INCIDENT_COMMAND = (
    'ssh -o BatchMode=yes metra-tower "dir C:\\Users\\jorge D:\\ /s /-c /a-d | findstr /i /r '
    '/c:\\"Directory of\\" /c:\\"\\.wav\\" | findstr /i /v /c:\\"VST\\""'
)
LAUNCH_ID = "toolu_01NPLW6kF1p6kngMNfdrgfsF"
TASK_ID = "bksxoirex"
SESSION = "c45421db-452b-44ae-9e8b-507c5bad19e6"
AGENT = "a708d2d6ac06d8904"
MOVED_TO_BACKGROUND = (
    "Directory of C:\\Users\\jorge\\AppData\\Local\\Application Data\\Application Data\n"
    f"Command did not complete within its 120s timeout and was moved to the background (ID: {TASK_ID}). "
    f"Output is being written to: /tmp/claude-1001/-home-jorge-ws/{SESSION}/tasks/{TASK_ID}.output. "
    "You will be notified when it completes."
)
LIVE_TASK = {"id": TASK_ID, "type": "shell", "status": "running", "description": "tower scan",
             "command": INCIDENT_COMMAND}


def _run(entry: str, payload: dict, tmp_path: Path) -> subprocess.CompletedProcess:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(tmp_path),
        "GAIA_DATA_DIR": str(tmp_path / "data"),
        "GAIA_HOOK_STDIN_TIMEOUT": "5",
    }
    return subprocess.run(
        [sys.executable, str(HOOKS / entry)], cwd=tmp_path, env=env,
        input=json.dumps(payload), capture_output=True, text=True, timeout=120,
    )


def _pre_tool_use(command: str, tmp_path: Path, background: bool = True) -> subprocess.CompletedProcess:
    return _run("pre_tool_use.py", {
        "hook_event_name": "PreToolUse", "session_id": SESSION, "cwd": str(tmp_path),
        "agent_id": AGENT, "agent_type": "gaia:gaia-operator", "tool_name": "Bash", "tool_use_id": LAUNCH_ID,
        "tool_input": {"command": command, "run_in_background": background, "timeout": 600000,
                       "description": "tower scan"},
    }, tmp_path)


def _scratch(tmp_path: Path) -> str:
    return str(tmp_path / "data" / "scratch")


REFUSED = [
    ("incident-ssh-dir-s", INCIDENT_COMMAND),
    ("find-over-root", "find / -name '*.wav'"),
    ("dev-server-to-spool", "npm run dev"),
    ("ci-watch-to-spool", "ghx -C /repo run watch 123 --exit-status --compact"),
    ("file-outside-scratch", "ls -R / &> /tmp/listing.txt"),
    ("stderr-still-spooled", "find / -name x > {scratch}/x.log"),
    ("head-that-keeps-all-but-n", "find / 2>&1 | head -n -5"),
    ("follow-mode-tail", "tail -f /var/log/syslog"),
]
PASSED = [
    ("incident-into-scratch", INCIDENT_COMMAND + " &> {scratch}/scan.log"),
    ("dev-server-into-scratch", "python3 -m http.server 8000 &> {scratch}/dev.log"),
    ("ci-watch-into-scratch", "ghx -C /repo run watch 123 --exit-status --compact --interval 30 &> {scratch}/ci.log"),
    ("bounded-pipeline", "find / -name '*.wav' 2>&1 | head -n 200"),
    ("silent-wait", "sleep 300"),
]


@pytest.mark.parametrize("case,command", REFUSED, ids=[c for c, _ in REFUSED])
def test_an_unbounded_background_command_is_refused_naming_the_route(case, command, tmp_path):
    result = _pre_tool_use(command.format(scratch=_scratch(tmp_path)), tmp_path)
    decision = json.loads(result.stdout.strip().splitlines()[-1])["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny", (case, result.stdout[-600:], result.stderr[-600:])
    assert "[BACKGROUND_OUTPUT]" in decision["permissionDecisionReason"]
    assert "&> ~/.gaia/scratch/<contract_id>.log" in decision["permissionDecisionReason"]


@pytest.mark.parametrize("case,command", PASSED, ids=[c for c, _ in PASSED])
def test_a_bounded_background_command_passes_with_its_input_intact(case, command, tmp_path):
    result = _pre_tool_use(command.format(scratch=_scratch(tmp_path)), tmp_path)
    decision = json.loads(result.stdout.strip().splitlines()[-1])["hookSpecificOutput"]
    assert decision["permissionDecision"] == "allow", (case, result.stdout[-600:])
    assert decision["updatedInput"]["run_in_background"] is True
    assert decision["updatedInput"]["command"].endswith(command.format(scratch=_scratch(tmp_path)))
    assert decision["updatedInput"]["timeout"] == 600000


def test_a_foreground_command_is_not_judged_by_the_background_rule(tmp_path):
    result = _pre_tool_use("find / -name '*.wav'", tmp_path, background=False)
    assert "[BACKGROUND_OUTPUT]" not in result.stdout


def _agent_transcript(tmp_path: Path) -> Path:
    path = tmp_path / f"agent-{AGENT}.jsonl"
    launch = {"type": "assistant", "agentId": AGENT, "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": LAUNCH_ID, "name": "Bash",
         "input": {"command": INCIDENT_COMMAND, "run_in_background": True}}]}}
    result = {"type": "user", "agentId": AGENT, "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": LAUNCH_ID, "content": MOVED_TO_BACKGROUND}]}}
    path.write_text("\n".join(json.dumps(record) for record in (launch, result)) + "\n")
    return path


def _subagent_stop(tmp_path: Path, background_tasks: list) -> subprocess.CompletedProcess:
    return _run("subagent_stop.py", {
        "hook_event_name": "SubagentStop", "session_id": SESSION, "cwd": str(tmp_path),
        "transcript_path": str(tmp_path / f"{SESSION}.jsonl"), "permission_mode": "acceptEdits",
        "stop_hook_active": False, "agent_id": AGENT, "agent_type": "gaia:gaia-operator",
        "agent_transcript_path": str(_agent_transcript(tmp_path)),
        "last_assistant_message": f"background tasks bodjxi7fa/{TASK_ID}/b1kxvdaid may still run on tower; harmless reads.",
        "background_tasks": background_tasks, "session_crons": [],
    }, tmp_path)


def test_the_incident_close_is_refused_while_its_scan_runs(tmp_path):
    result = _subagent_stop(tmp_path, [LIVE_TASK])
    output = json.loads(result.stdout.strip().splitlines()[-1])
    assert output["decision"] == "block", result.stdout[-600:]
    assert "[BACKGROUND_TASK_LIVE]" in output["reason"]
    assert TASK_ID in output["reason"] and "TaskStop" in output["reason"]


@pytest.mark.parametrize("case,tasks", [
    ("scan-stopped", []),
    ("another-agents-shell", [{**LIVE_TASK, "id": "b9zzzzzzz"}]),
    ("dispatched-subagent-in-flight", [{"id": "a1b2c3", "type": "subagent", "status": "running",
                                        "description": "dev", "agent_type": "gaia:developer"}]),
])
def test_a_close_with_nothing_of_its_own_running_is_not_held(case, tasks, tmp_path):
    result = _subagent_stop(tmp_path, tasks)
    assert "[BACKGROUND_TASK_LIVE]" not in result.stdout + result.stderr, case


def _main_transcript(tmp_path: Path) -> Path:
    path = tmp_path / f"{SESSION}.jsonl"
    launch = {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": "toolu_main", "name": "Bash",
         "input": {"command": "npm run dev", "run_in_background": True}}]}}
    result = {"type": "user", "toolUseResult": {"stdout": "", "stderr": "", "interrupted": False,
                                                "backgroundTaskId": "bmain0001"},
              "message": {"role": "user", "content": [
                  {"type": "tool_result", "tool_use_id": "toolu_main", "content": "running"}]}}
    path.write_text("\n".join(json.dumps(record) for record in (launch, result)) + "\n")
    return path


@pytest.mark.parametrize("case,tasks,held", [
    ("own-shell-running", [{"id": "bmain0001", "type": "shell", "status": "running", "command": "npm run dev"}], True),
    ("subagent-in-flight", [{"id": "a1b2c3", "type": "subagent", "status": "running", "agent_type": "gaia:developer"}],
     False),
])
def test_the_orchestrator_turn_is_held_only_by_its_own_shell(case, tasks, held, tmp_path):
    result = _run("stop_hook.py", {
        "hook_event_name": "Stop", "session_id": SESSION, "cwd": str(tmp_path),
        "transcript_path": str(_main_transcript(tmp_path)), "stop_hook_active": False,
        "last_assistant_message": "Done.", "background_tasks": tasks, "session_crons": [],
    }, tmp_path)
    assert ("[BACKGROUND_TASK_LIVE]" in result.stdout) is held, (case, result.stdout[-600:])


def test_a_command_the_host_moved_to_the_background_gets_a_notice(tmp_path):
    result = _run("post_tool_use.py", {
        "hook_event_name": "PostToolUse", "session_id": SESSION, "cwd": str(tmp_path),
        "agent_id": AGENT, "agent_type": "gaia:gaia-operator", "tool_name": "Bash", "tool_use_id": LAUNCH_ID,
        "tool_input": {"command": INCIDENT_COMMAND},
        "tool_response": {"stdout": MOVED_TO_BACKGROUND, "stderr": "", "interrupted": False,
                          "backgroundTaskId": TASK_ID, "timedOutAfterMs": 120000},
    }, tmp_path)
    context = json.loads(result.stdout.strip().splitlines()[-1])["hookSpecificOutput"]["additionalContext"]
    assert TASK_ID in context and "TaskStop" in context
