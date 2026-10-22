"""A turn's commands_run and files_checked come from what the harness recorded it running, on both hosts."""

from __future__ import annotations

import argparse
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
    from gaia.contract.tool_record import seal_cut_record

    agent = _agent()
    contract_id = _born_row(agent, agent)
    _read("/repo/a.py", agent)
    _read("/repo/b.py", agent)
    envelope = {"evidence_report": {"files_checked": ["/repo/a.py"]}}

    harness_agent_id = seal_cut_record(envelope, contract_id)

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


def _stored(contract_id: str) -> dict:
    from gaia.paths.resolver import db_path

    con = sqlite3.connect(db_path())
    try:
        raw = con.execute(
            "SELECT raw_handoff_json FROM agent_contract_handoffs WHERE contract_id = ?", (contract_id,),
        ).fetchone()[0]
    finally:
        con.close()
    return json.loads(raw)


def test_a_cut_turn_row_lists_the_paths_it_spooled_and_its_spool_is_deleted():
    from adapters.subagent_stop_core import salvage_truncated_draft
    from gaia.contract import read_spool
    from gaia.contract.drafts import save_draft

    minted, harness = _agent(), _agent()
    contract_id = _born_row(minted, harness)
    save_draft(contract_id, _envelope(minted, files=["/repo/a.py"]))
    _read("/repo/a.py", harness)
    _read("/repo/b.py", harness)

    outcome = salvage_truncated_draft(
        parsed_contract=None, task_info={"agent_id": harness, "agent": "developer", "workspace": "me"},
        session_id="ses-parent",
    )

    assert outcome["created"] is True
    assert _stored(contract_id)["evidence_report"]["files_checked"] == ["/repo/a.py", "/repo/b.py"]
    assert read_spool.read(harness) == []


