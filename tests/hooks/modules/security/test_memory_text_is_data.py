"""The text arguments of the gaia memory write verbs are data, never a command or a file read."""

import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parents[4] / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.security import sensitive_read_guard
from modules.security.protected_path_guard import unprovable_write_target
from modules.security.tiers import SecurityTier
from modules.tools.bash_validator import BashValidator
from modules.tools.shell_parser import parse_command
from tests.hooks.adapters.test_opencode_attestation_provenance import bare_gaia_on_path

_ADD = "gaia memory add --name=feedback_x --type=feedback --kind=defect --initiative=gaia_system --workspace=me "

DESTRUCTIVE_PROSE = [
    _ADD + "--description='Defect: prose read as a command' "
    "--body='Seen: git stash drop stash@{0}; then kubectl exec -it pod -- sh && git push --force origin main'",
    _ADD + "--description='Defect: multi-line prose' --body='First line.\ngit stash drop\nkubectl delete ns prod\n'",
    "gaia memory append thread_x --body='Next: git reset --hard HEAD~3 | tee log'",
    _ADD + "--description='Defect: method prose' --method='ran terraform apply -auto-approve' --body=x",
    _ADD + "--description 'Defect: markdown quoting' "
    "--body 'Ran `git stash drop` and then `kubectl exec -it pod -- sh`; see $(git push --force) in the log.'",
    _ADD + "--description=\"Defect: double quoted\" --body=\"Ran git stash drop, then kubectl exec -it api -- bash.\"",
    "gaia memory add --name feedback_x --type feedback --kind defect --class thread --status open "
    "--initiative gaia_system --workspace me --description 'Defect: target variables before an executor.' "
    "--body 'Not covered: a target variable set before an outer executor (KUBECONFIG=/x op run -- kubectl exec "
    "...). Compound commands ignore FLAG_BLOCKED, so `ls && git stash drop` does not get the bare block a lone "
    "`git stash drop` gets.'",
    "gaia memory add --name x --type feedback --kind defect --body 'see KUBECONFIG=/x op run -- kubectl exec pod'",
    "gaia memory add --name x --type feedback --kind defect --body 'see `ls && git stash drop` here'",
]

SECRET_PROSE = [
    _ADD + "--description='Defect: the gws credentials.json path' "
    "--body='The client secret lives at ~/.config/gws/credentials.json and ~/.ssh/id_rsa; nothing reads it.'",
    "gaia memory append thread_x --body 'see ~/.aws/credentials and ./.env'",
    _ADD + "--description 'Defect: .env named' --body 'Never run `cat ~/.config/gws/credentials.json`.'",
]


@pytest.fixture(autouse=True)
def _orchestrator_lane(tmp_path, monkeypatch):
    bare_gaia_on_path(tmp_path, monkeypatch)
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("GAIA_DB", raising=False)
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    monkeypatch.setattr(
        "modules.tools.bash_validator.check_memory_skill_loaded", lambda *a, **k: (True, None),
    )


def _main_session(command):
    payload = {"session_id": "memory-text-session", "cwd": str(HOOKS_DIR.parent), "tool_name": "Bash"}
    return BashValidator().validate(
        command, is_subagent=False, session_id="memory-text-session",
        agent_type="claude-code-primary", hook_payload=payload,
    )


@pytest.mark.parametrize("command", DESTRUCTIVE_PROSE)
def test_memory_write_quoting_a_destructive_command_is_not_t3(command):
    result = _main_session(command)
    assert result.allowed, result.reason
    assert result.tier != SecurityTier.T3_BLOCKED


@pytest.mark.parametrize("command", SECRET_PROSE)
def test_memory_write_naming_a_secret_path_is_not_refused(command):
    assert sensitive_read_guard.check(command) == (True, None)
    assert _main_session(command).allowed


@pytest.mark.parametrize("command", [
    _ADD + "--description=x --body=\"$(cat ~/.ssh/id_rsa)\"",
    _ADD + "--description=x --body=\"`cat ~/.aws/credentials`\"",
    _ADD + "--description=x --body-file ~/.ssh/id_rsa",
    "gaia memory append thread_x --body=x; cat ~/.ssh/id_rsa",
])
def test_a_real_secret_read_beside_memory_text_is_still_refused(command):
    allowed, reason = sensitive_read_guard.check(command)
    assert not allowed and reason
    assert not _main_session(command).allowed


@pytest.mark.parametrize("command", [
    "git commit -m 'see `ls && git stash drop` and > $OUT/x'",
    "gh pr create --title t --body 'run: cp a $DEST; tee $LOG'",
])
def test_single_quoted_cli_text_names_no_write_target(command):
    assert unprovable_write_target(command) is None


@pytest.mark.parametrize("command", [
    "git commit -m x && cp a $DEST",
    "git commit -m \"see $(cp a $DEST)\"",
    "git commit -m \"see `tee $LOG`\"",
    "echo 'C:\\' ; cp a $DEST",
])
def test_a_write_outside_single_quotes_is_still_read(command):
    assert unprovable_write_target(command)


def test_a_backslash_inside_single_quotes_does_not_hide_the_next_command():
    assert parse_command("echo 'a\\' ; rm -rf x") == ["echo 'a\\'", "rm -rf x"]


@pytest.mark.parametrize("command", [
    "gaia memory append thread_x --body=x && git push --force origin main",
    _ADD + "--description=x --body=\"$(git push --force origin main)\"",
    _ADD + "--replace --description=x --body=x",
])
def test_a_real_mutation_beside_memory_text_still_signs(command):
    result = _main_session(command)
    assert not result.allowed
    assert result.tier == SecurityTier.T3_BLOCKED
