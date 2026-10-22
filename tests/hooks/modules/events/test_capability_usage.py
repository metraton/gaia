"""Gaia's own CLI calls and skill loads are recorded in harness_events, through both hosts."""

from __future__ import annotations

import argparse
import importlib.machinery
import importlib.util
import json
import sqlite3
import sys
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
for _path in (REPO_ROOT, REPO_ROOT / "hooks"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from adapters.claude_code import ClaudeCodeAdapter  # noqa: E402
from adapters.opencode import OpenCodeAdapter  # noqa: E402
from modules.events.capability_usage import (  # noqa: E402
    CLI_INVOKED,
    SKILL_LOADED,
    SUBCOMMAND_TREE,
    gaia_invocations,
)

SESSION = "ses-capability-usage"
SUBAGENT_ID = "a" + "7" * 16
PRIVATE = "zebra-private-text"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setitem(globals(), "SESSION", f"ses-capability-usage-{uuid.uuid4().hex}")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "gaia_data"))
    monkeypatch.delenv("GAIA_DB", raising=False)
    monkeypatch.setenv("GAIA_WORKSPACE", "me")
    monkeypatch.setattr("gaia.store.writer.is_harness_session_bound", lambda _session: True)
    monkeypatch.setattr("modules.security.gaia_cli_only_guard.is_trusted_gaia_binary", lambda _binary: True)


