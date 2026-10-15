"""Every shell channel a host offers passes the same pre-tool gate, and an
unknown tool that carries a command is refused.

Claude Code's PowerShell tool is named ``PowerShell`` and sends its command
in ``tool_input.command`` with the same fields as Bash (tools-reference,
"PowerShell tool"). OpenCode's shell tool is ``bash`` in released builds and
``shell`` in the core rewrite (packages/core/src/tool/plugin/shell.ts), both
with ``command``. The probes are Windows-shaped commands run on Linux.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
for _path in (REPO_ROOT, REPO_ROOT / "hooks"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from adapters.claude_code import ClaudeCodeAdapter  # noqa: E402
from adapters.opencode import OpenCodeAdapter  # noqa: E402

SESSION = "ses-shell-channel-gate"
AGENT_ID = "a" + "7" * 16
AGENT_TYPE = "developer"
DESTRUCTIVE = "Remove-Item -Recurse -Force C:/x"
READ_ONLY = "Get-ChildItem C:/x"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "gaia_data"))
    monkeypatch.setenv("GAIA_WORKSPACE", "me")
    monkeypatch.setattr("gaia.store.writer.is_harness_session_bound", lambda _session: True)
    from modules.security import mutative_verbs, tiers

    mutative_verbs.detect_mutative_command.cache_clear()
    mutative_verbs._detect_mutative_command.cache_clear()
    tiers._classify_command_tier_cached.cache_clear()


def _claude(tool_name: str, tool_input: dict) -> dict:
    payload = {
        "hook_event_name": "PreToolUse",
        "session_id": SESSION,
        "tool_name": tool_name,
        "tool_input": tool_input,
        "tool_use_id": f"toolu-{tool_name}",
        "cwd": "/",
        "agent_id": AGENT_ID,
        "agent_type": AGENT_TYPE,
    }
    adapter = ClaudeCodeAdapter()
    response = adapter.adapt_pre_tool_use(adapter.parse_event(json.dumps(payload)))
    if isinstance(response.output, str):
        return {"decision": "deny", "reason": response.output, "approval_id": response.approval_id}
    specific = response.output.get("hookSpecificOutput", {})
    return {
        "decision": specific.get("permissionDecision", "allow" if response.exit_code == 0 else "deny"),
        "reason": specific.get("permissionDecisionReason", ""),
        "updated_command": (specific.get("updatedInput") or {}).get("command"),
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
    return {"decision": output.get("action"), "approval_id": output.get("approval_id")}


def test_powershell_destructive_command_is_classified_like_its_bash_twin():
    bash = _claude("Bash", {"command": DESTRUCTIVE})
    powershell = _claude("PowerShell", {"command": DESTRUCTIVE})

    assert bash["decision"] in ("deny", "ask"), bash
    assert powershell["decision"] == bash["decision"], (powershell, bash)
    assert (powershell["approval_id"] is None) == (bash["approval_id"] is None), (powershell, bash)


def test_powershell_read_only_command_passes_with_identity_in_powershell_syntax():
    verdict = _claude("PowerShell", {"command": READ_ONLY})

    assert verdict["decision"] == "allow", verdict
    assert verdict["updated_command"].startswith("$env:GAIA_DISPATCH_AGENT='developer'; "), verdict
    assert verdict["updated_command"].endswith(READ_ONLY), verdict


def test_unknown_tool_with_a_command_is_refused_on_both_hosts():
    claude = _claude("FutureShell", {"command": "rm -rf /srv/data"})
    opencode = _opencode("futureshell", {"command": "rm -rf /srv/data"})

    assert claude["decision"] == "deny", claude
    assert opencode["decision"] in ("deny", "block"), opencode


def test_opencode_shell_tool_is_gated_like_bash():
    bash = _opencode("bash", {"command": DESTRUCTIVE})
    shell = _opencode("shell", {"command": DESTRUCTIVE})

    assert bash["decision"] != "allow", bash
    assert shell["decision"] == bash["decision"], (shell, bash)
    assert (shell["approval_id"] is None) == (bash["approval_id"] is None), (shell, bash)


def test_monitor_command_is_classified_like_its_bash_twin():
    bash = _claude("Bash", {"command": "rm -rf /srv/data"})
    monitor = _claude("Monitor", {"command": "rm -rf /srv/data", "description": "watch"})

    assert bash["decision"] in ("deny", "ask"), bash
    assert monitor["decision"] == bash["decision"], (monitor, bash)
    assert (monitor["approval_id"] is None) == (bash["approval_id"] is None), (monitor, bash)


def test_powershell_secret_read_is_refused_toward_the_bash_tool():
    command = "gcloud secrets versions access latest --secret=db"
    bash = _claude("Bash", {"command": command})
    powershell = _claude("PowerShell", {"command": command})

    assert bash["decision"] == "allow", bash
    assert powershell["decision"] == "deny", powershell
    assert "Bash tool" in powershell["reason"], powershell


def test_tools_the_catch_all_newly_reaches_keep_their_verdict():
    adapter = ClaudeCodeAdapter()
    for tool_name, tool_input in (
        ("TodoWrite", {"todos": []}),
        ("mcp__gaia__notes", {"query": "x"}),
    ):
        payload = {
            "hook_event_name": "PreToolUse", "session_id": SESSION, "cwd": "/",
            "tool_name": tool_name, "tool_input": tool_input,
        }
        response = adapter.adapt_pre_tool_use(adapter.parse_event(json.dumps(payload)))
        assert response.exit_code == 0, (tool_name, response.output)
        specific = (response.output or {}).get("hookSpecificOutput", {}) if isinstance(response.output, dict) else {}
        assert specific.get("permissionDecision") in (None, "allow"), (tool_name, response.output)


def test_post_tool_closes_a_powershell_approval_like_a_bash_one(monkeypatch):
    from types import SimpleNamespace

    closed = []
    pre_state = SimpleNamespace(
        tier="T3", start_time_epoch=0, command=DESTRUCTIVE,
        metadata={"consumed_approval_id": "P-" + "1" * 32},
    )
    monkeypatch.setattr("modules.core.state.get_hook_state", lambda *a, **k: pre_state)
    monkeypatch.setattr("gaia.approvals.core.close_call", lambda approval_id, **k: closed.append(approval_id) or "closed")
    monkeypatch.setattr("gaia.approvals.core.release_call", lambda approval_id, **k: closed.append(approval_id))

    adapter = ClaudeCodeAdapter()
    for tool_name in ("Bash", "PowerShell"):
        adapter.adapt_post_tool_use(adapter.parse_event(json.dumps({
            "hook_event_name": "PostToolUse", "session_id": SESSION, "cwd": "/",
            "tool_name": tool_name, "tool_use_id": f"toolu-{tool_name}",
            "tool_input": {"command": DESTRUCTIVE},
            "tool_response": {"stdout": "", "stderr": "", "interrupted": False},
        })))

    assert len(closed) == 2, closed


def _matches(matcher: str, tool_name: str) -> bool:
    """Evaluate a matcher as the hooks reference ("Matcher patterns") says Claude Code does."""
    if matcher in ("*", ""):
        return True
    if re.fullmatch(r"[A-Za-z0-9_\- ,|]*", matcher):
        return tool_name in {name.strip() for name in re.split(r"[|,]", matcher)}
    return re.search(matcher, tool_name) is not None


def _matchers(event: str) -> list[list[str]]:
    hooks = json.loads((REPO_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
    manifest = json.loads((REPO_ROOT / "build" / "gaia.manifest.json").read_text(encoding="utf-8"))
    return [
        [entry["matcher"] for entry in hooks[event]],
        [entry["matcher"] for entry in manifest["hooks"]["matchers"][event]],
    ]


@pytest.mark.parametrize("event, tools", [
    ("PreToolUse", ("Bash", "PowerShell", "Monitor", "FutureShell", "mcp__server__run")),
    ("PostToolUse", ("Bash", "PowerShell", "Monitor")),
    ("PostToolUseFailure", ("Bash", "PowerShell", "Monitor")),
])
def test_every_shell_channel_reaches_the_hooks(event, tools):
    for matchers in _matchers(event):
        for tool_name in tools:
            assert any(_matches(m, tool_name) for m in matchers), (event, tool_name, matchers)


@pytest.mark.parametrize("tool_name, expected", [
    ("TodoWrite", 0), ("Skill", 0), ("ToolSearch", 0), ("TaskCreate", 0), ("TaskUpdate", 0),
    ("Read", 1), ("Glob", 1), ("WebFetch", 1), ("Task", 1), ("Agent", 1), ("SendMessage", 1),
    ("AskUserQuestion", 1), ("Edit", 1), ("Bash", 1),
])
def test_known_tools_keep_their_prior_pre_tool_exposure(tool_name, expected):
    for matchers in _matchers("PreToolUse"):
        assert sum(_matches(m, tool_name) for m in matchers) == expected, (tool_name, matchers)
