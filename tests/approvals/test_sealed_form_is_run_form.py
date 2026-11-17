"""A signed set item is matched against the command the host runs, through the Claude Code pre and post hooks."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
for _path in (_ROOT, _ROOT / "hooks"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from gaia.approvals import core  # noqa: E402
from gaia.store import writer  # noqa: E402

SESSION = "ses-sealed-form"
AGENT_ID = "a0f1e2d3c4b5a6978"
AGENT_TYPE = "developer"
MERGE = "gh pr merge 41 --squash"
REDIRECTS = [" > {tmp}/merge.log", " &> {tmp}/merge.log", " < {tmp}/body.txt"]


@pytest.fixture
def host(tmp_path, monkeypatch):
    from modules.core.paths import clear_path_cache

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("GAIA_DATA_DIR", str(data_dir))
    monkeypatch.delenv("GAIA_DB", raising=False)
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
    (tmp_path / "body.txt").write_text("hello\n")
    return {"tmp": str(tmp_path), "repo": str(tmp_path / "repo"), "shell": str(tmp_path / "shell"),
            "db": data_dir / "gaia.db", "data": data_dir}


def _approve(commands, cwd):
    approval_id = core.request_command_set(
        [{"command": c, "cwd": cwd, "expect_exit": [], "does": "Merges it.", "impact": "Lands."}
         for c in commands],
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
    """PreToolUse then, when allowed, PostToolUse with exit 0; return (decision, reason, command the host runs)."""
    adapter, event = _event("PreToolUse", command, tool_use_id, cwd)
    output = adapter.adapt_pre_tool_use(event).output
    decision = (output or {}).get("hookSpecificOutput", {}) if isinstance(output, dict) else {}
    verdict = decision.get("permissionDecision", "allow")
    runs = (decision.get("updatedInput") or {}).get("command") or command
    if verdict == "allow":
        adapter, event = _event(
            "PostToolUse", command, tool_use_id, cwd, duration_ms=40,
            tool_response={"stdout": "", "stderr": "", "interrupted": False, "isImage": False},
        )
        adapter.adapt_post_tool_use(event)
    return verdict, decision.get("permissionDecisionReason") or "", runs


def _grant(db, approval_id):
    con = sqlite3.connect(db)
    try:
        return con.execute(
            "SELECT status, next_index FROM approval_grants WHERE approval_id = ?", (approval_id,),
        ).fetchone()
    finally:
        con.close()


@pytest.mark.parametrize("suffix", REDIRECTS)
def test_a_command_signed_with_a_redirect_runs_under_its_own_signature(host, suffix):
    command = MERGE + suffix.format(tmp=host["tmp"])
    approval_id = _approve([command], host["repo"])

    verdict, reason, runs = _run(command, "tu-1", host["repo"])

    assert verdict == "allow", reason
    assert runs.endswith(command)
    assert _grant(host["db"], approval_id) == ("CONSUMED", 1)


@pytest.mark.parametrize("suffix", REDIRECTS)
def test_a_signature_without_a_redirect_refuses_the_run_with_one_added(host, suffix):
    approval_id = _approve([MERGE], host["repo"])

    verdict, reason, _ = _run(MERGE + suffix.format(tmp=host["tmp"]), "tu-1", host["repo"])

    assert verdict == "deny", reason
    assert _grant(host["db"], approval_id) == ("PENDING", 0)


def test_a_stdin_file_changed_after_signing_is_refused(host):
    command = f"{MERGE} < {host['tmp']}/body.txt"
    approval_id = _approve([command], host["repo"])
    Path(host["tmp"], "body.txt").write_text("rewritten after the signature\n")

    verdict, reason, _ = _run(command, "tu-1", host["repo"])

    assert verdict == "deny"
    assert "changed since it was signed" in reason, reason
    assert _grant(host["db"], approval_id) == ("PENDING", 0)


def test_a_signed_step_that_runs_free_advances_the_set(host):
    entry = Path(host["data"]) / "scratch" / AGENT_ID
    (entry / "x").mkdir(parents=True)
    remove = f"rm -rf {entry}/x"
    approval_id = _approve([remove, MERGE], host["repo"])

    elsewhere = _run(remove, "tu-0", host["shell"])
    assert elsewhere[0] == "allow" and _grant(host["db"], approval_id) == ("PENDING", 0), elsewhere

    first = _run(remove, "tu-1", host["repo"])
    second = _run(MERGE, "tu-2", host["repo"])

    assert first[0] == "allow", first
    assert second[0] == "allow", second
    assert _grant(host["db"], approval_id) == ("CONSUMED", 2)
