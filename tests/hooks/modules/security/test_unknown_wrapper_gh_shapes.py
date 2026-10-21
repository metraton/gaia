"""An unknown wrapper around gh pays consent for the mutations its arguments name."""

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[4]
for entry in (str(_REPO_ROOT), str(_REPO_ROOT / "hooks")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from modules.security import tiers as tiers_module  # noqa: E402
from modules.security.mutative_verbs import detect_mutative_command  # noqa: E402
from modules.security.tiers import SecurityTier, classify_command_tier  # noqa: E402

T0 = SecurityTier.T0_READ_ONLY
T3 = SecurityTier.T3_BLOCKED


def _clear_caches():
    detect_mutative_command.cache_clear()
    tiers_module._classify_command_tier_cached.cache_clear()


@pytest.fixture(autouse=True)
def _no_declared_wrapper(monkeypatch):
    monkeypatch.delenv("GAIA_CLI_ALIASES", raising=False)
    _clear_caches()
    yield
    _clear_caches()


class TestUnknownWrapperMutationsAreSigned:
    @pytest.mark.parametrize("command", [
        "ghx pr merge 1",
        "ghx -C /repo pr merge 1",
        "ghx repo delete x",
        "ghx workflow run ci.yml --ref main",
        "ghx run rerun 123456",
        "ghx release create v1.0.0",
        "ghx issue close 1",
        "ghx pr create --title t --body b",
        "ghx pr edit 1 --base main",
        "ghx auth switch -u someone",
        "ghx auth logout",
        "ghx api repos/o/r/issues -X POST -f title=t",
        "ghx api repos/o/r --method DELETE",
        "ghx pr comment 1 --body x",
        "ghx secret set NAME --body x",
        "ghx repo edit o/r --visibility public",
        "ghx pr close 1",
        "ghx pr update-branch 1",
    ])
    def test_mutation_is_consent(self, command):
        assert classify_command_tier(command) == T3, command

    @pytest.mark.xfail(strict=True, reason="no generic shape names these: they run free on any unknown binary")
    @pytest.mark.parametrize("command", [
        "ghx api repos/o/r/issues -f title=t",
        "ghx pr review 1 --approve",
        "ghx pr ready 1",
    ])
    def test_known_slip_mutation_runs_free(self, command):
        assert classify_command_tier(command) == T3, command

    @pytest.mark.parametrize("command", [
        "ghx pr list",
        "ghx -C /repo pr list",
        "ghx pr view 1",
        "ghx pr checks 1",
        "ghx run list",
        "ghx run view 123456",
        "ghx issue list",
        "ghx repo view o/r",
        "ghx auth status",
    ])
    def test_read_is_free(self, command):
        assert classify_command_tier(command) == T0, command
