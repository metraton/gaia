"""
AC-18 -- resume persiste draft (M2, task T6, decision #8).

A contract draft must survive a Claude-Code resume (SendMessage): the draft
is NOT reset between resumes, and ``gaia contract view`` shows the fields
already written before the resume happened.

Per decision #3 ("el mapeo 'sesion de CC -> id de contrato' vive en el hook
adaptador"), the actual on-disk persistence is already 100% delivered by T5's
``gaia.contract.drafts`` (a resume changes nothing about where/how the draft
is stored -- it is simply read again). What T6 adds is the CC-specific
bridge in the hook adapter: at ``PreToolUse:SendMessage`` (the resume call)
the adapter learns which ``agent_id`` a CC session is resuming, and at
``SubagentStart`` (which fires right after, before the resumed agent sees
its first token) it looks up that agent's live draft and hands its id +
current state back -- so the resumed agent finds its OWN prior draft instead
of blindly re-``init``-ing a fresh (and therefore reset) one.

These tests exercise both halves:
    1. Storage persistence across a real "resume" hook sequence
       (PreToolUse:SendMessage -> SubagentStart), driven through
       ``ClaudeCodeAdapter`` directly (the T6 file lane).
    2. The CLI-observable outcome (``gaia contract view``) proving the draft
       was NOT reset -- same draft_id, same previously-written fields.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_CLI = _REPO_ROOT / "bin" / "cli" / "contract.py"
HOOKS_DIR = _REPO_ROOT / "hooks"

if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))

from adapters.claude_code import ClaudeCodeAdapter  # noqa: E402
from modules.core.paths import clear_path_cache  # noqa: E402

AGENT_ID = "a1234abcd"


def _run(args: list, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CONTRACT_CLI), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


@pytest.fixture()
def resume_env(tmp_path, monkeypatch):
    """Isolate: GAIA_DATA_DIR (draft storage), cwd/.claude (hook state), and
    the adapter's resume-mapping cache -- so this test never touches the
    developer's real /tmp caches or ~/.gaia."""
    data_dir = tmp_path / "gaia_data"
    monkeypatch.setenv("GAIA_DATA_DIR", str(data_dir))
    monkeypatch.delenv("GAIA_DB", raising=False)
    monkeypatch.delenv("GAIA_DB_PATH", raising=False)

    (tmp_path / ".claude").mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(tmp_path)
    clear_path_cache()

    resume_map_dir = tmp_path / "resume_map"
    monkeypatch.setattr(ClaudeCodeAdapter, "RESUME_MAP_CACHE_DIR", resume_map_dir)
    # Isolate the sibling context cache too so a stray real /tmp file from a
    # previous manual run can never leak a false cache hit into this test.
    monkeypatch.setattr(ClaudeCodeAdapter, "CONTEXT_CACHE_DIR", tmp_path / "context_cache")

    env = dict(os.environ)
    yield env
    clear_path_cache()


def _cli_init_and_set(env: dict) -> str:
    """Build a partial, IN_PROGRESS draft via the CLI (the agent's own turn,
    pre-resume). Returns the minted draft_id."""
    init = _run(["init", "--agent-id", AGENT_ID, "--json"], env)
    assert init.returncode == 0, f"stderr={init.stderr!r}"
    draft_id = json.loads(init.stdout)["draft_id"]

    set1 = _run(
        ["set", "agent_status.next_action", "keep investigating T6", "--draft-id", draft_id],
        env,
    )
    assert set1.returncode == 0, f"stderr={set1.stderr!r}"

    add1 = _run(
        ["add", "evidence_report.files_checked",
         "hooks/adapters/claude_code.py", "--draft-id", draft_id],
        env,
    )
    assert add1.returncode == 0, f"stderr={add1.stderr!r}"

    return draft_id


def _view(env: dict, draft_id: str) -> dict:
    view = _run(["view", "--draft-id", draft_id], env)
    assert view.returncode == 0, f"stderr={view.stderr!r}"
    return json.loads(view.stdout)


class TestDraftSurvivesResume:
    def test_draft_not_reset_by_resume_sequence(self, resume_env):
        """Full sequence: agent writes partial state, CC resumes the agent
        (SendMessage -> SubagentStart) -- the SAME draft, with the SAME
        fields, is what 'gaia contract view' reports afterwards."""
        env = resume_env
        draft_id = _cli_init_and_set(env)
        before = _view(env, draft_id)

        adapter = ClaudeCodeAdapter()
        session_id = "sess-resume-001"

        # PreToolUse:SendMessage -- the orchestrator resumes AGENT_ID.
        send_result = adapter._adapt_send_message(
            "SendMessage",
            {"to": AGENT_ID, "message": "Continue where you left off."},
            session_id=session_id,
        )
        assert send_result.exit_code == 0

        # SubagentStart -- fires for the resumed agent right after.
        start_result = adapter.adapt_subagent_start({
            "hook_event_name": "SubagentStart",
            "session_id": session_id,
        })

        after = _view(env, draft_id)

        # The draft on disk is untouched by the resume -- same id, same
        # previously-written fields. This is the core of AC-18: NOT reset.
        assert after["draft_id"] == before["draft_id"] == draft_id
        assert after["envelope"] == before["envelope"]
        assert after["envelope"]["agent_status"]["next_action"] == "keep investigating T6"
        assert after["envelope"]["evidence_report"]["files_checked"] == [
            "hooks/adapters/claude_code.py",
        ]

        # The resumed agent is handed its OWN prior draft back (not asked to
        # re-emit it): SubagentStart injects a hint naming this exact draft.
        assert start_result.context_injected is True
        assert draft_id in start_result.additional_context
        assert "IN_PROGRESS" in start_result.additional_context

    def test_two_resumes_of_the_same_session_both_see_the_latest_state(self, resume_env):
        """AC-19 adjacency: a session may resume the SAME agent more than
        once (N messages) -- the resume mapping must not be a one-shot
        cache; the second resume must see state written after the first."""
        env = resume_env
        draft_id = _cli_init_and_set(env)

        adapter = ClaudeCodeAdapter()
        session_id = "sess-resume-002"

        adapter._adapt_send_message(
            "SendMessage", {"to": AGENT_ID, "message": "go"}, session_id=session_id,
        )
        first = adapter.adapt_subagent_start({"session_id": session_id})
        assert draft_id in first.additional_context

        # More progress happens on the SAME draft between the two resumes.
        set2 = _run(
            ["set", "agent_status.next_action", "second pass", "--draft-id", draft_id], env,
        )
        assert set2.returncode == 0

        adapter._adapt_send_message(
            "SendMessage", {"to": AGENT_ID, "message": "go again"}, session_id=session_id,
        )
        second = adapter.adapt_subagent_start({"session_id": session_id})

        assert draft_id in second.additional_context
        assert "second pass" in second.additional_context
        assert "second pass" not in first.additional_context

    def test_resume_without_a_prior_draft_does_not_fabricate_one(self, resume_env):
        """An agent that never called `contract init` has no draft; the
        resume bridge must degrade to no-injection, never invent state."""
        env = resume_env
        adapter = ClaudeCodeAdapter()
        session_id = "sess-resume-003"

        adapter._adapt_send_message(
            "SendMessage", {"to": "afeedfeed0", "message": "go"}, session_id=session_id,
        )
        result = adapter.adapt_subagent_start({"session_id": session_id})

        assert result.context_injected is False
        assert result.additional_context is None
