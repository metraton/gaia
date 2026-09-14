"""A contract rejection survives any exception raised after the verdict.

The SubagentStop gate emits its verdict early and the result dict that carries
``exit_code=2`` is assembled far below it. Every call in between is an
opportunity to lose the rejection, because the adapter's own handler rebuilds
that dict from scratch and a rebuilt dict carries no rejection -- so a turn
whose contract was REJECTED closed at ``exit_code=0``, indistinguishable from a
turn that passed.

The targets below are chosen to assert the property rather than a call list:
``process_update_contracts`` was not among the calls the audit enumerated, and
it must be covered all the same. A fix that isolated the known callers one by
one would pass a test naming only those callers and still lose the next
rejection someone's new call drops.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_HOOKS_DIR = str(Path(__file__).resolve().parents[2] / "hooks")
_REPO_ROOT = str(Path(__file__).resolve().parents[2])
for _p in (_HOOKS_DIR, _REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from adapters.claude_code import ClaudeCodeAdapter  # noqa: E402
from gaia.store.writer import (  # noqa: E402
    insert_dispatched_handoff,
    stamp_harness_agent_id,
)
from tests.fixtures.agent_ids import valid_agent_id  # noqa: E402

WORKSPACE = "me"
AGENT_ID = valid_agent_id("rejection-latch")
HARNESS_AGENT_ID = valid_agent_id("rejection-latch-harness")
SESSION_ID = "sess-rejection-latch"

# Each one runs AFTER the gate has produced its verdict and BEFORE the result
# dict exists, and none is wrapped by the adapter.
_POST_VERDICT_CALLS = (
    "modules.security.approval_cleanup.cleanup",
    "modules.context.context_writer.process_update_contracts",
    "modules.audit.workflow_recorder.record",
    "modules.audit.workflow_auditor.audit",
    "modules.memory.episode_writer.write",
    "modules.agents.contract_validator.validate_approval_request",
)

_EVIDENCE_KEYS = (
    "patterns_checked", "files_checked", "commands_run", "key_outputs",
    "verbatim_outputs", "cross_layer_impacts", "open_gaps",
)


@pytest.fixture(autouse=True)
def _isolated_substrate(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "gaia_data"))
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    monkeypatch.setenv("GAIA_WORKSPACE", WORKSPACE)
    monkeypatch.delenv("GAIA_CONTRACT_FULL_VERDICT_GATE", raising=False)
    yield


@pytest.fixture()
def default_db(tmp_path) -> Path:
    return tmp_path / "gaia_data" / "gaia.db"


def _envelope() -> dict:
    envelope = {
        "agent_status": {
            "agent_state": "COMPLETE",
            "agent_id": AGENT_ID,
            "pending_steps": [],
            "next_action": "done",
        },
        "evidence_report": {k: [] for k in _EVIDENCE_KEYS},
        "consolidation_report": None,
        "approval_request": None,
    }
    envelope["evidence_report"]["verification"] = {
        "method": "test", "result": "pass", "details": "suite green",
    }
    return envelope


def _fenced_output() -> str:
    return (
        "All done.\n\n```agent_contract_handoff\n"
        + json.dumps(_envelope())
        + "\n```\n"
    )


def _rejecting_turn(db_path: Path, suffix: str) -> str:
    """Birth a row and leave it UNFINALIZED, which the gate must reject.

    The fence above is well-formed on purpose: the rejection has to come from
    the unfinalized row, so that what the test breaks afterwards is the only
    variable.
    """
    contract_id = f"{AGENT_ID}.{suffix}"
    insert_dispatched_handoff(
        contract_id=contract_id,
        agent_id=AGENT_ID,
        workspace=WORKSPACE,
        session_id=SESSION_ID,
        db_path=db_path,
    )
    stamp_harness_agent_id(contract_id, HARNESS_AGENT_ID, db_path=db_path)
    return contract_id


def _subagent_stop_event(adapter: ClaudeCodeAdapter):
    payload = {
        "hook_event_name": "SubagentStop",
        "session_id": SESSION_ID,
        "agent_type": "gaia-system",
        "agent_id": HARNESS_AGENT_ID,
        "agent_transcript_path": "",
        "last_assistant_message": _fenced_output(),
        "stop_reason": "end_turn",
        "cwd": "/tmp",
    }
    return adapter.parse_event(json.dumps(payload))


def test_unfinalized_row_rejects_with_exit_2(default_db):
    """The control: without an injected failure this turn really is rejected.

    Without it a parametrized failure below could pass by rejecting nothing.
    """
    _rejecting_turn(default_db, "control")

    response = ClaudeCodeAdapter().adapt_subagent_stop(
        _subagent_stop_event(ClaudeCodeAdapter())
    )

    assert response.exit_code == 2
    assert response.output.get("contract_rejected") is True


@pytest.mark.parametrize("target", _POST_VERDICT_CALLS)
def test_rejection_survives_an_exception_raised_after_the_verdict(
    default_db, monkeypatch, target,
):
    _rejecting_turn(default_db, "latched")

    def _raise(*_args, **_kwargs):
        raise RuntimeError(f"{target} exploded after the verdict")

    monkeypatch.setattr(target, _raise)

    adapter = ClaudeCodeAdapter()
    response = adapter.adapt_subagent_stop(_subagent_stop_event(adapter))

    assert response.exit_code == 2, (
        f"a rejection was downgraded to exit {response.exit_code} because "
        f"{target} raised after the gate had already rejected the turn"
    )
    assert response.output.get("contract_rejected") is True
    assert response.output.get("contract_rejection_reason")
