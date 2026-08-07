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
    agent_id, and returns None -- never the harness id -- when nothing minted
    is recoverable; its private alias still resolves to the same function.
  * The minted id recovered from the transcript is the turn's OWN -- taken
    from its ``gaia contract init`` mint report, never from a peer's draft id
    the turn merely mentioned afterwards -- and ambiguity fails closed.

Drafts live under an isolated ``GAIA_DATA_DIR``; the DB is a separate isolated
file passed via ``task_info['db_path']``. The writer materializes the real
schema on first connect -- not a fixture.
"""

from __future__ import annotations

import json
import re
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
from gaia.contract.drafts import (  # noqa: E402
    mint_draft_id,
    resolve_draft_id,
    save_draft,
)
from gaia.store.writer import (  # noqa: E402
    finalize_agent_contract_handoff,
    insert_dispatched_handoff,
    stamp_harness_agent_id,
)
from modules.agents.handoff_persister import (  # noqa: E402
    _resolve_minted_agent_id,
    resolve_minted_agent_id,
)
from modules.agents.transcript_reader import (  # noqa: E402
    extract_minted_agent_id_from_transcript,
)
from tests.fixtures.agent_ids import valid_agent_id  # noqa: E402

VALID_AGENT_ID = valid_agent_id("a1234abcd")
WORKSPACE = "me"
# The HARNESS agent id: same shape as a minted one, different identifier space.
# Drafts are never keyed by it -- resolving under it silently matches nothing.
HARNESS_AGENT_ID = "aac5be534edc91e44"


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
    """The SubagentStop view of the turn.

    ``agent_id`` is the HARNESS id and is deliberately NOT ``VALID_AGENT_ID``.
    This file used to put the minted handle here, which made every lane appear
    to resolve without ever crossing between the two identifier spaces -- the
    exact masking the space confusion below is about.
    """
    return {
        "agent_id": HARNESS_AGENT_ID,
        "agent": "gaia-system",
        "workspace": WORKSPACE,
        "db_path": str(db_path),
    }


def _born_and_stamped(draft_id: str, db_path: Path) -> None:
    """Reproduce the dispatch lifecycle a turn's row really goes through: the
    row is born under the MINTED identity, then SubagentStart stamps the
    HARNESS id onto it. That row is the only artifact holding both, and so the
    only route from what SubagentStop knows to the draft key."""
    insert_dispatched_handoff(
        contract_id=draft_id,
        agent_id=VALID_AGENT_ID,
        workspace=WORKSPACE,
        db_path=db_path,
    )
    stamp_harness_agent_id(draft_id, HARNESS_AGENT_ID, db_path=db_path)


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


def test_resolver_returns_none_rather_than_the_harness_id_when_no_fence():
    # Fence absent and nothing else available -> None. This USED to return the
    # harness agent_id as a "last-resort LABEL", which is worse than nothing:
    # being non-empty it satisfies every `if not minted_agent_id` guard
    # downstream and carries a value that can never key a draft, so the rescue
    # paths failed silently instead of reporting that they could not resolve.
    assert resolve_minted_agent_id(None, {"agent_id": HARNESS_AGENT_ID}) is None


def test_private_alias_is_the_same_shared_resolver():
    assert _resolve_minted_agent_id is resolve_minted_agent_id


# ---------------------------------------------------------------------------
# Reconstruction fires on a finalized draft + missing fence
# ---------------------------------------------------------------------------

def test_reconstructs_envelope_from_finalized_draft(db):
    draft_id = mint_draft_id(VALID_AGENT_ID)
    env = _complete_envelope()
    save_draft(draft_id, env)
    _born_and_stamped(draft_id, db)
    _finalize(draft_id, env, db)  # the agent DID finalize -- row now converged

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
    _born_and_stamped(draft_id, db)
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
    _born_and_stamped(draft_id, db)
    # deliberately NOT calling _finalize -- the row stays DISPATCHED, so the
    # draft IS findable and the refusal comes from the finalized check, not
    # from an unresolvable identity.

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


# ---------------------------------------------------------------------------
# The two identifier spaces: harness agent_id vs CLI-minted agent id
# ---------------------------------------------------------------------------

def _init_report(draft_id: str) -> str:
    """The stdout `gaia contract init` prints when it MINTS a draft, verbatim
    (bin/cli/contract.py::cmd_init -> _write_if_valid). This is the ONLY
    transcript evidence that a draft id belongs to the turn being scanned."""
    agent_id = draft_id.split(".", 1)[0]
    return (
        f"OK: draft {draft_id} updated and validated.\n"
        f"agent_id: {agent_id}\n"
        f"draft_id: {draft_id}\n"
        "Reuse BOTH verbatim for the rest of this turn: agent_id in "
        "agent_status.agent_id, draft_id as --draft-id."
    )


def _transcript_lines(draft_id: str) -> list:
    """The turn's own `gaia contract init` + a later `set`, as production
    records them: a tool_use carrying the command, a tool_result carrying the
    CLI's stdout."""
    return [
        json.dumps({"type": "user",
                    "message": {"role": "user", "content": "do the thing"}}),
        json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "name": "Bash",
             "input": {"command": "gaia contract init"}},
        ]}}),
        json.dumps({"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "content": _init_report(draft_id)},
        ]}}),
        json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "name": "Bash", "input": {"command": (
                f"gaia contract set --draft-id {draft_id} "
                "agent_status.next_action done"
            )}},
        ]}}),
        json.dumps({"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result",
             "content": f"OK: draft {draft_id} updated and validated."},
        ]}}),
    ]


