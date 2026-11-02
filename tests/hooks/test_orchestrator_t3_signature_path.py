"""A main-session T3 reaches a signature the orchestrator can present and use, end to end.

Each step runs where it runs in Claude Code: the orchestrator's Bash calls and
its AskUserQuestion call go through the adapter's real PreToolUse and
PostToolUse entry points, and every ``gaia`` call the orchestrator makes runs
as a subprocess with the environment its shell has (``CLAUDE_CODE_SESSION_ID``,
no ``GAIA_DISPATCH_AGENT``).
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys

from tests.hooks.test_terminal_event_close import (  # noqa: F401 - the fixture is used by name
    SESSION,
    _adapter,
    _REPO_ROOT,
    host,
)

T3 = "gaia scan"
PHRASES = {
    "--what": "Rescan the workspace",
    "--question": "Rescan it now?",
    "--does": "Rescans the workspace projects.",
    "--impact": "Rewrites the project context rows.",
    "--rollback": "Run the scan again after fixing the source.",
    "--verification": "gaia context show",
    "--shared-state": "Yes: the project context rows.",
}


def _main_session(adapter, event_name: str, tool_name: str, tool_input: dict, tool_use_id: str,
                  repo: str, **fields):
    payload = {
        "session_id": SESSION,
        "transcript_path": "/nonexistent/transcript.jsonl",
        "cwd": repo,
        "permission_mode": "default",
        "hook_event_name": event_name,
        "tool_name": tool_name,
        "tool_input": tool_input,
        "tool_use_id": tool_use_id,
        **fields,
    }
    return adapter.parse_event(json.dumps(payload))


def _bash(adapter, command: str, tool_use_id: str, repo: str) -> dict:
    response = adapter.adapt_pre_tool_use(
        _main_session(adapter, "PreToolUse", "Bash", {"command": command}, tool_use_id, repo)
    )
    assert response.exit_code == 0, response.output
    return response.output.get("hookSpecificOutput", {}) if isinstance(response.output, dict) else {}


def _gaia(host, args: list[str]) -> subprocess.CompletedProcess:
    env = {key: value for key, value in os.environ.items()
           if key not in ("GAIA_DB", "GAIA_DISPATCH_AGENT", "GAIA_HOST_SESSION_ID")
           and not key.startswith("CLAUDE")}
    env["GAIA_DATA_DIR"] = str(host["db"].parent)
    env["CLAUDE_CODE_SESSION_ID"] = SESSION
    return subprocess.run(
        [sys.executable, str(_REPO_ROOT / "bin" / "gaia"), *args],
        cwd=host["repo"], env=env, capture_output=True, text=True, timeout=180,
    )


def _phrased(request_line: str) -> list[str]:
    """The request line the deny printed, with each ``<...>`` placeholder replaced by a phrase."""
    words = shlex.split(request_line)
    for index, word in enumerate(words):
        if word.startswith("<"):
            words[index] = PHRASES[words[index - 1]]
    return words


def test_a_main_session_t3_is_signed_through_the_orchestrator_question(host, tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "gaia").symlink_to(_REPO_ROOT / "bin" / "gaia")
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}")
    adapter, repo = _adapter(), host["repo"]

    denied = _bash(adapter, T3, "toolu_t3", repo)
    assert denied["permissionDecision"] == "deny", denied
    reason = denied["permissionDecisionReason"]
    assert re.search(r"approval_id: P-[0-9a-f]+", reason), reason
    request_line = next(line.strip() for line in reason.splitlines()
                        if line.strip().startswith("gaia approvals request-set"))
    assert "--agent-id claude-code-primary" in request_line

    words = _phrased(request_line)
    assert _bash(adapter, shlex.join(words), "toolu_request", repo).get("permissionDecision") != "deny"
    requested = _gaia(host, words[1:] + ["--json"])
    assert requested.returncode == 0, requested.stdout + requested.stderr
    approval_id = json.loads(requested.stdout.strip().splitlines()[-1])["approval_id"]

    question = f"gaia approvals question {approval_id}"
    assert _bash(adapter, question, "toolu_render", repo).get("permissionDecision") != "deny"
    rendered = _gaia(host, ["approvals", "question", approval_id])
    assert rendered.returncode == 0, rendered.stdout + rendered.stderr
    questions = json.loads(rendered.stdout)["questions"]

    asked = adapter.adapt_pre_tool_use(_main_session(
        adapter, "PreToolUse", "AskUserQuestion", {"questions": questions}, "toolu_ask", repo,
    ))
    assert asked.output == {}, asked.output
    answers = {q["question"]: next(o["label"] for o in q["options"] if o["label"].startswith("Approve"))
               for q in questions}
    adapter.adapt_post_tool_use(_main_session(
        adapter, "PostToolUse", "AskUserQuestion", {"questions": questions}, "toolu_ask", repo,
        tool_response={"questions": questions, "answers": answers},
    ))

    assert _bash(adapter, T3, "toolu_retry", repo).get("permissionDecision") != "deny"
    adapter.adapt_post_tool_use(_main_session(
        adapter, "PostToolUse", "Bash", {"command": T3}, "toolu_retry", repo,
        tool_response={"stdout": "", "stderr": "", "interrupted": False, "isImage": False},
    ))
    again = _bash(adapter, T3, "toolu_again", repo)
    assert again["permissionDecision"] == "deny", "the signature runs the command once"
