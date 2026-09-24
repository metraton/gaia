#!/usr/bin/env python3
"""One host question event must decide EVERY signature the user answered.

Provenance -- the recorded incident, not invention. On 2026-08-19 an agent
presented TWO protected-path approvals in ONE ``AskUserQuestion`` call, the user
approved BOTH, and only the first activated; re-presenting the second alone
worked. The host answers every question of a call independently, so a
two-signature call carries two answers.

Since plan 76 task 4 each answer is tied to its own signature by the position
the PreToolUse hook recorded under the call's tool_use_id, not by an id in the
label. These tests drive the real hook entry points (``adapt_pre_tool_use`` then
``adapt_post_tool_use``) on requests sealed by the real core, and read the
durable non-activation record back out of the store.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from gaia.store import writer

SESSION = "s-multi-decision"
REQUESTER = "gaia-system"
REPO = "/tmp/multi-decision-repo"


@pytest.fixture(autouse=True)
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("GAIA_DB", raising=False)
    con = sqlite3.connect(tmp_path / "gaia.db")
    con.executescript(writer._SCHEMA_PATH.read_text())
    con.commit()
    con.close()
    return tmp_path / "gaia.db"


def _request(n: int) -> str:
    from gaia.approvals import core

    return core.request_command_set(
        [{"command": f"git push origin feat/multi-{n}", "cwd": REPO,
          "does": f"git push: sube la rama {n}.", "impact": "La rama queda publicada."}],
        what=f"Publicar la rama {n}.", question=f"¿Publico la rama {n}?",
        session_id=SESSION, agent_id=REQUESTER,
    )


def _event(kind: str, payload: dict):
    from adapters.types import HookEvent, HookEventType

    event_type = HookEventType.PRE_TOOL_USE if kind == "PreToolUse" else HookEventType.POST_TOOL_USE
    return HookEvent(event_type=event_type, session_id=SESSION, payload={
        "hook_event_name": kind, "tool_name": "AskUserQuestion", "session_id": SESSION,
        "tool_use_id": "toolu_multi", **payload,
    })


def _ask_and_answer(approval_ids: list[str], labels: list) -> dict:
    """Ask the batch Gaia builds for ``approval_ids`` and answer position i with ``labels[i]``."""
    from adapters.claude_code import ClaudeCodeAdapter
    from gaia.approvals import core

    questions = [s.question for s in core.question_batch(approval_ids)]
    adapter = ClaudeCodeAdapter()
    adapter.adapt_pre_tool_use(_event("PreToolUse", {"tool_input": {"questions": questions}}))
    answers = {q["question"]: label for q, label in zip(questions, labels) if label is not None}
    return adapter.adapt_post_tool_use(_event("PostToolUse", {
        "tool_input": {"questions": questions},
        "tool_response": {"questions": questions, "answers": answers},
    })).output


def _status(approval_id: str) -> str:
    from gaia.approvals.store import get_by_id

    return get_by_id(approval_id)["status"]


def _non_activation_records() -> list[dict]:
    """Read the durable non-activation records back OUT of the store, never from a log."""
    from gaia.approvals.decision_audit import DECISION_NOT_ACTIVATED_EVENT
    from gaia.store.reader import cross_surface_query

    rows = cross_surface_query(surface="harness_events", type=DECISION_NOT_ACTIVATED_EVENT, last=50)
    return [json.loads(r["raw"]["payload"] or "{}") for r in rows]


@pytest.mark.parametrize("count", [2, 3, 4])
def test_every_approved_signature_in_one_call_activates(count):
    ids = [_request(n) for n in range(1, count + 1)]

    _ask_and_answer(ids, ["Approve"] * count)

    assert [_status(a) for a in ids] == ["approved"] * count


def test_a_mixed_call_decides_each_signature_by_its_own_answer():
    ids = [_request(n) for n in range(1, 4)]

    _ask_and_answer(ids, ["Reject", "Approve", None])

    assert [_status(a) for a in ids] == ["rejected", "approved", "pending"]


def test_an_approve_that_activates_nothing_is_recorded_and_withholds_no_other():
    from adapters.claude_code import ClaudeCodeAdapter
    from gaia.approvals import core, store

    first, second = _request(1), _request(2)
    questions = [s.question for s in core.question_batch([first, second])]
    ClaudeCodeAdapter().adapt_pre_tool_use(
        _event("PreToolUse", {"tool_input": {"questions": questions}})
    )
    store.reject(first, "ses-elsewhere")
    ClaudeCodeAdapter().adapt_post_tool_use(_event("PostToolUse", {
        "tool_input": {"questions": questions},
        "tool_response": {"questions": questions,
                          "answers": {q["question"]: "Approve" for q in questions}},
    }))

    assert _status(second) == "approved"
    records = _non_activation_records()
    assert [(r.get("reason"), r.get("approval_id")) for r in records] == [
        ("activation_failed", first)
    ]


def test_an_answer_that_decides_nothing_leaves_no_record():
    first = _request(1)

    _ask_and_answer([first], ["Bueno, eso es algo que podríamos discutir."])

    assert _status(first) == "pending"
    assert _non_activation_records() == []
