"""A SubagentStart only receives bridge entries its own session wrote.

Two Claude Code sessions share one Gaia temp root. The context cache
(PreToolUse:Agent -> SubagentStart) and the resume map
(PreToolUse:SendMessage -> SubagentStart) are written per session; a start in
one session must never be handed the events digest or the resumed draft of
the other, even when it has nothing of its own to receive.

Driven through the real ``hooks/subagent_start.py`` entrypoint in a
subprocess, with ``GAIA_DATA_DIR`` isolating the temp root and the draft
store, so what is asserted is what the host would inject.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
HOOKS_DIR = _REPO_ROOT / "hooks"
SUBAGENT_START_HOOK = HOOKS_DIR / "subagent_start.py"
CONTRACT_CLI = _REPO_ROOT / "bin" / "cli" / "contract.py"

if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))

from adapters.claude_code import ClaudeCodeAdapter  # noqa: E402
from tests.fixtures.agent_ids import valid_agent_id  # noqa: E402

SESSION_A = "sess-isolation-a"
SESSION_B = "sess-isolation-b"
AGENT_TYPE = "developer"
DESCRIPTION = "Fix the parser"
DIGEST_OF_A = "EVENTS-DIGEST-WRITTEN-BY-SESSION-A"
RESUMED_AGENT_OF_A = valid_agent_id("a5e55a0a")


@pytest.fixture()
def two_sessions(tmp_path, monkeypatch):
    """One Gaia data root shared by both sessions; the adapter's writers are
    pointed at the same temp dirs the hook subprocess resolves from it."""
    data_dir = tmp_path / "gaia_data"
    gaia_tmp = data_dir / "tmp"
    monkeypatch.setattr(
        ClaudeCodeAdapter, "CONTEXT_CACHE_DIR", gaia_tmp / "gaia-context-cache",
    )
    monkeypatch.setattr(
        ClaudeCodeAdapter, "RESUME_MAP_CACHE_DIR", gaia_tmp / "gaia-contract-resume-map",
    )
    (tmp_path / ".claude").mkdir()

    env = dict(os.environ)
    env["GAIA_DATA_DIR"] = str(data_dir)
    for inherited in ("GAIA_DB", "GAIA_DB_PATH", "CLAUDE_PLUGIN_ROOT"):
        env.pop(inherited, None)
    return env, tmp_path


def _subagent_start(env: dict, cwd: Path, session_id: str) -> str:
    """Run the real SubagentStart hook and return its additionalContext ('' if none)."""
    result = subprocess.run(
        [sys.executable, str(SUBAGENT_START_HOOK)],
        input=json.dumps({
            "hook_event_name": "SubagentStart",
            "session_id": session_id,
            "agent_type": AGENT_TYPE,
            "task_description": DESCRIPTION,
        }),
        capture_output=True,
        text=True,
        env=env,
        cwd=str(cwd),
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)["hookSpecificOutput"]
    assert output["hookEventName"] == "SubagentStart"
    return output.get("additionalContext", "")


def _init_draft(env: dict, cwd: Path, agent_id: str) -> str:
    result = subprocess.run(
        [sys.executable, str(CONTRACT_CLI), "init", "--agent-id", agent_id, "--json"],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(cwd),
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)["draft_id"]


def test_start_never_receives_the_events_digest_of_another_session(two_sessions):
    env, cwd = two_sessions
    ClaudeCodeAdapter()._cache_context_for_subagent(
        SESSION_A, AGENT_TYPE, DIGEST_OF_A, task_description=DESCRIPTION,
    )

    foreign = _subagent_start(env, cwd, SESSION_B)
    own = _subagent_start(env, cwd, SESSION_A)

    assert DIGEST_OF_A not in foreign
    assert DIGEST_OF_A in own


def test_start_never_receives_the_resumed_draft_of_another_session(two_sessions):
    env, cwd = two_sessions
    draft_id = _init_draft(env, cwd, RESUMED_AGENT_OF_A)
    ClaudeCodeAdapter()._cache_resume_mapping(SESSION_A, RESUMED_AGENT_OF_A)

    foreign = _subagent_start(env, cwd, SESSION_B)
    own = _subagent_start(env, cwd, SESSION_A)

    assert draft_id not in foreign
    assert draft_id in own
