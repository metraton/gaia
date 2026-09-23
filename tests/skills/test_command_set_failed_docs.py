"""Lock documentation to the frozen COMMAND_SET failure model."""

from pathlib import Path

from gaia.approvals import reading

ROOT = Path(__file__).resolve().parents[2]
SKILLS = ROOT / "skills"


def _read(name: str) -> str:
    return (SKILLS / name / "SKILL.md").read_text()


def test_pending_approvals_names_failed_as_frozen() -> None:
    content = _read("pending-approvals")
    assert f"`{reading.FAILED}` is frozen" in content
    assert "completed, failed and untouched" in content
    assert "anything still needed is a new request" in content


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
    assert "A set that failed is not resumed" in producer
    assert "does not duplicate the failure rule" in execution
