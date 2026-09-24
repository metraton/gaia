"""OpenCode's own approval adapter (plan 76, task 5).

The installed OpenCode (1.18.32) never triggers a plugin's ``permission.ask``
hook: its bundle triggers tool.execute.before/after, shell.env and the chat and
compaction hooks, and nothing named permission. The consent lane that hook fed
is therefore retired, and a signature reaches the user only through the native
question tool. An approval is identified by structure, never read out of text,
and it is presented only to the live session and agent that requested it.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOKS_DIR = REPO_ROOT / "hooks"
for path in (REPO_ROOT, HOOKS_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from adapters.opencode import OpenCodeAdapter  # noqa: E402
from adapters.tool_policy import PolicyVerdict  # noqa: E402
from adapters.types import ConsentRequest, HookEvent, HookEventType  # noqa: E402

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
    textual = PolicyVerdict(
        decision="deny", reason="[T3_BLOCKED] approval_id: P-" + "a" * 32,
    )
    structured = PolicyVerdict(
        decision="deny", reason="blocked", approval_id="P-" + "b" * 32,
    )

    assert "approval_id" not in OpenCodeAdapter._format_policy_verdict(textual).output
    assert OpenCodeAdapter._format_policy_verdict(structured).output["approval_id"] == "P-" + "b" * 32


def test_own_adapter_neither_imports_nor_instantiates_the_claude_code_adapter():
    tree = ast.parse((REPO_ROOT / "hooks" / "adapters" / "opencode.py").read_text(encoding="utf-8"))

    imported = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("claude_code")
    ]
    named = [
        node for node in ast.walk(tree)
        if isinstance(node, (ast.Name, ast.Attribute))
        and "ClaudeCodeAdapter" in {getattr(node, "id", None), getattr(node, "attr", None)}
    ]
    assert imported == [] and named == []


def test_own_adapter_formats_the_shared_policy_verdict_in_its_own_protocol():
    consent = ConsentRequest(
        operation="/repo/hooks/x.py", kind="file", reason="protected", tier="T3_BLOCKED",
        approval_id="c" * 32,
    )
    pending = OpenCodeAdapter._format_policy_verdict(
        PolicyVerdict(consent=consent, approval_id="P-" + "c" * 32),
    )
    inline = OpenCodeAdapter._format_policy_verdict(PolicyVerdict(consent=replace(
        consent, approval_id=None, updated_input={"file_path": "/repo/hooks/x.py"},
    )))
    refused = OpenCodeAdapter._format_policy_verdict(PolicyVerdict(refusal="no command"))

    assert (pending.output, pending.exit_code) == (
        {"action": "deny", "reason": "protected", "approval_id": "P-" + "c" * 32}, 2,
    )
    assert (inline.output, inline.exit_code) == (
        {"action": "ask", "reason": "protected",
         "updated_input": {"file_path": "/repo/hooks/x.py"}}, 0,
    )
    assert (refused.output, refused.exit_code) == ({"action": "deny", "reason": "no command"}, 2)
    assert OpenCodeAdapter._format_policy_verdict(PolicyVerdict()).output == {"action": "allow"}


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


# --------------------------------------------------------------------------- #
# The question surface, driven through the real plugin, bridge and CLIs
# --------------------------------------------------------------------------- #

@pytest.fixture()
def driven_env(tmp_path, monkeypatch, bootstrapped_db_template):
    from modules.core.paths import clear_path_cache
    from tests.integration.test_opencode_consent_retry_e2e import _isolated_env

    env, db_path = _isolated_env(tmp_path / "state", bootstrapped_db_template)
    monkeypatch.chdir(env["WORKSPACE"])
    for key in ("GAIA_DB", "GAIA_DATA_DIR", "GAIA_OPENCODE_ATTESTATION_DIR"):
        monkeypatch.setenv(key, env[key])
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)
    clear_path_cache()
    return env, db_path


def _rendered(approval_id):
    from gaia.approvals.store import get_by_id
    from gaia.approvals.surface import render

    return render(json.loads(get_by_id(approval_id)["payload_json"]), approval_id)


def _asked(rendered, text):
    return {
        "header": rendered.question["header"],
        "question": text,
        "options": rendered.question["options"],
        "multiple": False,
        "custom": False,
    }


def _opencode_presentations(approval_id):
    """The SHOWN records `opencode-present` wrote; the core's own SHOWN is not one."""
    from gaia.approvals.store import get_history

    return [
        event for event in get_history(approval_id)
        if event["event_type"] == "SHOWN"
        and json.loads(event.get("metadata_json") or "{}").get("host") == "opencode"
    ]


