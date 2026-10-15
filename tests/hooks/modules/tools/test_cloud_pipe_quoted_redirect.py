"""A `>` the shell reads as quoted text is not a redirect; one it reads as an operator still is."""

import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.tools.cloud_pipe_validator import validate_cloud_pipe  # noqa: E402

_RESUME_POINT = "/home/u/.gaia/cache/dev-plugin/ws/bin/gaia session resume-point set --session-id s1 --text "


@pytest.mark.parametrize("text", [
    '"compactemos -> write resume point -> user /compact"',
    '"the user\'s turn -> compactemos -> /compact"',
    '"said \\"compact\\" -> write the point"',
    "'single -> quoted'",
    '"a $(date) stamp -> then /compact"',
    '"line one\nline two -> /compact"',
])
def test_a_quoted_arrow_runs_free(text):
    assert validate_cloud_pipe(_RESUME_POINT + text) is None


@pytest.mark.parametrize("is_subagent", [False, True], ids=["orchestrator", "subagent"])
def test_the_full_validator_never_reads_a_quoted_arrow_as_a_redirect(is_subagent):
    from modules.tools.bash_validator import BashValidator

    command = _RESUME_POINT + '"... compactemos -> write resume point -> user /compact ..."'
    result = BashValidator().validate(command, is_subagent=is_subagent, session_id="t",
                                      agent_type="gaia-system" if is_subagent else None)

    assert "no redirects" not in (result.reason or "")


@pytest.mark.parametrize("command", [
    "kubectl get pods > pods.txt",
    'kubectl get pods --selector "app=web" >> pods.txt',
    "gcloud compute instances list 2>/dev/null",
])
def test_an_unquoted_redirect_on_a_cloud_command_is_still_refused(command):
    result = validate_cloud_pipe(command)

    assert result is not None
    reason = result["hookSpecificOutput"]["permissionDecisionReason"]
    assert "no redirects in cloud/infra commands" in reason


def test_a_redirect_on_a_non_cloud_command_is_not_called_a_cloud_command():
    reason = validate_cloud_pipe("gaia session snapshot > out.txt")["hookSpecificOutput"]["permissionDecisionReason"]

    assert "no redirects." in reason
    assert "cloud/infra" not in reason
