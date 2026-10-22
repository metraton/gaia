"""Plan 94 T9: rows are born with a surface, close with a summary, and fill-rate reports both."""

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

from tests.fixtures.agent_ids import valid_agent_id  # noqa: E402

CONTRACT_CLI = _REPO_ROOT / "bin" / "cli" / "contract.py"
AGENT_ID = valid_agent_id("surface-summary")
WORKSPACE = "me"
_EVIDENCE_KEYS = (
    "patterns_checked", "files_checked", "commands_run", "key_outputs",
    "verbatim_outputs", "cross_layer_impacts", "open_gaps",
)


def _run(args: list, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CONTRACT_CLI), *args],
        capture_output=True, text=True, env=env, timeout=60,
    )


@pytest.fixture()
def cli_env(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "gaia_data"))
    for var in ("GAIA_DB", "GAIA_DB_PATH", "CLAUDE_SESSION_ID", "GAIA_DISPATCH_AGENT"):
        monkeypatch.delenv(var, raising=False)
    return dict(os.environ)


def _envelope(state: str = "NEEDS_VERIFICATION", summary=None) -> dict:
    envelope = {
        "agent_status": {
            "agent_state": state,
            "agent_id": AGENT_ID,
            "pending_steps": [],
            "next_action": "independent verification",
        },
        "evidence_report": {k: [] for k in _EVIDENCE_KEYS},
        "consolidation_report": None,
        "approval_request": None,
    }
    if summary is not None:
        envelope["user_facing_summary"] = summary
    return envelope


def _draft(env: dict, envelope: dict) -> str:
    init = _run(["init", "--agent-id", AGENT_ID, "--json"], env)
    assert init.returncode == 0, init.stderr
    draft_id = json.loads(init.stdout)["draft_id"]
    fill = _run(["fill", "--draft-id", draft_id, "--json", json.dumps(envelope)], env)
    assert fill.returncode == 0, fill.stderr
    return draft_id


@pytest.mark.parametrize("summary", [None, "", "   "])
def test_finalize_refuses_a_close_without_user_facing_summary(cli_env, summary):
    draft_id = _draft(cli_env, _envelope(summary=summary))

    fin = _run(["finalize", "--draft-id", draft_id, "--json"], cli_env)

    assert fin.returncode == 1, fin.stdout
    payload = json.loads(fin.stdout)
    assert payload["reason"] == "user_facing_summary_required"
    assert "user_facing_summary" in payload["error"]
    assert f"gaia contract set --draft-id {draft_id} user_facing_summary" in payload["error"]


def test_finalize_accepts_the_close_once_the_summary_is_filled(cli_env):
    draft_id = _draft(cli_env, _envelope())
    assert _run(["finalize", "--draft-id", draft_id], cli_env).returncode == 1

    fixed = _run(
        ["set", "--draft-id", draft_id, "user_facing_summary", "Task 9 is ready for review."],
        cli_env,
    )
    assert fixed.returncode == 0, fixed.stderr

    fin = _run(["finalize", "--draft-id", draft_id, "--json"], cli_env)
    assert fin.returncode == 0, fin.stderr
    assert json.loads(fin.stdout)["status"] == "finalized"


def _schema_db(tmp_path) -> Path:
    from gaia.store.writer import _connect

    db = tmp_path / "gaia.db"
    _connect(db).close()
    return db


def test_an_agent_without_a_routing_row_is_born_with_its_declared_surface(tmp_path):
    from tools.context.context_provider import build_kernel_sections

    db = _schema_db(tmp_path)

    verifier = build_kernel_sections("gaia-verifier", WORKSPACE, turn_role="verifier", db_path=db)
    developer = build_kernel_sections("developer", WORKSPACE, db_path=db)

    assert verifier and verifier["surface"] == "verification"
    assert developer and developer["surface"] == "app_ci_tooling"


def _row(db, contract_id, *, state, summary=None, surface=None, cut_reason=None):
    from gaia.store.writer import finalize_agent_contract_handoff, insert_dispatched_handoff

    if surface is not None:
        insert_dispatched_handoff(
            contract_id, AGENT_ID, WORKSPACE,
            kernel_sections={"role": "primary", "surface": surface},
            db_path=db,
        )
    return finalize_agent_contract_handoff(
        contract_id, AGENT_ID, WORKSPACE, state,
        json.dumps(_envelope(state, summary)), cut_reason=cut_reason, db_path=db,
    )


def test_cut_lanes_close_without_a_summary_and_fill_rate_reports_both(cli_env):
    from gaia.store.writer import finalize_agent_contract_handoff, open_contract_continuation

    db = Path(cli_env["GAIA_DATA_DIR"]) / "gaia.db"
    _row(db, f"{AGENT_ID}.full", state="NEEDS_VERIFICATION", summary="done", surface="gaia_system")
    _row(db, f"{AGENT_ID}.nosummary", state="BLOCKED", surface="iac")
    _row(db, f"{AGENT_ID}.nosurface", state="NEEDS_INPUT", summary="which one?")
    reaped = _row(db, f"{AGENT_ID}.reaped", state="IN_PROGRESS", cut_reason="reaped")
    assert reaped.get("handoff_id"), "the reaper lane persists a row with no summary"

    open_contract_continuation(
        f"{AGENT_ID}.full", f"{AGENT_ID}.link",
        raw_handoff_json=json.dumps({"continues_contract_id": f"{AGENT_ID}.full"}),
        db_path=db,
    )
    finalize_agent_contract_handoff(
        f"{AGENT_ID}.link", AGENT_ID, WORKSPACE, "NEEDS_VERIFICATION",
        json.dumps(_envelope(summary="resumed and done")), db_path=db,
    )

    res = _run(["fill-rate", "--since", "7d", "--json"], cli_env)

    assert res.returncode == 0, res.stderr
    report = json.loads(res.stdout)
    assert report["rows"] == 4
    assert report["surface"]["filled"] == 3
    assert report["user_facing_summary"]["filled"] == 3
    assert report["surface"]["rate"] == pytest.approx(0.75)

    gated = _run(["fill-rate", "--since", "7d", "--min", "0.95", "--json"], cli_env)
    assert gated.returncode == 1
    assert json.loads(gated.stdout)["pass"] is False
