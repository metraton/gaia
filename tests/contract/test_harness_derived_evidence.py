"""A turn's commands_run and files_checked come from what the harness recorded it running, on both hosts."""

from __future__ import annotations

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
from adapters.opencode import OpenCodeAdapter  # noqa: E402

GITHUB_TOKEN = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "gaia_data"))
    monkeypatch.delenv("GAIA_DB", raising=False)
    monkeypatch.setenv("GAIA_WORKSPACE", "me")
    monkeypatch.setattr("gaia.store.writer.is_harness_session_bound", lambda _session: True)


def _agent() -> str:
    return "a" + uuid.uuid4().hex[:16]


def _born_row(agent_id: str, harness_agent_id: str) -> str:
    from gaia.store import writer

    contract_id = f"{agent_id}.{uuid.uuid4().hex[:12]}"
    dispatch = f"toolu-dispatch-{uuid.uuid4().hex[:8]}"
    writer.insert_dispatched_handoff(
        contract_id, agent_id, "me", agent_name="developer", dispatch_tool_use_id=dispatch,
    )
    writer.bind_harness_child_session(dispatch, harness_agent_id)
    return contract_id


def _claude(tool: str, tool_input: dict, response: dict, harness_agent_id: str) -> None:
    adapter = ClaudeCodeAdapter()
    adapter.adapt_post_tool_use(adapter.parse_event(json.dumps({
        "hook_event_name": "PostToolUse", "session_id": "ses-parent", "cwd": "/repo",
        "tool_name": tool, "tool_input": tool_input, "tool_response": response,
        "tool_use_id": f"toolu-{uuid.uuid4().hex[:8]}",
        "agent_id": harness_agent_id, "agent_type": "developer",
    })))


def _bash(command: str, harness_agent_id: str, stdout: str = "ok") -> None:
    _claude("Bash", {"command": command}, {"stdout": stdout, "stderr": "", "interrupted": False},
            harness_agent_id)


def _read(path: str, harness_agent_id: str, tool: str = "Read", tool_input: dict | None = None) -> dict:
    adapter = ClaudeCodeAdapter()
    return adapter.adapt_pre_tool_use(adapter.parse_event(json.dumps({
        "hook_event_name": "PreToolUse", "session_id": "ses-parent", "cwd": "/repo",
        "tool_name": tool, "tool_input": tool_input or {"file_path": path},
        "tool_use_id": f"toolu-{uuid.uuid4().hex[:8]}",
        "agent_id": harness_agent_id, "agent_type": "developer",
    }))).output


def _spool_lines(key: str) -> list:
    from gaia.contract import read_spool

    path = read_spool.spool_path(key)
    return path.read_text().splitlines() if path.exists() else []


def _envelope(agent_id: str, commands=(), files=(), **extra) -> dict:
    return {
        "agent_status": {"agent_state": "NEEDS_VERIFICATION", "agent_id": agent_id},
        "evidence_report": {"commands_run": list(commands), "files_checked": list(files)},
        **extra,
    }


def _stamp(envelope: dict, contract_id: str, worktree_scope: str = "shared") -> dict:
    from gaia.contract.tool_record import stamp_harness_fields

    stamp_harness_fields(envelope, contract_id, worktree_scope=worktree_scope)
    return envelope


def _payloads() -> str:
    from gaia.paths.resolver import db_path

    con = sqlite3.connect(db_path())
    try:
        return json.dumps(con.execute("SELECT payload FROM harness_events").fetchall())
    finally:
        con.close()


def test_a_turn_that_listed_nothing_finalizes_with_what_it_ran_and_read():
    agent = _agent()
    contract_id = _born_row(agent, agent)
    for command in ("git status", "ls /repo", "python3 -m pytest tests/x.py"):
        _bash(command, agent)
    _read("/repo/a.py", agent)
    _read("/repo/b.py", agent)

    report = _stamp(_envelope(agent), contract_id)["evidence_report"]

    assert [entry.split(" (exit")[0] for entry in report["commands_run"]] == [
        "git status", "ls /repo", "python3 -m pytest tests/x.py",
    ]
    assert report["files_checked"] == ["/repo/a.py", "/repo/b.py"]


def test_the_opencode_after_tool_event_resolves_the_subagent_by_its_session():
    child_session = f"ses-child-{uuid.uuid4().hex[:8]}"
    agent = _agent()
    contract_id = _born_row(agent, child_session)
    adapter = OpenCodeAdapter()
    adapter.adapt_post_tool_use(adapter.parse_event(json.dumps({
        "event": "tool.execute.after", "sessionID": child_session, "callID": "call-7",
        "agentID": "call-dispatch-handle", "agent": "developer", "tool": "bash",
        "args": {"command": "git log -1"}, "result": {"output": "commit abc"},
    })))

    report = _stamp(_envelope(agent), contract_id)["evidence_report"]

    assert [entry.split(" (exit")[0] for entry in report["commands_run"]] == ["git log -1"]


