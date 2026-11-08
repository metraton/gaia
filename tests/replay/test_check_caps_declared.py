"""A history command that newly asks counts against the friction cap unless the declared file reviews it."""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tools.validator_replay import replay  # noqa: E402

CAPS = {"history_newly_asking_max_count": 0, "history_newly_asking_max_fraction": 0.0,
        "pinned_changes_max_outside_declared": 0, "loosened_unreviewed_max": 0, "new_errors_max": 0}


def _result(*commands: str) -> dict:
    changes = [{"corpus": "history", "key": c, "command": c, "old": "FREE", "new": "BLOCKED",
                "old_rule": "", "new_rule": "floor.host-tool-output"} for c in commands]
    return {"changes": changes, "errors": [], "sizes": {"history": 100}}


def test_a_reviewed_tightening_does_not_count_against_the_history_cap():
    declared = {"reviews": {"jq . spool.txt": "intended: tool output handed to jq"}}
    assert replay.check_caps(_result("jq . spool.txt"), CAPS, declared) == []


def test_an_undeclared_tightening_still_counts():
    declared = {"reviews": {"jq . spool.txt": "intended"}}
    violations = replay.check_caps(_result("jq . spool.txt", "awk 1 spool.txt"), CAPS, declared)
    assert violations == ["history_newly_asking_max_count: 1 > 0", "history_newly_asking_max_fraction: 0.01 > 0.0"]
