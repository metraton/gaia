"""Lock documentation to the v42 frozen COMMAND_SET failure model."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SKILLS = ROOT / "skills"


def _read(name: str) -> str:
    return (SKILLS / name / "SKILL.md").read_text()


def test_approval_reference_names_failed_and_frozen_remainder() -> None:
    content = _read("agent-approval-protocol")
    assert "`PENDING`, `CONSUMED`, `FAILED`, `REVOKED`, or `EXPIRED`" in content
    assert "terminal/frozen `FAILED`" in content
    assert "completed indexes remain consumed" in content
    assert "later index remains unconsumed but" in content
    assert "unusable under this grant" in content


def test_execution_requires_new_consent_for_retry_and_remainder() -> None:
    command = _read("command-execution")
    execution = _read("execution")
    assert "cannot authorize a retry or any" in command
    assert "remaining index" in command
    assert "new approval for every retry/remainder command" in command
    assert "neither retry nor remainder may execute under" in execution
    assert "Every retry and every still-needed remainder command is a new" in execution
    assert "Unused items in the frozen grant do not" in execution
    assert "authorize execution" in execution


def test_all_protocol_branches_reject_failed_grant_resume() -> None:
    expected = {
        "agent-protocol": "failed COMMAND_SET is terminal/frozen",
        "subagent-request-approval": "previous COMMAND_SET in `FAILED` cannot be resumed",
        "agent-response": "treat `FAILED` as a",
        "pending-approvals": "COMMAND_SET grant in `FAILED` is not pending or resumable",
    }
    for skill, phrase in expected.items():
        assert phrase in _read(skill), f"{skill} must freeze FAILED COMMAND_SET grants"
    assert "terminal/frozen grant" in _read("agent-response")
