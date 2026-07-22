"""
AC-4 -- CLI validate-on-write, NO false-pass (M2).

Two INDEPENDENT checks, run as real subprocesses against
``bin/cli/contract.py`` (its standalone shim, not ``bin/gaia`` -- per the T4
hard constraint, this avoids the ``gaia dev`` / DB-bootstrap path entirely):

    1. "gaia contract init --agent-id a1234abcd" -> exit 0
       (a genuinely SHAPE-VALID envelope is produced and persisted).
    2. "gaia contract set agent_status.plan_status BOGUS" -> exit != 0,
       and the rejection carries the enum text (not a crash).

Also covers the CLI's own by-value building blocks (add / view / validate /
fill / finalize) so the "no false-pass" property is proven across every
mutating verb, not only the two AC-4 headline commands.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

CONTRACT_CLI = Path(__file__).resolve().parents[2] / "bin" / "cli" / "contract.py"

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
    """Isolated GAIA_DATA_DIR per test, inherited by every subprocess call."""
    data_dir = tmp_path / "gaia_data"
    monkeypatch.setenv("GAIA_DATA_DIR", str(data_dir))
    monkeypatch.delenv("GAIA_DB", raising=False)
    monkeypatch.delenv("GAIA_DB_PATH", raising=False)
    return dict(os.environ)


# ---------------------------------------------------------------------------
# AC-4 check 1: init on a valid agent-id exits 0 (no false-pass the OTHER
# way -- a genuinely valid write must not be spuriously rejected either).
# ---------------------------------------------------------------------------
def test_init_with_valid_agent_id_exits_zero(cli_env):
    proc = _run(["init", "--agent-id", VALID_AGENT_ID, "--json"], cli_env)

    assert proc.returncode == 0, (
        f"expected exit 0, got {proc.returncode}; stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    payload = json.loads(proc.stdout.strip())
    assert payload["status"] == "ok"
    assert payload["draft_id"]


# ---------------------------------------------------------------------------
# AC-4 check 2: set of a bogus plan_status exits non-zero WITH enum text,
# not a crash (no traceback, a clean rejection message on stderr).
# ---------------------------------------------------------------------------
def test_set_bogus_plan_status_rejected_with_enum_text(cli_env):
    init_proc = _run(["init", "--agent-id", VALID_AGENT_ID], cli_env)
    assert init_proc.returncode == 0, init_proc.stderr

    set_proc = _run(["set", "agent_status.plan_status", "BOGUS"], cli_env)

    assert set_proc.returncode != 0, (
        f"expected non-zero exit for a bogus plan_status, got 0; stdout={set_proc.stdout!r}"
    )
    # Not a crash: no Python traceback on stderr.
    assert "Traceback" not in set_proc.stderr
    # The rejection must carry the enum text (the valid plan_status values),
    # not a bare "invalid" with no guidance.
    assert "PLAN_STATUS" in set_proc.stderr
    assert "IN_PROGRESS" in set_proc.stderr
    assert "COMPLETE" in set_proc.stderr


def test_set_bogus_plan_status_does_not_persist_the_bad_value(cli_env):
    """The rejected write must not corrupt the on-disk draft (no false-pass
    means the invalid state never lands, not merely that the CLI printed an
    error)."""
    _run(["init", "--agent-id", VALID_AGENT_ID], cli_env)
    set_proc = _run(["set", "agent_status.plan_status", "BOGUS"], cli_env)
    assert set_proc.returncode != 0

    view_proc = _run(["view"], cli_env)
    assert view_proc.returncode == 0, view_proc.stderr
    payload = json.loads(view_proc.stdout)
    assert payload["envelope"]["agent_status"]["plan_status"] == "IN_PROGRESS"


# ---------------------------------------------------------------------------
# Two independent checks must not depend on each other's exit code -- prove
# the SAME session also accepts a legitimate follow-up write after a
# rejection (the rejection did not corrupt CLI/draft state).
# ---------------------------------------------------------------------------
def test_valid_write_after_a_rejected_write_still_succeeds(cli_env):
    _run(["init", "--agent-id", VALID_AGENT_ID], cli_env)
    rejected = _run(["set", "agent_status.plan_status", "BOGUS"], cli_env)
    assert rejected.returncode != 0

    good = _run(["set", "agent_status.plan_status", "COMPLETE"], cli_env)
    # COMPLETE with no verification.result == "pass" is ALSO invalid -- this
    # proves the validator is not merely rejecting the string "BOGUS" as a
    # special case, but genuinely enforcing the VERIFICATION_RESULT rule.
    assert good.returncode != 0
    assert "VERIFICATION_RESULT" in good.stderr

    legit = _run(["set", "agent_status.next_action", "still working"], cli_env)
    assert legit.returncode == 0, legit.stderr


# ---------------------------------------------------------------------------
# Malformed agent_id at init time is also rejected (no crash, no false-pass).
# ---------------------------------------------------------------------------
def test_init_with_malformed_agent_id_rejected_not_crashed(cli_env):
    proc = _run(["init", "--agent-id", "not-an-agent-id"], cli_env)

    assert proc.returncode != 0
    assert "Traceback" not in proc.stderr
    assert "AGENT_ID_FORMAT" in proc.stderr


# ---------------------------------------------------------------------------
# add / view / validate / fill round-trip: build a fully valid COMPLETE
# envelope purely by-value, verify it, and prove a subsequent invalid fill
# is rejected without disturbing the valid state.
# ---------------------------------------------------------------------------
def test_add_view_validate_fill_round_trip(cli_env):
    init_proc = _run(["init", "--agent-id", VALID_AGENT_ID, "--json"], cli_env)
    draft_id = json.loads(init_proc.stdout)["draft_id"]

    add_proc = _run(["add", "agent_status.pending_steps", "review diff"], cli_env)
    assert add_proc.returncode == 0, add_proc.stderr

    # Pre-completion state (still IN_PROGRESS): pending_steps carries the
    # added step -- proves the add/view round-trip before advancing to
    # COMPLETE, where COMPLETE_SHAPE (R4) requires pending_steps == [].
    pre_view_proc = _run(["view"], cli_env)
    assert pre_view_proc.returncode == 0, pre_view_proc.stderr
    pre_envelope = json.loads(pre_view_proc.stdout)["envelope"]
    assert pre_envelope["agent_status"]["pending_steps"] == ["review diff"]

    patch = json.dumps({
        "evidence_report": {
            "verification": {"method": "pytest", "result": "pass", "details": "AC-4 green"},
        },
    })
    fill_proc = _run(["fill", "--json", patch], cli_env)
    assert fill_proc.returncode == 0, fill_proc.stderr

    # Clear pending_steps and mark next_action done before completing --
    # COMPLETE_SHAPE (R4) requires pending_steps == [] and next_action ==
    # 'done' on any COMPLETE contract.
    clear_pending_proc = _run(["set", "agent_status.pending_steps", "[]"], cli_env)
    assert clear_pending_proc.returncode == 0, clear_pending_proc.stderr

    next_action_proc = _run(["set", "agent_status.next_action", "done"], cli_env)
    assert next_action_proc.returncode == 0, next_action_proc.stderr

    complete_proc = _run(["set", "agent_status.plan_status", "COMPLETE"], cli_env)
    assert complete_proc.returncode == 0, complete_proc.stderr

    validate_proc = _run(["validate", "--json"], cli_env)
    assert validate_proc.returncode == 0, validate_proc.stderr
    assert json.loads(validate_proc.stdout)["draft_id"] == draft_id

    view_proc = _run(["view"], cli_env)
    envelope = json.loads(view_proc.stdout)["envelope"]
    assert envelope["agent_status"]["pending_steps"] == []
    assert envelope["agent_status"]["next_action"] == "done"
    assert envelope["evidence_report"]["verification"]["result"] == "pass"

    finalize_proc = _run(["finalize", "--json"], cli_env)
    assert finalize_proc.returncode == 0, finalize_proc.stderr
    # T7 wired finalize to the store: the JSON status is now "finalized"
    # (was the "validated" scaffold placeholder before T7).
    assert json.loads(finalize_proc.stdout)["status"] == "finalized"

    # An invalid fill afterward is rejected and does not disturb the
    # already-valid, already-finalized-checked draft.
    bad_fill = _run(
        ["fill", "--json", json.dumps({"agent_status": {"plan_status": "NOT_REAL"}})],
        cli_env,
    )
    assert bad_fill.returncode != 0
    assert "PLAN_STATUS" in bad_fill.stdout or "PLAN_STATUS" in bad_fill.stderr

    still_good = _run(["view"], cli_env)
    envelope_after = json.loads(still_good.stdout)["envelope"]
    assert envelope_after["agent_status"]["plan_status"] == "COMPLETE"


# ---------------------------------------------------------------------------
# Operating on a nonexistent draft is a clean error, not a crash.
# ---------------------------------------------------------------------------
def test_set_without_any_draft_is_a_clean_error(cli_env):
    proc = _run(["set", "agent_status.plan_status", "COMPLETE"], cli_env)

    assert proc.returncode != 0
    assert "Traceback" not in proc.stderr
    assert "No draft found" in proc.stderr
