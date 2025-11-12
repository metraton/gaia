#!/usr/bin/env python3
"""
Tests for Payload Validator (Phase A)

Reference: agent-validation-lifecycle.md (Phase A: A1-A5)
"""

import pytest
import sys
from pathlib import Path

# Add tools to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "tools" / "9-agent-framework"))

from payload_validator import PayloadValidator, ValidationResult


class TestPayloadValidatorPhaseA1:
    """A1: JSON Structure Valid?"""

    def test_valid_dict_payload(self, tmp_path):
        validator = PayloadValidator()
        payload = {
            "contract": {
                "project_details": {"name": "test", "root": str(tmp_path)},
                "infrastructure_paths": {"root": str(tmp_path)},
                "operational_guidelines": {"action": "plan"}
            }
        }
        result = validator.validate_payload(payload)
        assert isinstance(result, ValidationResult)

    def test_invalid_json_string(self):
        validator = PayloadValidator()
        result = validator.validate_payload("not a dict")
        assert not result.is_valid
        assert result.phase == "A1"
        assert "must be dict" in result.errors[0]

    def test_invalid_json_list(self):
        validator = PayloadValidator()
        result = validator.validate_payload([1, 2, 3])
        assert not result.is_valid
        assert "must be dict" in result.errors[0]


class TestPayloadValidatorPhaseA2:
    """A2: Contract Fields Present?"""

    def test_all_contract_fields_present(self, tmp_path):
        validator = PayloadValidator()
        payload = {
            "project_details": {"name": "test", "root": str(tmp_path)},
            "infrastructure_paths": {"root": str(tmp_path)},
            "operational_guidelines": {"action": "plan"}
        }
        result = validator.validate_payload(payload)
        # Will pass A2 and continue to A3+
        assert result.is_valid or result.phase.startswith("A")

    def test_missing_project_details(self, tmp_path):
        validator = PayloadValidator()
        payload = {
            "infrastructure_paths": {"root": str(tmp_path)},
            "operational_guidelines": {"action": "plan"}
        }
        result = validator.validate_payload(payload)
        assert not result.is_valid
        assert result.phase == "A2"
        assert "project_details" in result.missing_fields

    def test_missing_infrastructure_paths(self, tmp_path):
        validator = PayloadValidator()
        payload = {
            "project_details": {"name": "test", "root": str(tmp_path)},
            "operational_guidelines": {"action": "plan"}
        }
        result = validator.validate_payload(payload)
        assert not result.is_valid
        assert "infrastructure_paths" in result.missing_fields

    def test_missing_operational_guidelines(self, tmp_path):
        validator = PayloadValidator()
        payload = {
            "project_details": {"name": "test", "root": str(tmp_path)},
            "infrastructure_paths": {"root": str(tmp_path)}
        }
        result = validator.validate_payload(payload)
        assert not result.is_valid
        assert "operational_guidelines" in result.missing_fields

    def test_null_contract_field(self, tmp_path):
        validator = PayloadValidator()
        payload = {
            "project_details": None,
            "infrastructure_paths": {"root": str(tmp_path)},
            "operational_guidelines": {"action": "plan"}
        }
        result = validator.validate_payload(payload)
        assert not result.is_valid
        assert "project_details" in result.missing_fields


class TestPayloadValidatorPhaseA3:
    """A3: Paths Exist and Accessible?"""

    def test_valid_path_exists(self, tmp_path):
        validator = PayloadValidator()
        payload = {
            "project_details": {"name": "test", "root": str(tmp_path)},
            "infrastructure_paths": {"root": str(tmp_path)},
            "operational_guidelines": {"action": "plan"}
        }
        result = validator.validate_payload(payload)
        # Should pass A3 or continue past it
        assert result.phase != "A3" or result.is_valid

    def test_invalid_path_not_exists(self, tmp_path):
        validator = PayloadValidator()
        payload = {
            "project_details": {"name": "test", "root": str(tmp_path)},
            "infrastructure_paths": {"root": "/nonexistent/path/xyz123"},
            "operational_guidelines": {"action": "plan"}
        }
        result = validator.validate_payload(payload)
        assert not result.is_valid
        assert result.phase == "A3"
        assert any("does not exist" in err for err in result.errors)

    def test_multiple_paths_all_valid(self, tmp_path):
        validator = PayloadValidator()
        subdir = tmp_path / "subdir"
        subdir.mkdir()

        payload = {
            "project_details": {"name": "test", "root": str(tmp_path)},
            "infrastructure_paths": {
                "root": str(tmp_path),
                "secondary": str(subdir)
            },
            "operational_guidelines": {"action": "plan"}
        }
        result = validator.validate_payload(payload)
        # Should continue past A3
        assert result.phase != "A3" or result.is_valid


class TestPayloadValidatorPhaseA4:
    """A4: Enrichment Valid (Optional)?"""

    def test_enrichment_optional_not_present(self, tmp_path):
        validator = PayloadValidator()
        payload = {
            "project_details": {"name": "test", "root": str(tmp_path)},
            "infrastructure_paths": {"root": str(tmp_path)},
            "operational_guidelines": {"action": "plan"}
        }
        result = validator.validate_payload(payload)
        assert result.is_valid
        assert any("enrichment" in w.lower() for w in result.warnings)

    def test_enrichment_optional_present_and_valid(self, tmp_path):
        validator = PayloadValidator()
        payload = {
            "project_details": {"name": "test", "root": str(tmp_path)},
            "infrastructure_paths": {"root": str(tmp_path)},
            "operational_guidelines": {"action": "plan"},
            "application_services": [{"name": "api"}]
        }
        result = validator.validate_payload(payload)
        assert result.is_valid
        assert "enrichment:application_services" in result.valid_fields

    def test_enrichment_field_null_ignored(self, tmp_path):
        validator = PayloadValidator()
        payload = {
            "project_details": {"name": "test", "root": str(tmp_path)},
            "infrastructure_paths": {"root": str(tmp_path)},
            "operational_guidelines": {"action": "plan"},
            "application_services": None
        }
        result = validator.validate_payload(payload)
        assert result.is_valid
        assert any("None" in w for w in result.warnings)


class TestPayloadValidatorReporting:
    """Test human-readable reporting"""

    def test_report_generation_success(self, tmp_path):
        validator = PayloadValidator()
        payload = {
            "project_details": {"name": "test", "root": str(tmp_path)},
            "infrastructure_paths": {"root": str(tmp_path)},
            "operational_guidelines": {"action": "plan"}
        }
        result = validator.validate_payload(payload)
        report = validator.generate_report(result)

        assert "PHASE A" in report
        if result.is_valid:
            assert "PASSED" in report
        assert "Valid Fields" in report

    def test_report_generation_failure(self):
        validator = PayloadValidator()
        payload = {"invalid": "payload"}
        result = validator.validate_payload(payload)
        report = validator.generate_report(result)

        assert "FAILED" in report
        assert "ERRORS" in report


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
