#!/usr/bin/env python3
"""A comment ends where bash ends it, and text the tokeniser cannot place is judged, never dropped."""

import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import require_tool

HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.tools.shell_parser import parse_command, strip_comment  # noqa: E402

READ = "{ print $1 }"
SYSTEM = 'BEGIN{system("touch pwned")}'
SIGNS = "kubectl delete pod web"

BACKTICK_HIDES = [
    (f"awk '{READ}' data.txt `true #` ; touch pwned", "touch pwned"),
    (f"awk '{READ}' `true #` data.txt ; touch pwned", "touch pwned"),
    ("cat data.txt `true #` ; touch pwned", "touch pwned"),
    (f"awk '{READ}' data.txt `true #` ; gawk '{SYSTEM}' data.txt", f"gawk '{SYSTEM}' data.txt"),
    (f"awk '{READ}' data.txt `true #` ; gawk -i inplace '{READ}' /tmp/notes.txt",
     f"gawk -i inplace '{READ}' /tmp/notes.txt"),
    (f"awk '{READ}' data.txt `true #` ; rm -rf /", "rm -rf /"),
]

MUST_STOP = [command for command, hidden in BACKTICK_HIDES if "touch" not in hidden] + [
    f"cat data.txt $(true #\n) ; {SIGNS}",
    f'cat "$(echo " #")" data.txt ; {SIGNS}',
    f"cat ${{x:- #}} data.txt ; {SIGNS}",
    f"cat a\\ #b ; {SIGNS}",
    f"cat data.txt x#; {SIGNS}",
    f"(( 1 # )) ; {SIGNS}",
    f"cat <<EOF\nfoo # $({SIGNS})\nEOF",
    f"cat data.txt `true # ; {SIGNS}",
    f"cat data.txt $(true # ; {SIGNS}",
]

CONTROL_FREE = [
    "cat data.txt # touch pwned",
    "cat data.txt\n# touch pwned",
]

# Each line prints MARK under bash exactly when its text after the first `#` is executed.
BASH_CASES = [
    "echo A `echo B #` ; echo MARK",
    "echo A $(echo B #\n) ; echo MARK",
    'echo "$(echo " #")" ; echo MARK',
    "echo ${x:- #} ; echo MARK",
    "echo a\\ #b ; echo MARK",
    "echo x#; echo MARK",
    "echo 'a #' ; echo MARK",
    'echo "a #" ; echo MARK',
    "echo $'a #' ; echo MARK",
    "(( 1 # )) ; echo MARK",
    "cat <<EOF\n# $(echo MARK)\nEOF",
    "echo A # ; echo MARK",
    "echo A ;# echo MARK",
    "[[ a == #b ]] ; echo MARK",
    "echo $[1 #] ; echo MARK",
]


@pytest.fixture
def cwd(tmp_path, monkeypatch):
    from modules.security import tiers
    from modules.security.mutative_verbs import detect_mutative_command

    (tmp_path / "data.txt").write_text("a b\n")
    monkeypatch.chdir(tmp_path)
    detect_mutative_command.cache_clear()
    tiers._classify_command_tier_cached.cache_clear()
    return str(tmp_path)


def _validate(command, cwd):
    from modules.tools.bash_validator import BashValidator

    return BashValidator().validate(
        command, is_subagent=True, session_id="t", agent_type="gaia-verifier", cwd=cwd,
    )


@pytest.mark.parametrize("command,hidden", BACKTICK_HIDES)
def test_every_guard_receives_the_command_after_a_backtick_hash(command, hidden):
    assert hidden in parse_command(command)


@pytest.mark.parametrize("command", MUST_STOP)
def test_what_bash_runs_after_a_hash_is_judged(command, cwd):
    assert _validate(command, cwd).allowed is False


@pytest.mark.parametrize("command", CONTROL_FREE)
def test_a_real_trailing_comment_stays_free(command, cwd):
    result = _validate(command, cwd)
    assert result.allowed is True, result.reason


@pytest.mark.parametrize("command", BASH_CASES)
def test_strip_comment_keeps_every_word_bash_executes(command, tmp_path):
    ran = subprocess.run([require_tool("bash"), "-c", command], cwd=tmp_path, capture_output=True, text=True)
    executed = "MARK" in ran.stdout
    assert ("MARK" in strip_comment(command)) or not executed, ran.stdout
