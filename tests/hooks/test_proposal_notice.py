"""One helper words every proposal a finished turn carries, for the dispatch result the orchestrator reads.

``memory_delta`` and ``update_contracts`` are both proposals the orchestrator
curates; each gets its own line, and a turn that proposed neither adds none.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(_REPO_ROOT / "hooks"), str(_REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from modules.agents.proposal_notices import proposal_notice  # noqa: E402

CONTRACT_ID = "a21d060aefc6855cc.0123456789ab"
FACT = {
    "operation": "create",
    "create": {"slug": "atom_alpha_admin_url", "type": "atom", "description": "d", "body": "b"},
}
UPDATE = {"contract": "project_identity", "payload": {"alpha": {"workflow": "w"}}}
EVIDENCE = {"contract": "evidence", "payload": {"brief_id": 1, "ac_id": "AC-1", "type": "text", "text": "t"}}


def _notice(contract) -> str:
    return proposal_notice(contract, contract_id=CONTRACT_ID, dispatch_project="alpha (/work/alpha)")


def test_a_project_context_proposal_names_its_count_contract_and_accept_verb():
    assert _notice({"update_contracts": [UPDATE, UPDATE]}) == (
        f"2 project-context updates proposed for alpha — contract {CONTRACT_ID}; "
        f"apply with `gaia context accept {CONTRACT_ID}`"
    )


def test_a_turn_proposing_both_kinds_gets_one_line_each_facts_first():
    lines = _notice({
        "memory_delta": {"version": 1, "proposals": [FACT]},
        "update_contracts": [UPDATE],
    }).splitlines()

    assert lines[0] == f"1 project fact proposed for alpha — contract {CONTRACT_ID}"
    assert lines[1].startswith(f"1 project-context update proposed for alpha — contract {CONTRACT_ID}")


def test_a_section_keyed_object_is_counted_and_says_it_was_reshaped():
    notice = _notice({"update_contracts": {"cluster_details": {"gke": {"version": "1.30"}}}})

    assert notice.startswith("1 project-context update proposed")
    assert "{section: payload}" in notice


@pytest.mark.parametrize("contract", [
    None,
    "not a dict",
    {},
    {"update_contracts": []},
    {"update_contracts": [EVIDENCE]},
    {"update_contracts": [{"contract": "project_identity"}]},
    {"update_contracts": "project_identity"},
])
def test_a_turn_with_nothing_for_the_orchestrator_to_accept_adds_no_notice(contract):
    assert _notice(contract) == ""


def test_evidence_entries_do_not_count_beside_a_real_proposal():
    assert _notice({"update_contracts": [EVIDENCE, UPDATE]}).startswith("1 project-context update proposed")
