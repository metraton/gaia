"""ghx, the user's per-repo gh account wrapper, is gh to every gate; a subagent never touches gh's credentials.

Two halves. The installer declares ``ghx=gh`` in the settings it owns, so the
classifier, the permanent blocks and the publish guard treat ``ghx`` as ``gh``.
And the account-credential subcommands of gh (``auth switch|login|refresh|
logout|setup-git|token``) are refused categorically to a subagent, by either
spelling, with a message naming ghx as the route.
"""

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[4]
for entry in (str(_REPO_ROOT), str(_REPO_ROOT / "hooks")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from modules.core import plugin_setup  # noqa: E402
from modules.security import gaia_cli_only_guard  # noqa: E402
from modules.security import subagent_gh_account_guard as guard  # noqa: E402
from modules.security import tiers as tiers_module  # noqa: E402
from modules.security.blocked_commands import is_blocked_command  # noqa: E402
from modules.security.mutative_verbs import detect_mutative_command  # noqa: E402
from modules.security.tiers import SecurityTier, classify_command_tier  # noqa: E402
from modules.tools.bash_validator import BashValidator  # noqa: E402

T0 = SecurityTier.T0_READ_ONLY
T3 = SecurityTier.T3_BLOCKED

AUTH_ACTIONS = ("switch", "login", "refresh", "logout", "setup-git", "token")

# Built from parts so this file never spells the irreversible command itself.
_REPO_DELETE = " ".join(("repo", "del" + "ete", "o/r", "--yes"))


def _clear_caches():
    detect_mutative_command.cache_clear()
    tiers_module._classify_command_tier_cached.cache_clear()


@pytest.fixture
def installer_declared_aliases(monkeypatch):
    """GAIA_CLI_ALIASES exactly as the installer writes it into settings.local.json."""
    env: dict = {}
    plugin_setup.declare_cli_aliases(env)
    monkeypatch.setenv(plugin_setup.CLI_ALIASES_ENV, env[plugin_setup.CLI_ALIASES_ENV])
    _clear_caches()
    yield
    _clear_caches()


def _as_subagent(command: str):
    return BashValidator().validate(
        command, is_subagent=True, session_id="s-ghx", agent_type="developer",
    )


@pytest.mark.usefixtures("installer_declared_aliases")
class TestGhxIsClassifiedAsGh:
    def test_merge_is_consent_like_gh(self):
        assert classify_command_tier("gh pr merge 1") == T3
        assert classify_command_tier("ghx pr merge 1") == T3
        assert classify_command_tier("ghx -C /repo pr merge 1") == T3

    def test_list_is_free_like_gh(self):
        assert classify_command_tier("gh pr list") == T0
        assert classify_command_tier("ghx pr list") == T0
        assert classify_command_tier("ghx -C /repo pr list") == T0

    @pytest.mark.parametrize("tail", [
        "workflow run ci.yml --ref main",
        "run rerun 123456",
        "pr create --title t --body b",
        "pr edit 1 --title t",
        "pr edit 1 --base main",
        "run view 123456",
    ])
    def test_tier_matches_gh(self, tail):
        assert classify_command_tier(f"ghx {tail}") == classify_command_tier(f"gh {tail}"), tail

    def test_permanent_block_reaches_ghx(self):
        assert is_blocked_command(f"gh {_REPO_DELETE}").is_blocked
        assert is_blocked_command(f"ghx {_REPO_DELETE}").is_blocked

    def test_the_declaration_is_what_makes_it_so(self, monkeypatch):
        """Withdraw the alias and ghx stops inheriting gh's exception for `pr create`."""
        assert classify_command_tier("ghx pr create --title t --body b") == T0
        monkeypatch.delenv(plugin_setup.CLI_ALIASES_ENV)
        _clear_caches()
        assert classify_command_tier("ghx pr create --title t --body b") == T3


@pytest.mark.usefixtures("installer_declared_aliases")
class TestAccountCredentialsAreRefusedToSubagents:
    @pytest.mark.parametrize("action", AUTH_ACTIONS)
    @pytest.mark.parametrize("program", [
        "gh",
        "ghx",
        "ghx -C /repo",
        "/home/jorge/bin/ghx",
        "GHX_ACCOUNT=metraton ghx",
        "env GH_HOST=github.com gh",
    ])
    def test_categorical_denial_without_approval_id(self, program, action):
        command = f"{program} auth {action}"
        result = _as_subagent(command)
        assert not result.allowed, command
        assert not result.approval_id, f"{command!r} got an approvable path"
        assert "ghx" in result.reason, result.reason

    def test_the_token_cannot_be_read_through_a_substitution(self):
        result = _as_subagent("GH_TOKEN=$(gh auth token --user x) gh pr list")
        assert not result.allowed
        assert not result.approval_id

    def test_a_chained_switch_is_refused(self):
        result = _as_subagent("gh pr list && gh auth switch -u someone")
        assert not result.allowed
        assert not result.approval_id

    @pytest.mark.parametrize("command", [
        "gh auth status",
        "ghx auth status",
        "ghx -C /repo auth status",
        "gh auth login --help",
        "gh pr list",
        "ghx -C /repo pr view 1",
        "echo auth switch",
    ])
    def test_reads_and_everything_else_stay_open(self, command):
        allowed, _ = guard.check(command, is_subagent=True)
        assert allowed, command

    def test_the_guard_is_a_role_rule_not_a_tier(self):
        allowed, reason = guard.check("gh auth switch -u someone", is_subagent=False)
        assert allowed and reason is None

    def test_the_orchestrator_cannot_reach_gh_at_all(self):
        allowed, _ = gaia_cli_only_guard.check("gh auth switch -u someone", {})
        assert not allowed
