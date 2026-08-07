"""Nonce preservation reads from the SAME source the row-first gate treats as
authoritative for the turn -- never from the fence alone.

Brief context: ``resolve_subagent_stop_gate`` (hooks/adapters/claude_code.py)
already inverted the SubagentStop contract-pass/reject VERDICT to prefer the
turn's own persisted ``agent_contract_handoffs`` row over the fenced
``agent_contract_handoff`` block the model retypes into its final message.
``preserved_nonces`` -- the set of ``approval_id``s ``adapt_subagent_stop``
protects from expiry -- was NOT part of that inversion: it was built by
reading ONLY ``parsed_contract`` (the fence), via ``parse_contract
(agent_output)``. A turn that recorded an APPROVAL_REQUEST on its row (via
`gaia contract fill`/`finalize`) but emitted no fenced declaration in its
final response text lost its nonce silently -- exactly the subsystem (T3
approvals) where a silent loss is most expensive.

These tests exercise the REAL production entry point,
``ClaudeCodeAdapter.adapt_subagent_stop`` -- driven exactly as Claude Code
drives it (a JSON SubagentStop payload through ``parse_event``), against a
real, isolated SQLite database (mirrors
tests/hooks/test_subagent_stop_gate_row_first_e2e.py's fixtures). No gate
function or writer is stubbed; only ``modules.security.approval_cleanup.cleanup``
is patched, to observe the ``preserve_nonces`` argument the hook actually
computed and passed it -- the one place the fix under test writes its answer.
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
from gaia.approvals.store import insert_requested  # noqa: E402
from gaia.store.writer import (  # noqa: E402
    finalize_agent_contract_handoff,
    insert_dispatched_handoff,
    mirror_partial_contract_handoff,
    stamp_harness_agent_id,
)
from tests.fixtures.agent_ids import valid_agent_id  # noqa: E402

WORKSPACE = "me"
# The CLI-minted identity a turn adopts (the row is keyed on this).
AGENT_ID = valid_agent_id("nonce-preservation")
# The harness's OWN per-run id (hook_data['agent_id'] on the SubagentStop
# payload) -- a DIFFERENT identifier space from AGENT_ID, by design (see
# ClaudeCodeAdapter._resolve_dispatch_row). A real dispatch never sets these
# equal; a test that does so masks exactly the class of gap this migration
# exists to close (see test_subagent_stop_gate_row_first_e2e.py's own
# module comment on the same point). Kept deliberately distinct here too.
HARNESS_AGENT_ID = valid_agent_id("nonce-preservation-harness")
SESSION_ID = "sess-nonce-preservation"

_EVIDENCE_KEYS = (
    "patterns_checked", "files_checked", "commands_run", "key_outputs",
    "verbatim_outputs", "cross_layer_impacts", "open_gaps",
)


def _evidence() -> dict:
    return {k: [] for k in _EVIDENCE_KEYS}


def _approval_request_envelope(approval_id: str) -> dict:
    """A well-formed APPROVAL_REQUEST envelope -- the shape a turn's OWN
    `gaia contract fill`/`finalize` call would have persisted after being
    blocked on a T3 command and relaying the hook's approval_id."""
    return {
        "agent_status": {
            "agent_state": "APPROVAL_REQUEST",
            "agent_id": AGENT_ID,
            "pending_steps": ["await user approval"],
            "next_action": "wait for approval",
        },
        "evidence_report": _evidence(),
        "consolidation_report": None,
        "approval_request": {
            "approval_id": approval_id,
            "exact_content": "echo test-nonce-preservation",
            "rationale": "test fixture",
            "risk_level": "low",
            "rollback": None,
            "verification": "manual",
        },
    }


def _mint_pending_approval(suffix: str) -> str:
    """Insert a REAL 'pending' row into the approvals table (not a stub), so
    the cross-check layer resolves the id cleanly instead of confusing the
    picture with an unrelated APPROVAL_ID_NOT_PENDING rejection."""
    sealed_payload = {
        "operation": "bash",
        "exact_content": f"echo test-nonce-preservation-{suffix}",
        "commands": [f"echo test-nonce-preservation-{suffix}"],
    }
    return insert_requested(sealed_payload, agent_id=AGENT_ID, session_id=SESSION_ID)


