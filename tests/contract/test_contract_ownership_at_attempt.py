"""A turn that opens or writes a contract adopted by another turn is told at the attempt.

The rows are served through the store's own lookup, faked here so each case
states exactly which harness agent owns which contract.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_HOOKS_DIR = Path(__file__).resolve().parents[2] / "hooks"
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))

from modules.security import contract_ownership_guard as guard  # noqa: E402
from modules.tools.bash_validator import BashValidator  # noqa: E402

MINE = "acaller0000000001"
OTHER = "aother00000000002"

OWN_ROW = {
    "id": 10, "contract_id": "a1111111111111111.aaaaaaaaaaaa",
    "agent_state": "NEEDS_VERIFICATION", "harness_agent_id": MINE,
}
OTHER_ROW = {
    "id": 11, "contract_id": "a2222222222222222.bbbbbbbbbbbb",
    "agent_state": "IN_PROGRESS", "harness_agent_id": OTHER,
}


@pytest.fixture
def rows(monkeypatch):
    table = [OWN_ROW, OTHER_ROW]

    def fake_list(*, harness_agent_id=None, contract_id=None, limit=100, **_):
        return [
            r for r in table
            if (harness_agent_id is None or r["harness_agent_id"] == harness_agent_id)
            and (contract_id is None or r["contract_id"] == contract_id)
        ][:limit]

    monkeypatch.setattr("gaia.store.writer.list_agent_contract_handoffs", fake_list)
    return table


def test_init_by_a_turn_that_owns_a_row_names_that_row(rows):
    allowed, reason = guard.check("gaia contract init --agent-id a3333333333333333", MINE)
    assert not allowed
    assert OWN_ROW["contract_id"] in reason
    assert "--draft-id" in reason


def test_init_by_a_turn_with_no_stamped_row_is_the_documented_fallback(rows):
    assert guard.check("gaia contract init", "afresh0000000003") == (True, None)


def test_write_to_a_draft_adopted_by_another_turn_names_the_owner(rows):
    command = f"gaia contract set agent_status.agent_state IN_PROGRESS --draft-id {OTHER_ROW['contract_id']}"
    allowed, reason = guard.check(command, MINE)
    assert not allowed
    assert OTHER in reason
    assert OWN_ROW["contract_id"] in reason


@pytest.mark.parametrize("verb", ["add", "fill", "finalize"])
def test_every_write_verb_is_checked(rows, verb):
    command = f"gaia contract {verb} --draft-id={OTHER_ROW['contract_id']}"
    assert not guard.check(command, MINE)[0]


def test_write_to_the_turns_own_draft_is_allowed(rows):
    command = f"gaia contract set work_phase framing --draft-id {OWN_ROW['contract_id']}"
    assert guard.check(command, MINE) == (True, None)


def test_orchestrator_is_not_checked(rows):
    assert guard.check("gaia contract init", "") == (True, None)


def test_read_verbs_and_other_commands_are_not_checked(rows):
    assert guard.check(f"gaia contract view --draft-id {OTHER_ROW['contract_id']}", MINE)[0]
    assert guard.check("gaia memory search contract", MINE)[0]


def test_chained_component_is_checked(rows):
    command = f"cd /tmp && gaia contract fill --draft-id {OTHER_ROW['contract_id']}"
    assert not guard.check(command, MINE)[0]


def test_failed_lookup_allows_the_command(monkeypatch):
    def broken(**_):
        raise RuntimeError("db unavailable")

    monkeypatch.setattr("gaia.store.writer.list_agent_contract_handoffs", broken)
    assert guard.check("gaia contract init", MINE) == (True, None)


def test_validator_refuses_the_collision_before_the_command_runs(rows):
    result = BashValidator().validate(
        f"gaia contract add --draft-id {OTHER_ROW['contract_id']}",
        is_subagent=True, agent_type="gaia-system",
        hook_payload={"agent_id": MINE, "session_id": "s"},
    )
    assert not result.allowed
    assert OTHER in result.reason
