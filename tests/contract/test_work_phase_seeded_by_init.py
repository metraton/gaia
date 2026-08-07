"""``gaia contract init`` seeds the ``work_phase`` slot (discoverability).

Mirrors test_failure_report_seeded_by_init.py: ``_initial_envelope`` seeds
``work_phase: None`` -- the same way ``consolidation_report``/
``approval_request``/``failure_report``/``memory_delta`` already are -- so a
fresh draft's `gaia contract view` shows the slot exists without making it
required. Absence and an explicit null reach the identical "no check" path in
``gaia.contract.validator.validate_form`` (WORK_PHASE_SHAPE only fires when the
field is present and non-null).

Style mirrors test_failure_report_seeded_by_init.py: real subprocesses against
`bin/cli/contract.py`'s standalone shim, isolated `GAIA_DATA_DIR`.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from tests.fixtures.agent_ids import valid_agent_id  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_CLI = _REPO_ROOT / "bin" / "cli" / "contract.py"

VALID_AGENT_ID = valid_agent_id("a1234abce")


def _run(args: list, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CONTRACT_CLI), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


@pytest.fixture()
def base_env(tmp_path, monkeypatch):
    data_dir = tmp_path / "gaia_data"
    monkeypatch.setenv("GAIA_DATA_DIR", str(data_dir))
    monkeypatch.delenv("GAIA_DB", raising=False)
    monkeypatch.delenv("GAIA_DB_PATH", raising=False)
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
    return dict(os.environ)


# ---------------------------------------------------------------------------
# Unit level: the helper itself carries the key, seeded null.
# ---------------------------------------------------------------------------
def test_initial_envelope_seeds_work_phase_as_null():
    sys.path.insert(0, str(_REPO_ROOT))
    sys.path.insert(0, str(_REPO_ROOT / "bin"))
    from cli import contract as contract_cli  # noqa: E402

    env = contract_cli._initial_envelope("a" + "0" * 16)
    assert "work_phase" in env
    assert env["work_phase"] is None


# ---------------------------------------------------------------------------
# CLI level: `init` succeeds, and the slot is visible via `view` -- both the
# full envelope and the --field subtree read.
# ---------------------------------------------------------------------------
def test_init_still_succeeds_with_the_seeded_slot(base_env):
    proc = _run(["init", "--agent-id", VALID_AGENT_ID, "--json"], base_env)
    assert proc.returncode == 0, f"init failed: {proc.stderr!r}"


def test_view_full_envelope_shows_work_phase_slot(base_env):
    _run(["init", "--agent-id", VALID_AGENT_ID, "--json"], base_env)
    view = _run(["view"], base_env)
    assert view.returncode == 0, f"stderr={view.stderr!r}"

    envelope = json.loads(view.stdout)["envelope"]
    assert "work_phase" in envelope
    assert envelope["work_phase"] is None


def test_view_field_work_phase_reads_null(base_env):
    _run(["init", "--agent-id", VALID_AGENT_ID, "--json"], base_env)
    view = _run(["view", "--field", "work_phase"], base_env)
    assert view.returncode == 0, f"stderr={view.stderr!r}"
    assert json.loads(view.stdout) is None


# ---------------------------------------------------------------------------
# `set` writes a real phase transition, and it survives to `view`.
# ---------------------------------------------------------------------------
def test_set_work_phase_persists_and_reads_back(base_env):
    _run(["init", "--agent-id", VALID_AGENT_ID, "--json"], base_env)
    setr = _run(["set", "work_phase", "investigating"], base_env)
    assert setr.returncode == 0, setr.stderr

    view = _run(["view", "--field", "work_phase"], base_env)
    assert view.returncode == 0, view.stderr
    assert json.loads(view.stdout) == "investigating"


# ---------------------------------------------------------------------------
# The seed does not turn the field into a requirement: a plain COMPLETE close
# still succeeds without ever touching work_phase.
# ---------------------------------------------------------------------------
def test_seeded_null_never_blocks_an_unrelated_complete(base_env):
    init = _run(["init", "--agent-id", VALID_AGENT_ID, "--json"], base_env)
    assert init.returncode == 0, init.stderr

    patch = json.dumps({
        "evidence_report": {
            "verification": {"method": "pytest", "result": "pass", "details": "ok"},
        },
    })
    assert _run(["fill", "--json", patch], base_env).returncode == 0
    assert _run(["set", "agent_status.next_action", "done"], base_env).returncode == 0
    complete = _run(["set", "agent_status.agent_state", "COMPLETE"], base_env)
    assert complete.returncode == 0, complete.stderr