def _decision(label, answer, request_id):
    return {"kind": "control-decision", "label": label, "answer": answer, "requestID": request_id}


def test_own_adapter_asks_the_renderer_single_string_and_closes_on_execute_after(driven_env):
    """The user is asked Gaia's string, not the model's; Approve arms the retry and after settles it."""
    from tests.integration import test_opencode_consent_retry_e2e as e2e

    env, db_path = driven_env
    approval_id = e2e._request_set(env, (e2e.FIRST_COMMAND,))
    rendered = _rendered(approval_id)

    driven = e2e._drive(env, [
        e2e._before("blocked", e2e.FIRST_COMMAND),
        _decision("approve", "approve", "question-signature"),
        e2e._before("retry", e2e.FIRST_COMMAND, call_id=e2e.RETRY_CALL_ID),
        {
            "kind": "after", "label": "settle", "sessionID": e2e.SESSION_ID,
            "callID": e2e.RETRY_CALL_ID, "tool": "bash", "command": e2e.FIRST_COMMAND,
            "metadata": {"exitCode": 0},
        },
    ])

    decision = e2e._step(driven, "approve")
    assert driven["presentations"][0]["approvalID"] == approval_id, driven["presentations"]
    assert decision["question"] == _asked(rendered, rendered.asked)
    instruction = driven["controlPrompts"][0]["body"]["parts"][0]["text"]
    assert decision["modelQuestion"]["question"] == "Gaia"
    assert rendered.text.splitlines()[0] not in instruction and approval_id not in instruction
    assert e2e._step(driven, "retry")["allowed"] is True
    assert e2e._control_closures(db_path) == [("decided", approval_id)]
    assert json.loads(e2e._grant(db_path, approval_id)["consumed_indexes_json"]) == [0]


def test_own_adapter_details_reasks_the_same_signature_with_the_renderer_details(driven_env):
    from tests.integration import test_opencode_consent_retry_e2e as e2e

    env, db_path = driven_env
    approval_id = e2e._request_set(env, (e2e.FIRST_COMMAND,))
    rendered = _rendered(approval_id)

    driven = e2e._drive(env, [
        e2e._before("blocked", e2e.FIRST_COMMAND),
        _decision("details", "details", "question-first"),
        {"kind": "observe-controls", "label": "after-details"},
        _decision("approve", "approve", "question-details"),
    ])

    assert e2e._step(driven, "details")["question"] == _asked(rendered, rendered.asked)
    assert e2e._step(driven, "approve")["question"] == _asked(rendered, rendered.asked_details)
    assert e2e._step(driven, "approve")["modelQuestion"]["question"] == "Gaia"
    assert e2e._step(driven, "after-details")["controlPromptCount"] == 2
    assert e2e._control_closures(db_path) == [
        ("details_requested", approval_id), ("decided", approval_id),
    ]
    assert len(_opencode_presentations(approval_id)) == 1
    assert e2e._approval_status(db_path, approval_id) == "approved"


@pytest.mark.parametrize("answers", [
    [["Approve, and also the next one"]],
    [["Approve", "Reject"]],
    [["Approve"], ["Approve"]],
])
def test_own_adapter_free_text_never_decides(driven_env, answers):
    from tests.integration import test_opencode_consent_retry_e2e as e2e

    env, db_path = driven_env
    approval_id = e2e._request_set(env, (e2e.FIRST_COMMAND,))

    e2e._drive(env, [
        e2e._before("blocked", e2e.FIRST_COMMAND),
        {**_decision("typed", "approve", "question-typed"), "answers": answers},
    ])

    assert e2e._approval_status(db_path, approval_id) == "pending"
    assert e2e._grant(db_path, approval_id) is None
    assert [reason for reason, _ in e2e._control_closures(db_path)] == ["reply_unreadable"]


def _request_set_expecting(env, *extra):
    from tests.integration import test_opencode_consent_retry_e2e as e2e

    argv = [sys.executable, str(GAIA_CLI), "approvals", "request-set"]
    for command in (e2e.FIRST_COMMAND, e2e.SECOND_COMMAND):
        argv += ["--command", command, "--does", "Publica una parte.", "--impact", "Queda visible."]
    argv += [
        "--what", "Publicar la rama y la imagen.", "--question", "¿Publico la rama y la imagen?",
        "--rollback", "revert the published revision",
        "--agent-id", e2e.AGENT_ID, "--session-id", e2e.SESSION_ID, "--json", *extra,
    ]
    result = subprocess.run(argv, cwd=env["WORKSPACE"], env=env, capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])["approval_id"]