@pytest.fixture(autouse=True)
def _isolated_substrate(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "gaia_data"))
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    monkeypatch.setenv("GAIA_WORKSPACE", WORKSPACE)
    monkeypatch.delenv("GAIA_CONTRACT_FULL_VERDICT_GATE", raising=False)
    yield


@pytest.fixture()
def default_db(tmp_path) -> Path:
    """The DB the adapter resolves by default (GAIA_DATA_DIR/gaia.db) when the
    hook payload carries no explicit db_path -- exactly how the real hook
    runs, and the SAME resolution `insert_requested` falls back to."""
    return tmp_path / "gaia_data" / "gaia.db"


def _birth_row(db_path: Path, *, contract_suffix: str) -> str:
    contract_id = f"{AGENT_ID}.{contract_suffix}"
    insert_dispatched_handoff(
        contract_id=contract_id,
        agent_id=AGENT_ID,
        workspace=WORKSPACE,
        session_id=SESSION_ID,
        db_path=db_path,
    )
    return contract_id


def _finalize_row(db_path: Path, contract_id: str, envelope: dict) -> None:
    finalize_agent_contract_handoff(
        contract_id=contract_id,
        agent_id=envelope["agent_status"]["agent_id"],
        workspace=WORKSPACE,
        agent_state=envelope["agent_status"]["agent_state"],
        raw_handoff_json=json.dumps(envelope),
        session_id=SESSION_ID,
        db_path=db_path,
    )


def _subagent_stop_event(
    adapter: ClaudeCodeAdapter, *,
    agent_output: str,
    stop_reason: str = "end_turn",
):
    payload = {
        "hook_event_name": "SubagentStop",
        "session_id": SESSION_ID,
        "agent_type": "gaia-system",
        # The harness's own per-run id, NOT the CLI-minted identity.
        "agent_id": HARNESS_AGENT_ID,
        "agent_transcript_path": "",
        "last_assistant_message": agent_output,
        "stop_reason": stop_reason,
        "cwd": "/tmp",
    }
    return adapter.parse_event(json.dumps(payload))


def _run_and_capture_preserved_nonces(monkeypatch, adapter, event) -> "set | None":
    """Run the real hook and return the ``preserve_nonces`` argument
    ``adapt_subagent_stop`` actually passed to approval cleanup -- the exact
    seam the fix under test writes its answer into."""
    captured: dict = {}

    def _fake_cleanup(agent_type, session_id=None, preserve_nonces=None):
        captured["preserve_nonces"] = preserve_nonces
        return False

    import modules.security.approval_cleanup as approval_cleanup_module
    monkeypatch.setattr(approval_cleanup_module, "cleanup", _fake_cleanup)

    adapter.adapt_subagent_stop(event)
    return captured.get("preserve_nonces")


# ---------------------------------------------------------------------------
# THE CENTRAL CASE: approval recorded on a CLEANLY FINALIZED row, no fence at
# all in the agent's final response text. This is exactly the shape a turn
# takes when it runs `gaia contract finalize` with agent_state=APPROVAL_REQUEST
# but forgets (or is prevented by truncation of its own text) to also echo the
# fenced block. Fails on the pre-fix code: parsed_contract is None (no fence),
# `isinstance(parsed_contract, dict)` is False, preserved_nonces stays empty,
# and cleanup_approval is called with preserve_nonces=None.
# ---------------------------------------------------------------------------

def test_approval_on_finalized_row_survives_with_no_fence_at_all(default_db, monkeypatch):
    approval_id = _mint_pending_approval("central")
    contract_id = _birth_row(default_db, contract_suffix="central")
    stamp_harness_agent_id(contract_id, HARNESS_AGENT_ID, db_path=default_db)
    _finalize_row(default_db, contract_id, _approval_request_envelope(approval_id))

    adapter = ClaudeCodeAdapter()
    event = _subagent_stop_event(adapter, agent_output="")  # no fence whatsoever

    preserved = _run_and_capture_preserved_nonces(monkeypatch, adapter, event)

    assert preserved is not None, (
        "the row's own APPROVAL_REQUEST was dropped -- nonce preservation "
        "read only the (absent) fence"
    )
    assert approval_id in preserved


