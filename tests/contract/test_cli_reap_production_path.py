"""
gate 1039 (plan 65, task 552) -- `gaia contract reap`, the reaper's
production invocation path.

T15's own re-run of gate 1016 had no production caller for
``dispatch_lifecycle.reap_stale_turn`` and fell back to ``python3 -c``, which
is not one. This file covers ONLY the new CLI wiring -- the promotion logic
itself (age window, liveness check, idempotent candidate selection) is
already covered by tests/contract/test_reap_stale_dispatched_handoffs.py and
tests/hooks/modules/agents/test_dispatch_lifecycle_reap_stale_turn.py, which
this task leaves untouched.

Every CLI call runs as a real subprocess against ``bin/cli/contract.py``'s
standalone shim (same convention as test_cli_e2e_idempotent.py), against an
isolated ``GAIA_DATA_DIR`` -- never the real ``~/.gaia`` substrate.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.fixtures.agent_ids import valid_agent_id  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_CLI = _REPO_ROOT / "bin" / "cli" / "contract.py"

WORKSPACE = "me"
AGENT_ID = valid_agent_id("reapcli1")

# Any row not touched inside this many seconds counts as stale; the fixture
# below backdates created_at by a full hour, well outside this window.
OLDER_THAN_SECONDS = 300


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
    """Isolated GAIA_DATA_DIR per test, inherited by every subprocess call."""
    data_dir = tmp_path / "gaia_data"
    monkeypatch.setenv("GAIA_DATA_DIR", str(data_dir))
    monkeypatch.delenv("GAIA_DB", raising=False)
    monkeypatch.delenv("GAIA_DB_PATH", raising=False)
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    return dict(os.environ)


def _db_path(env: dict) -> Path:
    return Path(env["GAIA_DATA_DIR"]) / "gaia.db"


def _birth_stale_row(contract_id: str, env: dict) -> None:
    """Insert a DISPATCHED row and backdate created_at by an hour."""
    from gaia.store.writer import insert_dispatched_handoff

    db_path = _db_path(env)
    outcome = insert_dispatched_handoff(
        contract_id,
        AGENT_ID,
        WORKSPACE,
        session_id="sess-" + contract_id,
        db_path=db_path,
    )
    assert outcome["status"] == "applied"

    con = sqlite3.connect(str(db_path))
    try:
        backdated = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 3600)
        )
        con.execute(
            "UPDATE agent_contract_handoffs SET created_at = ? WHERE contract_id = ?",
            (backdated, contract_id),
        )
        con.commit()
    finally:
        con.close()


def _agent_state(contract_id: str, env: dict) -> str:
    con = sqlite3.connect(str(_db_path(env)))
    try:
        row = con.execute(
            "SELECT agent_state FROM agent_contract_handoffs WHERE contract_id = ?",
            (contract_id,),
        ).fetchone()
    finally:
        con.close()
    assert row is not None
    return row[0]


def test_reap_verb_promotes_a_stale_row_with_literal_output(cli_env):
    _birth_stale_row("reapcliA.contract", cli_env)

    result = _run(
        ["reap", "--older-than-seconds", str(OLDER_THAN_SECONDS), "--json"],
        cli_env,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "ok"
    assert payload["checked"] == 1
    assert payload["reaped"] == ["reapcliA.contract"]
    assert payload["spared"] == []
    assert payload["cut_reason"] == "reaped"
    assert _agent_state("reapcliA.contract", cli_env) != "DISPATCHED"


def test_reap_second_run_is_a_zero_change_no_op(cli_env):
    _birth_stale_row("reapcliB.contract", cli_env)

    first = _run(
        ["reap", "--older-than-seconds", str(OLDER_THAN_SECONDS), "--json"],
        cli_env,
    )
    assert first.returncode == 0, first.stderr
    assert json.loads(first.stdout)["reaped"] == ["reapcliB.contract"]

    second = _run(
        ["reap", "--older-than-seconds", str(OLDER_THAN_SECONDS), "--json"],
        cli_env,
    )
    assert second.returncode == 0, second.stderr
    second_payload = json.loads(second.stdout)
    assert second_payload["checked"] == 0
    assert second_payload["reaped"] == []
    assert second_payload["spared"] == []


def test_reap_plain_text_output_reports_literal_counts(cli_env):
    _birth_stale_row("reapcliC.contract", cli_env)

    result = _run(["reap", "--older-than-seconds", str(OLDER_THAN_SECONDS)], cli_env)

    assert result.returncode == 0, result.stderr
    assert "checked=1" in result.stdout
    assert "reaped=1" in result.stdout
    assert "spared=0" in result.stdout


def test_reap_rejects_non_positive_older_than_seconds(cli_env):
    result = _run(["reap", "--older-than-seconds", "0", "--json"], cli_env)

    assert result.returncode == 1
    assert json.loads(result.stdout)["status"] == "error"


def test_reap_requires_the_flag(cli_env):
    result = _run(["reap"], cli_env)

    assert result.returncode != 0
    assert "--older-than-seconds" in result.stderr
