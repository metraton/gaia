"""`gaia contract view --harness-id` resolves the LATEST link of a resume chain.

A resumed turn is born into a new row that continues the one it resumes and
inherits its harness_agent_id, so one harness id names every link of the chain.
The view must land on the live link and list the links it chose from, also
when links born in the same second tie on created_at.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_CLI = _REPO_ROOT / "bin" / "cli" / "contract.py"
SCHEMA = _REPO_ROOT / "gaia" / "store" / "schema.sql"

HARNESS_ID = "a72c35b67bf34b36c"
AGENT_ID = "a79b39782eaef327e"
SESSION = "sess-resume-chain"
SAME_SECOND = "2026-10-02T04:58:26Z"


@pytest.fixture(autouse=True)
def _isolated_substrate(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "gaia-data"))
    monkeypatch.setenv("GAIA_WORKSPACE", "me")
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)


def _envelope(state: str, key_outputs: list[str]) -> str:
    return json.dumps({
        "agent_status": {
            "agent_id": AGENT_ID,
            "agent_state": state,
            "pending_steps": [],
            "next_action": "done",
        },
        "evidence_report": {"key_outputs": key_outputs},
    })


@pytest.fixture()
def chain_ids(tmp_path) -> list[int]:
    return _seed_chain(tmp_path, (1, 2, 3))


def _seed_chain(tmp_path, link_ids: tuple[int, ...]) -> list[int]:
    """Write three links of one resume chain, all born in one second, and
    return their ids in chain order; ``link_ids[i]`` is the id of link ``i``.
    """
    db_path = tmp_path / "gaia-data" / "gaia.db"
    db_path.parent.mkdir(parents=True)
    con = sqlite3.connect(str(db_path))
    try:
        con.executescript(SCHEMA.read_text())
        con.execute(
            "INSERT INTO workspaces (name, identity, created_at) VALUES ('me', 'me', ?)",
            (SAME_SECOND,),
        )
        ids: list[int] = []
        links = (
            ("NEEDS_VERIFICATION", []),
            ("APPROVAL_REQUEST", ["approval requested"]),
            ("COMPLETE", ["pushed 54799fa6"]),
        )
        for position, (state, key_outputs) in enumerate(links):
            cur = con.execute(
                "INSERT INTO agent_contract_handoffs (id, contract_id, agent_id, "
                "session_id, workspace, kind, agent_state, raw_handoff_json, "
                "created_at, harness_agent_id, continues_handoff_id) "
                "VALUES (?, ?, ?, ?, 'me', 'investigation', ?, ?, ?, ?, ?)",
                (
                    link_ids[position],
                    f"{AGENT_ID}.link{position}",
                    AGENT_ID,
                    SESSION,
                    state,
                    _envelope(state, key_outputs),
                    SAME_SECOND,
                    HARNESS_ID,
                    ids[-1] if ids else None,
                ),
            )
            ids.append(cur.lastrowid)
        con.commit()
    finally:
        con.close()
    return ids


def _view_id(harness_id: str, *extra: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(_REPO_ROOT)
    return subprocess.run(
        [sys.executable, str(CONTRACT_CLI), "view", "--harness-id", harness_id, *extra],
        capture_output=True, text=True, env=env,
    )


def _view(*extra: str) -> subprocess.CompletedProcess:
    return _view_id(HARNESS_ID, *extra)


def test_view_by_harness_id_resolves_the_latest_link_and_names_the_chain(chain_ids):
    result = _view()

    assert result.returncode == 0, result.stderr
    shown = json.loads(result.stdout)
    assert shown["handoff_id"] == chain_ids[-1]
    assert shown["contract_id"] == f"{AGENT_ID}.link2"
    assert shown["envelope"]["agent_status"]["agent_state"] == "COMPLETE"
    assert shown["links"] == chain_ids


def test_view_by_harness_id_follows_the_chain_when_ids_disagree_with_it(tmp_path):
    chain = _seed_chain(tmp_path, (10, 30, 20))

    result = _view()

    assert result.returncode == 0, result.stderr
    shown = json.loads(result.stdout)
    assert shown["handoff_id"] == chain[-1] == 20
    assert shown["envelope"]["agent_status"]["agent_state"] == "COMPLETE"


@pytest.mark.parametrize("wrong_kind_of_id", [AGENT_ID, SESSION])
def test_view_by_another_kind_of_id_names_the_expected_id_and_the_rows_harness_id(
    chain_ids, wrong_kind_of_id,
):
    result = _view_id(wrong_kind_of_id)

    assert result.returncode == 1
    message = result.stderr + result.stdout
    assert "Task result's agentId in Claude Code" in message
    assert "child session id in OpenCode" in message
    assert HARNESS_ID in message


def test_view_by_an_unknown_id_names_the_expected_id_and_no_row(chain_ids):
    result = _view_id("a0000000000000000")

    assert result.returncode == 1
    message = result.stderr + result.stdout
    assert "Task result's agentId in Claude Code" in message
    assert HARNESS_ID not in message


def test_view_field_by_harness_id_reads_the_latest_link(chain_ids):
    result = _view("--field", "evidence_report.key_outputs")

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == ["pushed 54799fa6"]
