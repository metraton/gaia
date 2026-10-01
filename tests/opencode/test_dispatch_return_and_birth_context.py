"""What a Task dispatch carries in OpenCode: the contract summary back to the orchestrator, the events digest out to the specialist."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
for _path in (str(_ROOT), str(_ROOT / "hooks")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from adapters.opencode import CLOSING_RULES_KERNEL, OpenCodeAdapter
from adapters.tool_policy import ToolPolicy
from gaia.store.writer import (
    bind_harness_child_session,
    claim_dispatch_row,
    insert_dispatched_handoff,
)
from tests.fixtures.agent_ids import valid_agent_id

PLUGIN = _ROOT / "opencode" / "plugin.ts"
PARENT_SESSION = "ses-parent-t8"
CHILD_SESSION = "ses-child-t8"
DIGEST = "# Recent Session Events (last 24h)\n- [T3_BLOCKED] git push refused 2h ago"


@pytest.fixture(autouse=True)
def _isolated_gaia_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    return tmp_path


def _bound_dispatch(db: Path, call_id: str) -> None:
    agent_id = valid_agent_id("at8return")
    insert_dispatched_handoff(
        f"{agent_id}.t8cafe0001", agent_id, "me",
        session_id=PARENT_SESSION, db_path=db,
        agent_name="gaia-system", kind="investigation",
        dispatch_tool_use_id=call_id,
    )
    claim_dispatch_row(dispatch_tool_use_id=call_id, db_path=db)
    bind_harness_child_session(
        dispatch_tool_use_id=call_id, harness_agent_id=CHILD_SESSION, db_path=db,
    )


def _task_after(call_id: str, metadata: dict | None = None):
    return OpenCodeAdapter().parse_event(json.dumps({
        "event": "tool.execute.after",
        "sessionID": PARENT_SESSION,
        "callID": call_id,
        "tool": "task",
        "args": {"description": "d", "prompt": "p", "subagent_type": "gaia-system"},
        "result": {
            "output": "child finished",
            "metadata": {"sessionId": CHILD_SESSION} if metadata is None else metadata,
            "exit_code": 0,
            "is_error": False,
        },
    }))


def test_contract_summary_is_the_shared_policy_line_beside_the_task_result(tmp_path):
    _bound_dispatch(tmp_path / "gaia.db", "call-t8-summary")

    response = OpenCodeAdapter().adapt_post_tool_use(_task_after("call-t8-summary"))

    expected = ToolPolicy._build_contract_summary_context(
        {"tool_response": {"agentId": CHILD_SESSION}, "session_id": PARENT_SESSION}
    )
    assert expected
    assert response.output == {"action": "allow", "additional_context": expected}


def test_contract_summary_is_absent_when_the_task_result_names_no_child():
    response = OpenCodeAdapter().adapt_post_tool_use(_task_after("call-t8-orphan", metadata={}))

    assert response.output == {"action": "allow"}


def _plugin_after(bridge_after: dict) -> dict:
    script = f'''
      const {{ GaiaOpenCodePlugin }} = await import({json.dumps(str(PLUGIN))})
      const gaiaBridge = async (event) => {{
        if (event.event === "identity.attest") return {{ action: "allow", attestation: "attested" }}
        if (event.event === "tool.execute.after") return {json.dumps(bridge_after)}
        return {{ action: "allow" }}
      }}
      const hooks = await GaiaOpenCodePlugin({{
        gaiaBridge,
        client: {{ session: {{ messages: async () => ({{ data: [] }}) }} }},
      }})
      await hooks.event({{ event: {{
        type: "message.updated",
        properties: {{ info: {{ role: "assistant", sessionID: "root", agent: "gaia-orchestrator" }} }},
      }} }})
      const args = {{ subagent_type: "gaia-system", prompt: "p" }}
      await hooks["tool.execute.before"]({{ sessionID: "root", callID: "task-call", tool: "task" }}, {{ args }})
      const output = {{ output: "child finished\\n", metadata: {{ sessionId: "ses-child" }} }}
      await hooks["tool.execute.after"]({{ sessionID: "root", callID: "task-call", tool: "task", args }}, output)
      console.log(JSON.stringify(output))
    '''
    result = subprocess.run(["bun", "-e", script], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_contract_summary_from_the_bridge_is_appended_to_the_task_output():
    line = "Contract a1.tok: launched, not finalized yet -- gaia contract view a1.tok"

    output = _plugin_after({"action": "allow", "additional_context": line})

    assert output["output"] == f"child finished\n{line}\n"
    assert output["output"].count(line) == 1


def test_contract_summary_absent_leaves_the_task_output_untouched():
    output = _plugin_after({"action": "allow"})

    assert output["output"] == "child finished\n"


def _task_before(prompt: str):
    return OpenCodeAdapter().parse_event(json.dumps({
        "event": "tool.execute.before",
        "sessionID": PARENT_SESSION,
        "callID": "call-t8-birth",
        "tool": "task",
        "args": {"description": "dispatch gaia-system", "prompt": prompt, "subagent_type": "gaia-system"},
    }))


def _dispatch_with_digest(monkeypatch, digest):
    monkeypatch.setattr(
        OpenCodeAdapter, "_is_attested_control_plane", classmethod(lambda cls, event: True),
    )
    monkeypatch.setattr(
        "modules.session.session_event_injector.build_session_events", lambda parameters: digest,
    )
    return OpenCodeAdapter().adapt_pre_tool_use(_task_before("OBJECTIVE_T8_DIGEST project=gaia"))


def test_events_digest_rides_the_task_prompt_ahead_of_the_kernel(monkeypatch):
    response = _dispatch_with_digest(monkeypatch, DIGEST)

    prompt = response.output["updated_input"]["prompt"]
    assert prompt.count(DIGEST) == 1
    assert prompt.count("# Your Contract") == 1
    assert prompt.index(DIGEST) < prompt.index("# Your Contract")
    assert prompt.count("OBJECTIVE_T8_DIGEST") == 1
    assert prompt.endswith(CLOSING_RULES_KERNEL)


def test_events_digest_absent_leaves_only_the_kernel(monkeypatch):
    response = _dispatch_with_digest(monkeypatch, None)

    prompt = response.output["updated_input"]["prompt"]
    assert prompt.startswith("# Your Contract")
    assert "# Recent Session Events" not in prompt
