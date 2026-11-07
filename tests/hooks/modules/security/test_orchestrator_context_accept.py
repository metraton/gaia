"""The orchestrator accepts a specialist's project-context proposal unsigned.

``gaia context accept CONTRACT_ID`` writes project context, which only the
orchestrator may do. It must pass the orchestrator lane and classify below T3,
or the only agent allowed to write would need a signature to do so.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "hooks"))

from modules.security import gaia_cli_only_guard as guard  # noqa: E402
from modules.security.tiers import SecurityTier, classify_command_tier  # noqa: E402

TRUSTED = "/trusted/node_modules/gaia/bin/gaia"


@pytest.fixture
def run_guard(monkeypatch):
    monkeypatch.setattr(guard, "is_trusted_gaia_binary", lambda _binary: True)

    def _run(argline: str):
        return guard.check(f"{TRUSTED} {argline}", {"session_id": "s"})

    return _run


def test_accept_is_classified_t0_not_t3() -> None:
    assert classify_command_tier("gaia context accept c1") is SecurityTier.T0_READ_ONLY
    assert classify_command_tier("gaia context accept c1 --workspace me --json") is (
        SecurityTier.T0_READ_ONLY
    )
    assert classify_command_tier("gaia context accept c1 --dry-run") is not (
        SecurityTier.T3_BLOCKED
    )


@pytest.mark.parametrize("argline", [
    "context accept c1",
    "context accept c1 --workspace me",
    "context accept c1 --dry-run --json",
])
def test_the_orchestrator_lane_admits_accept(run_guard, argline) -> None:
    assert run_guard(argline) == (True, None)


@pytest.mark.parametrize("argline", [
    "context accept",
    "context accept --dry-run",
    "context accept c1 --yes",
])
def test_accept_without_a_contract_id_or_with_an_unvetted_flag_is_refused(run_guard, argline) -> None:
    assert run_guard(argline)[0] is False
