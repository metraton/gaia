"""
Tests for evidence clause validation in contract_validator.py (T9 / M3).

Covers validate_evidence_update_contract_payload() and the evidence-aware
parse_update_contracts() integration.
"""

import sys
from pathlib import Path

# Make the hooks package importable
_HOOKS_DIR = Path(__file__).parent.parent.parent / "hooks"
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))

from modules.agents.contract_validator import (
    validate_evidence_update_contract_payload,
    parse_update_contracts,
)


# ---------------------------------------------------------------------------
# validate_evidence_update_contract_payload
# ---------------------------------------------------------------------------

def test_evidence_payload_valid():
    """Well-formed evidence payload returns no errors."""
    payload = {
        "brief_id": 1,
        "ac_id": "AC-M3",
        "type": "command_output",
        "text": "pytest passed: 5 passed in 0.12s",
        "task_id": "T7",
        "created_by_agent": "gaia-system",
    }
    errors = validate_evidence_update_contract_payload(payload)
    assert errors == [], f"Expected no errors, got: {errors}"


def test_evidence_payload_artifact_path_outside_canonical_root_rejected():
    """A non-canonical artifact_path (e.g. /tmp) is now rejected, not accepted.

    INVERTED (was test_evidence_payload_valid_artifact_path): this test used
    to assert that an arbitrary path like "/tmp/report.txt" passed validation
    with zero errors -- affirming, as correct and desired, that the evidence
    clause skipped the canonical-root guard (gaia.evidence.fs
    .require_canonical_artifact_path) that bin/cli/ac.py and bin/cli/brief.py
    already apply to the same field. That was the hole AC-2 closes: any
    artifact_path -- including one pointing into a repo's working tree --
    was inserted into the evidence table verbatim. The guard now runs here
    too, so a path outside the canonical evidence root is rejected and the
    error names the offending path, instead of silently passing through.
    """
    payload = {
        "brief_id": 42,
        "ac_id": "AC-1",
        "type": "file",
        "artifact_path": "/tmp/report.txt",
    }
    errors = validate_evidence_update_contract_payload(payload)
    assert errors, "Expected the out-of-canonical-root artifact_path to be rejected"
    assert any("/tmp/report.txt" in e for e in errors), (
        f"Expected the error to name the offending path, got: {errors}"
    )


def test_evidence_payload_artifact_path_in_repo_working_tree_rejected():
    """Adversarial: artifact_path pointing at a repo's working tree is rejected by name.

    Distinct from the /tmp case above -- this uses a path shaped like a real
    repository file (this very test file) to demonstrate the guard is a
    structural canonical-root check, not a name/path blocklist tuned to one
    example.
    """
    repo_file = str(Path(__file__).resolve())
    payload = {
        "brief_id": 1,
        "ac_id": "AC-2",
        "type": "file",
        "artifact_path": repo_file,
    }
    errors = validate_evidence_update_contract_payload(payload)
    assert errors, "Expected a repo working-tree artifact_path to be rejected"
    assert any(repo_file in e for e in errors), (
        f"Expected the error to name the offending repo path, got: {errors}"
    )


def test_evidence_payload_valid_canonical_artifact_path():
    """An artifact_path already under the canonical evidence root still passes.

    Confirms the guard is scoped to location, not to artifact_path per se --
    a real blob path returned by `gaia evidence add` continues to validate
    cleanly.
    """
    from gaia.paths.resolver import evidence_dir

    canonical_path = str(evidence_dir() / "ws" / "brief" / "AC-1" / "blob.txt")
    payload = {
        "brief_id": 42,
        "ac_id": "AC-1",
        "type": "file",
        "artifact_path": canonical_path,
    }
    errors = validate_evidence_update_contract_payload(payload)
    assert errors == [], f"Expected no errors for a canonical path, got: {errors}"


def test_evidence_payload_missing_brief_id():
    """Missing brief_id produces an error mentioning the field name."""
    payload = {
        "ac_id": "AC-M3",
        "type": "text",
        "text": "some evidence",
    }
    errors = validate_evidence_update_contract_payload(payload)
    assert any("brief_id" in e for e in errors), f"Expected brief_id error, got: {errors}"


