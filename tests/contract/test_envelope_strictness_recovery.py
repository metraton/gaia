"""The handle on the inside of the door, and the two keys the DB sweep missed.

Closing the envelope's vocabulary introduced a trap: every write validates the
WHOLE envelope and no verb removes a key, so a draft carrying one invalid key
rejected every `set`, `fill` and `finalize` -- including the write that would
have corrected it. 70 of the 238 draft files already on disk were in exactly
that state, put there by the CLI itself when it still accepted a root orphan
silently.

The property under test is: NO DRAFT CAN BE BORN IMPOSSIBLE TO CLOSE. A
historical envelope is not the fault of the agent that inherits it, so the
vocabulary is enforced on what an agent WRITES and repaired on what it
INHERITS -- out loud, never silently.

Alongside it, the two system-written keys a sweep of the persisted population
could not see:

  * ``reconciled`` -- written by `gaia contract reconcile`, present in ZERO
    rows, and the only system key that turns a VALID verdict invalid. A green
    test (tests/cli/test_contract_reconcile.py) already asserted it must be
    there; it passed only because nothing revalidates after reconciling.
  * ``binding_rejection`` -- written inside a dict LITERAL, a shape a sweep
    looking for ``envelope["key"] = ...`` cannot match.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from gaia.contract.validator import (
    AGENT_WRITABLE_TOP_LEVEL_KEYS,
    SYSTEM_WRITTEN_ENVELOPE_KEYS,
    FormErrorCode,
    validate_form,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CLI = str(_REPO_ROOT / "bin" / "cli" / "contract.py")


def _valid_envelope() -> dict:
    return {
        "agent_status": {
            "agent_state": "IN_PROGRESS",
            "agent_id": "a1b2c30f1e2d3c4b5",
            "pending_steps": [],
            "next_action": "continue",
        },
        "evidence_report": {
            "patterns_checked": [],
            "files_checked": [],
            "commands_run": [],
            "key_outputs": [],
            "verbatim_outputs": [],
            "cross_layer_impacts": [],
            "open_gaps": [],
        },
        "consolidation_report": None,
        "approval_request": None,
    }


def _sanitize(envelope: dict, removals=None) -> dict:
    """Imported inside the call so the module still collects on a tree that
    has not grown ``sanitize_envelope`` yet."""
    from gaia.contract.validator import sanitize_envelope

    return sanitize_envelope(envelope, removals=removals)


def _cli(args, env, expect=0):
    proc = subprocess.run(
        [sys.executable, _CLI] + args,
        capture_output=True, text=True, env=env, cwd=str(_REPO_ROOT),
    )
    if expect is not None:
        assert proc.returncode == expect, (
            f"{args} -> rc={proc.returncode}\n"
            f"stdout={proc.stdout}\nstderr={proc.stderr}"
        )
    return proc


@pytest.fixture
def cli_env(tmp_path):
    return dict(
        PATH="/usr/bin:/bin",
        HOME=str(tmp_path),
        GAIA_DATA_DIR=str(tmp_path / "gaia"),
    )


# ---------------------------------------------------------------------------
# 1. The two system keys the database sweep could not see
# ---------------------------------------------------------------------------
def test_reconciled_is_accepted():
    """`gaia contract reconcile` writes it; nothing had revalidated after."""
    env = _valid_envelope()
    env["reconciled"] = True

    result = validate_form(env)

    assert result.ok is True, result.error_summary()


def test_reconciled_does_not_invalidate_an_otherwise_valid_envelope():
    """The distinguishing property: this key lands on a VALID envelope.

    Every other system key lands on envelopes that already failed for other
    reasons, so rejecting one of those changed no verdict. Rejecting this one
    turns a passing contract into a failing one.
    """
    env = _valid_envelope()
    assert validate_form(env).ok is True

    env["reconciled"] = True
    env["superseded_by_contract_id"] = "a2222222222222222.real"

    assert validate_form(env).ok is True


def test_binding_rejection_is_accepted():
    """Written into the birth envelope as a key of a dict literal."""
    env = _valid_envelope()
    env["binding_rejection"] = {
        "reason": "plan_task_not_owned",
        "attempted_plan_task_id": 41,
    }

    assert validate_form(env).ok is True


def test_the_reconcile_verb_still_produces_a_validating_envelope(cli_env):
    """End-to-end through the real CLI, not just the key in isolation."""
    created = _cli(["init", "--json"], cli_env)
    draft_id = json.loads(created.stdout)["draft_id"]
    _cli(["set", "agent_status.next_action", "keep going",
          "--draft-id", draft_id], cli_env)

    viewed = _cli(["view", "--draft-id", draft_id, "--json"], cli_env)
    envelope = json.loads(viewed.stdout)["envelope"]
    envelope["reconciled"] = True

    assert validate_form(envelope).ok is True, validate_form(envelope).error_summary()


# ---------------------------------------------------------------------------
# 2. No draft can be born impossible to close
# ---------------------------------------------------------------------------
def test_a_draft_carrying_a_root_orphan_can_still_be_written(cli_env):
    """The measured shape: 70 draft files on disk carry exactly this."""
    created = _cli(["init", "--json"], cli_env)
    draft_id = json.loads(created.stdout)["draft_id"]

    # Corrupt the draft the way the CLI itself used to, before the door closed.
    draft_file = (
        Path(cli_env["GAIA_DATA_DIR"]) / "contract_drafts" / f"{draft_id}.json"
    )
    stored = json.loads(draft_file.read_text())
    stored["commands_run"] = ["gaia doctor"]
    stored["summary"] = "an undeclared key from an older turn"
    draft_file.write_text(json.dumps(stored))

    written = _cli(
        ["add", "evidence_report.open_gaps", "still open",
         "--draft-id", draft_id, "--json"],
        cli_env,
    )

    payload = json.loads(written.stdout)
    assert payload["status"] == "ok"
    # Announced on both paths -- never silent.
    assert "sanitized" in payload
    assert any("commands_run" in line for line in payload["sanitized"])
    assert "[SANITIZED]" in written.stderr
    # And the write itself landed.
    viewed = _cli(
        ["view", "--draft-id", draft_id, "--field", "evidence_report.open_gaps"],
        cli_env,
    )
    assert json.loads(viewed.stdout) == ["still open"]


def test_a_stuck_draft_can_still_be_finalized(cli_env):
    """`finalize` was rejected too, so the turn could not even be closed."""
    created = _cli(["init", "--json"], cli_env)
    draft_id = json.loads(created.stdout)["draft_id"]

    draft_file = (
        Path(cli_env["GAIA_DATA_DIR"]) / "contract_drafts" / f"{draft_id}.json"
    )
    stored = json.loads(draft_file.read_text())
    stored["key_outputs"] = ["an orphan at the root"]
    stored["agent_status"]["agent_state"] = "BLOCKED"
    draft_file.write_text(json.dumps(stored))

    finalized = _cli(["finalize", "--draft-id", draft_id, "--json"], cli_env)

    assert json.loads(finalized.stdout)["status"] == "finalized"
    assert "[SANITIZED]" in finalized.stderr


def test_sanitize_keeps_the_value_when_a_scalar_sits_where_a_list_belongs():
    """Repair beats removal for a REQUIRED key: removing it would trade one
    unclosable draft for another, since MISSING_FIELD blocks writes too."""
    env = _valid_envelope()
    env["evidence_report"]["key_outputs"] = "ran pytest"

    removals: list = []
    cleaned = _sanitize(env, removals)

    assert cleaned["evidence_report"]["key_outputs"] == ["ran pytest"]
    assert validate_form(cleaned).ok is True
    assert any("key_outputs" in line for line in removals)


def test_sanitize_reports_where_a_misplaced_key_belonged():
    env = _valid_envelope()
    env["commands_run"] = ["gaia doctor"]

    removals: list = []
    cleaned = _sanitize(env, removals)

    assert "commands_run" not in cleaned
    assert validate_form(cleaned).ok is True
    assert any("evidence_report.commands_run" in line for line in removals)


def test_sanitize_never_mutates_its_input():
    """The historical row is not touched; only the new draft is repaired."""
    env = _valid_envelope()
    env["summary"] = "undeclared"

    _sanitize(env)

    assert env["summary"] == "undeclared"


def test_sanitize_leaves_a_clean_envelope_and_its_system_keys_alone():
    env = _valid_envelope()
    env["reconciled"] = True
    env["_contract_tag"] = "agent_contract_handoff"

    removals: list = []
    cleaned = _sanitize(env, removals)

    assert removals == []
    assert cleaned == env


@pytest.mark.parametrize(
    "corruption",
    [
        {"commands_run": ["x"]},
        {"summary": "prose"},
        {"findings": ["a"]},
        {"investigation": {"a": 1}},
        {"cluster_details": {}},
        {"work_summary": "x"},
        {"context_updates": []},
    ],
)
def test_every_measured_stuck_shape_becomes_writable(corruption):
    """The shapes actually found across the 70 stuck draft files."""
    env = _valid_envelope()
    env.update(corruption)
    assert validate_form(env).ok is False

    assert validate_form(_sanitize(env)).ok is True


def test_read_verbs_do_not_sanitize(cli_env):
    """`validate` must report the draft as it IS -- repairing on a read would
    hide the very defect the caller asked about."""
    created = _cli(["init", "--json"], cli_env)
    draft_id = json.loads(created.stdout)["draft_id"]

    draft_file = (
        Path(cli_env["GAIA_DATA_DIR"]) / "contract_drafts" / f"{draft_id}.json"
    )
    stored = json.loads(draft_file.read_text())
    stored["summary"] = "undeclared"
    draft_file.write_text(json.dumps(stored))

    validated = _cli(["validate", "--draft-id", draft_id], cli_env, expect=1)

    assert "UNKNOWN_FIELD" in validated.stderr
    # Untouched on disk: a read verb persists nothing.
    assert json.loads(draft_file.read_text())["summary"] == "undeclared"


# ---------------------------------------------------------------------------
# 3. The messages teach the right thing
# ---------------------------------------------------------------------------
def test_unknown_field_never_advertises_a_system_key():
    """The message offering 'declared keys' must offer what the AGENT may
    write. Listing backstop/reaped/salvaged as options teaches an agent to
    write a key only the rescue lanes may write."""
    env = _valid_envelope()
    env["zzzz_nothing_like_it"] = 1

    detail = [
        e.detail for e in validate_form(env).errors
        if e.code == FormErrorCode.UNKNOWN_FIELD
    ][0]

    for system_key in SYSTEM_WRITTEN_ENVELOPE_KEYS:
        assert system_key not in detail, (
            f"{system_key!r} is written by the system, never by an agent, and "
            f"must not be offered as an available field"
        )
    # It still names something useful.
    assert any(key in detail for key in AGENT_WRITABLE_TOP_LEVEL_KEYS)


def test_misplaced_key_says_how_to_get_unstuck_not_only_where_it_belongs():
    """Naming the correct path is necessary and was not sufficient: an agent
    obeying it literally was still stuck, because the problem is the key
    already sitting in the draft, and there is no delete verb."""
    env = _valid_envelope()
    env["commands_run"] = ["gaia doctor"]

    detail = [
        e.detail for e in validate_form(env).errors
        if e.code == FormErrorCode.MISPLACED_KEY
    ][0]

    assert "evidence_report.commands_run" in detail
    assert "persisted NOTHING" in detail
    assert "strips" in detail