def _after(label, call_id, command, exit_code):
    from tests.integration import test_opencode_consent_retry_e2e as e2e

    return {
        "kind": "after", "label": label, "sessionID": e2e.SESSION_ID, "callID": call_id,
        "tool": "bash", "command": command, "metadata": {"exitCode": exit_code},
    }


def test_own_adapter_same_index_and_declared_exit_as_the_core(driven_env):
    """A declared non-zero exit advances the set; an undeclared one freezes it at its index."""
    from tests.integration import test_opencode_consent_retry_e2e as e2e

    env, db_path = driven_env
    approval_id = _request_set_expecting(env, "--expect-exit", "1=1")

    driven = e2e._drive(env, [
        e2e._before("blocked", e2e.FIRST_COMMAND),
        _decision("approve", "approve", "question-set"),
        e2e._before("out-of-order", e2e.SECOND_COMMAND, call_id="call-out-of-order"),
        e2e._before("first", e2e.FIRST_COMMAND, call_id="call-first"),
        _after("first-done", "call-first", e2e.FIRST_COMMAND, 1),
        e2e._before("second", e2e.SECOND_COMMAND, call_id="call-second"),
        _after("second-done", "call-second", e2e.SECOND_COMMAND, 7),
    ])

    assert e2e._step(driven, "out-of-order")["allowed"] is False
    assert "out_of_order" in e2e._step(driven, "out-of-order")["error"]
    assert e2e._step(driven, "second")["allowed"] is True, driven["steps"]
    grant = e2e._grant(db_path, approval_id)
    assert (grant["status"], grant["failed_index"]) == ("FAILED", 1), grant


def test_own_adapter_same_directory_as_the_core(driven_env):
    """A retry run from another directory is not the sealed command, so it never consumes the grant."""
    from tests.integration import test_opencode_consent_retry_e2e as e2e

    env, db_path = driven_env
    approval_id = e2e._request_set(env, (e2e.FIRST_COMMAND,))
    elsewhere = str(Path(env["WORKSPACE"]).parent)

    driven = e2e._drive(env, [
        e2e._before("blocked", e2e.FIRST_COMMAND),
        _decision("approve", "approve", "question-directory"),
        {
            "kind": "before", "label": "elsewhere", "sessionID": e2e.SESSION_ID,
            "callID": "call-elsewhere", "tool": "bash",
            "args": {"command": e2e.FIRST_COMMAND, "workdir": elsewhere},
        },
    ])

    assert e2e._step(driven, "elsewhere")["allowed"] is False, driven["steps"]
    grant = e2e._grant(db_path, approval_id)
    assert (grant["status"], grant["next_index"], grant["reservation_tool_use_id"]) == ("PENDING", 0, None)


def test_own_adapter_asks_a_batch_one_signature_after_another(driven_env):
    from tests.integration import test_opencode_consent_retry_e2e as e2e

    env, db_path = driven_env
    first = e2e._request_set(env, (e2e.FIRST_COMMAND,))
    second = e2e._request_set(env, (e2e.SECOND_COMMAND,))

    driven = e2e._drive(env, [
        e2e._before("blocked-first", e2e.FIRST_COMMAND),
        e2e._before("blocked-second", e2e.SECOND_COMMAND, call_id="call-second"),
        {"kind": "lifecycle", "label": "idle", "sessionID": e2e.SESSION_ID, "eventType": "session.idle"},
        _decision("reject-first", "reject", "question-first"),
        _decision("approve-second", "approve", "question-second"),
    ], auto_safe_idle=False)

    assert [item["approvalID"] for item in driven["presentations"]] == [first, second]
    assert e2e._step(driven, "reject-first")["question"]["question"] == _rendered(first).asked
    assert e2e._step(driven, "approve-second")["question"]["question"] == _rendered(second).asked
    control_prompts = [p for p in driven["controlPrompts"] if p["path"]["id"] == e2e.SESSION_ID]
    assert len(control_prompts) == 2
    assert e2e._control_closures(db_path) == [("decided", first), ("decided", second)]
    assert e2e._approval_status(db_path, first) == "rejected"
    assert e2e._approval_status(db_path, second) == "approved"
