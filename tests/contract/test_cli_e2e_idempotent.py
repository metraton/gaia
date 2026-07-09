"""
AC-6 -- e2e + idempotencia (M2/M3, task T7).

A full ``init -> set/add -> finalize`` cycle inserts EXACTLY ONE row into
``agent_contract_handoffs``; a SECOND ``finalize`` of the SAME draft is a
genuine no-op (no duplicate row).

``finalize`` is the SOLE idempotent writer of that typed row. The idempotency
key is the draft's OWN contract id (``draft_id``, minted by the CLI, shape
``"{agent_id}.{token}"`` -- never derived from CLAUDE_SESSION_ID). The store
writer ``gaia.store.writer.finalize_agent_contract_handoff`` issues
``INSERT ... ON CONFLICT(contract_id) DO NOTHING`` against the
``idx_agent_contract_handoffs_contract_id`` UNIQUE index, so a racing agent
finalize and (T9) a hook backstop converge to ONE row.

Every CLI call runs as a real subprocess against ``bin/cli/contract.py``'s
standalone shim (NOT ``bin/gaia`` -- avoids the ``gaia dev`` / DB-bootstrap
path, per the T4/T5/T7 hard constraints). The fresh DB is materialized by the
writer's own ``_connect`` from ``gaia/store/schema.sql`` inside the isolated
``GAIA_DATA_DIR`` -- so this test exercises the real, current schema (the v28
``contract_id`` column + UNIQUE index), not a hand-rolled fixture.
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

VALID_AGENT_ID = "a1234abcd"


def _run(args: list, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CONTRACT_CLI), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


@pytest.fixture()
def cli_env(tmp_path, monkeypatch):
    """Isolated GAIA_DATA_DIR per test, inherited by every subprocess call.

    Both the contract drafts (``<data_dir>/contract_drafts/``) and the
    SQLite DB (``<data_dir>/gaia.db``) land under this dir, so nothing
    touches the real ``~/.gaia`` substrate.
    """
    data_dir = tmp_path / "gaia_data"
    monkeypatch.setenv("GAIA_DATA_DIR", str(data_dir))
    monkeypatch.delenv("GAIA_DB", raising=False)
    monkeypatch.delenv("GAIA_DB_PATH", raising=False)
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
    # Never let a stray dispatch-agent env var trip the write guard.
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    return dict(os.environ)


def _db_path(env: dict) -> Path:
    return Path(env["GAIA_DATA_DIR"]) / "gaia.db"


def _count_handoffs(env: dict, contract_id: str | None = None) -> int:
    db = _db_path(env)
    if not db.is_file():
        return 0
    con = sqlite3.connect(str(db))
    try:
        if contract_id is None:
            row = con.execute(
                "SELECT COUNT(*) FROM agent_contract_handoffs"
            ).fetchone()
        else:
            row = con.execute(
                "SELECT COUNT(*) FROM agent_contract_handoffs WHERE contract_id = ?",
                (contract_id,),
            ).fetchone()
        return int(row[0])
    finally:
        con.close()


def _build_complete_draft(env: dict) -> str:
    """init -> set/add -> fill a genuinely-valid COMPLETE envelope.

    Returns the draft_id. Uses the same by-value building blocks an agent
    would: init, add to a list field, fill the verification block, and set
    plan_status=COMPLETE last (COMPLETE requires verification.result==pass,
    which the validator enforces -- so this proves the cycle produces a
    truly-valid terminal envelope, not a stub).
    """
    init = _run(["init", "--agent-id", VALID_AGENT_ID, "--json"], env)
    assert init.returncode == 0, f"init failed: {init.stderr!r}"
    draft_id = json.loads(init.stdout)["draft_id"]

    add = _run(["add", "agent_status.pending_steps", "step-1"], env)
    assert add.returncode == 0, add.stderr

    patch = json.dumps({
        "evidence_report": {
            "verification": {
                "method": "pytest",
                "result": "pass",
                "details": "AC-6 e2e",
            },
        },
    })
    fill = _run(["fill", "--json", patch], env)
    assert fill.returncode == 0, fill.stderr

    na = _run(["set", "agent_status.next_action", "done"], env)
    assert na.returncode == 0, na.stderr

    complete = _run(["set", "agent_status.plan_status", "COMPLETE"], env)
    assert complete.returncode == 0, complete.stderr

    return draft_id


# ---------------------------------------------------------------------------
# AC-6 headline: init->set/add->finalize inserts EXACTLY one row.
# ---------------------------------------------------------------------------
def test_full_cycle_inserts_exactly_one_row(cli_env):
    assert _count_handoffs(cli_env) == 0  # nothing before

    draft_id = _build_complete_draft(cli_env)

    fin = _run(["finalize", "--draft-id", draft_id, "--json"], cli_env)
    assert fin.returncode == 0, f"finalize failed: {fin.stderr!r}"
    payload = json.loads(fin.stdout)
    assert payload["status"] == "finalized"
    assert payload["created"] is True
    assert payload["draft_id"] == draft_id
    first_handoff_id = payload["handoff_id"]
    assert first_handoff_id is not None

    # EXACTLY one row overall, and exactly one for this contract id.
    assert _count_handoffs(cli_env) == 1
    assert _count_handoffs(cli_env, contract_id=draft_id) == 1


# ---------------------------------------------------------------------------
# AC-6 headline: a SECOND finalize of the SAME draft is a genuine no-op.
# ---------------------------------------------------------------------------
def test_second_finalize_is_a_noop_one_row_total(cli_env):
    draft_id = _build_complete_draft(cli_env)

    first = _run(["finalize", "--draft-id", draft_id, "--json"], cli_env)
    assert first.returncode == 0, first.stderr
    first_payload = json.loads(first.stdout)
    assert first_payload["created"] is True
    first_id = first_payload["handoff_id"]
    assert _count_handoffs(cli_env) == 1

    # Second finalize of the SAME draft.
    second = _run(["finalize", "--draft-id", draft_id, "--json"], cli_env)
    assert second.returncode == 0, second.stderr
    second_payload = json.loads(second.stdout)

    # It is a no-op: not a new insert, and it reports the SAME handoff_id.
    assert second_payload["status"] == "finalized"
    assert second_payload["created"] is False, (
        "second finalize must NOT create a new row"
    )
    assert second_payload["handoff_id"] == first_id, (
        "second finalize must resolve to the SAME row"
    )

    # The load-bearing assertion: STILL exactly one row, not two.
    assert _count_handoffs(cli_env) == 1
    assert _count_handoffs(cli_env, contract_id=draft_id) == 1


# ---------------------------------------------------------------------------
# Repeated finalizes (>2) stay a no-op -- idempotency is not a one-shot.
# ---------------------------------------------------------------------------
def test_repeated_finalize_never_duplicates(cli_env):
    draft_id = _build_complete_draft(cli_env)

    ids = set()
    for _ in range(5):
        proc = _run(["finalize", "--draft-id", draft_id, "--json"], cli_env)
        assert proc.returncode == 0, proc.stderr
        ids.add(json.loads(proc.stdout)["handoff_id"])

    # Every call resolved to the SAME single row id, and there is one row.
    assert len(ids) == 1
    assert _count_handoffs(cli_env) == 1


# ---------------------------------------------------------------------------
# Two DISTINCT drafts finalize to two DISTINCT rows (the key is per-contract,
# not a global singleton). Guards against an over-broad idempotency key.
# ---------------------------------------------------------------------------
def test_distinct_drafts_produce_distinct_rows(cli_env):
    draft_a = _build_complete_draft(cli_env)
    draft_b = _build_complete_draft(cli_env)
    assert draft_a != draft_b

    fa = _run(["finalize", "--draft-id", draft_a, "--json"], cli_env)
    fb = _run(["finalize", "--draft-id", draft_b, "--json"], cli_env)
    assert fa.returncode == 0 and fb.returncode == 0

    id_a = json.loads(fa.stdout)["handoff_id"]
    id_b = json.loads(fb.stdout)["handoff_id"]
    assert id_a != id_b
    assert _count_handoffs(cli_env) == 2
    assert _count_handoffs(cli_env, contract_id=draft_a) == 1
    assert _count_handoffs(cli_env, contract_id=draft_b) == 1


# ---------------------------------------------------------------------------
# The persisted row carries the resolved plan_status and the contract id key.
# ---------------------------------------------------------------------------
def test_persisted_row_carries_task_status_and_contract_id(cli_env):
    draft_id = _build_complete_draft(cli_env)
    fin = _run(["finalize", "--draft-id", draft_id, "--json"], cli_env)
    assert fin.returncode == 0, fin.stderr

    con = sqlite3.connect(str(_db_path(cli_env)))
    try:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT contract_id, agent_id, task_status FROM agent_contract_handoffs"
        ).fetchall()
    finally:
        con.close()

    assert len(rows) == 1
    row = rows[0]
    assert row["contract_id"] == draft_id
    assert row["agent_id"] == VALID_AGENT_ID
    assert row["task_status"] == "COMPLETE"
