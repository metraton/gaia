"""Project context is stored and read by the project it is about, not by the caller's workspace.

Measured defect (plan 94, F3): only ``project_identity`` was routed by owner, so
a ``cluster_details`` proposal for a century project accepted from workspace
``ws`` landed in ``ws``, where no reader of the century project looks.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[4]
for _p in (str(_REPO_ROOT / "hooks"), str(_REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from modules.context import context_writer  # noqa: E402

SESSION_WS = "ws"
CENTURY_WS = "century-inc"
CENTURY = "century-platform"
CENTURY_ID = "github.com/century/century-platform"
OTHER = "billing-api"
OTHER_ID = "github.com/century/billing-api"
PROPOSER = "a1234567890abcdef.0123456789ab"
GKE = {"gke": {"version": "1.30"}}


@pytest.fixture()
def db_path(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "gaia.db"
    monkeypatch.setenv("GAIA_DB", str(path))
    monkeypatch.delenv("GAIA_DATA_DIR", raising=False)
    con = sqlite3.connect(str(path))
    try:
        con.executescript((_REPO_ROOT / "gaia" / "store" / "schema.sql").read_text())
        for name in (SESSION_WS, CENTURY_WS):
            con.execute("INSERT INTO workspaces (name, identity) VALUES (?, ?)", (name, name))
        for name, identity in ((CENTURY, CENTURY_ID), (OTHER, OTHER_ID)):
            con.execute(
                "INSERT INTO projects (workspace, name, path, project_identity) VALUES (?, ?, ?, ?)",
                (CENTURY_WS, name, f"/repos/{name}", identity),
            )
        con.execute(
            "INSERT INTO project_context_contracts (workspace, contract_name, payload) "
            "VALUES (?, 'stack', ?)", (CENTURY_WS, json.dumps({"language": "go"})),
        )
        con.commit()
    finally:
        con.close()
    return path


def _rows(db_path: Path, sql: str, *params) -> list:
    con = sqlite3.connect(str(db_path))
    try:
        return con.execute(sql, params).fetchall()
    finally:
        con.close()


def _get_contract(*argv) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONPATH=str(_REPO_ROOT))
    return subprocess.run(
        [sys.executable, str(_REPO_ROOT / "bin" / "gaia"), "context", "get-contract", *argv],
        capture_output=True, text=True, env=env,
    )


def test_project_context_by_project_a_section_from_ws_lands_under_the_dispatch_project(db_path):
    clause = [{"contract": "cluster_details", "payload": GKE}]

    outcome = context_writer.apply_proposal(
        clause, SESSION_WS, db_path, project=CENTURY,
        proposed_by=PROPOSER, proposer_agent="cloud-troubleshooter",
    )

    assert outcome["errors"] == []
    assert outcome["applied"] == [
        {"contract": "cluster_details", "workspace": CENTURY_WS, "project": CENTURY_ID},
    ]
    stored = _rows(db_path, "SELECT project_identity, payload FROM project_context "
                            "WHERE contract_name = 'cluster_details'")
    assert stored == [(CENTURY_ID, json.dumps(GKE))]
    assert _rows(db_path, "SELECT * FROM project_context_contracts "
                          "WHERE contract_name = 'cluster_details'") == []


def test_project_context_by_project_an_entry_naming_its_project_wins_over_the_dispatch(db_path):
    clause = [{"contract": "cluster_details", "project": OTHER, "payload": GKE}]

    outcome = context_writer.apply_proposal(clause, SESSION_WS, db_path, project=CENTURY)

    assert outcome["errors"] == []
    assert [a["project"] for a in outcome["applied"]] == [OTHER_ID]


def test_project_context_by_project_a_section_with_no_project_is_refused_and_written_nowhere(db_path):
    outcome = context_writer.apply_proposal(
        [{"contract": "cluster_details", "payload": GKE}], SESSION_WS, db_path,
    )

    assert outcome["applied"] == []
    assert len(outcome["errors"]) == 1 and "Repair:" in outcome["errors"][0]
    assert _rows(db_path, "SELECT * FROM project_context") == []
    assert _rows(db_path, "SELECT * FROM project_context_contracts "
                          "WHERE contract_name = 'cluster_details'") == []


def test_project_context_by_project_every_write_records_proposer_and_agent_inserts_included(db_path):
    for version in ("1.30", "1.31"):
        outcome = context_writer.apply_proposal(
            [{"contract": "cluster_details", "payload": {"gke": {"version": version}}}],
            SESSION_WS, db_path, project=CENTURY,
            proposed_by=PROPOSER, proposer_agent="cloud-troubleshooter",
        )
        assert outcome["errors"] == []

    history = _rows(
        db_path,
        "SELECT project_identity, contract_name, before_payload_json, after_payload_json, "
        "changed_by_agent, proposed_by FROM project_context_history ORDER BY id",
    )
    assert history == [
        (CENTURY_ID, "cluster_details", None, json.dumps({"gke": {"version": "1.30"}}),
         "cloud-troubleshooter", PROPOSER),
        (CENTURY_ID, "cluster_details", json.dumps({"gke": {"version": "1.30"}}),
         json.dumps({"gke": {"version": "1.31"}}), "cloud-troubleshooter", PROPOSER),
    ]


def test_project_context_by_project_the_first_write_merges_into_what_readers_saw(db_path):
    """A2: a project with no new row reads the old one; a delta must not hide its keys."""
    context_writer.apply_proposal(
        [{"contract": "stack", "payload": {"runtime": "1.22"}}], SESSION_WS, db_path, project=CENTURY,
    )

    payload = json.loads(_rows(db_path, "SELECT payload FROM project_context "
                                        "WHERE project_identity = ?", CENTURY_ID)[0][0])
    assert payload == {"language": "go", "runtime": "1.22"}


def test_project_context_by_project_get_contract_reads_the_project_row(db_path):
    context_writer.apply_proposal(
        [{"contract": "cluster_details", "payload": GKE}], SESSION_WS, db_path, project=CENTURY,
    )

    shown = _get_contract("--section", "cluster_details", "--project", CENTURY_ID, "--json")

    assert shown.returncode == 0, shown.stderr
    result = json.loads(shown.stdout)
    assert result["payload"] == GKE and result["project"] == CENTURY_ID
    assert _get_contract("--section", "cluster_details", "--project", OTHER_ID).returncode == 1


def test_project_context_by_project_get_contract_falls_back_to_the_owning_workspace_row(db_path):
    shown = _get_contract("--section", "stack", "--project", OTHER, "--json")

    assert shown.returncode == 0, shown.stderr
    assert json.loads(shown.stdout)["payload"] == {"language": "go"}


def test_project_context_by_project_the_close_proposes_under_the_dispatch_project(db_path):
    result = context_writer.process_update_contracts(
        {"update_contracts": [{"contract": "cluster_details", "payload": GKE}]},
        {"agent": "cloud-troubleshooter", "db_path": db_path, "workspace": SESSION_WS,
         "project": f"{CENTURY} (/repos/{CENTURY})"},
    )

    assert result["errors"] == []
    assert result["proposed"] == [{
        "contract": "cluster_details", "workspace": CENTURY_WS,
        "project": CENTURY_ID, "keys": ["gke"],
    }]
    assert _rows(db_path, "SELECT * FROM project_context") == []