def _write_transcript(path: Path, lines: list) -> Path:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _transcript_with_draft(tmp_path: Path, draft_id: str) -> Path:
    """A subagent transcript in which the CLI-minted draft id appears, exactly
    as it does in production: minted by `gaia contract init`, then reused."""
    return _write_transcript(
        tmp_path / "agent-transcript.jsonl", _transcript_lines(draft_id)
    )


def _harness_task_info(db_path: Path, transcript: Path) -> dict:
    """task_info as the builder produces it in production: the HARNESS agent id
    in ``agent_id`` -- which is NOT the id the draft is keyed by."""
    return {
        "agent_id": HARNESS_AGENT_ID,
        "agent": "gaia-system",
        "workspace": WORKSPACE,
        "db_path": str(db_path),
        "agent_transcript_path": str(transcript),
    }


def test_resolver_recovers_minted_id_when_task_info_carries_the_harness_id(tmp_path):
    # The regression: task_info['agent_id'] is the harness id, the draft is keyed
    # by the CLI-minted one, and both match ^a[0-9a-f]{16,}$ -- so the old
    # fallback returned the harness id and every draft lookup silently missed.
    draft_id = mint_draft_id(VALID_AGENT_ID)
    transcript = _transcript_with_draft(tmp_path, draft_id)
    resolved = resolve_minted_agent_id(
        None, _harness_task_info(tmp_path / "gaia.db", transcript)
    )
    assert resolved == VALID_AGENT_ID
    assert resolved != HARNESS_AGENT_ID


def test_resolver_prefers_precomputed_minted_id_over_harness_id(tmp_path):
    # task_info_builder precomputes it once per turn; no transcript re-read.
    task_info = {"agent_id": HARNESS_AGENT_ID, "minted_agent_id": VALID_AGENT_ID}
    assert resolve_minted_agent_id(None, task_info) == VALID_AGENT_ID


def test_reconstruction_finds_draft_under_harness_id_task_info(db, tmp_path):
    # End to end for the exact production shape: draft finalized under a
    # CLI-minted id, hook_data carrying the harness id. Before the fix,
    # resolve_draft_id globbed '{harness-id}.*', matched nothing, returned None
    # -- and BOTH the M4 reconstruction and the T9 backstop step 1a were dead.
    draft_id = mint_draft_id(VALID_AGENT_ID)
    env = _complete_envelope()
    save_draft(draft_id, env)
    _finalize(draft_id, env, db)
    transcript = _transcript_with_draft(tmp_path, draft_id)

    recon = _adapter()._reconstruct_contract_from_finalized_draft(
        task_info=_harness_task_info(db, transcript),
        parsed_contract=None,  # the fence was lost
    )

    assert recon is not None, (
        "the finalized draft must be found even though task_info carries the "
        "harness agent id, not the minted one"
    )
    assert recon["reconstructed_from_finalized_draft"] == draft_id
    assert recon["agent_status"]["agent_state"] == "COMPLETE"


