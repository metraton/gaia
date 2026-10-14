"""The host consent verb guard judges what a shell would run, never ordinary script syntax.

A `[ "$X" = 0 ]` test in a script once read as a program known only at run time,
so `bash <script>` and `bash -n <script>` were refused with the consent-verb
text although nothing in them reached `gaia approvals`.
"""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent.parent
sys.path.insert(0, str(REPO / "hooks"))

from modules.security.host_consent_verb_guard import REJECTION_MESSAGE, check  # noqa: E402
from modules.tools.bash_validator import BashValidator  # noqa: E402

SUBAGENT = {"agent_id": "a1b2c3d4e5f60718", "agent_type": "cloud-troubleshooter", "session_id": "s-script-body"}
BINDING = "P-0123456789abcdef0123456789abcdef --session-id s --call-id c --token t --reply once"
TEST_BUILTIN_BODY = 'X=0\n[ "$X" = "0" ] && echo same\n[ -n "$X" ] || echo empty\n'


def _verdict(command):
    return BashValidator().validate(
        command, is_subagent=True, session_id=SUBAGENT["session_id"],
        agent_type=SUBAGENT["agent_type"],
        hook_payload={**SUBAGENT, "tool_name": "Bash", "tool_input": {"command": command}},
    )


def _script(tmp_path, body):
    path = tmp_path / "probe.sh"
    path.write_text("#!/usr/bin/env bash\n" + body)
    return path


@pytest.mark.parametrize("form", ["bash -n {}", "bash {}"], ids=["syntax-check", "run"])
def test_a_script_using_the_test_builtin_runs(tmp_path, form):
    verdict = _verdict(form.format(_script(tmp_path, TEST_BUILTIN_BODY)))

    assert verdict.allowed is True, verdict.reason


def test_a_mutating_script_reaches_the_approval_lane_not_the_consent_fence(tmp_path):
    script = _script(tmp_path, TEST_BUILTIN_BODY + "kubectl delete pod probe\n")

    verdict = _verdict(f"bash {script}")

    assert verdict.allowed is False
    assert REJECTION_MESSAGE not in (verdict.reason or ""), verdict.reason
    assert verdict.block_response is not None, "a T3 script must carry an approval to sign"


def test_a_top_level_test_builtin_is_not_a_program_known_at_run_time():
    assert check('[ "$HOME" = /nowhere ]') == (True, None)


def test_a_subagent_calling_the_consent_verb_is_still_refused():
    verdict = _verdict(f"gaia approvals opencode-present {BINDING}")

    assert verdict.allowed is False
    assert verdict.reason.endswith(REJECTION_MESSAGE), verdict.reason
    assert "receives the consent verb" in verdict.reason
    assert verdict.block_response is None


def test_a_script_that_runs_the_consent_verb_is_refused_and_named(tmp_path):
    script = _script(tmp_path, TEST_BUILTIN_BODY + f"gaia approvals opencode-decide {BINDING}\n")

    verdict = _verdict(f"bash {script}")

    assert verdict.allowed is False
    assert verdict.reason.endswith(REJECTION_MESSAGE), verdict.reason
    assert str(script) in verdict.reason


def test_a_syntax_check_of_that_script_runs_nothing_and_is_free(tmp_path):
    script = _script(tmp_path, f"gaia approvals opencode-decide {BINDING}\n")

    assert check(f"bash -n {script}") == (True, None)


def test_a_glob_that_expands_to_the_verb_stays_refused():
    allowed, reason = check(f"gaia approvals [o]pencode-decide {BINDING}")

    assert allowed is False
    assert "only known at run time" in reason


def test_a_program_known_only_at_run_time_is_named_as_such():
    allowed, reason = check(f"G=gaia; A=approvals; V=opencode-decide; $G $A $V {BINDING}")

    assert allowed is False
    assert "only known at run time" in reason
