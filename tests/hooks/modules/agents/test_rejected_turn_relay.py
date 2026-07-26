"""A turn rejected by the full-verdict gate must not lose its substantive work.

The measured defect: the gate returns exit_code=2 when the final message
carries no fenced ``agent_contract_handoff``; the harness hands that rejection
to the SUBAGENT, and the repair turn it produces -- usually a thin re-emission
("the contract is already finalized, this only adds the envelope") -- REPLACES
the rejected message in everything the orchestrator receives. The diagnosis is
gone from the relay while the work itself was real.

The gate is unchanged (a turn without a contract is still rejected). What these
tests pin is that the rejection no longer destroys the message: the substantive
text is preserved AND reinjected verbatim into the repair instruction, which is
the only in-band route back to the orchestrator.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_HOOKS_DIR = str(Path(__file__).resolve().parents[4] / "hooks")
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

from adapters.claude_code import ClaudeCodeAdapter  # noqa: E402
from modules.agents import rejected_turn_relay as relay  # noqa: E402

SUBSTANTIVE = (
    "The 20:08:10 diagnosis: resolve_minted_agent_id falls back to the harness "
    "agent id, so resolve_draft_id globs a key space no draft lives in and "
    "returns None without failing loudly."
)
FENCE = (
    "```agent_contract_handoff\n"
    '{"agent_status": {"agent_state": "COMPLETE"}}\n'
    "```"
)


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "gaia_data"))
    monkeypatch.setenv("GAIA_WORKSPACE", "me")
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    yield


def test_substantive_text_drops_the_contract_fence_only():
    output = f"{SUBSTANTIVE}\n\n```python\nprint('evidence')\n```\n\n{FENCE}"
    text = relay.substantive_text(output)
    assert SUBSTANTIVE in text
    assert "print('evidence')" in text, "evidence blocks are substantive, not the fence"
    assert "agent_contract_handoff" not in text


def test_rejected_text_survives_the_rejection():
    # The acceptance criterion: after a fence-less turn is rejected, its
    # substantive content is still recoverable -- verbatim, in the message the
    # harness delivers back, not only in the DB.
    key = relay.preservation_key("sess-1", {"agent_id": "aac5be534edc91e44"})
    outcome = relay.on_rejection(SUBSTANTIVE, key=key, rejection_reason="REPAIR: no fence")

    assert SUBSTANTIVE in outcome["reason"], (
        "the rejected turn's text must ride back inside the repair message"
    )
    assert "REPAIR: no fence" in outcome["reason"], "the gate's own verdict is kept"
    assert "VERBATIM" in outcome["reason"], "the relay obligation must be explicit"
    assert outcome["chars"] == len(SUBSTANTIVE)
    assert relay.load(key) == SUBSTANTIVE, "and it is durable beyond the turn"


def test_second_rejection_does_not_erode_the_original_text():
    # The repair attempt is thinner than what it replaced. Preserving it over
    # the original would reproduce the defect one turn later.
    key = relay.preservation_key("sess-2", {"agent_id": "aac5be534edc91e44"})
    relay.on_rejection(SUBSTANTIVE, key=key, rejection_reason="REPAIR: no fence")
    outcome = relay.on_rejection(
        "The contract is already finalized; this only adds the envelope.",
        key=key, rejection_reason="REPAIR: still no fence",
    )

    assert outcome["carried_forward"] is True
    assert SUBSTANTIVE in outcome["reason"]
    assert SUBSTANTIVE in relay.load(key)


def test_repair_that_reproduces_the_text_is_not_duplicated_and_closes_out():
    key = relay.preservation_key("sess-3", {"agent_id": "aac5be534edc91e44"})
    relay.on_rejection(SUBSTANTIVE, key=key, rejection_reason="REPAIR: no fence")

    repaired = f"{SUBSTANTIVE}\n\n{FENCE}"
    closed = relay.on_accepted(repaired, key=key)

    assert closed == {"relayed": True, "chars": len(SUBSTANTIVE)}
    assert relay.load(key) is None, "a closed turn leaves no preserved residue"


def test_accepted_turn_reports_when_the_text_was_not_relayed():
    key = relay.preservation_key("sess-4", {"agent_id": "aac5be534edc91e44"})
    relay.on_rejection(SUBSTANTIVE, key=key, rejection_reason="REPAIR: no fence")

    closed = relay.on_accepted(f"Only the envelope.\n\n{FENCE}", key=key)

    assert closed["relayed"] is False, (
        "an unrelayed repair must be reported, not silently accepted"
    )
    assert closed["chars"] == len(SUBSTANTIVE)


def test_nothing_preserved_when_the_turn_had_no_prose():
    key = relay.preservation_key("sess-5", {"agent_id": "aac5be534edc91e44"})
    outcome = relay.on_rejection(FENCE, key=key, rejection_reason="REPAIR: bad fence")

    assert outcome["chars"] == 0
    assert outcome["reason"] == "REPAIR: bad fence", "no empty relay notice"
    assert relay.load(key) is None


def test_accepted_turn_without_a_preserved_file_is_a_noop():
    key = relay.preservation_key("sess-6", {"agent_id": "aac5be534edc91e44"})
    assert relay.on_accepted(f"clean turn {FENCE}", key=key) is None


# ---------------------------------------------------------------------------
# End to end through the real SubagentStop lifecycle
# ---------------------------------------------------------------------------

def test_adapter_rejection_carries_the_substantive_text_back():
    """The full path: a fence-less turn from a real Gaia agent is still
    rejected (exit 2, gate unchanged), and the rejection the harness relays to
    the subagent now carries the rejected turn's own text verbatim."""
    adapter = ClaudeCodeAdapter()
    event = adapter.parse_event(json.dumps({
        "hook_event_name": "SubagentStop",
        "session_id": "sess-e2e-relay",
        "agent_type": "developer",
        "agent_id": "aac5be534edc91e44",
        "agent_transcript_path": "",
        "last_assistant_message": SUBSTANTIVE,
        "stop_reason": "end_turn",
        "cwd": "/tmp",
    }))

    response = adapter.adapt_subagent_stop(event)

    assert response.exit_code == 2, "the gate still rejects a fence-less turn"
    assert response.output["contract_rejected"] is True
    assert SUBSTANTIVE in response.output["contract_rejection_reason"]
    assert response.output["preserved_output_chars"] == len(SUBSTANTIVE)
    assert Path(response.output["preserved_output_path"]).is_file()


