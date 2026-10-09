"""The ownership guard compares against the identity each host actually stamps.

Claude Code: payload ``agent_id`` is the subagent's id and is what the row is
stamped with; ``session_id`` is the shared parent session.
OpenCode: payload ``agent_id`` is the Task call id (``dispatchBySession``),
``session_id`` is the child session, and the row is stamped with the child
session id.

Real DB, real contract CLI, the real validator; the payloads are the two
shapes the adapters build.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(_REPO_ROOT / "hooks"), str(_REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from gaia.store.writer import (  # noqa: E402
    finalize_agent_contract_handoff,
    insert_dispatched_handoff,
    stamp_harness_agent_id,
)
from modules.security import contract_ownership_guard as guard  # noqa: E402
from modules.tools.bash_validator import BashValidator  # noqa: E402
from tests.fixtures.agent_ids import valid_agent_id  # noqa: E402

CONTRACT_CLI = _REPO_ROOT / "bin" / "cli" / "contract.py"
EVIDENCE_KEYS = (
    "patterns_checked", "files_checked", "commands_run", "key_outputs",
    "verbatim_outputs", "cross_layer_impacts", "open_gaps",
)

# host -> (stamped harness id, payload agent_id, payload session_id, dispatch call id)
HOSTS = {
    "claude_code": {
        "me": ("acc-me-harness", "acc-me-harness", "parent-session", "toolu_me"),
        "other": ("acc-other-harness", "acc-other-harness", "parent-session", "toolu_other"),
    },
    "opencode": {
        "me": ("ses_child_me", "call_me", "ses_child_me", "call_me"),
        "other": ("ses_child_other", "call_other", "ses_child_other", "call_other"),
    },
}


@pytest.fixture(params=sorted(HOSTS))
def host(request, tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "gaia_data"))
    for var in ("GAIA_DB", "GAIA_DB_PATH", "CLAUDE_SESSION_ID", "GAIA_DISPATCH_AGENT"):
        monkeypatch.delenv(var, raising=False)
    return HOSTS[request.param]


def _db() -> Path:
    return Path(os.environ["GAIA_DATA_DIR"]) / "gaia.db"


def _born(who: tuple, label: str) -> str:
    stamped, _agent, _session, call_id = who
    agent_id = valid_agent_id(f"both-hosts-{label}")
    contract_id = f"{agent_id}.{label}"
    insert_dispatched_handoff(
        contract_id=contract_id, agent_id=agent_id, workspace="me",
        session_id="parent-session", kind="task_execution", agent_name="gaia-system",
        dispatch_prompt_id=f"p-{label}", dispatch_tool_use_id=call_id, db_path=_db(),
    )
    stamp_harness_agent_id(contract_id, stamped, db_path=_db())
    return contract_id


def _close(contract_id: str) -> None:
    agent_id = contract_id.split(".")[0]
    envelope = {
        "agent_status": {
            "agent_state": "COMPLETE", "agent_id": agent_id,
            "pending_steps": [], "next_action": "done",
        },
        "evidence_report": {
            **{k: [] for k in EVIDENCE_KEYS},
            "verification": {"method": "t", "result": "pass", "details": "t"},
        },
        "consolidation_report": None, "approval_request": None,
    }
    finalize_agent_contract_handoff(
        contract_id=contract_id, agent_id=agent_id, workspace="me",
        agent_state="COMPLETE", raw_handoff_json=json.dumps(envelope),
        session_id="parent-session", db_path=_db(),
    )


def _check(who: tuple, command: str):
    _stamped, agent_id, session_id, _call = who
    return guard.check(command, agent_id, session_id)


def test_own_row_write_is_allowed(host):
    own = _born(host["me"], "own")
    command = f"gaia contract set work_phase executing --draft-id {own}"
    assert _check(host["me"], command) == (True, None)

    _stamped, agent_id, session_id, _call = host["me"]
    result = BashValidator().validate(
        command, is_subagent=True, agent_type="gaia-system",
        hook_payload={"agent_id": agent_id, "session_id": session_id},
    )
    assert result.allowed


def test_foreign_row_write_is_denied_naming_the_owner(host):
    own = _born(host["me"], "mine")
    foreign = _born(host["other"], "theirs")
    command = f"gaia contract fill --draft-id {foreign}"

    allowed, reason = _check(host["me"], command)
    assert not allowed
    assert foreign in reason and own in reason

    _stamped, agent_id, session_id, _call = host["me"]
    result = BashValidator().validate(
        command, is_subagent=True, agent_type="gaia-system",
        hook_payload={"agent_id": agent_id, "session_id": session_id},
    )
    assert not result.allowed


def test_init_by_a_turn_that_owns_a_row_is_denied(host):
    own = _born(host["me"], "owner")
    allowed, reason = _check(host["me"], "gaia contract init")
    assert not allowed and own in reason


def test_init_by_a_turn_with_no_row_is_the_fallback(host):
    _born(host["other"], "someone-else")
    assert _check(host["me"], "gaia contract init") == (True, None)


def test_resumed_turn_on_a_finalized_contract_can_still_close(host):
    closed = _born(host["me"], "closed")
    _close(closed)

    allowed, reason = _check(host["me"], "gaia contract init")
    assert not allowed and closed in reason

    env = dict(os.environ)
    opened = subprocess.run(
        [sys.executable, str(CONTRACT_CLI), "set", "work_phase", "executing",
         "--draft-id", closed],
        capture_output=True, text=True, env=env, timeout=30,
    )
    assert opened.returncode == 0, opened.stderr
    link_id = json.loads(
        subprocess.run(
            [sys.executable, str(CONTRACT_CLI), "chain", "--contract-id", closed, "--json"],
            capture_output=True, text=True, env=env, timeout=30,
        ).stdout
    )
    link_id = _live_link(link_id, closed)

    assert _check(host["me"], f"gaia contract add --draft-id {link_id}") == (True, None)
    status = {
        "agent_state": "NEEDS_VERIFICATION", "agent_id": closed.split(".")[0],
        "pending_steps": [], "next_action": "verify",
    }
    for args in (
        ["set", "agent_status", json.dumps(status), "--draft-id", link_id],
        ["finalize", "--draft-id", link_id],
    ):
        done = subprocess.run(
            [sys.executable, str(CONTRACT_CLI), *args],
            capture_output=True, text=True, env=env, timeout=30,
        )
        assert done.returncode == 0, done.stderr


def _live_link(chain_payload: dict, closed: str) -> str:
    links = [
        row["contract_id"] for row in chain_payload.get("chain", chain_payload.get("links", []))
    ]
    assert len(links) == 2 and links[0] == closed, chain_payload
    return links[-1]