def test_finalize_writes_the_row_from_the_harness_record_and_deletes_the_spool():
    import importlib.util

    from gaia.contract import read_spool
    from gaia.contract.drafts import save_draft
    from gaia.contract.tool_record import ANNOTATION_MARK

    spec = importlib.util.spec_from_file_location("contract_cli_under_test", REPO_ROOT / "bin" / "cli" / "contract.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    agent = _agent()
    contract_id = _born_row(agent, agent)
    for command in ("git status", "ls /repo"):
        _bash(command, agent)
    _read("/repo/a.py", agent)
    draft = _envelope(agent, commands=["terraform apply"], files=["/repo/a.py"], conclusion="read and ran")
    draft["agent_status"].update(pending_steps=[], next_action="pending")
    draft["evidence_report"].update(
        patterns_checked=[], key_outputs=[], verbatim_outputs=[], cross_layer_impacts=[], open_gaps=[],
        verification={"type": "command", "command": "true", "method": "ran it", "result": "pass", "details": "ok"},
    )
    save_draft(contract_id, draft)

    code = cli.cmd_finalize(argparse.Namespace(draft_id=contract_id, agent_id=None, json=True))

    report = _stored(contract_id)["evidence_report"]
    assert code == 0
    assert [entry.split(" (exit")[0] for entry in report["commands_run"]] == [
        "git status", "ls /repo", ANNOTATION_MARK + "terraform apply",
    ]
    assert report["files_checked"] == ["/repo/a.py"]
    assert read_spool.read(agent) == []


def _spool_write_verdict(tool: str, tool_input: dict, harness_agent_id: str | None, cwd: str = "/repo") -> str:
    """'refused' when the call is denied naming the read spool; otherwise what the hook answered."""
    adapter = ClaudeCodeAdapter()
    event = {
        "hook_event_name": "PreToolUse", "session_id": "ses-parent", "cwd": cwd,
        "tool_name": tool, "tool_input": tool_input, "tool_use_id": f"toolu-{uuid.uuid4().hex[:8]}",
    }
    if harness_agent_id:
        event.update(agent_id=harness_agent_id, agent_type="developer")
    output = adapter.adapt_pre_tool_use(adapter.parse_event(json.dumps(event))).output
    answer = json.dumps(output) if not isinstance(output, str) else output
    if "read spool" in answer:
        return "refused"
    return "delegated" if "DELEGATION REQUIRED" in answer else answer


def test_the_read_spool_refusal_holds_for_a_primary_role_past_delegate_mode(tmp_path):
    from gaia.contract import read_spool
    from adapters.tool_policy import ToolPolicy

    agent = _agent()
    _born_row(agent, agent)
    _read("/repo/a.py", agent)
    spool = str(read_spool.spool_path(agent))

    for tool_input in ({"file_path": spool, "content": "{}\n"}, {"file_path": spool, "old_string": "a", "new_string": "b"}):
        verdict = ToolPolicy()._write_edit_verdict("Write", tool_input, is_subagent=False)
        assert verdict.decision == "deny"
        assert "read spool" in verdict.reason


@pytest.mark.parametrize("subagent", [True, False], ids=["subagent", "main_session"])
def test_the_read_spool_is_a_protected_path_no_tool_but_the_recorder_writes(tmp_path, subagent):
    from gaia.contract import read_spool
    from gaia.contract.tool_record import ANNOTATION_MARK

    agent = _agent()
    contract_id = _born_row(agent, agent)
    _read("/repo/a.py", agent)
    spool = read_spool.spool_path(agent)
    line = json.dumps({"tool": "Read", "path": "/repo/forged.py"})
    copied = tmp_path / "forged.jsonl"
    copied.write_text(line + "\n")
    attempts = {
        "write_tool": ("Write", {"file_path": str(spool), "content": line + "\n"}),
        "edit_tool": ("Edit", {"file_path": str(spool), "old_string": "a.py", "new_string": "forged.py"}),
    }
    if subagent:
        attempts.update({
            "printf_append": ("Bash", {"command": f"printf '%s\\n' '{line}' >> {spool}"}),
            "tee_append": ("Bash", {"command": f"tee -a {spool}"}),
            "cp_over": ("Bash", {"command": f"cp {copied} {spool}"}),
        })

    verdicts = {
        label: _spool_write_verdict(tool, tool_input, agent if subagent else None)
        for label, (tool, tool_input) in attempts.items()
    }

    assert verdicts == {label: "refused" if subagent else "delegated" for label in attempts}
    report = _stamp(_envelope(agent), contract_id)["evidence_report"]
    assert report["files_checked"] == ["/repo/a.py"]
    assert not any(entry.startswith(ANNOTATION_MARK) for entry in report["files_checked"])


@pytest.fixture
def data_dir_under_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("GAIA_DATA_DIR", str(home / ".gaia"))


def _spool_spellings(spool: Path, line: str) -> dict:
    """Each way a shell reaches the spool without writing its absolute path: name -> (command, cwd)."""
    spool_dir = spool.parent
    below_home = spool.relative_to(Path.home())
    append = f"printf '%s\\n' '{line}' >>"
    return {
        "home_variable": (f"{append} $HOME/{below_home}", "/repo"),
        "home_variable_braced": (f"{append} ${{HOME}}/{below_home}", "/repo"),
        "home_variable_quoted": (f'{append} "$HOME/{below_home}"', "/repo"),
        "tee_home_variable": (f"echo '{line}' | tee -a $HOME/{below_home}", "/repo"),
        "cp_home_variable": (f"cp /dev/null $HOME/{below_home}", "/repo"),
        "relative_to_cwd": (f"{append} {spool.name}", str(spool_dir)),
        "relative_dot_to_cwd": (f"{append} ./{spool.name}", str(spool_dir)),
        "relative_from_parent": (f"{append} {spool_dir.name}/{spool.name}", str(spool_dir.parent)),
        "after_cd": (f"cd {spool_dir} && {append} {spool.name}", "/repo"),
    }


def test_a_shell_spelling_of_the_read_spool_that_hides_its_path_is_still_refused(data_dir_under_home):
    from gaia.contract import read_spool

    agent = _agent()
    _born_row(agent, agent)
    _read("/repo/a.py", agent)
    spool = read_spool.spool_path(agent)
    line = json.dumps({"tool": "Read", "path": "/repo/forged.py"})

    verdicts = {
        name: _spool_write_verdict("Bash", {"command": command}, agent, cwd=cwd)
        for name, (command, cwd) in _spool_spellings(spool, line).items()
    }

    assert verdicts == {name: "refused" for name in verdicts}
    assert read_spool.read(agent) == ["/repo/a.py"]


def test_a_navigation_chain_that_ends_in_the_read_spool_is_still_refused(data_dir_under_home):
    from gaia.contract import read_spool

    agent = _agent()
    _born_row(agent, agent)
    _read("/repo/a.py", agent)
    spool = read_spool.spool_path(agent)
    spool_dir = spool.parent
    below_home = spool_dir.relative_to(Path.home())
    append = f"printf x >> {spool.name}"
    chains = {
        "home_variable_then_cd": f"cd $HOME/{below_home.parent} && cd {spool_dir.name} && {append}",
        "pushd_absolute": f"pushd {spool_dir} && {append}",
        "pushd_tilde": f"pushd ~/{below_home} && {append}",
        "cd_dash": f"cd {spool_dir} && cd /tmp && cd - && {append}",
        "popd": f"pushd {spool_dir} && popd && {append}",
    }

    verdicts = {name: _spool_write_verdict("Bash", {"command": command}, agent) for name, command in chains.items()}

    unknown_directory = {"cd_dash", "popd"}
    assert {n: v for n, v in verdicts.items() if n not in unknown_directory} == {
        n: "refused" for n in chains if n not in unknown_directory
    }
    assert all("[PROTECTED_PATH]" in verdicts[n] for n in unknown_directory), verdicts
    assert read_spool.read(agent) == ["/repo/a.py"]


def test_a_read_from_inside_the_read_spool_directory_is_allowed_and_a_write_names_its_file(data_dir_under_home):
    from gaia.contract import read_spool

    agent = _agent()
    _born_row(agent, agent)
    _read("/repo/a.py", agent)
    spool = read_spool.spool_path(agent)
    spool_dir = str(spool.parent)

    reads = {c: _spool_write_verdict("Bash", {"command": c}, agent, cwd=spool_dir) for c in ("ls", f"cat {spool.name}")}
    write = _spool_write_verdict("Bash", {"command": f"printf x >> {spool.name}"}, agent, cwd=spool_dir)

    assert "refused" not in reads.values()
    assert write == "refused"


def test_a_write_beside_the_read_spool_is_not_mistaken_for_it(data_dir_under_home):
    from gaia.contract import read_spool

    agent = _agent()
    _born_row(agent, agent)
    _read("/repo/a.py", agent)
    spool_dir = read_spool.spool_dir()
    neighbours = {
        "home_variable": ("printf x >> $HOME/notes.txt", "/repo"),
        "relative_in_other_cwd": (f"printf x >> {read_spool.spool_path(agent).name}", "/repo"),
        "relative_beside_dir": ("printf x >> notes.txt", str(spool_dir.parent)),
    }

    verdicts = {
        name: _spool_write_verdict("Bash", {"command": command}, agent, cwd=cwd)
        for name, (command, cwd) in neighbours.items()
    }

    assert "refused" not in verdicts.values()


def _cut(contract_id: str, agent: str, cut_reason: str) -> None:
    from gaia.store.writer import finalize_agent_contract_handoff

    finalize_agent_contract_handoff(
        contract_id=contract_id, agent_id=agent, workspace="me", agent_state="IN_PROGRESS",
        raw_handoff_json=json.dumps({"degraded": True}), cut_reason=cut_reason,
    )


@pytest.mark.parametrize("cut_reason", ["reaped", "backstop_capture", "salvaged_truncation"])
def test_a_turn_resumed_after_a_cut_reads_only_its_own_calls(cut_reason):
    from gaia.contract import read_spool

    agent = _agent()
    cut_contract = _born_row(agent, agent)
    _bash("echo cut-turn", agent)
    _read("/repo/cut.py", agent)
    _cut(cut_contract, agent, cut_reason)

    cut_row = _stored(cut_contract)
    assert cut_row["harness_record"]["harness_agent_id"] == agent
    assert cut_row["harness_record"]["through_event_id"] > 0
    assert cut_row["evidence_report"]["files_checked"] == ["/repo/cut.py"]
    assert read_spool.read(agent) == []

    resumed = _born_row(agent, agent)
    _bash("echo after", agent)
    _read("/repo/after.py", agent)
    report = _stamp(_envelope(agent), resumed)["evidence_report"]

    assert [entry.split(" (exit")[0] for entry in report["commands_run"]] == ["echo after"]
    assert report["files_checked"] == ["/repo/after.py"]


def test_a_turn_resumed_after_a_salvaged_draft_reads_only_its_own_calls():
    from adapters.subagent_stop_core import salvage_truncated_draft
    from gaia.contract.drafts import save_draft

    agent = _agent()
    cut_contract = _born_row(agent, agent)
    save_draft(cut_contract, _envelope(agent))
    _bash("echo cut-turn", agent)
    _read("/repo/cut.py", agent)
    outcome = salvage_truncated_draft(
        parsed_contract=None, task_info={"agent_id": agent, "agent": "developer", "workspace": "me"},
        session_id="ses-parent",
    )
    assert outcome["created"] is True

    resumed = _born_row(agent, agent)
    _bash("echo after", agent)
    report = _stamp(_envelope(agent), resumed)["evidence_report"]

    assert [entry.split(" (exit")[0] for entry in report["commands_run"]] == ["echo after"]
    assert report["files_checked"] == []


def test_a_url_password_or_private_key_never_reaches_the_tool_record():
    password = "Pa55w0rd" + "XyzQ"
    agent = _agent()
    _born_row(agent, agent)
    _bash(f"cat > /tmp/app.env <<'EOF'\nDATABASE_URL=postgres://app:{password}@db.internal:5432/app\nEOF", agent,
          stdout="")
    body = "MIIEowIBAAKCAQEA" + "Zk9" * 800
    _bash("cat key.pem", agent, stdout=f"-----BEGIN RSA PRIVATE KEY-----\n{body}\n-----END RSA PRIVATE KEY-----\n")

    payloads = _payloads()
    assert password not in payloads
    assert "MIIEowIBAAKCAQEA" not in payloads and "Zk9Zk9Zk9" not in payloads


def test_a_token_straddling_the_excerpt_window_leaves_no_prefix():
    agent = _agent()
    _born_row(agent, agent)
    closed = f"-----BEGIN RSA PRIVATE KEY-----\n{'Q' * 1900}\n-----END RSA PRIVATE KEY-----\n"
    _bash("cat mixed.txt", agent, stdout=closed + "z" * (2000 - len(closed) - 20) + GITHUB_TOKEN)

    assert GITHUB_TOKEN[:20] not in _payloads()


def test_a_spool_that_reached_its_bound_finalizes_with_one_truncation_marker(monkeypatch):
    from gaia.contract import read_spool

    monkeypatch.setattr(read_spool, "MAX_SPOOL_BYTES", 1024)
    agent = _agent()
    contract_id = _born_row(agent, agent)
    for n in range(200):
        _read(f"/repo/{n}.py", agent)

    files = _stamp(_envelope(agent), contract_id)["evidence_report"]["files_checked"]

    assert files[-1] == read_spool.TRUNCATION_MARK
    assert files.count(read_spool.TRUNCATION_MARK) == 1
    assert 0 < len(files) - 1 < 200