def test_no_reconstruction_when_transcript_holds_no_minted_id(db, tmp_path):
    # A turn that never ran `gaia contract init` leaves no minted id anywhere:
    # the harness id resolves nothing, so there is nothing to reconstruct. The
    # fix must not invent a draft out of an unrelated one.
    draft_id = mint_draft_id(VALID_AGENT_ID)
    env = _complete_envelope()
    save_draft(draft_id, env)
    _finalize(draft_id, env, db)
    transcript = tmp_path / "bare-transcript.jsonl"
    transcript.write_text(
        json.dumps({"type": "assistant",
                    "message": {"role": "assistant", "content": "no cli here"}}) + "\n",
        encoding="utf-8",
    )

    task_info = _harness_task_info(db, transcript)
    # No mint report AND no dispatch row to bridge through: the resolver has
    # nothing, and says so. It must NOT hand back the harness id, which globs
    # '{harness-id}.*' and matches the finalized draft not at all.
    assert resolve_minted_agent_id(None, task_info) is None
    assert resolve_draft_id(explicit=None, agent_id=HARNESS_AGENT_ID) is None

    recon = _adapter()._reconstruct_contract_from_finalized_draft(
        task_info=task_info, parsed_contract=None,
    )
    assert recon is None


# ---------------------------------------------------------------------------
# Ownership: a draft id MENTIONED in the transcript is not a draft id OWNED
# ---------------------------------------------------------------------------
#
# Recovering the minted id from the transcript is what made both draft rescues
# work again -- but a turn routinely mentions ANOTHER agent's draft id (an
# operator asked to recover a peer's contract runs `gaia contract view
# --draft-id <peer>`), and it mentions it AFTER its own. Taking the last
# mention would seal the peer's finalized envelope as this turn's outcome:
# `_reconstruct_contract_from_finalized_draft` only checks that a terminal row
# exists for the draft, never who owns it. These prove the recovered id comes
# from the turn's own `gaia contract init` mint report, and that ambiguity
# fails closed rather than resolving someone else's draft.

FOREIGN_AGENT_ID = valid_agent_id("peer-agent")


def _peer_lookup_lines(peer_draft_id: str) -> list:
    """A `gaia contract view --draft-id <peer>` against ANOTHER agent's draft:
    the peer id lands in the command AND in the envelope the CLI echoes back."""
    return [
        json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "name": "Bash", "input": {
                "command": f"gaia contract view --draft-id {peer_draft_id}"}},
        ]}}),
        json.dumps({"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "content": json.dumps({
                "draft_id": peer_draft_id,
                "agent_status": {"agent_state": "COMPLETE",
                                 "agent_id": FOREIGN_AGENT_ID},
            })},
        ]}}),
    ]


def test_minted_id_is_the_turns_own_not_the_last_mentioned(tmp_path):
    own_draft_id = mint_draft_id(VALID_AGENT_ID)
    peer_draft_id = mint_draft_id(FOREIGN_AGENT_ID)
    transcript = _write_transcript(
        tmp_path / "own-then-peer.jsonl",
        _transcript_lines(own_draft_id) + _peer_lookup_lines(peer_draft_id),
    )

    # The fixture must actually reproduce the hazard: scanning for the bare
    # draft-id shape and taking the last match lands on the PEER.
    naive_last = re.findall(
        r"\b(a[0-9a-f]{16,})\.[0-9a-f]{8,}\b", transcript.read_text(encoding="utf-8")
    )[-1]
    assert naive_last == FOREIGN_AGENT_ID

    recovered = extract_minted_agent_id_from_transcript(str(transcript))

    assert recovered == VALID_AGENT_ID
    assert recovered != FOREIGN_AGENT_ID, (
        "the peer id is mentioned LAST; taking the last match attributes "
        "another agent's draft to this turn"
    )