@pytest.mark.parametrize("broken", ["preservation_key", "on_rejection"])
def test_relay_failure_does_not_downgrade_the_rejection(monkeypatch, broken):
    """The relay enriches a rejection; it must never be able to erase one.

    exit_code=2 is driven by ``result['contract_rejected']``, and the outer
    ``except`` in ``adapt_subagent_stop`` rebuilds ``result`` WITHOUT that key
    -- so a raise anywhere in the relay used to turn a hard rejection into
    exit 0, silently passing a turn the gate had refused.
    """
    def _boom(*_a, **_k):
        raise RuntimeError("relay exploded")

    monkeypatch.setattr(relay, broken, _boom)

    adapter = ClaudeCodeAdapter()
    event = adapter.parse_event(json.dumps({
        "hook_event_name": "SubagentStop",
        "session_id": f"sess-relay-broken-{broken}",
        "agent_type": "developer",
        "agent_id": "aac5be534edc91e44",
        "agent_transcript_path": "",
        "last_assistant_message": SUBSTANTIVE,
        "stop_reason": "end_turn",
        "cwd": "/tmp",
    }))

    response = adapter.adapt_subagent_stop(event)

    assert response.exit_code == 2, (
        f"a failure in relay.{broken} downgraded the rejection to exit "
        f"{response.exit_code}"
    )
    assert response.output["contract_rejected"] is True
    assert response.output["contract_rejection_reason"], (
        "the gate's own repair message must survive the relay failure"
    )
