"""Both hosts refuse sensitive reads alike and sign account writes alike (plan 76 task 13).

Claude Code reaches the shared policy through PreToolUse; OpenCode through its
adapter's policy lane, which now carries read, glob, grep and list. Reading or
listing a sensitive path is denied with the one reason and no approval, on
every tool; an account-path write through Write, Edit or apply_patch gets the
verdict a Bash write onto it gets: a request for a signature with phrases.
Every path is a stand-in under a temporary HOME.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
for _path in (REPO_ROOT, REPO_ROOT / "hooks"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from adapters.claude_code import ClaudeCodeAdapter  # noqa: E402
from adapters.opencode import OpenCodeAdapter  # noqa: E402

SESSION = "ses-sensitive-guard"
AGENT_ID = "a" + "5" * 16
AGENT_TYPE = "developer"
PHRASES_MARKER = "Nothing is shown to the user without your phrases."


@pytest.fixture()
def home(tmp_path, monkeypatch):
    fake = tmp_path / "home"
    (fake / ".ssh").mkdir(parents=True)
    (fake / ".ssh" / "standin_key").write_text("stand-in\n", encoding="utf-8")
    (fake / "project").mkdir()
    (fake / "project" / ".env").write_text("STANDIN=1\n", encoding="utf-8")
    (fake / "project" / ".env.example").write_text("STANDIN=\n", encoding="utf-8")
    (fake / "notes").mkdir()
    (fake / "notes" / "plain.txt").write_text("hello\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(fake))
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "gaia_data"))
    monkeypatch.setenv("GAIA_WORKSPACE", "me")
    # The child's Task binding is not what is measured here.
    monkeypatch.setattr("gaia.store.writer.is_harness_session_bound", lambda _session: True)
    from modules.security import mutative_verbs, tiers

    mutative_verbs.detect_mutative_command.cache_clear()
    mutative_verbs._detect_mutative_command.cache_clear()
    tiers._classify_command_tier_cached.cache_clear()
    return fake


# --------------------------------------------------------------------- hosts


def _claude(tool_name: str, tool_input: dict, *, subagent: bool = True) -> dict:
    payload = {
        "hook_event_name": "PreToolUse",
        "session_id": SESSION,
        "tool_name": tool_name,
        "tool_input": tool_input,
        "cwd": "/",
    }
    if subagent:
        payload.update({"agent_id": AGENT_ID, "agent_type": AGENT_TYPE})
    adapter = ClaudeCodeAdapter()
    response = adapter.adapt_pre_tool_use(adapter.parse_event(json.dumps(payload)))
    if isinstance(response.output, str):
        # A refusal: this host blocks on exit 2 with the text on stderr.
        return {"decision": "deny", "reason": response.output, "approval_id": response.approval_id}
    specific = response.output.get("hookSpecificOutput", {})
    return {
        "decision": specific.get("permissionDecision", "allow" if response.exit_code == 0 else "deny"),
        "reason": specific.get("permissionDecisionReason", ""),
        "approval_id": response.approval_id,
    }


def _opencode(tool: str, args: dict) -> dict:
    adapter = OpenCodeAdapter()
    event = adapter.parse_event(json.dumps({
        "event": "tool.execute.before",
        "sessionID": SESSION,
        "callID": f"call-{tool}",
        "agentID": AGENT_ID,
        "roleContext": {
            "role": AGENT_TYPE,
            "issuer": "opencode-runtime",
            "attestation": f"{SESSION}:{AGENT_TYPE}",
            "verified": True,
        },
        "tool": tool,
        "args": args,
    }))
    output = adapter.adapt_pre_tool_use(event).output
    return {
        "decision": output.get("action"),
        "reason": output.get("reason", ""),
        "approval_id": output.get("approval_id"),
    }


# ------------------------------------------------------------ sensitive reads


def _sensitive_calls(home: Path):
    key = str(home / ".ssh" / "standin_key")
    env = str(home / "project" / ".env")
    return {
        "claude": [
            ("Read", {"file_path": key}),
            ("Read", {"file_path": env}),
            ("Glob", {"pattern": "*", "path": str(home / ".ssh")}),
            ("Glob", {"pattern": ".ssh/*", "path": str(home)}),
            ("Grep", {"pattern": "STANDIN", "path": env}),
            ("Grep", {"pattern": "STANDIN", "path": str(home / "project"), "glob": ".env*"}),
            ("Bash", {"command": f"cat {key}"}),
            ("Bash", {"command": f"head -n 1 {env}"}),
            ("Bash", {"command": f"ls {home / '.ssh'}"}),
        ],
        "opencode": [
            ("read", {"filePath": key}),
            ("read", {"filePath": env}),
            ("glob", {"pattern": "*", "path": str(home / ".ssh")}),
            ("grep", {"pattern": "STANDIN", "path": str(home / "project"), "include": ".env*"}),
            ("list", {"path": str(home / ".ssh")}),
            ("bash", {"command": f"less {env}"}),
            ("bash", {"command": f"ls -la {home / '.ssh'}"}),
        ],
    }


def test_sensitive_path_guard_claude_code_refuses_every_read_tool_without_a_signature(home):
    for tool, tool_input in _sensitive_calls(home)["claude"]:
        verdict = _claude(tool, tool_input)
        assert verdict["decision"] == "deny", (tool, tool_input, verdict)
        assert "[SENSITIVE_PATH]" in verdict["reason"], (tool, verdict)
        assert verdict["approval_id"] is None, (tool, verdict)


def test_sensitive_path_guard_opencode_refuses_every_read_tool_without_a_signature(home):
    for tool, args in _sensitive_calls(home)["opencode"]:
        verdict = _opencode(tool, args)
        assert verdict["decision"] == "deny", (tool, args, verdict)
        assert "[SENSITIVE_PATH]" in verdict["reason"], (tool, verdict)
        assert verdict["approval_id"] is None, (tool, verdict)


def test_sensitive_path_guard_both_hosts_give_the_same_reason(home):
    key = str(home / ".ssh" / "standin_key")
    claude = _claude("Read", {"file_path": key})["reason"]
    opencode = _opencode("read", {"filePath": key})["reason"]
    assert claude.startswith("[SENSITIVE_PATH]")
    assert claude == opencode
    assert claude in _claude("Bash", {"command": f"cat {key}"})["reason"]
    assert claude in _opencode("bash", {"command": f"cat {key}"})["reason"]


def test_sensitive_path_guard_ordinary_reads_outside_the_project_pass_on_both_hosts(home):
    plain = str(home / "notes" / "plain.txt")
    template = str(home / "project" / ".env.example")
    scratch = str(home / ".gaia" / "scratch")
    for tool, tool_input in [
        ("Read", {"file_path": plain}), ("Read", {"file_path": template}),
        ("Glob", {"pattern": "**/*.txt", "path": str(home)}),
        ("Grep", {"pattern": "hello", "path": str(home / "notes")}),
        ("Read", {"file_path": f"{scratch}/notes.txt"}),
        ("Bash", {"command": f"cat {plain}"}), ("Bash", {"command": f"ls {home}"}),
    ]:
        assert _claude(tool, tool_input)["decision"] != "deny", (tool, tool_input)
    for tool, args in [
        ("read", {"filePath": plain}), ("read", {"filePath": template}),
        ("glob", {"pattern": "**/*.txt", "path": str(home)}),
        ("grep", {"pattern": "hello", "path": str(home / "notes")}),
        ("list", {"path": str(home / "notes")}),
    ]:
        assert _opencode(tool, args)["decision"] == "allow", (tool, args)


# --------------------------------------------------------------- account writes


def _signature_request(verdict: dict) -> bool:
    return (
        verdict["decision"] == "deny"
        and str(verdict["approval_id"] or "").startswith("P-")
        and PHRASES_MARKER in verdict["reason"]
    )


def test_sensitive_path_guard_claude_code_account_write_asks_the_signature_bash_asks(home):
    target = str(home / ".ssh" / "authorized_keys")
    bash = _claude("Bash", {"command": f"echo standin >> {target}"})
    assert _signature_request(bash), bash
    for tool, tool_input in [
        ("Write", {"file_path": target, "content": "standin"}),
        ("Edit", {"file_path": str(home / ".npmrc"), "old_string": "a", "new_string": "b"}),
    ]:
        verdict = _claude(tool, tool_input)
        assert _signature_request(verdict), (tool, verdict)


def test_sensitive_path_guard_opencode_account_write_asks_the_signature_bash_asks(home):
    target = str(home / ".ssh" / "authorized_keys")
    bash = _opencode("bash", {"command": f"echo standin >> {target}"})
    assert _signature_request(bash), bash
    patch = f"*** Begin Patch\n*** Add File: {home / '.pgpass'}\n+standin\n*** End Patch"
    for tool, args in [
        ("write", {"file_path": target, "content": "standin"}),
        ("edit", {"file_path": str(home / ".azure" / "config"), "oldString": "a", "newString": "b"}),
        ("apply_patch", {"patchText": patch}),
    ]:
        verdict = _opencode(tool, args)
        assert _signature_request(verdict), (tool, verdict)


def test_sensitive_path_guard_main_session_account_write_is_asked_like_bash(home):
    """Outside delegate mode the main session is asked inline, by either tool."""
    from adapters.tool_policy import ToolPolicy
    from modules.tools.bash_validator import validate_bash_command

    target = str(home / ".gitconfig")
    bash = validate_bash_command(f"echo standin >> {target}")
    write = ToolPolicy()._write_edit_verdict("Write", {"file_path": target}, is_subagent=False)
    assert bash.allowed is False and bash.approval_id is None
    assert write.consent is not None and write.consent.approval_id is None
    assert "grants access to your own account" in write.consent.reason


def test_sensitive_path_guard_ordinary_write_outside_the_project_stays_free(home):
    plain = str(home / "notes" / "new.txt")
    assert _claude("Write", {"file_path": plain, "content": "x"})["decision"] != "deny"
    assert _opencode("write", {"file_path": plain, "content": "x"})["decision"] == "allow"
