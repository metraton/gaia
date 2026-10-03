"""The orchestrator can set, snooze and cancel a reminder itself, and nothing wider."""

import sys
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parents[4] / "hooks"
sys.path.insert(0, str(HOOKS))

from modules.security import gaia_cli_only_guard as guard  # noqa: E402
from modules.security.tiers import SecurityTier, classify_command_tier  # noqa: E402

TRUSTED = "/trusted/node_modules/gaia/bin/gaia"


@pytest.fixture
def run_guard(monkeypatch):
    monkeypatch.setattr(guard, "is_trusted_gaia_binary", lambda _binary: True)

    def _run(argline: str):
        return guard.check(f"{TRUSTED} {argline}", {"session_id": "s"})

    return _run


@pytest.mark.parametrize("argline", [
    "notifications add --kind reminder --at 2026-10-01T16:00 --headline X",
    "notifications add --kind routine --cron '0 17 * * 5' --headline 'Load timesheet' "
    "--memory aaxis:project_aaxis_timesheet",
    "notifications snooze 12 --for 2h",
    "notifications cancel 12",
])
def test_bounded_reminder_writes_pass_the_guard_at_t0(run_guard, argline):
    allowed, reason = run_guard(argline)
    assert allowed, reason
    assert classify_command_tier(f"{TRUSTED} {argline}") == SecurityTier.T0_READ_ONLY


@pytest.mark.parametrize("argline", [
    "notifications add --kind report --task probe --headline X",
    "notifications add --task probe --headline X",
    "notifications add --kind reminder --at 2026-10-01T16:00 --headline X --session-id s",
    "notifications add --kind reminder --headline X",
    "notifications add --kind reminder --headline x --in 30m",
    "notifications snooze 12",
    "notifications snooze --all --for 2h",
    "notifications cancel 12 13",
    "task add demo --title X",
    "schedule register demo",
])
def test_writes_outside_the_bounded_shape_stay_denied(run_guard, argline):
    allowed, reason = run_guard(argline)
    assert not allowed
    assert reason
