"""A long command with an inline prose argument classifies in time: the validator runs on every Bash call."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[4]
if str(_REPO / "hooks") not in sys.path:
    sys.path.insert(0, str(_REPO / "hooks"))

from modules.tools.bash_validator import BashValidator  # noqa: E402

_PARAGRAPH = (
    "## Context\n"
    "- in the demo the worker fell behind realtime 82/25/12/36 times before 14:35Z (one call had 24.6 s of "
    "silence) with a 900m request under a 2 CPU limit, and both demo replicas ran on one node. The node "
    "replacements were autoscaler consolidation, which evicted 12 and 25 pods, the proxy among them, and "
    "a revert of this PR is the rollback if the new pods stay Pending while the old ones keep serving.\n"
    "- worker: CPU request 900m -> 2 (equal to the limit), annotation safe-to-evict: \\\"false\\\", "
    "label_values(metric{namespace=\\\"app-\\${env}\\\"}, trace_id), first value by default.\n\n"
)


def _pr_create(length: int) -> str:
    head = (
        "ghx -C /home/u/ws/.project-worktrees/repo/0123abcd pr create --repo org/repo "
        "--head feat/x --title \"fix(voice-agent): reserve full CPU and spread replicas\" --body \""
        "## Summary\nthe trace view loads the call's trace without a click.\n\n"
    )
    body = (_PARAGRAPH * (length // len(_PARAGRAPH) + 1))[: length - len(head) - 1]
    return f"{head}{body}\""


@pytest.mark.parametrize("length, seconds", [(4_000, 3.0), (20_000, 10.0)])
@pytest.mark.parametrize("is_subagent", [True, False])
def test_a_long_inline_body_classifies_within_its_bound(length, seconds, is_subagent):
    command = _pr_create(length)
    started = time.monotonic()
    result = BashValidator().validate(command, is_subagent=is_subagent, session_id="s", agent_type="developer",
                                      hook_payload={"agent_type": "developer", "agent_id": "a1"})
    elapsed = time.monotonic() - started

    assert elapsed < seconds, f"{length}-char command took {elapsed:.1f}s"
    assert result.allowed is True, (result.tier, result.reason)
