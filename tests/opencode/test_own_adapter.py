"""OpenCode's own approval adapter (plan 76, task 5).

The installed OpenCode (1.18.32) never triggers a plugin's ``permission.ask``
hook: its bundle triggers tool.execute.before/after, shell.env and the chat and
compaction hooks, and nothing named permission. The consent lane that hook fed
is therefore retired, and a signature reaches the user only through the native
question tool. An approval is identified by structure, never read out of text,
and it is presented only to the live session and agent that requested it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOKS_DIR = REPO_ROOT / "hooks"
for path in (REPO_ROOT, HOOKS_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from adapters.opencode import OpenCodeAdapter  # noqa: E402
from adapters.types import HookEvent, HookEventType, HookResponse  # noqa: E402

GAIA_CLI = REPO_ROOT / "bin" / "gaia"
PLUGIN = REPO_ROOT / "opencode" / "plugin.ts"
DRIVER = REPO_ROOT / "tests" / "opencode" / "presentation_driver.ts"

SESSION_ID = "ses-own-adapter"
AGENT_ID = "gaia-system"
COMMAND = "git -C /srv/repo push origin main"

PRESENTABLE = {
    "operation": "PUSH command intercepted: push",
    "exact_content": COMMAND,
    "commands": [COMMAND],
    "what": "Publicar la rama main en el remoto",
    "question": "¿Publico la rama?",
    "items": [{
        "command": COMMAND,
        "does": "Sube los commits locales de main.",
        "impact": "El remoto queda igual que la rama local.",
    }],
}


@pytest.fixture()
def db_env(tmp_path, monkeypatch, bootstrapped_db_template):
    from tests.conftest import copy_bootstrapped_db

    db_path = tmp_path / "own-adapter.db"
    copy_bootstrapped_db(bootstrapped_db_template, db_path)
    monkeypatch.setenv("GAIA_DB", str(db_path))
    env = os.environ.copy()
    env["GAIA_DB"] = str(db_path)
    return env


def _pending(payload=PRESENTABLE, *, session_id=SESSION_ID, agent_id=AGENT_ID):
    from gaia.approvals.store import insert_requested

    return insert_requested(dict(payload), agent_id=agent_id, session_id=session_id)


def _events(approval_id):
    from gaia.approvals.store import get_history

    return [event["event_type"] for event in get_history(approval_id)]


def _present(env, approval_id, *, session_id=SESSION_ID, agent_id=AGENT_ID, call_id="call-1"):
    result = subprocess.run(
        [
            sys.executable, str(GAIA_CLI), "approvals", "opencode-present", approval_id,
            "--session-id", session_id,
            "--agent-id", agent_id,
            "--call-id", call_id,
            "--token", f"token-{call_id}",
            "--json",
        ],
        env=env, capture_output=True, text=True, timeout=120,
    )
    emitted = json.loads(result.stdout.strip().splitlines()[-1]) if result.stdout.strip() else {}
    return result.returncode, emitted


def test_own_adapter_plugin_offers_no_permission_ask_lane():
    """The host never calls permission.ask, so the plugin must not rely on it."""
    script = (
        f"import {{ GaiaOpenCodePlugin }} from {json.dumps(str(PLUGIN))};"
        "const hooks = await GaiaOpenCodePlugin({"
        " gaiaBridge: async () => ({ action: 'allow' }), client: { session: {} } });"
        "const module = await import(" + json.dumps(str(PLUGIN)) + ");"
        "console.log(JSON.stringify({ hooks: Object.keys(hooks),"
        " exports: Object.keys(module) }));"
    )
    result = subprocess.run(
        ["bun", "-e", script], capture_output=True, text=True, timeout=120, cwd=REPO_ROOT,
    )
    assert result.returncode == 0, result.stderr
    shape = json.loads(result.stdout.strip().splitlines()[-1])

    assert "permission.ask" not in shape["hooks"]
    assert {"tool.execute.before", "tool.execute.after", "shell.env"} <= set(shape["hooks"])
    assert "permissionDecisionLane" not in shape["exports"]


def test_own_adapter_translation_reads_no_approval_id_from_reason_text():
    textual = HookResponse(output={"hookSpecificOutput": {
        "permissionDecision": "deny",
        "permissionDecisionReason": "[T3_BLOCKED] approval_id: P-" + "a" * 32,
    }})
    structured = HookResponse(
        output={"hookSpecificOutput": {
            "permissionDecision": "deny", "permissionDecisionReason": "blocked",
        }},
        approval_id="P-" + "b" * 32,
    )

    assert "approval_id" not in OpenCodeAdapter._translate_policy_response(textual).output
    assert OpenCodeAdapter._translate_policy_response(structured).output["approval_id"] == "P-" + "b" * 32


def test_own_adapter_reactive_bash_block_names_its_approval_structurally(db_env, monkeypatch):
    monkeypatch.setattr(OpenCodeAdapter, "_identity_rejection", classmethod(lambda cls, e, t: None))
    monkeypatch.setattr(
        OpenCodeAdapter, "_child_binding_backstop_denial", classmethod(lambda cls, e: None),
    )
    monkeypatch.setattr(OpenCodeAdapter, "_policy_agent_type", classmethod(lambda cls, e: AGENT_ID))
    event = HookEvent(
        event_type=HookEventType.PRE_TOOL_USE, session_id=SESSION_ID,
        payload={"tool_name": "bash", "tool_input": {"command": COMMAND}},
        call_id="call-bash", host_agent_id="handle-1",
    )

    output = OpenCodeAdapter().adapt_pre_tool_use(event).output

    assert output["action"] == "deny", output
    from gaia.approvals.store import get_by_id

    row = get_by_id(output["approval_id"])
    assert row is not None and row["session_id"] == SESSION_ID and row["agent_id"] == AGENT_ID


def test_own_adapter_question_answers_never_activate_through_the_bridge(db_env, monkeypatch):
    """A decision reaches Gaia only through opencode-decide, never a label in a tool result."""
    approval_id = _pending()
    monkeypatch.setattr(OpenCodeAdapter, "_is_attested_control_plane", classmethod(lambda cls, e: True))
    event = HookEvent(
        event_type=HookEventType.POST_TOOL_USE, session_id=SESSION_ID,
        payload={
            "tool_name": "AskUserQuestion",
            "tool_input": {},
            "tool_response": {"answers": {"¿Publico la rama?": f"Approve once [{approval_id}]"}},
        },
        call_id="call-question",
    )

    OpenCodeAdapter().adapt_post_tool_use(event)

    from gaia.approvals.store import get_by_id

    assert get_by_id(approval_id)["status"] == "pending"


def test_own_adapter_presents_only_to_the_requesting_session_and_agent(db_env):
    approval_id = _pending()

    foreign_session = _present(db_env, approval_id, session_id="ses-orchestrator")
    foreign_agent = _present(db_env, approval_id, agent_id="developer")

    assert foreign_session[0] == 1 and "requesting session" in foreign_session[1]["error"]
    assert foreign_agent[0] == 1 and "requesting agent" in foreign_agent[1]["error"]
    assert _events(approval_id) == ["REQUESTED"]


def test_own_adapter_never_adopts_a_pending_without_requester(db_env):
    from gaia.approvals.store import get_by_id

    approval_id = _pending(session_id=None)

    code, emitted = _present(db_env, approval_id)

    assert code == 1 and "no requesting session" in emitted["error"]
    assert get_by_id(approval_id)["session_id"] is None
    assert _events(approval_id) == ["REQUESTED"]


def test_own_adapter_refuses_a_request_without_phrases(db_env):
    bare = {key: value for key, value in PRESENTABLE.items() if key not in {"what", "items"}}
    approval_id = _pending(bare)

    code, emitted = _present(db_env, approval_id)

    assert code == 1 and "--what (title)" in emitted["error"]
    assert _events(approval_id) == ["REQUESTED"]


def test_own_adapter_a_resumed_requester_reopens_its_question(db_env):
    approval_id = _pending()

    first = _present(db_env, approval_id, call_id="call-before-restart")
    resumed = _present(db_env, approval_id, call_id="call-after-restart")

    assert first[0] == 0 and resumed[0] == 0, (first, resumed)
    assert _events(approval_id) == ["REQUESTED", "SHOWN", "SHOWN"]
