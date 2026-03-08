#!/usr/bin/env python3
"""Tests for response-contract repair enforcement in pre_tool_use."""

import json
import sys
import time
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parents[2] / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

import pre_tool_use
from modules.agents.response_contract import MAX_REPAIR_ATTEMPTS, load_pending_repair, clear_contract_dir_cache
from modules.core.paths import clear_path_cache


@pytest.fixture(autouse=True)
def isolated_session(tmp_path, monkeypatch):
    clear_path_cache()
    clear_contract_dir_cache()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CLAUDE_SESSION_ID", "session-a")
    (tmp_path / ".claude" / "session" / "active" / "response-contract" / "session-a").mkdir(
        parents=True,
        exist_ok=True,
    )
    yield
    clear_path_cache()
    clear_contract_dir_cache()


@pytest.fixture
def saved_states(monkeypatch):
    captured = []

    def _save(state):
        captured.append(state)
        return True

    monkeypatch.setattr(pre_tool_use, "save_hook_state", _save)
    return captured


def _write_pending_repair(base: Path, *, attempts: int = 0, session_id: str = "session-a"):
    payload = {
        "timestamp": "2026-03-07T00:00:00",
        "created_at_epoch": time.time(),
        "ttl_minutes": 30,
        "session_id": session_id,
        "agent": "cloud-troubleshooter",
        "agent_id": "a12345",
        "task_id": "a12345",
        "missing": ["EVIDENCE_REPORT", "AGENT_ID"],
        "invalid": [],
        "repair_attempts": attempts,
        "recommended_action": "resume_same_agent_contract_repair",
    }
    target = (
        base
        / ".claude"
        / "session"
        / "active"
        / "response-contract"
        / session_id
        / "pending-repair.json"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2))


class TestResponseContractRepairGuard:
    def test_new_task_is_blocked_until_same_agent_is_repaired(self, tmp_path):
        _write_pending_repair(tmp_path)

        result = pre_tool_use._handle_task(
            "Task",
            {
                "subagent_type": "gitops-operator",
                "prompt": "Investigate the deployment drift.",
            },
        )

        assert isinstance(result, str)
        assert "Previous agent response contract is incomplete" in result
        assert 'resume="a12345"' in result

    def test_resume_of_same_agent_gets_repair_prompt_injected(self, tmp_path, saved_states):
        _write_pending_repair(tmp_path)

        result = pre_tool_use._handle_task(
            "Task",
            {
                "resume": "a12345",
                "prompt": "Continue the investigation and fix the report.",
            },
        )

        assert isinstance(result, dict)
        updated = result["hookSpecificOutput"]["updatedInput"]
        assert "Repair your previous response contract only." in updated["prompt"]
        assert "Missing fields:" in updated["prompt"]
        assert len(saved_states) == 1
        assert saved_states[0].metadata["has_approval"] is False

    def test_nonce_resume_is_blocked_until_contract_is_repaired(self, tmp_path):
        _write_pending_repair(tmp_path)

        result = pre_tool_use._handle_task(
            "Task",
            {
                "resume": "a12345",
                "prompt": "APPROVE:deadbeefdeadbeefdeadbeefdeadbeef",
            },
        )

        assert isinstance(result, str)
        assert "Previous agent response contract is incomplete" in result

    def test_pending_repair_from_other_session_is_ignored(self, tmp_path, monkeypatch):
        _write_pending_repair(tmp_path, session_id="session-old")
        monkeypatch.setenv("CLAUDE_SESSION_ID", "session-new")

        result = pre_tool_use._handle_task(
            "Task",
            {
                "subagent_type": "gitops-operator",
                "prompt": "Investigate the deployment drift.",
            },
        )

        assert result is None

    def test_auto_repair_limit_escalates_and_clears_pending(self, tmp_path):
        _write_pending_repair(tmp_path, attempts=MAX_REPAIR_ATTEMPTS)

        result = pre_tool_use._handle_task(
            "Task",
            {
                "resume": "a12345",
                "prompt": "Continue the investigation and fix the report.",
            },
        )

        assert isinstance(result, str)
        assert "Automatic repair retry limit reached" in result
        assert load_pending_repair() is None
