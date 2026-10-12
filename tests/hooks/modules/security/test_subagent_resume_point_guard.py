"""The resume point is the orchestrator's: a subagent may read the session, never write its resume point."""

import sys
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parents[4] / "hooks"
sys.path.insert(0, str(HOOKS))

from modules.security.tiers import SecurityTier  # noqa: E402
from modules.tools.bash_validator import BashValidator  # noqa: E402

SET = "gaia session resume-point set --session-id s --text t"

# (case_id, command, is_subagent, expected_allowed)
RESUME_POINT_GUARD_TABLE = [
    ("subagent-set-refused", SET, True, False),
    ("subagent-redispatched-set-refused", f"python3 /x/bin/{SET}", True, False),
    ("subagent-chained-set-refused", f"cd /tmp && {SET}", True, False),
    ("orchestrator-set-free", SET, False, True),
    ("subagent-snapshot-free", "gaia session snapshot --session-id s --json", True, True),
    ("subagent-set-help-free", "gaia session resume-point set --help", True, True),
]


@pytest.mark.parametrize(
    "case_id,command,is_subagent,expected_allowed",
    RESUME_POINT_GUARD_TABLE,
    ids=[row[0] for row in RESUME_POINT_GUARD_TABLE],
)
def test_resume_point_guard_verdict(case_id, command, is_subagent, expected_allowed):
    result = BashValidator().validate(command, is_subagent=is_subagent, agent_type="developer" if is_subagent else "")

    assert result.allowed is expected_allowed, f"{case_id}: {result.reason}"
    if expected_allowed:
        assert result.tier == SecurityTier.T0_READ_ONLY, case_id
    else:
        assert "orchestrator" in result.reason
        assert "approval_id" not in result.reason
