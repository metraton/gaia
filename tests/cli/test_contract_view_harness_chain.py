"""`gaia contract view --harness-id` resolves the LATEST link of a resume chain.

A resumed turn is born into a new row that continues the one it resumes and
inherits its harness_agent_id, so one harness id names every link of the chain.
The view must land on the live link and say which links it chose from; links
born in the same second tie on created_at, which is where a timestamp-only pick
returned the chain's first, empty link.
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
    """Three links of one resume chain, oldest first, all born in one second."""
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
                "INSERT INTO agent_contract_handoffs (contract_id, agent_id, "
                "session_id, workspace, kind, agent_state, raw_handoff_json, "
                "created_at, harness_agent_id, continues_handoff_id) "
                "VALUES (?, ?, ?, 'me', 'investigation', ?, ?, ?, ?, ?)",
                (
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


def _view(*extra: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(_REPO_ROOT)
    return subprocess.run(
        [sys.executable, str(CONTRACT_CLI), "view", "--harness-id", HARNESS_ID, *extra],
        capture_output=True, text=True, env=env,
    )


def test_view_by_harness_id_resolves_the_latest_link_and_names_the_chain(chain_ids):
    result = _view()

    assert result.returncode == 0, result.stderr
    shown = json.loads(result.stdout)
    assert shown["handoff_id"] == chain_ids[-1]
    assert shown["contract_id"] == f"{AGENT_ID}.link2"
    assert shown["envelope"]["agent_status"]["agent_state"] == "COMPLETE"
    assert shown["links"] == chain_ids


def test_view_field_by_harness_id_reads_the_latest_link(chain_ids):
    result = _view("--field", "evidence_report.key_outputs")

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == ["pushed 54799fa6"]
