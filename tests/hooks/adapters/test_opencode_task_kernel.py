"""Task-dispatch kernel prepend for the OpenCode adapter (plan 65, task 9).

OpenCode has no event that reliably fires before a dispatched subagent's
first tool call the way Claude Code's SubagentStart does -- OpenCode's own
start-adjacent signal (``message.part.updated``) only reports the
callID<->child-session binding, sometimes after the child has already acted.
So the kernel is embedded directly into the Task call's own ``prompt``
argument, in place, at the same PreToolUse call that births the row --
exercising the SAME mechanism ``applyUpdatedInput`` (T6) already applies to
every other tool's ``updated_input``.

These tests mock every collaborator at the module boundary it is imported
from (``ClaudeCodeAdapter.adapt_pre_tool_use`` for the delegated birth+
validation, ``gaia.store.writer.claim_dispatch_row`` for the row claim, and
``modules.context.kernel_builder.build_dispatch_kernel`` for the render) so
this subset stays a pure adapter test, per test_opencode.py's own convention
of monkeypatching ``ClaudeCodeAdapter`` methods rather than touching the DB.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HOOKS_DIR = Path(__file__).parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from adapters.opencode import CLOSING_RULES_KERNEL, OpenCodeAdapter
from adapters.types import HookResponse


def _bypass_control_plane_attestation(monkeypatch):
    """These tests exercise the kernel-prepend branch alone -- attestation
    provenance (who may issue a "task" dispatch at all) is a separate,
    already-covered concern (test_opencode_attestation_provenance.py)."""
    monkeypatch.setattr(
        OpenCodeAdapter, "_is_attested_control_plane",
        classmethod(lambda cls, event: True),
    )


def _task_event(prompt: str = "do the thing"):
    return OpenCodeAdapter().parse_event(json.dumps({
        "event": "tool.execute.before",
        "sessionID": "ses-parent",
        "callID": "call-task-1",
        "tool": "task",
        "args": {
            "description": "dispatch gaia-system",
            "prompt": prompt,
            "subagent_type": "gaia-system",
        },
    }))


def test_task_dispatch_births_row_with_dispatch_tool_use_id_from_callid(monkeypatch):
    """AC-1(a): the delegated call carries the OpenCode callID as the
    correlation key the birth path stamps into dispatch_tool_use_id."""
    from adapters.claude_code import ClaudeCodeAdapter

    _bypass_control_plane_attestation(monkeypatch)
    seen = {}

    def fake_policy(_self, event):
        seen["tool_use_id"] = event.payload.get("tool_use_id")
        return HookResponse(output={})

    monkeypatch.setattr(ClaudeCodeAdapter, "adapt_pre_tool_use", fake_policy)
    monkeypatch.setattr(
        "gaia.store.writer.claim_dispatch_row", lambda **kwargs: None
    )

    OpenCodeAdapter().adapt_pre_tool_use(_task_event())

    assert seen["tool_use_id"] == "call-task-1"


def test_task_dispatch_prepends_rendered_kernel_while_preserving_original_prompt(monkeypatch):
    """AC-1(b)/AC-4: updated_input carries the kernel prepended to the
    ORIGINAL prompt, and every other Task argument is left untouched by the
    caller (field-by-field merge, T6) since only ``prompt`` is rewritten."""
    from adapters.claude_code import ClaudeCodeAdapter

    _bypass_control_plane_attestation(monkeypatch)
    monkeypatch.setattr(
        ClaudeCodeAdapter, "adapt_pre_tool_use",
        lambda _self, event: HookResponse(output={}),
    )
    captured_claim = {}

    def fake_claim(**kwargs):
        captured_claim.update(kwargs)
        return {"contract_id": "a1.tok", "agent_id": "a1"}

    monkeypatch.setattr("gaia.store.writer.claim_dispatch_row", fake_claim)
    monkeypatch.setattr(
        "modules.context.kernel_builder.build_dispatch_kernel",
        lambda row: "# Your Contract\n\ncontract_id: a1.tok\nagent_id:    a1",
    )

    response = OpenCodeAdapter().adapt_pre_tool_use(
        _task_event(prompt="original dispatch prompt")
    )

    assert captured_claim.get("dispatch_tool_use_id") == "call-task-1"
    assert response.output["action"] == "allow"
    updated_prompt = response.output["updated_input"]["prompt"]
    assert updated_prompt.startswith("# Your Contract")
    assert updated_prompt.endswith("original dispatch prompt")
    assert updated_prompt.index("# Your Contract") < updated_prompt.index(
        "original dispatch prompt"
    )


def test_task_dispatch_appends_closing_rules_between_kernel_and_original_prompt(monkeypatch):
    """gate 1041(a) (plan 65, task 553): the adapter's own render/inject path
    (T9, this module) appends the two contract-closing rules -- mandatory
    ``--draft-id`` from the first call, ``finalize`` as a separate last step
    (agent-protocol principles 2 and 10) -- to the kernel it injects, with
    the original prompt preserved after them. This is an ADAPTER-side append,
    never a ``kernel_builder.build_dispatch_kernel`` change: the mock below
    returns a kernel with none of this text, so its presence in the merged
    prompt proves the OpenCode adapter added it, not the (untouched,
    data-only) kernel builder."""
    from adapters.claude_code import ClaudeCodeAdapter

    _bypass_control_plane_attestation(monkeypatch)
    monkeypatch.setattr(
        ClaudeCodeAdapter, "adapt_pre_tool_use",
        lambda _self, event: HookResponse(output={}),
    )
    monkeypatch.setattr(
        "gaia.store.writer.claim_dispatch_row",
        lambda **kwargs: {"contract_id": "a1.tok", "agent_id": "a1"},
    )
    monkeypatch.setattr(
        "modules.context.kernel_builder.build_dispatch_kernel",
        lambda row: "# Your Contract\n\ncontract_id: a1.tok\nagent_id:    a1",
    )

    response = OpenCodeAdapter().adapt_pre_tool_use(
        _task_event(prompt="original dispatch prompt")
    )

    updated_prompt = response.output["updated_input"]["prompt"]
    assert "--draft-id" in CLOSING_RULES_KERNEL
    assert "finalize" in CLOSING_RULES_KERNEL
    assert CLOSING_RULES_KERNEL in updated_prompt
    kernel_index = updated_prompt.index("# Your Contract")
    rules_index = updated_prompt.index(CLOSING_RULES_KERNEL)
    prompt_index = updated_prompt.index("original dispatch prompt")
    assert kernel_index < rules_index < prompt_index


def test_task_dispatch_degrades_to_plain_allow_when_claim_finds_nothing(monkeypatch):
    """A birth/claim miss (writer error, already-claimed row, degraded
    birth) must never block or corrupt the dispatch -- the prompt is
    forwarded exactly as the delegated call returned it."""
    from adapters.claude_code import ClaudeCodeAdapter

    _bypass_control_plane_attestation(monkeypatch)
    monkeypatch.setattr(
        ClaudeCodeAdapter, "adapt_pre_tool_use",
        lambda _self, event: HookResponse(output={}),
    )
    monkeypatch.setattr(
        "gaia.store.writer.claim_dispatch_row", lambda **kwargs: None
    )

    response = OpenCodeAdapter().adapt_pre_tool_use(_task_event())

    assert response.output == {"action": "allow"}


def test_task_dispatch_denied_by_policy_never_reaches_the_claim_step(monkeypatch):
    """A denied/asked Task dispatch has no row to claim a kernel from --
    the claim step must not even run."""
    from adapters.claude_code import ClaudeCodeAdapter

    called = {"claim": False}

    def fail_if_called(**kwargs):
        called["claim"] = True
        return None

    monkeypatch.setattr(
        ClaudeCodeAdapter, "adapt_pre_tool_use",
        lambda _self, event: HookResponse(
            output={"action": "deny", "reason": "blocked agent"}, exit_code=2,
        ),
    )
    monkeypatch.setattr("gaia.store.writer.claim_dispatch_row", fail_if_called)

    response = OpenCodeAdapter().adapt_pre_tool_use(_task_event())

    assert response.output["action"] == "deny"
    assert called["claim"] is False