def test_no_reconstruction_of_a_peers_finalized_draft(db, tmp_path):
    # The turn inits its own draft and never finalizes it, then reads a PEER's
    # draft, which IS finalized. Resolving the last-mentioned id would find a
    # terminal row and reconstruct the peer's COMPLETE as this turn's outcome.
    own_draft_id = mint_draft_id(VALID_AGENT_ID)
    save_draft(own_draft_id, _complete_envelope())

    peer_draft_id = mint_draft_id(FOREIGN_AGENT_ID)
    peer_env = _complete_envelope()
    peer_env["agent_status"]["agent_id"] = FOREIGN_AGENT_ID
    peer_env["evidence_report"]["key_outputs"] = ["the peer's work"]
    save_draft(peer_draft_id, peer_env)
    finalize_agent_contract_handoff(
        contract_id=peer_draft_id,
        agent_id=FOREIGN_AGENT_ID,
        workspace=WORKSPACE,
        agent_state="COMPLETE",
        raw_handoff_json=json.dumps(peer_env),
        db_path=db,
    )

    transcript = _write_transcript(
        tmp_path / "peer-finalized.jsonl",
        _transcript_lines(own_draft_id) + _peer_lookup_lines(peer_draft_id),
    )

    recon = _adapter()._reconstruct_contract_from_finalized_draft(
        task_info=_harness_task_info(db, transcript), parsed_contract=None,
    )

    assert recon is None, (
        "this turn's own draft was never finalized; the only finalized draft "
        f"belongs to another agent -- reconstructed instead: {recon}"
    )


def test_own_draft_still_reconstructs_when_a_peer_draft_is_mentioned_later(db, tmp_path):
    # The guard must not disable the rescue: with the turn's OWN draft
    # finalized, a later peer mention changes nothing.
    own_draft_id = mint_draft_id(VALID_AGENT_ID)
    env = _complete_envelope()
    save_draft(own_draft_id, env)
    _finalize(own_draft_id, env, db)
    transcript = _write_transcript(
        tmp_path / "own-finalized-peer-mentioned.jsonl",
        _transcript_lines(own_draft_id)
        + _peer_lookup_lines(mint_draft_id(FOREIGN_AGENT_ID)),
    )

    recon = _adapter()._reconstruct_contract_from_finalized_draft(
        task_info=_harness_task_info(db, transcript), parsed_contract=None,
    )

    assert recon is not None
    assert recon["reconstructed_from_finalized_draft"] == own_draft_id


def test_two_mint_reports_fail_closed(tmp_path):
    # A second mint report -- a re-`init`, or one quoted into the task prompt --
    # makes ownership ambiguous. Resolving either would be a guess.
    transcript = _write_transcript(
        tmp_path / "two-mints.jsonl",
        _transcript_lines(mint_draft_id(VALID_AGENT_ID))
        + _transcript_lines(mint_draft_id(FOREIGN_AGENT_ID)),
    )
    assert extract_minted_agent_id_from_transcript(str(transcript)) is None


def test_peer_mention_alone_resolves_nothing(tmp_path):
    # No mint report at all: every draft id present belongs to someone else.
    transcript = _write_transcript(
        tmp_path / "peer-only.jsonl",
        _peer_lookup_lines(mint_draft_id(FOREIGN_AGENT_ID)),
    )
    assert extract_minted_agent_id_from_transcript(str(transcript)) is None


def test_json_mode_mint_report_is_recognized(tmp_path):
    # `gaia contract init --json` reports the same mint in the other output
    # mode; the agent_id_minted key is what distinguishes it from any other
    # JSON payload carrying a draft_id.
    own_draft_id = mint_draft_id(VALID_AGENT_ID)
    peer_draft_id = mint_draft_id(FOREIGN_AGENT_ID)
    transcript = _write_transcript(
        tmp_path / "json-mint.jsonl",
        [json.dumps({"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "content": json.dumps({
                "status": "ok", "draft_id": own_draft_id,
                "agent_id": VALID_AGENT_ID, "agent_id_minted": True,
            })},
        ]}})] + _peer_lookup_lines(peer_draft_id),
    )
    assert extract_minted_agent_id_from_transcript(str(transcript)) == VALID_AGENT_ID
