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


def test_command_execution_owns_fail_fast_and_fresh_consent() -> None:
    command = _read("command-execution")
    execution = _read("execution")
    assert "first non-zero or mismatched result ends execution" in command
    assert "terminal/frozen `FAILED`" in command
    assert "cannot authorize a retry or any" in command
    assert "Later indexes stay untouched" in command
    assert "fresh investigation" in command
    assert "new request and new approval" in command
    assert "apply `command-execution`'s COMMAND_SET fail-fast rule" in execution
    assert "terminal/frozen `FAILED`" not in execution


def test_agent_protocol_owns_failed_set_consumer_reconciliation() -> None:
    protocol = _read("agent-protocol")
    assert "never evidence that execution\nhappened" in protocol
    assert "completed indexes, the failed index, and untouched indexes" in protocol
    assert "three distinct" in protocol
    assert "failed COMMAND_SET is terminal/frozen" in protocol
    assert "fresh investigation and the approval branch" in protocol


def test_other_audited_branches_cross_link_instead_of_restate() -> None:
    execution = _read("execution")
    producer = _read("subagent-request-approval")
    assert "`command-execution` for the\nexecution-time stop" in producer
    assert "`agent-protocol` for consumer reconciliation" in producer
    assert "does not duplicate the failure rule" in execution
