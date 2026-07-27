"""``gaia contract init`` seeds the ``failure_report`` slot (discoverability).

Before this, ``_initial_envelope`` did not carry the key at all: the CLI
already accepted ``failure_report`` on write (shared validation), but a fresh
draft never showed the slot, so an agent inspecting `gaia contract view`
right after `init` had no way to discover the field exists. Seeding it
``None`` -- the same way ``consolidation_report``/``approval_request`` are
already seeded -- makes the slot visible without making it required: absence
and an explicit null reach the identical "no check" path in
``gaia.contract.validator.validate_form`` (FAILURE_REPORT_SHAPE only fires
when the block is present and non-null).

Style mirrors test_cli_view_field.py: real subprocesses against
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

VALID_AGENT_ID = valid_agent_id("a1234abcd")


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
def test_initial_envelope_seeds_failure_report_as_null():
    sys.path.insert(0, str(_REPO_ROOT))
    sys.path.insert(0, str(_REPO_ROOT / "bin"))
    from cli import contract as contract_cli  # noqa: E402

    env = contract_cli._initial_envelope("a" + "0" * 16)
    assert "failure_report" in env
    assert env["failure_report"] is None


# ---------------------------------------------------------------------------
# CLI level: `init` succeeds (the seed does not make the field required),
# and the slot is visible via `view` -- both the full envelope and the
# --field subtree read.
# ---------------------------------------------------------------------------
def test_init_still_succeeds_with_the_seeded_slot(base_env):
    proc = _run(["init", "--agent-id", VALID_AGENT_ID, "--json"], base_env)
    assert proc.returncode == 0, f"init failed: {proc.stderr!r}"


def test_view_full_envelope_shows_failure_report_slot(base_env):
    _run(["init", "--agent-id", VALID_AGENT_ID, "--json"], base_env)
    view = _run(["view"], base_env)
    assert view.returncode == 0, f"stderr={view.stderr!r}"

    envelope = json.loads(view.stdout)["envelope"]
    assert "failure_report" in envelope
    assert envelope["failure_report"] is None


def test_view_field_failure_report_reads_null(base_env):
    _run(["init", "--agent-id", VALID_AGENT_ID, "--json"], base_env)
    view = _run(["view", "--field", "failure_report"], base_env)
    assert view.returncode == 0, f"stderr={view.stderr!r}"
    assert json.loads(view.stdout) is None


# ---------------------------------------------------------------------------
# The seed does not turn the field into a requirement: a plain `set` of
# agent_state to a terminal COMPLETE (with its own dependencies met) still
# succeeds without ever touching failure_report.
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
