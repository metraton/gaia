"""
``report_prose`` -- the top-level home of a turn's full narrative.

The narrative a turn writes has until now travelled ONLY in the agent's
message, which nothing persists: the row keeps the evidence lists and
``user_facing_summary``, and the account that explains them dies with the
transcript. ``report_prose`` is that account's field on the envelope.

Two directions are covered here, and the second is the one that decides
whether the delivery is correct:

    * a multi-paragraph narrative -- newlines, accents, apostrophes -- goes
      in through ``fill --json-file`` and comes back out of
      ``view --field report_prose`` byte-for-byte identical
    * a turn that writes NO ``report_prose`` still validates and still
      finalizes, exit 0. The field is typed from day one and OPTIONAL in this
      delivery; a test that only proved the happy path could not tell an
      optional field from one made mandatory too early.

``user_facing_summary`` is deliberately NOT typed alongside it: it is
advisory, 236 rows already carry one, and typing it would start rejecting
turns that close today. A new key has no such history, which is the whole
reason the narrative gets a field of its own instead of a stricter reading of
the old one.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from gaia.contract.validator import (
    AGENT_WRITABLE_TOP_LEVEL_KEYS,
    SYSTEM_WRITTEN_ENVELOPE_KEYS,
    TOP_LEVEL_FIELD_TYPES,
    FormErrorCode,
    sanitize_envelope,
    validate_form,
)
from tests.fixtures.agent_ids import valid_agent_id  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_CLI = _REPO_ROOT / "bin" / "cli" / "contract.py"

VALID_AGENT_ID = valid_agent_id("a1234abcd")

# Every hazard the field exists to survive, in one value: paragraph breaks,
# accented and non-ASCII characters, an apostrophe, and embedded double
# quotes -- the exact text that breaks when a narrative is passed as a
# shell-quoted argument instead of a file.
MULTI_PARAGRAPH_PROSE = (
    "Veníamos a darle al relato un lugar en la fila, no en el mensaje.\n"
    "El hallazgo abarató la tarea: el sobre entero se persiste como blob "
    "JSON, así que no hizo falta migración.\n"
    "\n"
    'Lo que encontramos fue esto: el turno cerraba y "el relato" se perdía '
    "con la transcripción.\n"
    "\tLa sangría y los saltos de línea son parte del texto.\n"
    "\n"
    "Dónde estamos: el campo existe, está tipado str y es opcional."
)


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


# ---------------------------------------------------------------------------
# The form layer: declared, typed, optional.
# ---------------------------------------------------------------------------
def test_report_prose_is_declared_as_a_string():
    assert TOP_LEVEL_FIELD_TYPES["report_prose"] == (str,)


def test_report_prose_is_agent_writable_not_system_written():
    """The agent authors it, so an UNKNOWN_FIELD message may offer it as an
    option -- unlike the rescue-lane keys, which only the system may write."""
    assert "report_prose" in AGENT_WRITABLE_TOP_LEVEL_KEYS
    assert "report_prose" not in SYSTEM_WRITTEN_ENVELOPE_KEYS


def test_multi_paragraph_report_prose_validates():
    envelope = _valid_envelope()
    envelope["report_prose"] = MULTI_PARAGRAPH_PROSE
    assert validate_form(envelope).ok


def test_absent_report_prose_still_validates():
    """The opposite direction: the field is OPTIONAL in this delivery."""
    envelope = _valid_envelope()
    assert "report_prose" not in envelope
    result = validate_form(envelope)
    assert result.ok
    assert FormErrorCode.MISSING_FIELD not in result.codes


def test_null_report_prose_still_validates():
    """An explicit null is the seeded convention for an optional field, and a
    type is enforced only on a present, non-null value."""
    envelope = _valid_envelope()
    envelope["report_prose"] = None
    assert validate_form(envelope).ok


@pytest.mark.parametrize(
    "value",
    [
        ["paragraph one", "paragraph two"],
        {"body": "the narrative"},
        42,
        True,
    ],
)
def test_non_string_report_prose_is_rejected_by_type(value):
    envelope = _valid_envelope()
    envelope["report_prose"] = value
    result = validate_form(envelope)
    assert not result.ok
    assert FormErrorCode.FIELD_TYPE in result.codes
    offending = [e for e in result.errors if e.code == FormErrorCode.FIELD_TYPE]
    assert any(e.field == "report_prose" for e in offending)


def test_sanitize_drops_a_wrong_typed_report_prose_and_says_so():
    """An INHERITED envelope (a row read back, a resumed draft) carrying a
    mistyped value must not lock every later write out; sanitize repairs it.
    There is no lossless reading of a list as prose, so it is removed and the
    removal is reported rather than performed silently."""
    envelope = _valid_envelope()
    envelope["report_prose"] = ["paragraph one", "paragraph two"]
    removals: list = []
    cleaned = sanitize_envelope(envelope, removals=removals)
    assert "report_prose" not in cleaned
    assert any("report_prose" in line for line in removals)
    assert validate_form(cleaned).ok


# ---------------------------------------------------------------------------
# The CLI seam: written by file, read back verbatim.
# ---------------------------------------------------------------------------
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


def test_cli_round_trip_of_multi_paragraph_prose_is_verbatim(cli_env, tmp_path):
    draft_id = _init_draft(cli_env)

    patch_path = tmp_path / "patch.json"
    patch_path.write_text(
        json.dumps({"report_prose": MULTI_PARAGRAPH_PROSE}), encoding="utf-8"
    )

    fill = _run(
        ["fill", "--draft-id", draft_id, "--json-file", str(patch_path)], cli_env
    )
    assert fill.returncode == 0, fill.stderr

    view = _run(["view", "--draft-id", draft_id, "--field", "report_prose"], cli_env)
    assert view.returncode == 0, view.stderr
    assert json.loads(view.stdout) == MULTI_PARAGRAPH_PROSE


def test_cli_rejects_a_non_string_report_prose_on_write(cli_env, tmp_path):
    """Validate-on-write: the rejected patch never lands, so the draft keeps
    reading as a draft that never had the field."""
    draft_id = _init_draft(cli_env)

    patch_path = tmp_path / "patch.json"
    patch_path.write_text(json.dumps({"report_prose": ["one", "two"]}))

    fill = _run(
        ["fill", "--draft-id", draft_id, "--json-file", str(patch_path)], cli_env
    )
    assert fill.returncode == 1
    assert "report_prose" in (fill.stdout + fill.stderr)

    view = _run(["view", "--draft-id", draft_id, "--field", "report_prose"], cli_env)
    assert view.returncode == 1


def test_cli_finalize_without_report_prose_exits_zero(cli_env):
    """The mandatory opposite direction: a turn that never writes the field
    closes exactly as it did before the field existed."""
    draft_id = _init_draft(cli_env)

    set_state = _run(
        ["set", "--draft-id", draft_id, "agent_status.agent_state", "BLOCKED"], cli_env
    )
    assert set_state.returncode == 0, set_state.stderr

    absent = _run(["view", "--draft-id", draft_id, "--field", "report_prose"], cli_env)
    assert absent.returncode == 1

    fin = _run(["finalize", "--draft-id", draft_id, "--json"], cli_env)
    assert fin.returncode == 0, f"finalize failed: {fin.stderr!r}"
    assert json.loads(fin.stdout)["status"] == "finalized"
