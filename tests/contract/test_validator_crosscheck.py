"""
AC-3 -- cross-check layer (layer 2): approval_id -> real pending grant.

    * WITH gaia.db, an approval_id that does NOT resolve to a real pending
      row -> REJECTED with the named code APPROVAL_ID_NOT_PENDING (revives
      the DEAD ``nonce_issue`` in
      hooks/modules/agents/contract_validator.py::validate_approval_request,
      which was hardcoded to ``None`` and never actually checked).
    * WITHOUT gaia.db, the layer degrades gracefully: it never creates the
      database as a side effect, and the FORM layer alone still validates.
    * No harness import: gaia.contract.crosscheck must not import anything
      under hooks/, directly or transitively.
"""

import importlib
import sqlite3
import sys
from pathlib import Path

import pytest

from gaia.contract.crosscheck import (
    CrossCheckErrorCode,
    validate,
    validate_crosscheck,
)
from gaia.contract.validator import validate_form


def _valid_envelope(approval_id: str) -> dict:
    """A shape-valid APPROVAL_REQUEST envelope carrying ``approval_id``."""
    return {
        "agent_status": {
            "agent_state": "APPROVAL_REQUEST",
            "agent_id": "a1b2c3",
            "pending_steps": ["blocked command"],
            "next_action": "awaiting user approval",
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
        "approval_request": {
            "operation": "MUTATIVE command intercepted: push",
            "exact_content": "git push origin main",
            "scope": "git",
            "risk_level": "high",
            "rollback": None,
            "rationale": "push mutates remote state",
            "verification": "confirm remote ref advanced",
            "approval_id": approval_id,
        },
    }


def _make_gaia_db(tmp_path: Path) -> Path:
    """Materialize a real gaia.db schema at tmp_path via the canonical writer
    (same schema.sql the production DB uses), so the test exercises the real
    `approvals` table shape rather than a hand-rolled stand-in."""
    from gaia.store.writer import _connect

    db_path = tmp_path / "gaia.db"
    con = _connect(db_path)
    con.close()
    return db_path


def _insert_approval(db_path: Path, approval_id: str, status: str) -> None:
    con = sqlite3.connect(str(db_path))
    try:
        con.execute(
            "INSERT INTO approvals (id, agent_id, session_id, status, "
            "fingerprint, payload_json) VALUES (?, ?, ?, ?, ?, ?)",
            (approval_id, "a1b2c3", "sess-1", status, "fp", "{}"),
        )
        con.commit()
    finally:
        con.close()


# ---------------------------------------------------------------------------
# WITH gaia.db -- resolves to a real pending grant -> ok
# ---------------------------------------------------------------------------
def test_with_db_pending_approval_id_resolves_ok(tmp_path):
    db_path = _make_gaia_db(tmp_path)
    _insert_approval(db_path, "P-abc123", "pending")

    result = validate_crosscheck(_valid_envelope("P-abc123"), db_path=db_path)

    assert result.ok is True
    assert result.checked is True
    assert result.skipped is False
    assert result.errors == ()


# ---------------------------------------------------------------------------
# WITH gaia.db -- approval_id absent from approvals entirely -> REJECTED
# (revives the dead nonce_issue: a fabricated/never-requested id is caught)
# ---------------------------------------------------------------------------
def test_with_db_unknown_approval_id_rejected(tmp_path):
    db_path = _make_gaia_db(tmp_path)
    # No row inserted at all for this id.

    result = validate_crosscheck(_valid_envelope("P-doesnotexist"), db_path=db_path)

    assert result.ok is False
    assert result.checked is True
    assert [e.code for e in result.errors] == [CrossCheckErrorCode.APPROVAL_ID_NOT_PENDING]
    assert "P-doesnotexist" in result.errors[0].detail
    assert result.repair_message


# ---------------------------------------------------------------------------
# WITH gaia.db -- approval_id exists but is already consumed (not pending)
# -> REJECTED (a stale/replayed id must not pass)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("status", ["approved", "rejected", "revoked", "expired"])
def test_with_db_non_pending_status_rejected(tmp_path, status):
    db_path = _make_gaia_db(tmp_path)
    _insert_approval(db_path, "P-stale000", status)

    result = validate_crosscheck(_valid_envelope("P-stale000"), db_path=db_path)

    assert result.ok is False
    assert [e.code for e in result.errors] == [CrossCheckErrorCode.APPROVAL_ID_NOT_PENDING]
    assert status in result.errors[0].detail


# ---------------------------------------------------------------------------
# WITHOUT gaia.db -- graceful fallback: no crash, no DB created, form layer
# alone still validates the envelope.
# ---------------------------------------------------------------------------
def test_without_db_degrades_gracefully_form_still_validates(tmp_path):
    missing_db_path = tmp_path / "nonexistent" / "gaia.db"
    assert not missing_db_path.exists()

    envelope = _valid_envelope("P-whatever-not-checkable")

    crosscheck_result = validate_crosscheck(envelope, db_path=missing_db_path)

    assert crosscheck_result.ok is True
    assert crosscheck_result.skipped is True
    assert crosscheck_result.checked is False
    # The graceful-degrade path must NEVER create the database as a side
    # effect of attempting the cross-check.
    assert not missing_db_path.exists()
    assert not missing_db_path.parent.exists()

    # The form layer is unaffected and independently still validates.
    form_result = validate_form(envelope)
    assert form_result.ok is True


def test_without_db_composed_validate_passes_on_form_alone(tmp_path):
    missing_db_path = tmp_path / "gaia.db"
    envelope = _valid_envelope("P-whatever-not-checkable")

    result = validate(envelope, db_path=missing_db_path)

    assert result.ok is True
    assert result.crosscheck.skipped is True
    assert not missing_db_path.exists()


# ---------------------------------------------------------------------------
# No approval_id present at all -> nothing to check, ok regardless of DB
# ---------------------------------------------------------------------------
def test_no_approval_id_present_is_a_noop(tmp_path):
    envelope = {
        "agent_status": {
            "agent_state": "IN_PROGRESS",
            "agent_id": "a1b2c3",
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

    result = validate_crosscheck(envelope, db_path=tmp_path / "gaia.db")

    assert result.ok is True
    assert result.checked is False
    assert result.skipped is False


# ---------------------------------------------------------------------------
# Composition: layer 2 rejection surfaces through the combined validate()
# ---------------------------------------------------------------------------
def test_composed_validate_rejects_on_bad_approval_id(tmp_path):
    db_path = _make_gaia_db(tmp_path)
    _insert_approval(db_path, "P-good", "pending")

    bad_envelope = _valid_envelope("P-forged-nonce")
    result = validate(bad_envelope, db_path=db_path)

    assert result.ok is False
    assert result.form.ok is True
    assert result.crosscheck.ok is False
    assert CrossCheckErrorCode.APPROVAL_ID_NOT_PENDING in [
        e.code for e in result.crosscheck.errors
    ]


def test_composed_validate_skips_crosscheck_on_form_failure(tmp_path):
    db_path = _make_gaia_db(tmp_path)
    # Shape-invalid envelope (bad agent_id) -- layer 2 must not even run.
    envelope = _valid_envelope("P-forged-nonce")
    envelope["agent_status"]["agent_id"] = "not-valid"

    result = validate(envelope, db_path=db_path)

    assert result.ok is False
    assert result.form.ok is False
    assert result.crosscheck.checked is False


# ---------------------------------------------------------------------------
# Harness-import boundary: gaia.contract.crosscheck must not pull in hooks/,
# directly or transitively.
# ---------------------------------------------------------------------------
def test_crosscheck_module_does_not_import_harness():
    before = set(sys.modules.keys())
    for mod_name in list(sys.modules):
        if mod_name == "gaia.contract.crosscheck" or mod_name.startswith(
            "gaia.contract.crosscheck."
        ):
            del sys.modules[mod_name]

    module = importlib.import_module("gaia.contract.crosscheck")
    importlib.reload(module)

    after = set(sys.modules.keys()) - before
    hooks_imports = {name for name in after if name == "hooks" or name.startswith("hooks.")}
    assert hooks_imports == set(), (
        f"gaia.contract.crosscheck pulled in harness modules: {hooks_imports}"
    )