def test_evidence_payload_missing_ac_id():
    """Missing ac_id produces an error mentioning the field name."""
    payload = {
        "brief_id": 1,
        "type": "text",
        "text": "some evidence",
    }
    errors = validate_evidence_update_contract_payload(payload)
    assert any("ac_id" in e for e in errors), f"Expected ac_id error, got: {errors}"


def test_evidence_payload_invalid_type():
    """Unknown type value returns an error."""
    payload = {
        "brief_id": 1,
        "ac_id": "AC-1",
        "type": "unknown_type",
        "text": "some evidence",
    }
    errors = validate_evidence_update_contract_payload(payload)
    assert any("type" in e or "unknown_type" in e for e in errors), (
        f"Expected type error, got: {errors}"
    )


def test_evidence_payload_both_text_and_artifact_path():
    """Providing both text and artifact_path returns a mutex error."""
    payload = {
        "brief_id": 1,
        "ac_id": "AC-1",
        "type": "text",
        "text": "inline content",
        "artifact_path": "/tmp/also_file.txt",
    }
    errors = validate_evidence_update_contract_payload(payload)
    assert any("mutually exclusive" in e or ("text" in e and "artifact_path" in e) for e in errors), (
        f"Expected mutex error, got: {errors}"
    )


def test_evidence_payload_neither_text_nor_artifact_path():
    """Providing neither text nor artifact_path returns an error."""
    payload = {
        "brief_id": 1,
        "ac_id": "AC-1",
        "type": "screenshot",
    }
    errors = validate_evidence_update_contract_payload(payload)
    assert any("text" in e or "artifact_path" in e or "exactly one" in e for e in errors), (
        f"Expected missing-payload error, got: {errors}"
    )


def test_evidence_payload_all_valid_types():
    """All five valid type values pass validation."""
    valid_types = ["text", "file", "command_output", "url", "screenshot"]
    for ev_type in valid_types:
        payload = {
            "brief_id": 1,
            "ac_id": "AC-1",
            "type": ev_type,
            "text": "evidence content",
        }
        errors = validate_evidence_update_contract_payload(payload)
        assert errors == [], f"Type {ev_type!r} should be valid, got errors: {errors}"


# ---------------------------------------------------------------------------
# parse_update_contracts -- evidence-aware integration
# ---------------------------------------------------------------------------

def test_parse_update_contracts_passes_through_evidence_entries():
    """parse_update_contracts passes evidence entries through for writer-level validation.

    Payload-level validation (fail-together, D8) is done by the writer, not the parser.
    Both well-formed and malformed evidence entries (structurally valid dicts with
    contract+payload keys) are passed through to the caller.
    """
    contract = {
        "update_contracts": [
            # Structurally valid but payloads will be validated at write time
            {
                "contract": "evidence",
                "payload": {
                    "ac_id": "AC-1",
                    "type": "text",
                    "text": "some text",
                    # brief_id is missing -- but parse_update_contracts does not reject this
                },
            },
            # Valid non-evidence entry
            {
                "contract": "app_services",
                "payload": {"service": "web"},
            },
        ]
    }
    results = parse_update_contracts(contract)
    # Both entries are structurally valid and pass through
    assert len(results) == 2
    contract_names = {r["contract"] for r in results}
    assert "evidence" in contract_names
    assert "app_services" in contract_names


def test_parse_update_contracts_valid_evidence_included():
    """Well-formed evidence entry is included in parse results."""
    contract = {
        "update_contracts": [
            {
                "contract": "evidence",
                "payload": {
                    "brief_id": 1,
                    "ac_id": "AC-M3",
                    "type": "command_output",
                    "text": "all tests passed",
                },
            },
        ]
    }
    results = parse_update_contracts(contract)
    assert len(results) == 1
    assert results[0]["contract"] == "evidence"


def test_parse_update_contracts_non_evidence_passes_without_type_check():
    """Non-evidence contract types are returned without payload validation."""
    contract = {
        "update_contracts": [
            {
                "contract": "some_other_contract",
                "payload": {"anything": "goes"},
            },
        ]
    }
    results = parse_update_contracts(contract)
    assert len(results) == 1
    assert results[0]["contract"] == "some_other_contract"
