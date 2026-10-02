"""A guard decision depends only on what bash would execute.

Text bash keeps literal (inside single quotes, or a correctly closed double-quoted
span) never reads as a redirect, a substitution or a consent-verb invocation;
what bash would expand or execute, an ANSI-C span and an open quote are read as
they were before quoting was resolved.
"""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent.parent
sys.path.insert(0, str(REPO / "hooks"))

from modules.security.host_consent_verb_guard import REJECTION_MESSAGE  # noqa: E402
from modules.tools.bash_validator import BashValidator  # noqa: E402
from modules.tools.cloud_pipe_validator import validate_cloud_pipe  # noqa: E402

GAIA = str(REPO / "bin" / "gaia")
DRAFT = "a1b2c3d4e5f60718.abc"
SUBAGENT = {"agent_id": "a1b2c3d4e5f60718", "agent_type": "gaia-system", "session_id": "s-quoted"}
ORCHESTRATOR = {"session_id": "s-quoted"}
SUBSTITUTION_REFUSAL = "command substitution detected"


def _verdict(command, payload):
    return BashValidator().validate(
        command,
        is_subagent=bool(payload.get("agent_id")),
        session_id=payload["session_id"],
        agent_type=payload.get("agent_type", ""),
        hook_payload={**payload, "tool_name": "Bash", "tool_input": {"command": command}},
    )


@pytest.mark.parametrize("value", [
    "'{\"note\": \"it'\\''s a > b\"}'",
    "\"{\\\"note\\\": \\\"a > b\\\"}\"",
], ids=["single-quoted-with-escaped-apostrophe", "double-quoted-with-escaped-quotes"])
def test_redirect_sign_inside_quoted_json_is_not_a_redirect(value):
    verdict = _verdict(f"gaia contract set --draft-id {DRAFT} report_prose {value}", SUBAGENT)

    assert verdict.allowed is True, verdict.reason


@pytest.mark.parametrize("text", ["'reads $(date) as text'", "'a `code` span'"])
def test_substitution_inside_single_quotes_runs_nothing(text):
    verdict = _verdict(f"{GAIA} memory search {text}", ORCHESTRATOR)

    assert verdict.allowed is True, verdict.reason


def test_git_grep_with_directory_flag_and_consent_verb_pattern_is_a_read():
    verdict = _verdict(f"git -C {REPO} grep -n 'opencode-present' -- hooks", SUBAGENT)

    assert verdict.allowed is True, verdict.reason


def test_redirect_after_an_escaped_quote_is_a_redirect():
    assert validate_cloud_pipe("echo it\\'s > f") is not None


def test_redirect_after_a_quoted_word_is_a_redirect():
    assert validate_cloud_pipe("echo 'a' > f") is not None


def test_redirect_after_an_unterminated_quote_is_read_as_before():
    assert validate_cloud_pipe('kubectl get pods "a\\" > out') is not None


@pytest.mark.parametrize("command", [
    f'{GAIA} memory search "$(rm -rf x)"',
    f'{GAIA} memory search "`id`"',
    f"{GAIA} memory search 'x'$(id)",
    f"{GAIA} memory search $'$(id)'",
    f"{GAIA} memory search 'a $(id)",
], ids=["double-quoted-substitution", "double-quoted-backticks", "substitution-after-quoted-word",
        "ansi-c-span", "unterminated-quote"])
def test_substitution_bash_would_run_or_cannot_be_parsed_is_refused(command):
    verdict = _verdict(command, ORCHESTRATOR)

    assert verdict.allowed is False
    assert SUBSTITUTION_REFUSAL in str(verdict.reason), verdict.reason


def test_mutation_inside_double_quoted_substitution_stays_t3():
    verdict = _verdict('echo "$(rm -rf x)"', SUBAGENT)

    assert verdict.allowed is False
    assert "T3" in str(verdict.reason), verdict.reason


def test_shell_wrapper_content_is_still_executed_code():
    assert _verdict("bash -c 'echo > /etc/x'", SUBAGENT).allowed is False


def test_consent_verb_run_through_a_shell_wrapper_is_refused():
    verdict = _verdict(f"bash -c 'git -C {REPO} grep x; gaia approvals opencode-present P-1'", SUBAGENT)

    assert verdict.allowed is False
    assert verdict.reason == REJECTION_MESSAGE, verdict.reason


def test_git_config_flag_does_not_make_git_a_reader():
    verdict = _verdict("git -c core.pager=less grep opencode-decide", SUBAGENT)

    assert verdict.allowed is False
    assert verdict.reason == REJECTION_MESSAGE, verdict.reason
