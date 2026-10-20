"""The orchestrator learns that a finished turn proposed project facts from the dispatch result.

What the user feels: a fact a specialist found (a tool URL, a login method) is
not left sitting in a contract nobody opens -- the result line the orchestrator
receives for the dispatch names how many were proposed and which contract holds
them; a turn that proposed nothing adds no line.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(_REPO_ROOT / "hooks"), str(_REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from modules.agents.task_result_observer import build_contract_summary_line  # noqa: E402

CONTRACT_ID = "a21d060aefc6855cc.0123456789ab"
PROPOSAL = {
    "operation": "create",
    "create": {
        "slug": "atom_alpha_admin_url",
        "type": "atom",
        "description": "Fact: alpha admin lives at https://admin.alpha.test.",
        "body": "Measured 2026-10-20 from the ingress manifest.",
    },
}


def _line(memory_delta) -> str:
    envelope = {
        "agent_status": {"agent_state": "COMPLETE"},
        "evidence_report": {
            "patterns_checked": [], "files_checked": [], "commands_run": [],
            "key_outputs": ["one"], "verbatim_outputs": [], "cross_layer_impacts": [],
            "open_gaps": [], "verification": {"result": "pass"},
        },
        "memory_delta": memory_delta,
    }
    row = {
        "agent_state": "COMPLETE",
        "contract_id": CONTRACT_ID,
        "dispatch_project": "alpha (/work/alpha)",
        "raw_handoff_json": json.dumps(envelope),
    }
    return build_contract_summary_line(
        {"agentId": "a21d060aefc6855cc"}, row_lookup=lambda task_info, session_id: row,
    )


def test_proposed_facts_are_named_on_their_own_line_of_the_dispatch_result():
    line = _line({"version": 1, "proposals": [PROPOSAL, PROPOSAL]})

    assert line.splitlines()[-1] == f"2 project facts proposed for alpha — contract {CONTRACT_ID}"
    assert line.startswith("a21d060aefc6855cc: state=COMPLETE")


def test_one_fact_reads_in_the_singular():
    line = _line({"version": 1, "proposals": [PROPOSAL]})

    assert line.splitlines()[-1] == f"1 project fact proposed for alpha — contract {CONTRACT_ID}"


FREE_SHAPE_PROPOSAL = {
    "type": "atom",
    "name": "atom_alpha_login_sso",
    "description": "Fact: alpha signs in through the corporate SSO.",
    "body": "Measured 2026-10-20 from the login page.",
}


def test_the_older_free_shape_without_version_or_operation_is_counted_too():
    line = _line({"proposals": [FREE_SHAPE_PROPOSAL, FREE_SHAPE_PROPOSAL]})

    assert line.splitlines()[-1] == f"2 project facts proposed for alpha — contract {CONTRACT_ID}"


def test_free_shape_entries_without_a_description_and_body_are_not_counted():
    line = _line({"proposals": [FREE_SHAPE_PROPOSAL, {"name": "atom_x"}]})

    assert line.splitlines()[-1] == f"1 project fact proposed for alpha — contract {CONTRACT_ID}"


def test_the_free_shape_reads_as_a_create_with_its_name_as_slug():
    from modules.agents.response_contract import parse_memory_delta

    block = parse_memory_delta("", parsed_contract={"memory_delta": {"proposals": [FREE_SHAPE_PROPOSAL]}})

    assert block.proposals == [{
        "operation": "create",
        "create": {
            "description": FREE_SHAPE_PROPOSAL["description"],
            "body": FREE_SHAPE_PROPOSAL["body"],
            "slug": "atom_alpha_login_sso",
            "type": "atom",
        },
    }]


@pytest.mark.parametrize("memory_delta", [None, {"version": 1, "proposals": []}, {"proposals": []}])
def test_a_turn_with_no_proposed_facts_adds_no_line(memory_delta):
    line = _line(memory_delta)

    assert "project fact" not in line
    assert len(line.splitlines()) == 1
