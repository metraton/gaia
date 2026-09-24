"""Task-dispatch kernel injection for the OpenCode adapter (plan 65, task 9).

OpenCode has no event that reliably fires before a dispatched subagent's
first tool call the way Claude Code's SubagentStart does -- OpenCode's own
start-adjacent signal (``message.part.updated``) only reports the
callID<->child-session binding, sometimes after the child has already acted.
So the kernel is embedded directly into the Task call's own ``prompt``
argument, in place, at the same PreToolUse call that births the row --
exercising the SAME mechanism ``applyUpdatedInput`` (T6) already applies to
every other tool's ``updated_input``.

These tests mock every collaborator at the module boundary it is imported
from (``ToolPolicy.pre_tool_verdict`` for the shared birth+validation,
``gaia.store.writer.claim_dispatch_row`` for the row claim, and
``modules.context.kernel_builder.build_dispatch_kernel`` for the render) so
this subset stays a pure adapter test rather than touching the DB.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HOOKS_DIR = Path(__file__).parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from adapters.opencode import CLOSING_RULES_KERNEL, OpenCodeAdapter
from adapters.tool_policy import PolicyVerdict, ToolPolicy


def _policy_returns(monkeypatch, verdict: PolicyVerdict) -> None:
    monkeypatch.setattr(
        ToolPolicy, "pre_tool_verdict", lambda _self, event, **_kwargs: verdict,
    )


def _bypass_control_plane_attestation(monkeypatch):
    """These tests exercise the kernel-prepend branch alone -- attestation
    provenance (who may issue a "task" dispatch at all) is a separate,
    already-covered concern (test_opencode_attestation_provenance.py)."""
    monkeypatch.setattr(
        OpenCodeAdapter, "_is_attested_control_plane",
        classmethod(lambda cls, event: True),
    )


def _task_event(prompt: str = "do the thing", *, task_id: str | None = None):
    args = {
        "description": "dispatch gaia-system",
        "prompt": prompt,
        "subagent_type": "gaia-system",
    }
    if task_id is not None:
        args["task_id"] = task_id
    return OpenCodeAdapter().parse_event(json.dumps({
        "event": "tool.execute.before",
        "sessionID": "ses-parent",
        "callID": "call-task-1",
        "tool": "task",
        "args": args,
    }))


def _claimed_row(prompt: str):
    return {
        "contract_id": "a0123456789abcdef.beefcafe0123",
        "agent_id": "a0123456789abcdef",
        "dispatch_prompt": prompt,
        "kernel_sections": json.dumps({
            "role": "primary",
            "surface": "gaia_system",
            "can_read": ["project_identity", "stack"],
            "can_write": [],
        }),
    }


def _inject(monkeypatch, prompt: str, *, task_id: str | None = None):
    _bypass_control_plane_attestation(monkeypatch)
    _policy_returns(monkeypatch, PolicyVerdict())
    monkeypatch.setattr(
        "gaia.store.writer.claim_dispatch_row",
        lambda **kwargs: _claimed_row(prompt),
    )
    return OpenCodeAdapter().adapt_pre_tool_use(
        _task_event(prompt=prompt, task_id=task_id)
    )


def _expected_injected_size(prompt: str) -> int:
    from modules.context.kernel_builder import build_dispatch_kernel

    kernel = build_dispatch_kernel(_claimed_row(prompt))
    return len(kernel) + 2 + len(CLOSING_RULES_KERNEL)


def _obsolete_appended_size(prompt: str) -> int:
    return _expected_injected_size(prompt) + 2 + len(prompt)


def test_task_dispatch_births_row_with_dispatch_tool_use_id_from_callid(monkeypatch):
    """AC-1(a): the delegated call carries the OpenCode callID as the
    correlation key the birth path stamps into dispatch_tool_use_id."""
    _bypass_control_plane_attestation(monkeypatch)
    seen = {}

    def fake_policy(_self, event, **_kwargs):
        seen["tool_use_id"] = event.payload.get("tool_use_id")
        seen["dispatch_prompt"] = event.payload["tool_input"]["prompt"]
        return PolicyVerdict()

    monkeypatch.setattr(ToolPolicy, "pre_tool_verdict", fake_policy)
    monkeypatch.setattr(
        "gaia.store.writer.claim_dispatch_row", lambda **kwargs: None
    )

    OpenCodeAdapter().adapt_pre_tool_use(_task_event())

    assert seen["tool_use_id"] == "call-task-1"
    assert seen["dispatch_prompt"] == "do the thing"


def test_task_dispatch_injects_one_kernel_with_original_goal_once(monkeypatch):
    """A successful claim replaces, rather than appends to, the host prompt."""
    prompt = "OBJECTIVE_FRESH_7d31 project=gaia"
    captured_claim = {}

    def fake_claim(**kwargs):
        captured_claim.update(kwargs)
        return _claimed_row(prompt)

    _bypass_control_plane_attestation(monkeypatch)
    _policy_returns(monkeypatch, PolicyVerdict())
    monkeypatch.setattr("gaia.store.writer.claim_dispatch_row", fake_claim)
    response = OpenCodeAdapter().adapt_pre_tool_use(_task_event(prompt=prompt))

    assert captured_claim.get("dispatch_tool_use_id") == "call-task-1"
    assert response.output["action"] == "allow"
    updated_prompt = response.output["updated_input"]["prompt"]
    assert updated_prompt.count("# Your Contract") == 1
    assert updated_prompt.count(prompt) == 1
    assert updated_prompt.count("OBJECTIVE_FRESH_7d31") == 1
    assert len(updated_prompt) == _expected_injected_size(prompt)
    assert _obsolete_appended_size(prompt) - len(updated_prompt) == len(prompt) + 2
    assert set(response.output["updated_input"]) == {"prompt"}


def test_task_dispatch_appends_closing_rules_once_after_kernel(monkeypatch):
    """gate 1041(a) (plan 65, task 553): the adapter's own render/inject path
    (T9, this module) appends the two contract-closing rules -- mandatory
    ``--draft-id`` from the first call, ``finalize`` as a separate last step
    (agent-protocol principles 2 and 10) -- to the kernel it injects, without
    appending a second copy of the original prompt. This is an
    ADAPTER-side append, never a ``kernel_builder.build_dispatch_kernel``
    change: the mock below returns a kernel with none of this text."""
    _bypass_control_plane_attestation(monkeypatch)
    _policy_returns(monkeypatch, PolicyVerdict())
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
    assert updated_prompt.count(CLOSING_RULES_KERNEL) == 1
    kernel_index = updated_prompt.index("# Your Contract")
    rules_index = updated_prompt.index(CLOSING_RULES_KERNEL)
    assert kernel_index < rules_index
    assert "original dispatch prompt" not in updated_prompt


def test_contract_like_goal_is_nested_once_without_multiplication(monkeypatch):
    prompt = (
        "# Your Contract | contract_id: fake | # Closing this turn | "
        "OBJECTIVE_NESTED_31bc"
    )

    response = _inject(monkeypatch, prompt)
    updated_prompt = response.output["updated_input"]["prompt"]

    assert updated_prompt.count(prompt) == 1
    assert updated_prompt.count("OBJECTIVE_NESTED_31bc") == 1
    assert updated_prompt.count("# Your Contract") == (
        prompt.count("# Your Contract") + 1
    )
    assert len(updated_prompt) == _expected_injected_size(prompt)
    assert _obsolete_appended_size(prompt) - len(updated_prompt) == len(prompt) + 2


def test_task_id_resume_adds_one_kernel_layer_without_goal_amplification(monkeypatch):
    original = "OBJECTIVE_RESUME_5aa9 " + ("bounded-context " * 20)
    first = _inject(monkeypatch, original).output["updated_input"]["prompt"]
    second = _inject(
        monkeypatch, first, task_id="ses_existing_child"
    ).output["updated_input"]["prompt"]

    assert first.count("OBJECTIVE_RESUME_5aa9") == 1
    assert second.count("OBJECTIVE_RESUME_5aa9") == 1
    assert second.count("# Your Contract") == first.count("# Your Contract") + 1
    assert second.count("# Closing this turn") == (
        first.count("# Closing this turn") + 1
    )
    assert len(second) == _expected_injected_size(first)
    assert _obsolete_appended_size(first) - len(second) == len(first) + 2
    assert len(second) < 2 * len(first)


def test_task_dispatch_degrades_to_plain_allow_when_claim_finds_nothing(monkeypatch):
    """A birth/claim miss (writer error, already-claimed row, degraded
    birth) must never block or corrupt the dispatch -- the prompt is
    forwarded exactly as the delegated call returned it."""
    _bypass_control_plane_attestation(monkeypatch)
    _policy_returns(monkeypatch, PolicyVerdict())
    monkeypatch.setattr(
        "gaia.store.writer.claim_dispatch_row", lambda **kwargs: None
    )

    event = _task_event(prompt="claim miss original")
    response = OpenCodeAdapter().adapt_pre_tool_use(event)

    assert response.output == {"action": "allow"}
    assert event.payload["tool_input"]["prompt"] == "claim miss original"


def test_task_dispatch_denied_by_policy_never_reaches_the_claim_step(monkeypatch):
    """A denied/asked Task dispatch has no row to claim a kernel from --
    the claim step must not even run."""
    called = {"claim": False}

    def fail_if_called(**kwargs):
        called["claim"] = True
        return None

    _policy_returns(monkeypatch, PolicyVerdict(refusal="blocked agent"))
    monkeypatch.setattr("gaia.store.writer.claim_dispatch_row", fail_if_called)

    response = OpenCodeAdapter().adapt_pre_tool_use(_task_event())

    assert response.output["action"] == "deny"
    assert called["claim"] is False