# ---------------------------------------------------------------------------
# The row was mirrored (via `gaia contract fill`) but NEVER closed by
# `finalize` -- GATE_SOURCE_ROW_UNFINALIZED. The gate rejects this turn
# regardless of the fence, but the property under test is narrower: the
# approval this turn's OWN record carries must still survive cleanup, because
# the row -- not the fence -- is what the gate consulted to make ANY decision
# about this turn. This is the literal case the task names: "aprobación en la
# fila, sin declaración final."
# ---------------------------------------------------------------------------

def test_approval_mirrored_onto_unfinalized_row_survives_with_no_fence(default_db, monkeypatch):
    approval_id = _mint_pending_approval("unfinalized")
    contract_id = _birth_row(default_db, contract_suffix="unfinalized")
    stamp_harness_agent_id(contract_id, HARNESS_AGENT_ID, db_path=default_db)
    # `gaia contract fill` mirrors the partial draft onto the row WITHOUT
    # finalizing it -- cut_reason stays 'never_finalized'.
    mirror_partial_contract_handoff(
        contract_id,
        json.dumps(_approval_request_envelope(approval_id)),
        db_path=default_db,
    )

    adapter = ClaudeCodeAdapter()
    event = _subagent_stop_event(adapter, agent_output="", stop_reason="end_turn")

    preserved = _run_and_capture_preserved_nonces(monkeypatch, adapter, event)

    assert preserved is not None
    assert approval_id in preserved


# ---------------------------------------------------------------------------
# Control: NO dispatch row reachable at all -- the gate's own residual
# fallback case. Nonce preservation must still read the fence here, exactly
# as it did before this fix -- degrading to the declaration only in the same
# case the gate itself does.
# ---------------------------------------------------------------------------

def test_no_row_at_all_still_reads_the_fence_for_nonce_preservation(default_db, monkeypatch):
    approval_id = _mint_pending_approval("fence-fallback")
    envelope = _approval_request_envelope(approval_id)
    agent_output = (
        "Blocked -- awaiting your approval.\n\n```agent_contract_handoff\n"
        + json.dumps(envelope) + "\n```\n"
    )

    adapter = ClaudeCodeAdapter()
    event = _subagent_stop_event(adapter, agent_output=agent_output, stop_reason="end_turn")

    preserved = _run_and_capture_preserved_nonces(monkeypatch, adapter, event)

    assert preserved is not None
    assert approval_id in preserved


# ---------------------------------------------------------------------------
# Control: a cleanly finalized row that is COMPLETE (no approval at all)
# yields no preserved nonce, whether or not a stray fence mentions one --
# once the row is authoritative, a fence-only approval_id must NOT survive.
# ---------------------------------------------------------------------------

def test_finalized_complete_row_ignores_a_stray_fence_approval_id(default_db, monkeypatch):
    stray_approval_id = _mint_pending_approval("stray")
    contract_id = _birth_row(default_db, contract_suffix="complete")
    stamp_harness_agent_id(contract_id, HARNESS_AGENT_ID, db_path=default_db)
    complete_envelope = {
        "agent_status": {
            "agent_state": "COMPLETE",
            "agent_id": AGENT_ID,
            "pending_steps": [],
            "next_action": "done",
        },
        "evidence_report": {
            **_evidence(),
            "verification": {"method": "test", "result": "pass", "details": "green"},
        },
        "consolidation_report": None,
        "approval_request": None,
    }
    _finalize_row(default_db, contract_id, complete_envelope)

    # A stray fence in the final text names an unrelated approval_id -- must
    # be ignored once the row (COMPLETE, no approval_request) is authoritative.
    stray_fence_envelope = _approval_request_envelope(stray_approval_id)
    agent_output = (
        "All done.\n\n```agent_contract_handoff\n"
        + json.dumps(stray_fence_envelope) + "\n```\n"
    )

    adapter = ClaudeCodeAdapter()
    event = _subagent_stop_event(adapter, agent_output=agent_output, stop_reason="end_turn")

    preserved = _run_and_capture_preserved_nonces(monkeypatch, adapter, event)

    assert not preserved
