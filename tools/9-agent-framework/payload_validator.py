#!/usr/bin/env python3
"""
Phase A: Agnostic Payload Validation

Validates that incoming payload contains required fields and is structurally sound.
Does NOT assume project structure - only validates what was promised.

Reference: agent-validation-lifecycle.md (Phase A: A1-A5)
"""

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    """Result of payload validation"""
    is_valid: bool
    phase: str  # "A1", "A2", etc.
    errors: List[str]
    warnings: List[str]
    valid_fields: Dict[str, Any]  # Fields that passed validation
    missing_fields: List[str]  # Required fields that are missing


class PayloadValidator:
    """
    Validates incoming payload against contract and enrichment schema.

    Agnistic approach:
    - Validates structure, not semantics
    - Validates paths exist, not what's in them
    - Reports what's missing, doesn't fail on optional enrichment
    - Tracks origin of all data for later reporting
    """

    def __init__(self):
        self.contract_required = [
            "project_details",
            "infrastructure_paths",
            "operational_guidelines"
        ]

        self.contract_nested_required = {
            "project_details": ["name", "root"],
            "infrastructure_paths": [],  # No strict requirement, but should have something
            "operational_guidelines": ["action"]  # At minimum, what are we doing?
        }

        self.enrichment_optional = [
            "application_services",
            "recent_changes",
            "issue_context"
        ]

    def validate_payload(self, payload: Dict[str, Any]) -> ValidationResult:
        """
        Main validation entry point (Phase A).

        Returns ValidationResult with is_valid=True only if contract fields present.
        Enrichment fields are optional (warnings if missing, not errors).
        """

        errors = []
        warnings = []
        valid_fields = {}
        missing_fields = []

        # A1: JSON Structure Valid?
        logger.debug("A1: Checking JSON structure...")
        if not isinstance(payload, dict):
            errors.append(f"Payload must be dict, got {type(payload).__name__}")
            return ValidationResult(
                is_valid=False,
                phase="A1",
                errors=errors,
                warnings=warnings,
                valid_fields={},
                missing_fields=self.contract_required
            )

        # A2: Contract Fields Present?
        logger.debug("A2: Checking contract fields...")
        for field in self.contract_required:
            if field not in payload:
                errors.append(f"Missing contract field: {field}")
                missing_fields.append(field)
            elif payload[field] is None:
                errors.append(f"Contract field is None: {field}")
                missing_fields.append(field)
            else:
                valid_fields[field] = payload[field]

                # A2b: Nested validation for known contracts
                if field in self.contract_nested_required:
                    nested_required = self.contract_nested_required[field]
                    if nested_required:  # Only if there are requirements
                        for nested_field in nested_required:
                            if nested_field not in payload[field]:
                                errors.append(
                                    f"Missing nested field: {field}.{nested_field}"
                                )

        if errors:
            return ValidationResult(
                is_valid=False,
                phase="A2",
                errors=errors,
                warnings=warnings,
                valid_fields=valid_fields,
                missing_fields=missing_fields
            )

        # A3: Paths Exist and Accessible?
        logger.debug("A3: Checking infrastructure paths...")
        if "infrastructure_paths" in valid_fields:
            paths = valid_fields["infrastructure_paths"]
            if isinstance(paths, dict):
                for path_name, path_value in paths.items():
                    if isinstance(path_value, str):
                        path_obj = Path(path_value)
                        if not path_obj.exists():
                            errors.append(f"Path does not exist: {path_name}={path_value}")
                        elif not path_obj.is_dir() and path_obj.parent.exists():
                            # File should exist or parent should exist
                            if path_obj.is_dir():
                                valid_fields[f"path_accessible:{path_name}"] = True
                            elif path_obj.parent.is_dir():
                                valid_fields[f"path_accessible:{path_name}"] = True
                            else:
                                errors.append(f"No access to path: {path_name}")

        if errors:
            return ValidationResult(
                is_valid=False,
                phase="A3",
                errors=errors,
                warnings=warnings,
                valid_fields=valid_fields,
                missing_fields=missing_fields
            )

        # A4: Enrichment Valid (Optional)?
        logger.debug("A4: Checking enrichment fields...")
        enrichment_present = 0
        for field in self.enrichment_optional:
            if field in payload:
                if payload[field] is not None:
                    valid_fields[f"enrichment:{field}"] = True
                    enrichment_present += 1
                else:
                    warnings.append(f"Enrichment field is None (will be ignored): {field}")

        if enrichment_present == 0:
            warnings.append(
                "No enrichment data provided. Will proceed with local discovery only."
            )

        # A5: Metadata Coherent?
        logger.debug("A5: Checking metadata...")
        if "metadata" in payload:
            if isinstance(payload["metadata"], dict):
                valid_fields["metadata"] = payload["metadata"]
                # Check for required metadata fields
                required_meta = ["agent_type", "timestamp"]
                for meta_field in required_meta:
                    if meta_field not in payload["metadata"]:
                        warnings.append(f"Missing metadata field: {meta_field}")
        else:
            warnings.append("No metadata provided")

        # SUCCESS
        logger.debug("A5: Payload validation PASSED")
        return ValidationResult(
            is_valid=True,
            phase="A5",
            errors=[],
            warnings=warnings,
            valid_fields=valid_fields,
            missing_fields=[]
        )

    def generate_report(self, result: ValidationResult) -> str:
        """Generate human-readable validation report"""

        lines = []
        lines.append(f"\n{'='*60}")
        lines.append(f"PHASE A: PAYLOAD VALIDATION - {result.phase}")
        lines.append(f"{'='*60}")

        if result.is_valid:
            lines.append("✓ VALIDATION PASSED\n")
        else:
            lines.append("✗ VALIDATION FAILED\n")

        if result.errors:
            lines.append("ERRORS:")
            for err in result.errors:
                lines.append(f"  ❌ {err}")
            lines.append("")

        if result.warnings:
            lines.append("WARNINGS:")
            for warn in result.warnings:
                lines.append(f"  ⚠️  {warn}")
            lines.append("")

        lines.append(f"Valid Fields: {len(result.valid_fields)}")
        lines.append(f"Missing Fields: {len(result.missing_fields)}")

        if result.missing_fields:
            lines.append(f"  - {', '.join(result.missing_fields)}")

        lines.append("")
        return "\n".join(lines)


# CLI Usage
if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.DEBUG)

    if len(sys.argv) > 1:
        payload_file = sys.argv[1]
        with open(payload_file) as f:
            payload = json.load(f)
    else:
        # Example payload
        payload = {
            "contract": {
                "project_details": {"name": "test", "root": "/tmp"},
                "infrastructure_paths": {"root": "/tmp/terraform"},
                "operational_guidelines": {"action": "plan"}
            },
            "enrichment": {}
        }

    validator = PayloadValidator()
    result = validator.validate_payload(payload)
    print(validator.generate_report(result))
    print(f"\nResult: {result.is_valid}")