def test_grep_and_glob_are_recorded_by_the_path_they_search():
    agent = _agent()
    contract_id = _born_row(agent, agent)
    _read("", agent, tool="Grep", tool_input={"pattern": "def ", "path": "/repo/gaia"})
    _read("", agent, tool="Glob", tool_input={"pattern": "**/*.py"})

    report = _stamp(_envelope(agent), contract_id)["evidence_report"]

    assert report["files_checked"] == ["/repo/gaia", "/repo"]


def test_a_read_refused_before_it_runs_is_not_recorded_as_checked():
    agent = _agent()
    contract_id = _born_row(agent, agent)
    refused = _read("/repo/.env", agent)
    _read("/repo/a.py", agent)

    report = _stamp(_envelope(agent), contract_id)["evidence_report"]

    assert refused["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert report["files_checked"] == ["/repo/a.py"]


def test_an_opencode_read_is_recorded_once_when_policy_lets_it_run():
    child_session = f"ses-child-{uuid.uuid4().hex[:8]}"
    agent = _agent()
    contract_id = _born_row(agent, child_session)
    adapter = OpenCodeAdapter()
    call = {"sessionID": child_session, "callID": "call-8", "agentID": "call-dispatch-handle",
            "agent": "developer", "tool": "read", "args": {"filePath": "/repo/a.py"}}
    adapter.adapt_pre_tool_use(adapter.parse_event(json.dumps({"event": "tool.execute.before", **call})))
    adapter.adapt_post_tool_use(adapter.parse_event(json.dumps({
        "event": "tool.execute.after", **call, "result": {"output": "contents"},
    })))

    report = _stamp(_envelope(agent), contract_id)["evidence_report"]

    assert report["files_checked"] == ["/repo/a.py"]
    assert len(_spool_lines(child_session)) == 1


def test_a_read_is_spooled_and_opens_no_database_record():
    from gaia.contract import read_spool

    agent = _agent()
    _born_row(agent, agent)
    _read("/repo/a.py", agent)

    assert read_spool.read(agent) == ["/repo/a.py"]
    assert "tool.called" not in _event_types()


def test_a_path_is_redacted_before_it_reaches_the_spool():
    from gaia.contract import read_spool

    agent = _agent()
    _read(f"/repo/{GITHUB_TOKEN}.txt", agent)

    assert GITHUB_TOKEN not in json.dumps(_spool_lines(agent))
    assert len(read_spool.read(agent)) == 1


def test_a_missing_or_unwritable_state_dir_never_fails_the_tool_call(tmp_path, monkeypatch):
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("x")
    monkeypatch.setenv("GAIA_DATA_DIR", str(blocker))
    agent = _agent()

    output = _read("/repo/a.py", agent)

    assert output.get("hookSpecificOutput", {}).get("permissionDecision") != "deny"


def test_parallel_appends_each_land_as_one_whole_line():
    from concurrent.futures import ThreadPoolExecutor

    from gaia.contract import read_spool

    agent = _agent()
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda n: read_spool.append(agent, "Read", f"/repo/{n}.py"), range(400)))

    assert sorted(read_spool.read(agent)) == sorted(f"/repo/{n}.py" for n in range(400))


def test_the_spool_stops_growing_at_its_bound(monkeypatch):
    from gaia.contract import read_spool

    monkeypatch.setattr(read_spool, "MAX_SPOOL_BYTES", 1024)
    agent = _agent()
    for n in range(200):
        read_spool.append(agent, "Read", f"/repo/{n}.py")

    assert read_spool.spool_path(agent).stat().st_size < 1024 + read_spool.MAX_LINE_BYTES
    assert 0 < len(read_spool.read(agent)) < 200


def test_a_torn_or_foreign_line_is_skipped_when_the_spool_is_read():
    from gaia.contract import read_spool

    agent = _agent()
    read_spool.append(agent, "Read", "/repo/a.py")
    with open(read_spool.spool_path(agent), "ab") as handle:
        handle.write(b'{"path": "/repo/tor\nnot json\n{"path": 7}\n')
    read_spool.append(agent, "Read", "/repo/b.py")

    assert read_spool.read(agent) == ["/repo/a.py", "/repo/b.py"]


