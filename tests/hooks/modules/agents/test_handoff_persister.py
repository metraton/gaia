"""M4 missing-fence footgun (Option A) + the shared minted-agent-id resolver.

The SubagentStop gate parses the fenced ``agent_contract_handoff`` out of the
agent's response TEXT, not its finalized DB row. So a turn that did all its
work via the ``gaia contract`` CLI and ran ``gaia contract finalize`` (writing
a valid terminal row) but never echoed the fence in its last message is
hard-rejected by the full-verdict gate. Option A closes that hole:
``ClaudeCodeAdapter._reconstruct_contract_from_finalized_draft`` rebuilds the
envelope from the FINALIZED draft when the fence is missing, so the gate parses
the completed contract.

This suite proves:
  * Fence missing + a FINALIZED draft (terminal row exists for its draft_id)
    -> the envelope is reconstructed from the draft, tagged like
    ``parse_contract`` output, and carries a provenance marker.
  * Fence missing + a draft that was NOT finalized (no terminal row) -> NO
    reconstruction (that is the salvage / backstop path's job).
  * Fence PRESENT -> the method is a no-op (nothing to reconstruct).
  * The reconstructed envelope passes the full-verdict contract gate that the
    bare (fence-less) output would have failed.
  * The shared resolver ``resolve_minted_agent_id`` prefers the envelope's
    agent_id and falls back to task_info, and its private alias still resolves.

Drafts live under an isolated ``GAIA_DATA_DIR``; the DB is a separate isolated
file passed via ``task_info['db_path']``. The writer materializes the real
schema on first connect -- not a fixture.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_HOOKS_DIR = str(Path(__file__).resolve().parents[4] / "hooks")
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

from adapters.claude_code import (  # noqa: E402
    ClaudeCodeAdapter,
    evaluate_contract_gate,
)
from gaia.contract.drafts import mint_draft_id, save_draft  # noqa: E402
from gaia.store.writer import finalize_agent_contract_handoff  # noqa: E402
from modules.agents.handoff_persister import (  # noqa: E402
    _resolve_minted_agent_id,
    resolve_minted_agent_id,
)

VALID_AGENT_ID = "a1234abcd"
WORKSPACE = "me"


@pytest.fixture(autouse=True)
def _isolated_substrate(tmp_path, monkeypatch):
    """Isolate the drafts substrate and clear the dispatch id (mirrors the
    truncation-salvage suite)."""
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "gaia_data"))
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    monkeypatch.setenv("GAIA_WORKSPACE", WORKSPACE)
    yield


@pytest.fixture()
def db(tmp_path):
    return tmp_path / "gaia.db"


def _complete_envelope() -> dict:
    """A genuine, gate-passing COMPLETE envelope as the CLI would finalize it."""
    return {
        "agent_status": {
            "agent_state": "COMPLETE",
            "agent_id": VALID_AGENT_ID,
            "pending_steps": [],
            "next_action": "done",
        },
        "evidence_report": {
            "patterns_checked": [], "files_checked": [], "commands_run": [],
            "key_outputs": ["did the thing"], "verbatim_outputs": [],
            "cross_layer_impacts": [], "open_gaps": [],
            "verification": {"method": "test", "checks": ["m4"],
                             "result": "pass", "details": "ok"},
        },
        "consolidation_report": None,
        "approval_request": None,
    }


def _task_info(db_path: Path) -> dict:
    return {
        "agent_id": VALID_AGENT_ID,
        "agent": "gaia-system",
        "workspace": WORKSPACE,
        "db_path": str(db_path),
    }


def _finalize(draft_id: str, envelope: dict, db_path: Path) -> None:
    finalize_agent_contract_handoff(
        contract_id=draft_id,
        agent_id=VALID_AGENT_ID,
        workspace=WORKSPACE,
        agent_state=envelope["agent_status"]["agent_state"],
        raw_handoff_json=json.dumps(envelope),
        db_path=db_path,
    )


def _adapter() -> ClaudeCodeAdapter:
    return ClaudeCodeAdapter()


# ---------------------------------------------------------------------------
# Shared resolver
# ---------------------------------------------------------------------------

def test_resolver_prefers_envelope_agent_id():
    parsed = {"agent_status": {"agent_id": "aff0091"}}
    assert resolve_minted_agent_id(parsed, {"agent_id": "aother9"}) == "aff0091"


def test_resolver_falls_back_to_task_info_when_no_fence():
    # Fence absent (parsed_contract None) -> task_info agent_id is used. This is
    # exactly what the reconstruction path relies on.
    assert resolve_minted_agent_id(None, {"agent_id": VALID_AGENT_ID}) == VALID_AGENT_ID


def test_private_alias_is_the_same_shared_resolver():
    assert _resolve_minted_agent_id is resolve_minted_agent_id


# ---------------------------------------------------------------------------
# Reconstruction fires on a finalized draft + missing fence
# ---------------------------------------------------------------------------

def test_reconstructs_envelope_from_finalized_draft(db):
    draft_id = mint_draft_id(VALID_AGENT_ID)
    env = _complete_envelope()
    save_draft(draft_id, env)
    _finalize(draft_id, env, db)  # the agent DID finalize -- row now exists

    recon = _adapter()._reconstruct_contract_from_finalized_draft(
        task_info=_task_info(db),
        parsed_contract=None,  # fence missing from the response text
    )

    assert recon is not None, (
        "a finalized draft with a missing fence must be reconstructed"
    )
    assert recon["agent_status"]["agent_state"] == "COMPLETE"
    assert recon["reconstructed_from_finalized_draft"] == draft_id
    assert recon["_contract_tag"] == "agent_contract_handoff"


def test_reconstructed_envelope_passes_the_gate(db):
    # The bare (fence-less) turn would be rejected; the reconstructed envelope
    # must pass the SAME full-verdict gate.
    #
    # agent_type is "gaia-verifier" here, not "gaia-system": the envelope under
    # test is a COMPLETE envelope. Under the plan 34 finalize gate
    # (hooks/adapters/claude_code.py::_blind_verification_required), a COMPLETE
    # is rejected ONLY when the turn is bound to a plan_task_id; this test
    # reconstructs an UNBOUND turn (no plan_task_id), so the COMPLETE passes
    # regardless of the emitting agent's role -- a concern orthogonal to what
    # THIS test proves (that reconstruction produces a gate-passing envelope
    # identical in shape to a real fence).
    draft_id = mint_draft_id(VALID_AGENT_ID)
    env = _complete_envelope()
    save_draft(draft_id, env)
    _finalize(draft_id, env, db)

    # Missing fence -> parse yields None -> gate rejects.
    bare_verdict = evaluate_contract_gate(
        None, agent_type="gaia-verifier",
        stop_reason_classification=None, ramp_enabled=True, db_path=str(db),
    )
    assert bare_verdict.rejected is True

    recon = _adapter()._reconstruct_contract_from_finalized_draft(
        task_info=_task_info(db), parsed_contract=None,
    )
    recon_verdict = evaluate_contract_gate(
        recon, agent_type="gaia-verifier",
        stop_reason_classification=None, ramp_enabled=True, db_path=str(db),
    )
    assert recon_verdict.rejected is False, (
        f"reconstructed envelope must pass the gate: {recon_verdict.rejection_reason}"
    )


# ---------------------------------------------------------------------------
# Negative cases
# ---------------------------------------------------------------------------

def test_no_reconstruction_when_draft_not_finalized(db):
    # Draft on disk but NEVER finalized (no terminal row) -> not a finished turn
    # missing its fence; it is salvage/backstop territory. Do NOT reconstruct.
    draft_id = mint_draft_id(VALID_AGENT_ID)
    save_draft(draft_id, _complete_envelope())
    # deliberately NOT calling _finalize

    recon = _adapter()._reconstruct_contract_from_finalized_draft(
        task_info=_task_info(db), parsed_contract=None,
    )
    assert recon is None


def test_no_reconstruction_when_fence_present(db):
    # A usable fence is present -> nothing to reconstruct (no-op).
    draft_id = mint_draft_id(VALID_AGENT_ID)
    env = _complete_envelope()
    save_draft(draft_id, env)
    _finalize(draft_id, env, db)

    recon = _adapter()._reconstruct_contract_from_finalized_draft(
        task_info=_task_info(db),
        parsed_contract={"agent_status": {"agent_state": "COMPLETE",
                                          "agent_id": VALID_AGENT_ID}},
    )
    assert recon is None


def test_no_reconstruction_when_no_draft(db):
    # No draft at all -> nothing to reconstruct.
    recon = _adapter()._reconstruct_contract_from_finalized_draft(
        task_info=_task_info(db), parsed_contract=None,
    )
    assert recon is None
