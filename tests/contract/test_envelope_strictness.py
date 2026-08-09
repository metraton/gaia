"""Envelope strictness: declared types, misplaced paths, and unknown keys.

The form layer used to check PRESENCE without checking TYPE, accept an
evidence key written at the root as a silent orphan, and accept any
unrecognized key as a new field. Each of the five cases below was measured in
the live population before it was closed:

    * a string where a list is declared      -> FIELD_TYPE
      (evidence lists mistyped in six of the seven; pending_steps carried a
      bare string on 166 rows)
    * an evidence key written WITHOUT its prefix, at the root -> MISPLACED_KEY
      (root orphans: commands_run x62, key_outputs x59, files_checked x48, ...)
    * a mistyped field name (files_checkd)   -> UNKNOWN_FIELD, naming the
      nearest declared key
    * a lower-case agent_state and a spaced/upper-case work_phase, both of
      which validated NORMALIZED and persisted RAW

The last group is the delicate one and has its own section: several top-level
keys are written by the SYSTEM, not by an agent, and rejecting one of them
would break the mechanism that writes it. ``_contract_tag`` in particular is
stamped onto EVERY fence-parsed envelope by
``modules.agents.contract_validator.parse_contract`` before that same dict is
handed to ``validate_form``, and ``continues_contract_id`` is what lets a
resumed turn mint a continuation at all.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from gaia.contract.validator import FormErrorCode, validate_form

_REPO_ROOT = Path(__file__).resolve().parents[2]


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


def _codes(envelope: dict):
    return validate_form(envelope).codes


def _canonicalize(envelope: dict) -> dict:
    """Imported inside the call so this module still collects on a tree that
    has not grown ``canonicalize_envelope`` yet -- the before/after run needs
    each case to fail on its OWN behaviour, not on a collection error."""
    from gaia.contract.validator import canonicalize_envelope

    return canonicalize_envelope(envelope)


# ---------------------------------------------------------------------------
# Measured case 1 -- a string where a list is declared
# ---------------------------------------------------------------------------
def test_string_in_an_evidence_list_is_rejected_by_type():
    env = _valid_envelope()
    env["evidence_report"]["commands_run"] = "ran pytest"

    result = validate_form(env)

    assert result.ok is False
    assert FormErrorCode.FIELD_TYPE in result.codes
    offending = [e for e in result.errors if e.code == FormErrorCode.FIELD_TYPE]
    assert offending[0].field == "evidence_report.commands_run"
    # The rejection names the field, the type that arrived, and the one
    # expected -- in the envelope's own JSON vocabulary, not Python's.
    assert "got string" in offending[0].detail
    assert "must be an array" in offending[0].detail


def test_string_in_pending_steps_is_rejected_by_type():
    """The 166-row case: pending_steps carrying prose instead of a list."""
    env = _valid_envelope()
    env["agent_status"]["pending_steps"] = "nothing pending"

    result = validate_form(env)

    assert result.ok is False
    offending = [e for e in result.errors if e.code == FormErrorCode.FIELD_TYPE]
    assert offending and offending[0].field == "agent_status.pending_steps"


@pytest.mark.parametrize(
    "field,value",
    [
        ("patterns_checked", "grep"),
        ("files_checked", {"a": 1}),
        ("key_outputs", 42),
        ("verbatim_outputs", {"out": "x"}),
        ("open_gaps", "none"),
        ("cross_layer_impacts", "none"),
    ],
)
def test_every_evidence_list_checks_its_type(field, value):
    env = _valid_envelope()
    env["evidence_report"][field] = value

    result = validate_form(env)

    assert result.ok is False
    assert FormErrorCode.FIELD_TYPE in result.codes


# ---------------------------------------------------------------------------
# Measured case 2 -- an evidence key written without its prefix
# ---------------------------------------------------------------------------
def test_evidence_key_at_the_root_is_rejected_not_normalized():
    env = _valid_envelope()
    env["commands_run"] = ["gaia doctor"]

    result = validate_form(env)

    assert result.ok is False
    offending = [e for e in result.errors if e.code == FormErrorCode.MISPLACED_KEY]
    assert offending and offending[0].field == "commands_run"
    # The message says where it belongs -- and the value is NOT moved there.
    assert "evidence_report.commands_run" in offending[0].detail
    assert env["evidence_report"]["commands_run"] == []


def test_agent_status_key_at_the_root_is_rejected():
    env = _valid_envelope()
    env["next_action"] = "done"

    result = validate_form(env)

    assert result.ok is False
    offending = [e for e in result.errors if e.code == FormErrorCode.MISPLACED_KEY]
    assert offending and "agent_status.next_action" in offending[0].detail


# ---------------------------------------------------------------------------
# Measured case 3 -- a mistyped field name
# ---------------------------------------------------------------------------
def test_mistyped_evidence_key_is_rejected_with_the_nearest_name():
    env = _valid_envelope()
    env["evidence_report"]["files_checkd"] = []

    result = validate_form(env)

    assert result.ok is False
    offending = [e for e in result.errors if e.code == FormErrorCode.UNKNOWN_FIELD]
    assert offending and offending[0].field == "evidence_report.files_checkd"
    assert "files_checked" in offending[0].detail


def test_unknown_root_key_with_no_near_match_is_still_rejected():
    env = _valid_envelope()
    env["zzzz_not_a_field"] = 1

    result = validate_form(env)

    assert result.ok is False
    assert FormErrorCode.UNKNOWN_FIELD in result.codes


# ---------------------------------------------------------------------------
# Measured cases 4 and 5 -- validated normalized, persisted canonical
# ---------------------------------------------------------------------------
def test_lowercase_agent_state_is_persisted_uppercase():
    env = _valid_envelope()
    env["agent_status"]["agent_state"] = "in_progress"

    assert validate_form(env).ok is True
    canonical = _canonicalize(env)

    assert canonical["agent_status"]["agent_state"] == "IN_PROGRESS"
    # The input is never mutated in place; canonicalization returns a new dict.
    assert env["agent_status"]["agent_state"] == "in_progress"


def test_spaced_uppercase_work_phase_is_persisted_canonical():
    env = _valid_envelope()
    env["work_phase"] = "  Investigating  "

    assert validate_form(env).ok is True
    canonical = _canonicalize(env)

    assert canonical["work_phase"] == "investigating"


def test_verification_result_and_type_are_persisted_canonical():
    env = _valid_envelope()
    env["agent_status"]["agent_state"] = "COMPLETE"
    env["agent_status"]["next_action"] = " Done "
    env["evidence_report"]["verification"] = {
        "method": "pytest",
        "result": "PASS",
        "type": " Command ",
        "command": "pytest tests/contract",
        "details": "green",
    }

    assert validate_form(env).ok is True
    canonical = _canonicalize(env)
    verification = canonical["evidence_report"]["verification"]

    assert verification["result"] == "pass"
    assert verification["type"] == "command"
    assert canonical["agent_status"]["next_action"] == "done"


def test_canonicalizing_a_clean_envelope_changes_nothing():
    env = _valid_envelope()

    assert _canonicalize(env) == env


# ---------------------------------------------------------------------------
# The delicate part -- keys the SYSTEM writes must keep validating
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "key,value",
    [
        # Stamped on EVERY fence-parsed envelope by parse_contract, then fed
        # straight into validate_form. Rejecting it breaks all fence validation.
        ("_contract_tag", "agent_contract_handoff"),
        ("_contract_tag", "json:contract"),
        # The continuation link -- rejecting it breaks resumed turns.
        ("continues_contract_id", "a1b2c30f1e2d3c4b5.deadbeefcafe"),
        # Birth markers carried across into a continuation seed.
        ("born_at_dispatch", True),
        ("agent_name", "gaia-system"),
        # The rescue lanes that write rows for turns with no envelope of
        # their own (handoff_persister / the SubagentStop backstop / salvage).
        ("degraded", True),
        ("auto_captured", True),
        ("backstop", "hook_subagent_stop"),
        ("reaped", True),
        ("salvaged", "truncation"),
        ("dispatch_closed_at_subagent_stop", True),
        ("superseded_by_contract_id", "a1b2c30f1e2d3c4b5.beefcafe0001"),
        ("agent_output_preview", "Listo."),
        ("reconstructed_from_finalized_draft", "a1b2c30f1e2d3c4b5.0f0f0f0f0f0f"),
        ("fallback", True),
        ("agent_state", "DISPATCHED"),
    ],
)
def test_system_written_keys_are_still_accepted(key, value):
    env = _valid_envelope()
    env[key] = value

    result = validate_form(env)

    assert result.ok is True, result.error_summary()


def test_a_fence_parsed_envelope_still_validates():
    """parse_contract stamps _contract_tag; the gate validates that same dict."""
    sys.path.insert(0, str(_REPO_ROOT / "hooks"))
    from modules.agents.contract_validator import parse_contract

    body = json.dumps(_valid_envelope())
    parsed = parse_contract("```agent_contract_handoff\n%s\n```\n" % body)

    assert parsed["_contract_tag"] == "agent_contract_handoff"
    assert validate_form(parsed).ok is True


def test_a_continuation_seed_still_validates():
    """The envelope a resumed turn's new link is born with.

    Mirrors bin/cli/contract.py::_continuation_seed -- the blank starting shape
    plus the provenance key and the birth markers carried across.
    """
    from gaia.contract.drafts import initial_envelope

    seed = initial_envelope("a1b2c30f1e2d3c4b5")
    seed["continues_contract_id"] = "a1b2c30f1e2d3c4b5.cc6aa603aaf2"
    seed["born_at_dispatch"] = True
    seed["agent_name"] = "gaia-system"

    assert validate_form(seed).ok is True


def test_a_continuation_can_still_be_minted_end_to_end(tmp_path):
    """The mechanism itself, not just the seed's shape.

    Writes to a CLOSED contract through the real CLI and asserts a new link is
    minted and lands the write. If the unknown-key door rejected
    ``continues_contract_id``, the write to the new link would fail validation
    and resumed turns would stop working.
    """
    env = dict(
        PATH="/usr/bin:/bin",
        HOME=str(tmp_path),
        GAIA_DATA_DIR=str(tmp_path / "gaia"),
    )
    cli = str(_REPO_ROOT / "bin" / "cli" / "contract.py")

    created = subprocess.run(
        [sys.executable, cli, "init", "--json"],
        capture_output=True, text=True, env=env, cwd=str(_REPO_ROOT),
    )
    assert created.returncode == 0, created.stderr
    draft_id = json.loads(created.stdout)["draft_id"]

    closed = subprocess.run(
        [sys.executable, cli, "set", "agent_status.agent_state", "BLOCKED",
         "--draft-id", draft_id],
        capture_output=True, text=True, env=env, cwd=str(_REPO_ROOT),
    )
    assert closed.returncode == 0, closed.stderr

    finalized = subprocess.run(
        [sys.executable, cli, "finalize", "--draft-id", draft_id, "--json"],
        capture_output=True, text=True, env=env, cwd=str(_REPO_ROOT),
    )
    assert finalized.returncode == 0, finalized.stderr

    resumed = subprocess.run(
        [sys.executable, cli, "add", "evidence_report.open_gaps", "still open",
         "--draft-id", draft_id, "--json"],
        capture_output=True, text=True, env=env, cwd=str(_REPO_ROOT),
    )

    assert resumed.returncode == 0, resumed.stderr
    payload = json.loads(resumed.stdout)
    continuation = payload.get("continuation")
    assert continuation, payload
    assert continuation["continues_contract_id"] == draft_id
    assert payload["draft_id"] != draft_id


# ---------------------------------------------------------------------------
# The CLI persists the canonical value, not the raw one
# ---------------------------------------------------------------------------
def test_cli_persists_the_canonical_agent_state(tmp_path):
    env = dict(
        PATH="/usr/bin:/bin",
        HOME=str(tmp_path),
        GAIA_DATA_DIR=str(tmp_path / "gaia"),
    )
    cli = str(_REPO_ROOT / "bin" / "cli" / "contract.py")

    created = subprocess.run(
        [sys.executable, cli, "init", "--json"],
        capture_output=True, text=True, env=env, cwd=str(_REPO_ROOT),
    )
    assert created.returncode == 0, created.stderr
    draft_id = json.loads(created.stdout)["draft_id"]

    written = subprocess.run(
        [sys.executable, cli, "set", "agent_status.agent_state", "blocked",
         "--draft-id", draft_id],
        capture_output=True, text=True, env=env, cwd=str(_REPO_ROOT),
    )
    assert written.returncode == 0, written.stderr
    # No silent conversion: the write announces what it canonicalized.
    assert "agent_status.agent_state" in written.stderr

    viewed = subprocess.run(
        [sys.executable, cli, "view", "--draft-id", draft_id,
         "--field", "agent_status.agent_state"],
        capture_output=True, text=True, env=env, cwd=str(_REPO_ROOT),
    )
    assert viewed.returncode == 0, viewed.stderr
    # `view --field` prints the stored value verbatim as JSON.
    assert json.loads(viewed.stdout.strip()) == "BLOCKED"


def test_cli_rejects_a_misplaced_evidence_key(tmp_path):
    env = dict(
        PATH="/usr/bin:/bin",
        HOME=str(tmp_path),
        GAIA_DATA_DIR=str(tmp_path / "gaia"),
    )
    cli = str(_REPO_ROOT / "bin" / "cli" / "contract.py")

    created = subprocess.run(
        [sys.executable, cli, "init", "--json"],
        capture_output=True, text=True, env=env, cwd=str(_REPO_ROOT),
    )
    assert created.returncode == 0, created.stderr
    draft_id = json.loads(created.stdout)["draft_id"]

    rejected = subprocess.run(
        [sys.executable, cli, "add", "commands_run", "gaia doctor",
         "--draft-id", draft_id],
        capture_output=True, text=True, env=env, cwd=str(_REPO_ROOT),
    )

    assert rejected.returncode == 1
    assert "MISPLACED_KEY" in rejected.stderr
    assert "evidence_report.commands_run" in rejected.stderr