def test_a_cut_turn_is_rescued_with_the_paths_it_read_and_the_spool_is_consumed():
    from gaia.contract import read_spool
    from gaia.contract.tool_record import recover_spooled_files

    agent = _agent()
    contract_id = _born_row(agent, agent)
    _read("/repo/a.py", agent)
    _read("/repo/b.py", agent)
    envelope = {"evidence_report": {"files_checked": ["/repo/a.py"]}}

    harness_agent_id = recover_spooled_files(envelope, contract_id)

    assert envelope["evidence_report"]["files_checked"] == ["/repo/a.py", "/repo/b.py"]
    assert harness_agent_id == agent
    read_spool.discard(harness_agent_id)
    assert read_spool.read(agent) == []


def test_finalizing_consumes_the_spool_so_a_resumed_turn_starts_empty():
    from gaia.contract.tool_record import commit_harness_record

    agent = _agent()
    first_contract = _born_row(agent, agent)
    _read("/repo/a.py", agent)
    first = _stamp(_envelope(agent), first_contract)
    commit_harness_record(first)
    resumed_contract = _born_row(agent, agent)
    _read("/repo/b.py", agent)

    report = _stamp(_envelope(agent), resumed_contract)["evidence_report"]

    assert first["evidence_report"]["files_checked"] == ["/repo/a.py"]
    assert report["files_checked"] == ["/repo/b.py"]


def test_a_command_the_agent_claims_but_never_ran_survives_only_as_annotation():
    from gaia.contract.tool_record import ANNOTATION_MARK

    agent = _agent()
    contract_id = _born_row(agent, agent)
    _bash("git status", agent)

    report = _stamp(_envelope(agent, commands=["git status", "terraform apply"]), contract_id)[
        "evidence_report"
    ]

    unmarked = [entry for entry in report["commands_run"] if not entry.startswith(ANNOTATION_MARK)]
    assert [entry.split(" (exit")[0] for entry in unmarked] == ["git status"]
    assert ANNOTATION_MARK + "terraform apply" in report["commands_run"]


def test_a_credential_in_the_output_or_command_is_never_recorded():
    agent = _agent()
    _born_row(agent, agent)
    _bash(f"echo {GITHUB_TOKEN}", agent, stdout=f"token={GITHUB_TOKEN}\n")

    assert GITHUB_TOKEN not in _payloads()
    assert "tool.called" in _event_types()


def _event_types() -> str:
    from gaia.paths.resolver import db_path

    con = sqlite3.connect(db_path())
    try:
        return json.dumps(con.execute("SELECT type FROM harness_events").fetchall())
    finally:
        con.close()


def test_a_huge_output_is_recorded_as_a_bounded_excerpt():
    from modules.events.capability_usage import EXCERPT_LIMIT

    agent = _agent()
    _born_row(agent, agent)
    _bash("cat big.log", agent, stdout="x" * 1_000_000)

    payload = _payloads()
    assert "tool.called" in _event_types()
    assert len(payload) < EXCERPT_LIMIT * 4


def test_two_agents_of_the_same_name_in_parallel_keep_their_own_records():
    first, second = _agent(), _agent()
    first_contract, second_contract = _born_row(first, first), _born_row(second, second)
    _bash("echo first", first)
    _bash("echo second", second)

    first_report = _stamp(_envelope(first), first_contract)["evidence_report"]
    second_report = _stamp(_envelope(second), second_contract)["evidence_report"]

    assert [entry.split(" (exit")[0] for entry in first_report["commands_run"]] == ["echo first"]
    assert [entry.split(" (exit")[0] for entry in second_report["commands_run"]] == ["echo second"]


def test_a_resumed_turn_gets_only_the_calls_made_after_the_previous_close():
    from gaia.store.writer import finalize_agent_contract_handoff

    agent = _agent()
    first_contract = _born_row(agent, agent)
    _bash("echo before", agent)
    first = _stamp(_envelope(agent), first_contract)
    finalize_agent_contract_handoff(
        contract_id=first_contract, agent_id=agent, workspace="me",
        agent_state="NEEDS_VERIFICATION", raw_handoff_json=json.dumps(first),
    )
    resumed_contract = _born_row(agent, agent)
    _bash("echo after", agent)

    report = _stamp(_envelope(agent), resumed_contract)["evidence_report"]

    assert [entry.split(" (exit")[0] for entry in report["commands_run"]] == ["echo after"]


def test_an_agent_written_worktree_scope_or_foreign_agent_id_never_wins():
    agent = _agent()
    contract_id = _born_row(agent, agent)
    envelope = _envelope(_agent(), worktree_scope="managed")

    _stamp(envelope, contract_id, worktree_scope="shared")

    assert envelope["agent_status"]["agent_id"] == agent
    assert envelope["worktree_scope"] == "shared"