def _rows(event_type: str) -> list[dict]:
    from gaia.paths.resolver import db_path

    path = db_path()
    if not path.exists():
        return []
    con = sqlite3.connect(path)
    try:
        found = con.execute(
            "SELECT agent, result, severity, payload FROM harness_events WHERE type = ? ORDER BY id",
            (event_type,),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()
    return [
        {"agent": agent, "result": result, "severity": severity, "meta": json.loads(payload or "{}")}
        for agent, result, severity, payload in found
    ]


def _claude(event: dict, *, agent_id: str = "", agent_type: str = "") -> dict:
    payload = {"session_id": SESSION, "cwd": "/", **event}
    if agent_id:
        payload.update(agent_id=agent_id, agent_type=agent_type)
    adapter = ClaudeCodeAdapter()
    parsed = adapter.parse_event(json.dumps(payload))
    if payload["hook_event_name"] == "PreToolUse":
        response = adapter.adapt_pre_tool_use(parsed)
    else:
        response = adapter.adapt_post_tool_use(parsed)
    if isinstance(response.output, str):
        return {"decision": "deny", "reason": response.output}
    specific = response.output.get("hookSpecificOutput", {})
    return {
        "decision": specific.get("permissionDecision", "allow" if response.exit_code == 0 else "deny"),
        "reason": specific.get("permissionDecisionReason", ""),
        "updated": specific.get("updatedInput"),
    }


def _claude_pre(command: str, *, tool_use_id: str = "toolu-1", **who) -> dict:
    return _claude({
        "hook_event_name": "PreToolUse", "tool_name": "Bash",
        "tool_input": {"command": command}, "tool_use_id": tool_use_id,
    }, **who)


def _claude_post(command: str, *, tool_use_id: str = "toolu-1", **who) -> None:
    _claude({
        "hook_event_name": "PostToolUse", "tool_name": "Bash",
        "tool_input": {"command": command}, "tool_use_id": tool_use_id,
        "tool_response": {"stdout": "out", "stderr": "", "interrupted": False},
    }, **who)


def _claude_failure(command: str, error: str, *, tool_use_id: str = "toolu-1") -> None:
    _claude({
        "hook_event_name": "PostToolUseFailure", "tool_name": "Bash",
        "tool_input": {"command": command}, "tool_use_id": tool_use_id, "error": error,
    })


def _claude_skill(tool_input: dict, **who) -> None:
    _claude({
        "hook_event_name": "PostToolUse", "tool_name": "Skill", "tool_input": tool_input,
        "tool_response": {"success": True}, "tool_use_id": "toolu-skill",
    }, **who)


def _opencode(event: str, tool: str, args: dict, **extra) -> dict:
    adapter = OpenCodeAdapter()
    parsed = adapter.parse_event(json.dumps({
        "event": event, "sessionID": SESSION, "callID": "call-1", "tool": tool, "args": args, **extra,
    }))
    if event == "tool.execute.before":
        return adapter.adapt_pre_tool_use(parsed).output
    return adapter.adapt_post_tool_use(parsed).output


def _parser_paths() -> set[str]:
    loader = importlib.machinery.SourceFileLoader("gaia_cli_entry", str(REPO_ROOT / "bin" / "gaia"))
    spec = importlib.util.spec_from_loader("gaia_cli_entry", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    parser = module._build_parser(module._discover_plugins())
    paths: set[str] = set()

    def walk(node, prefix):
        for action in node._actions:
            if isinstance(action, argparse._SubParsersAction):
                for name, child in action.choices.items():
                    paths.add(" ".join(prefix + (name,)))
                    walk(child, prefix + (name,))

    walk(parser, ())
    return paths


def test_the_verb_grammar_is_the_cli_parser_tree():
    declared = {
        f"{parent} {child}".strip() for parent, children in SUBCOMMAND_TREE.items() for child in children
    }

    assert declared == _parser_paths()


@pytest.mark.parametrize(("command", "expected"), (
    (f"gaia memory search '{PRIVATE}' --initiative gaia --limit=5", [("memory search", ("--initiative", "--limit"))]),
    ("gaia task gate list T-1 --json", [("task gate list", ("--json",))]),
    ("gaia session resume-point set --text x", [("session resume-point set", ("--text",))]),
    ("/opt/pkg/bin/gaia --json brief list", [("brief list", ("--json",))]),
    ("python3 /src/bin/gaia doctor", [("doctor", ())]),
    ("GAIA_DB=/tmp/x.db gaia paths db", [("paths db", ())]),
    ("export GAIA_DISPATCH_AGENT=developer TMPDIR=/t; gaia contract view", [("contract view", ())]),
    ("gaia memory show a && gaia query --last 3", [("memory show", ()), ("query", ("--last",))]),
    ("gaia", [("", ())]),
    ("gaia --help", [("", ("--help",))]),
    (f"gaia {PRIVATE} --x", [("", ("--x",))]),
    (f"gaia memory add --body '--{PRIVATE} words'", [("memory add", ("--body",))]),
    ("ls /usr/bin/gaia", []),
    ("echo gaia memory search", []),
    ("git status", []),
))
def test_an_invocation_is_named_by_its_verb_and_flag_names_only(command, expected):
    assert gaia_invocations(command) == expected


def test_an_orchestrator_read_is_recorded_once_with_its_outcome():
    command = f"gaia memory search '{PRIVATE}' --json"
    assert _claude_pre(command)["decision"] != "deny"
    assert _rows(CLI_INVOKED) == []

    _claude_post(command)

    [row] = _rows(CLI_INVOKED)
    assert row["agent"] == "gaia-orchestrator"
    assert row["meta"]["role"] == "orchestrator"
    assert row["meta"]["verb"] == "memory search"
    assert row["meta"]["flags"] == ["--json"]
    assert row["meta"]["session_id"] == SESSION
    assert (row["meta"]["outcome"], row["meta"]["exit_code"]) == ("allowed", 0)
    assert row["result"] == f"gaia memory search: allowed exit=0 session={SESSION}"
    assert PRIVATE not in json.dumps(row)


def test_a_subagent_call_is_recorded_from_the_command_it_asked_for():
    command = "gaia contract view --draft-id x"
    verdict = _claude_pre(command, agent_id=SUBAGENT_ID, agent_type="developer")
    rewritten = verdict["updated"]["command"]
    assert rewritten != command

    _claude_post(rewritten, agent_id=SUBAGENT_ID, agent_type="developer")

    [row] = _rows(CLI_INVOKED)
    assert row["agent"] == "developer"
    assert row["meta"]["role"] == "subagent"
    assert row["meta"]["agent_id"] == SUBAGENT_ID
    assert row["meta"]["verb"] == "contract view"


def test_a_failed_run_carries_the_exit_code_the_host_reports():
    _claude_pre("gaia brief show missing")

    _claude_failure("gaia brief show missing", "Exit code 3\nnot found")

    [row] = _rows(CLI_INVOKED)
    assert (row["meta"]["outcome"], row["meta"]["exit_code"]) == ("allowed", 3)
    assert row["severity"] == "warning"


def test_a_call_the_user_refused_at_the_host_prompt_is_recorded_as_refused():
    _claude_failure("gaia brief list", "The user doesn't want to proceed with this tool use.")

    [row] = _rows(CLI_INVOKED)
    assert (row["meta"]["outcome"], row["meta"]["exit_code"]) == ("refused", None)


@pytest.mark.parametrize(("command", "verb"), (
    (f"gaia memory add --type project --name n --body {PRIVATE}", "memory add"),
    ("gaia install", "install"),
))
def test_a_denied_orchestrator_call_is_recorded_at_the_refusal(command, verb):
    assert _claude_pre(command)["decision"] == "deny"

    [row] = _rows(CLI_INVOKED)
    assert row["agent"] == "gaia-orchestrator"
    assert (row["meta"]["verb"], row["meta"]["outcome"]) == (verb, "denied")
    assert PRIVATE not in json.dumps(row)


def test_a_command_that_is_not_gaia_leaves_no_row():
    _claude_pre("git status", agent_id=SUBAGENT_ID, agent_type="developer")
    _claude_post("git status", agent_id=SUBAGENT_ID, agent_type="developer")

    assert _rows(CLI_INVOKED) == []


def test_opencode_records_a_run_gaia_call():
    _opencode("tool.execute.after", "bash", {"command": f"gaia memory list --type {PRIVATE}"},
              result={"output": "rows"})

    [row] = _rows(CLI_INVOKED)
    assert row["meta"]["verb"] == "memory list"
    assert row["meta"]["session_id"] == SESSION
    assert (row["meta"]["outcome"], row["meta"]["exit_code"]) == ("allowed", 0)
    assert PRIVATE not in json.dumps(row)


def test_opencode_records_a_gaia_call_its_policy_denied():
    output = _opencode("tool.execute.before", "bash", {"command": "gaia memory search x; rm -rf /"})
    assert output["action"] == "deny"

    [row] = _rows(CLI_INVOKED)
    assert (row["meta"]["verb"], row["meta"]["outcome"]) == ("memory search", "denied")


def test_a_claude_code_skill_load_is_recorded_without_its_arguments():
    _claude_skill({"skill": "gaia:code-review", "args": PRIVATE})
    _claude_skill({"skill": "gaia:investigation"}, agent_id=SUBAGENT_ID, agent_type="gaia-system")

    orchestrator, specialist = _rows(SKILL_LOADED)
    assert (orchestrator["agent"], orchestrator["meta"]["skill"]) == ("gaia-orchestrator", "gaia:code-review")
    assert orchestrator["meta"]["session_id"] == SESSION
    assert (specialist["agent"], specialist["meta"]["role"]) == ("gaia-system", "subagent")
    assert specialist["meta"]["skill"] == "gaia:investigation"
    assert PRIVATE not in json.dumps(orchestrator)


def test_the_memory_skill_keeps_its_guard_event_beside_the_new_one():
    _claude_skill({"skill": "gaia:memory"})

    assert [row["meta"]["skill"] for row in _rows(SKILL_LOADED)] == ["gaia:memory"]
    assert len(_rows("memory.skill_loaded")) == 1
    assert _claude_pre("gaia memory add --type project --name n --body b")["decision"] != "deny"


def test_opencode_records_its_skill_tool():
    _opencode("tool.execute.after", "skill", {"name": "reminders"}, result={"output": "body"})

    [row] = _rows(SKILL_LOADED)
    assert (row["meta"]["skill"], row["meta"]["session_id"]) == ("reminders", SESSION)


def test_a_tool_that_loads_no_skill_leaves_no_row():
    _claude_pre("gaia brief list")
    _claude_post("gaia brief list")

    assert _rows(SKILL_LOADED) == []


def _broken_writer(monkeypatch):
    def explode(**_kwargs):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr("gaia.store.writer.write_harness_event", explode)


@pytest.mark.parametrize("command", ("gaia brief list", "gaia install"))
def test_a_failing_database_leaves_the_pre_verdict_unchanged(command, monkeypatch):
    expected = _claude_pre(command, tool_use_id="toolu-a")
    _broken_writer(monkeypatch)

    assert _claude_pre(command, tool_use_id="toolu-b") == expected


def test_a_failing_recorder_leaves_the_post_and_skill_paths_running(monkeypatch):
    import modules.events.capability_usage as usage

    def explode(*_args, **_kwargs):
        raise RuntimeError("parser bug")

    monkeypatch.setattr(usage, "gaia_invocations", explode)
    monkeypatch.setattr(usage, "_caller", explode)
    expected = _claude_pre("gaia install", tool_use_id="toolu-a")

    _claude_post("gaia brief list")
    _claude_skill({"skill": "gaia:memory"})

    assert _claude_pre("gaia install", tool_use_id="toolu-b") == expected
    assert _rows(CLI_INVOKED) == [] and _rows(SKILL_LOADED) == []
    assert len(_rows("memory.skill_loaded")) == 1


def test_an_unimportable_recorder_leaves_the_verdict_unchanged(monkeypatch):
    expected = _claude_pre("gaia install", tool_use_id="toolu-a")
    monkeypatch.setitem(sys.modules, "modules.events.capability_usage", None)

    assert _claude_pre("gaia install", tool_use_id="toolu-b") == expected
    _claude_post("gaia brief list")
    _claude_skill({"skill": "gaia:code-review"})


def test_harness_event_retention_prunes_the_new_types():
    from gaia.store.writer import prune_harness_events

    _claude_post("gaia brief list")
    _claude_skill({"skill": "gaia:code-review"})
    assert _rows(CLI_INVOKED) and _rows(SKILL_LOADED)

    prune_harness_events(cutoff_days=-1)

    assert _rows(CLI_INVOKED) == [] and _rows(SKILL_LOADED) == []
