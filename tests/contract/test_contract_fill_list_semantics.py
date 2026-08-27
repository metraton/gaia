"""``gaia contract fill`` never discards entries already in a list field.

The defect this locks down was measured, not theorised: a verifier wrote twelve
entries into ``evidence_report.verbatim_outputs`` across two ``fill`` calls and
ended with eight, because ``_deep_merge`` assigns a list through the same branch
as a scalar and therefore cannot report the replacement. Both calls returned
``status: ok`` with a zero exit, so the loss was invisible until that turn read
its own draft back and counted.

Filling across several calls is the NORMAL path, not an edge case: a single call
carrying a full evidence envelope routinely exceeds the shell's command-length
limit. So the verb has to be safe under repetition.

The decided semantics: ``fill`` writes a list only while that list is still
empty. A patch that would discard entries already there is refused WHOLE --
nothing written, non-zero exit, every colliding field named with its counts.
``add`` extends a list, ``set`` replaces one deliberately; ``fill`` guesses
neither. An append default was rejected because it turns a corrective re-fill
into a duplicate and the ``[]``-to-clear idiom into a no-op -- the same silent
surprise pointed the other way.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.fixtures.agent_ids import valid_agent_id

_REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_CLI = _REPO_ROOT / "bin" / "cli" / "contract.py"

AGENT_ID = valid_agent_id("fill-list-semantics")
DRAFT_ID = f"{AGENT_ID}.lists"

FIRST_BATCH = {
    "evidence_report": {
        "verbatim_outputs": ["V1", "V2", "V3"],
        "files_checked": ["F1", "F2", "F3"],
    }
}
SECOND_BATCH = {
    "evidence_report": {
        "verbatim_outputs": ["V4", "V5", "V6"],
        "files_checked": ["F4", "F5", "F6"],
    }
}


def _run(args: list, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CONTRACT_CLI), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


_ENV_PASSTHROUGH = ("PATH", "HOME", "PYTHONPATH", "LANG", "LC_ALL", "TMPDIR")


@pytest.fixture()
def cli_env(tmp_path):
    """Isolated GAIA_DATA_DIR: drafts AND the DB land under it, never the real
    substrate -- these tests write evidence-shaped payloads.

    Built from an allowlist rather than a copy of ``os.environ`` for a second
    reason: pytest renders the locals of a failing frame, and this dict is an
    argument to every helper here, so inheriting the developer's environment
    would print their API tokens into any failure report.
    """
    env = {key: os.environ[key] for key in _ENV_PASSTHROUGH if key in os.environ}
    env["GAIA_DATA_DIR"] = str(tmp_path / "gaia_data")
    return env


def _fresh_draft(env: dict) -> None:
    init = _run(["init", "--agent-id", AGENT_ID, "--draft-id", DRAFT_ID, "--json"], env)
    assert init.returncode == 0, init.stderr


def _fill(env: dict, patch: dict) -> subprocess.CompletedProcess:
    return _run(["fill", "--draft-id", DRAFT_ID, "--json", json.dumps(patch)], env)


def _field(env: dict, dotted: str) -> list:
    view = _run(["view", "--draft-id", DRAFT_ID, "--field", dotted, "--json"], env)
    assert view.returncode == 0, view.stderr
    return json.loads(view.stdout)


def test_a_second_fill_of_a_populated_list_is_refused_and_loses_nothing(cli_env):
    """The measured defect, on two fields, one of them not verbatim_outputs."""
    _fresh_draft(cli_env)
    assert _fill(cli_env, FIRST_BATCH).returncode == 0

    second = _fill(cli_env, SECOND_BATCH)

    assert second.returncode != 0, "a silent ok here IS the defect"
    payload = json.loads(second.stdout)
    assert payload["status"] == "error"
    assert "evidence_report.verbatim_outputs" in payload["error"]
    assert "evidence_report.files_checked" in payload["error"]
    assert _field(cli_env, "evidence_report.verbatim_outputs") == ["V1", "V2", "V3"]
    assert _field(cli_env, "evidence_report.files_checked") == ["F1", "F2", "F3"]


def test_the_refusal_names_both_counts_so_the_caller_sees_the_size_of_the_loss(cli_env):
    _fresh_draft(cli_env)
    assert _fill(cli_env, FIRST_BATCH).returncode == 0

    error = json.loads(_fill(cli_env, SECOND_BATCH).stdout)["error"]

    assert "3 existing entries" in error
    assert "3 incoming entries" in error
    assert "gaia contract add" in error, "the extend route has to be named"
    assert "gaia contract set" in error, "the replace route has to be named"


def test_the_refusal_is_atomic_across_a_patch_that_mixes_a_list_with_a_scalar(cli_env):
    """A colliding list rejects the WHOLE patch: a half-applied envelope is a
    worse state than a rejected one, because nothing records which half landed.
    """
    _fresh_draft(cli_env)
    assert _fill(cli_env, FIRST_BATCH).returncode == 0

    mixed = _fill(cli_env, {
        "agent_status": {"next_action": "this must not land"},
        "evidence_report": {"files_checked": ["F9"]},
    })

    assert mixed.returncode != 0
    view = _run(["view", "--draft-id", DRAFT_ID, "--json"], cli_env)
    envelope = json.loads(view.stdout)["envelope"]
    assert envelope["agent_status"]["next_action"] != "this must not land"
    assert envelope["evidence_report"]["files_checked"] == ["F1", "F2", "F3"]


def test_fill_still_writes_a_list_that_is_still_empty(cli_env):
    """The normal path is untouched: every list starts [] in the draft skeleton,
    so a first fill per field never collides."""
    _fresh_draft(cli_env)

    first = _fill(cli_env, FIRST_BATCH)

    assert first.returncode == 0, first.stderr
    assert _field(cli_env, "evidence_report.verbatim_outputs") == ["V1", "V2", "V3"]


def test_a_re_issued_identical_patch_is_not_refused(cli_env):
    """Re-sending a patch after correcting one of its fields must not be blocked
    by the fields that already landed unchanged."""
    _fresh_draft(cli_env)
    assert _fill(cli_env, FIRST_BATCH).returncode == 0

    again = _fill(cli_env, FIRST_BATCH)

    assert again.returncode == 0, again.stderr
    assert _field(cli_env, "evidence_report.files_checked") == ["F1", "F2", "F3"]


def test_an_empty_list_that_would_clear_a_populated_one_is_refused_too(cli_env):
    """No magic []. Clearing a populated list is a deliberate replace, so it
    goes through ``set`` where the intent is written down."""
    _fresh_draft(cli_env)
    assert _fill(cli_env, FIRST_BATCH).returncode == 0

    cleared = _fill(cli_env, {"evidence_report": {"files_checked": []}})

    assert cleared.returncode != 0
    assert _field(cli_env, "evidence_report.files_checked") == ["F1", "F2", "F3"]


def test_add_is_the_route_that_extends_a_populated_list(cli_env):
    _fresh_draft(cli_env)
    assert _fill(cli_env, FIRST_BATCH).returncode == 0

    added = _run(
        ["add", "--draft-id", DRAFT_ID, "evidence_report.files_checked", "F4"],
        cli_env,
    )

    assert added.returncode == 0, added.stderr
    assert _field(cli_env, "evidence_report.files_checked") == ["F1", "F2", "F3", "F4"]


def test_set_is_the_route_that_replaces_a_populated_list(cli_env):
    _fresh_draft(cli_env)
    assert _fill(cli_env, FIRST_BATCH).returncode == 0

    replaced = _run(
        ["set", "--draft-id", DRAFT_ID, "evidence_report.files_checked", '["F9"]'],
        cli_env,
    )

    assert replaced.returncode == 0, replaced.stderr
    assert _field(cli_env, "evidence_report.files_checked") == ["F9"]
