"""The orchestrator reads pending proposals and decides them; backfill stays outside its lane."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "hooks"))

from modules.security import gaia_cli_only_guard as guard  # noqa: E402

TRUSTED = "/trusted/node_modules/gaia/bin/gaia"


@pytest.fixture
def run_guard(monkeypatch):
    monkeypatch.setattr(guard, "is_trusted_gaia_binary", lambda _binary: True)

    def _run(argline: str):
        return guard.check(f"{TRUSTED} {argline}", {"session_id": "s"})

    return _run


@pytest.mark.parametrize("argline", [
    "contract proposals list",
    "contract proposals list --all --kind memory --contract-id c1 --json",
    "contract proposals accept 7",
    "contract proposals accept 7 --project github.com/metraton/gaia --workspace me --json",
    "contract proposals dismiss 7 --reason stale",
])
def test_the_orchestrator_lane_admits_the_proposals_verbs(run_guard, argline) -> None:
    assert run_guard(argline) == (True, None)


@pytest.mark.parametrize("argline", [
    "contract proposals backfill --apply",
    "contract proposals list --yes",
    "contract proposals accept 7 --force",
])
def test_backfill_and_unvetted_flags_are_refused(run_guard, argline) -> None:
    assert run_guard(argline)[0] is False
