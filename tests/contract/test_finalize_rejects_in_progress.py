"""
finalize requires a CLOSING state; IN_PROGRESS is rejected.

Live finding (row 10955): an agent checkpointed IN_PROGRESS, ran finalize, and
the row closed CLEAN (cut_reason NULL) while declaring the turn never ended --
neither cut nor closed, a limbo invisible to `contract list --cut` and to the
terminal-state reads. finalize is the turn's clean close, so the one state
that asserts the turn continues is the one state a close may not carry.

Scope: the CLI seam only. The rescue lanes (SubagentStop persister / reaper /
salvage) write through gaia.store.writer directly and legitimately record
IN_PROGRESS as the verdict of a CUT turn -- those stamp a cut lane, never a
clean close, and are covered by their own suites.
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
def cli_env(tmp_path, monkeypatch):
    data_dir = tmp_path / "gaia_data"
    monkeypatch.setenv("GAIA_DATA_DIR", str(data_dir))
    monkeypatch.delenv("GAIA_DB", raising=False)
    monkeypatch.delenv("GAIA_DB_PATH", raising=False)
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    return dict(os.environ)


def _init_draft(env: dict) -> str:
    init = _run(["init", "--agent-id", VALID_AGENT_ID, "--json"], env)
    assert init.returncode == 0, f"init failed: {init.stderr!r}"
    return json.loads(init.stdout)["draft_id"]


def test_finalize_rejects_in_progress_draft(cli_env):
    """A draft still IN_PROGRESS (init's seed state) may not close clean."""
    draft_id = _init_draft(cli_env)

    fin = _run(["finalize", "--draft-id", draft_id, "--json"], cli_env)
    assert fin.returncode == 1
    payload = json.loads(fin.stdout)
    assert payload["status"] == "rejected"
    assert payload["reason"] == "closing_state_required"
    # The message must instruct the fix, not just refuse.
    assert "gaia contract set agent_status.agent_state" in payload["error"]


def test_finalize_accepts_closing_state_after_fix(cli_env):
    """The instructed correction (set a closing state, re-finalize) works."""
    draft_id = _init_draft(cli_env)

    rejected = _run(["finalize", "--draft-id", draft_id, "--json"], cli_env)
    assert rejected.returncode == 1

    # BLOCKED is a closing, non-terminal state -- the cheapest legal close.
    set_state = _run(
        ["set", "agent_status.agent_state", "BLOCKED"], cli_env
    )
    assert set_state.returncode == 0, set_state.stderr

    fin = _run(["finalize", "--draft-id", draft_id, "--json"], cli_env)
    assert fin.returncode == 0, f"finalize failed: {fin.stderr!r}"
    payload = json.loads(fin.stdout)
    assert payload["status"] == "finalized"
