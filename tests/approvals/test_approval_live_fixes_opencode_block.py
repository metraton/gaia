"""D37 in OpenCode: the signature is Gaia's questions, one line per command.

``opencode-present`` hands the plugin the renderer's own questions, one per
sealed command, and their Details re-asks, and nothing else: no text is
posted into any session before the question (D26 and D27 are superseded), and
the question is asked only from the orchestrator's call (D39), driven in
test_approval_live_fixes_orchestrator_presents.
"""

from __future__ import annotations

import json
import subprocess
import sys

from tests.approvals.test_approval_live_fixes_orchestrator_presents import _rendered, _shown
from tests.integration.test_opencode_consent_retry_e2e import (  # noqa: F401 (db_env is a fixture)
    AGENT_ID,
    FIRST_COMMAND,
    GAIA_CLI,
    SECOND_COMMAND,
    SESSION_ID,
    _request_set,
    db_env,
)


def _renderer_questions(questions):
    fields = ("question", "header", "options")
    return [{key: question[key] for key in fields} for question in questions]


def test_approval_live_fixes_opencode_presentation_emits_one_question_per_command(db_env):
    from bin.cli.approvals import _opencode_presentation
    from gaia.approvals import store

    approval_id = _request_set(db_env[0])
    rendered = _rendered(approval_id)

    signature = _opencode_presentation(store.get_by_id(approval_id), SESSION_ID, "call-block")["signature"]

    assert set(signature) == {"questions", "details_questions"}
    assert signature["questions"] == _renderer_questions(rendered.questions)
    assert signature["details_questions"] == _renderer_questions(rendered.details_questions)
    for question, details, command in zip(
        signature["questions"], signature["details_questions"], (FIRST_COMMAND, SECOND_COMMAND),
    ):
        assert question["question"].startswith("[GAIA-SECURITY] [ AGENT-REQUEST ]")
        assert details["question"].startswith("[GAIA-SECURITY] [ DETAILS ]")
        assert command in question["question"] and command in details["question"]
        assert "\n" not in question["question"] and "\n" not in details["question"]


def test_approval_live_fixes_a_preview_renders_the_signature_and_records_nothing(db_env):
    env, _ = db_env
    approval_id = _request_set(env)

    result = subprocess.run(
        [
            sys.executable, str(GAIA_CLI), "approvals", "opencode-present", approval_id,
            "--session-id", SESSION_ID, "--agent-id", AGENT_ID,
            "--call-id", "call-preview", "--token", "preview", "--preview", "--json",
        ],
        cwd=env["WORKSPACE"], env=env, capture_output=True, text=True, timeout=120,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    previewed = json.loads(result.stdout.strip().splitlines()[-1])
    assert previewed["status"] == "previewed"
    assert previewed["signature"]["questions"] == _renderer_questions(_rendered(approval_id).questions)
    assert _shown(approval_id) == []
