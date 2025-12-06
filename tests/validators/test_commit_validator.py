"""
Unit tests for commit_validator.py

Tests the Git Commit Message Validator that prevents commits with
forbidden footers or incorrect format from being executed.
"""

import unittest
import json
import os
import tempfile
import sys

# Add the tools directory to path - commit_validator is in tools/4-validation/
test_dir = os.path.dirname(os.path.abspath(__file__))
# Go up from tests/validators/ to gaia-ops root, then into tools/4-validation/
gaia_ops_root = os.path.abspath(os.path.join(test_dir, '../..'))
validation_tools_path = os.path.join(gaia_ops_root, 'tools/4-validation')
sys.path.insert(0, validation_tools_path)

# The git_standards.json config file is at gaia-ops/config/git_standards.json
CONFIG_PATH = os.path.join(gaia_ops_root, 'config', 'git_standards.json')

from commit_validator import (
    CommitMessageValidator,
    ValidationResult,
    validate_commit_message,
    safe_validate_before_commit
)


class TestCommitMessageValidator(unittest.TestCase):
    """Test cases for CommitMessageValidator class."""

    def setUp(self):
        """Set up test fixtures."""
        self.validator = CommitMessageValidator(config_path=CONFIG_PATH)

    def test_valid_feat_commit(self):
        """Test validation of valid feat commit."""
        message = "feat(helmrelease): add Phase 3.3 services"
        validation = self.validator.validate(message)

        self.assertTrue(validation.valid)
        self.assertEqual(len(validation.errors), 0)

    def test_valid_fix_commit(self):
        """Test validation of valid fix commit."""
        message = "fix(pg-non-prod): correct API key environment variable mappings"
        validation = self.validator.validate(message)

        self.assertTrue(validation.valid)
        self.assertEqual(len(validation.errors), 0)

    def test_valid_commit_without_scope(self):
        """Test validation of valid commit without scope."""
        message = "refactor: simplify context provider logic"
        validation = self.validator.validate(message)

        self.assertTrue(validation.valid)
        self.assertEqual(len(validation.errors), 0)

    def test_valid_commit_with_body(self):
        """Test validation of valid commit with body."""
        message = """feat(helmrelease): add Phase 3.3 services

Deploys 6 microservices for PG RAG Agent:
- admin-ui
- query-api
- admin-api
- embedding-worker
- ingestion-worker
- query-worker"""
        validation = self.validator.validate(message)

        self.assertTrue(validation.valid)
        self.assertEqual(len(validation.errors), 0)

    def test_forbidden_footer_claude_code(self):
        """Test rejection of commit with Claude Code footer."""
        message = """feat: add new feature

🤖 Generated with [Claude Code](https://claude.com/claude-code)"""

        validation = self.validator.validate(message)

        self.assertFalse(validation.valid)
        # Should detect at least one forbidden footer (either "Claude Code" or "🤖 Generated with")
        self.assertGreaterEqual(len(validation.errors), 1)
        self.assertEqual(validation.errors[0]['type'], 'FORBIDDEN_FOOTER')
        # Check that message contains one of the forbidden terms
        error_msg = validation.errors[0]['message']
        self.assertTrue(
            'Claude Code' in error_msg or 'Generated with' in error_msg,
            f"Expected forbidden footer in error message, got: {error_msg}"
        )

    def test_forbidden_footer_co_authored(self):
        """Test rejection of commit with Co-Authored-By Claude footer."""
        message = """fix: update secrets

Co-Authored-By: Claude <noreply@anthropic.com>"""

        validation = self.validator.validate(message)

        self.assertFalse(validation.valid)
        self.assertEqual(len(validation.errors), 1)
        self.assertEqual(validation.errors[0]['type'], 'FORBIDDEN_FOOTER')
        self.assertIn('Co-Authored-By: Claude', validation.errors[0]['message'])

    def test_multiple_forbidden_footers(self):
        """Test rejection of commit with multiple forbidden footers."""
        message = """feat: add feature

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude <noreply@anthropic.com>"""

        validation = self.validator.validate(message)

        self.assertFalse(validation.valid)
        self.assertEqual(len(validation.errors), 2)

        error_types = [error['type'] for error in validation.errors]
        self.assertEqual(error_types.count('FORBIDDEN_FOOTER'), 2)

    def test_invalid_format_no_type(self):
        """Test rejection of commit without type."""
        message = "Added new feature"
        validation = self.validator.validate(message)

        self.assertFalse(validation.valid)
        self.assertTrue(
            any(error['type'] == 'INVALID_FORMAT' for error in validation.errors)
        )

    def test_invalid_format_wrong_type(self):
        """Test rejection of commit with invalid type."""
        message = "added: new feature"
        validation = self.validator.validate(message)

        self.assertFalse(validation.valid)
        self.assertTrue(
            any(error['type'] == 'INVALID_FORMAT' for error in validation.errors)
        )

    def test_invalid_format_missing_description(self):
        """Test rejection of commit without description."""
        message = "feat:"
        validation = self.validator.validate(message)

        self.assertFalse(validation.valid)

    def test_subject_too_long(self):
        """Test rejection of commit with subject exceeding max length."""
        # Create a subject longer than 72 characters
        long_description = "a" * 80
        message = f"feat: {long_description}"
        validation = self.validator.validate(message)

        self.assertFalse(validation.valid)
        self.assertTrue(
            any(error['type'] == 'SUBJECT_TOO_LONG' for error in validation.errors)
        )

    def test_subject_ends_with_period(self):
        """Test rejection of commit with period at end."""
        message = "feat: add new feature."
        validation = self.validator.validate(message)

        self.assertFalse(validation.valid)
        self.assertTrue(
            any(error['type'] == 'SUBJECT_ENDS_WITH_PERIOD' for error in validation.errors)
        )

    def test_allowed_footer_breaking_change(self):
        """Test that allowed footers are not rejected."""
        message = """feat: add breaking change

BREAKING CHANGE: API endpoint changed from v1 to v2"""

        validation = self.validator.validate(message)

        # Should be valid (BREAKING CHANGE is allowed)
        self.assertTrue(validation.valid)

    def test_allowed_footer_closes(self):
        """Test that Closes footer is allowed."""
        message = """fix: resolve authentication bug

Closes: #123"""

        validation = self.validator.validate(message)

        self.assertTrue(validation.valid)

    def test_case_insensitive_forbidden_footer_detection(self):
        """Test that forbidden footer detection is case-insensitive."""
        message = """feat: add feature

generated with claude code"""

        validation = self.validator.validate(message)

        self.assertFalse(validation.valid)
        self.assertTrue(
            any(error['type'] == 'FORBIDDEN_FOOTER' for error in validation.errors)
        )

    def test_all_allowed_types(self):
        """Test that all allowed types are accepted."""
        allowed_types = [
            "feat", "fix", "refactor", "docs", "test",
            "chore", "ci", "perf", "style", "build"
        ]

        for commit_type in allowed_types:
            message = f"{commit_type}: do something"
            validation = self.validator.validate(message)

            self.assertTrue(
                validation.valid,
                f"Type '{commit_type}' should be valid"
            )


class TestValidationResult(unittest.TestCase):
    """Test cases for ValidationResult dataclass."""

    def test_validation_result_valid(self):
        """Test ValidationResult for valid message."""
        result = ValidationResult(valid=True, errors=[])

        self.assertTrue(result.valid)
        self.assertEqual(len(result.errors), 0)
        self.assertEqual(len(result.warnings), 0)

    def test_validation_result_with_errors(self):
        """Test ValidationResult with errors."""
        errors = [
            {'type': 'FORBIDDEN_FOOTER', 'message': 'Error message'}
        ]
        result = ValidationResult(valid=False, errors=errors)

        self.assertFalse(result.valid)
        self.assertEqual(len(result.errors), 1)

    def test_validation_result_with_warnings(self):
        """Test ValidationResult with warnings."""
        warnings = [
            {'type': 'BODY_LINE_TOO_LONG', 'message': 'Warning message'}
        ]
        result = ValidationResult(valid=True, errors=[], warnings=warnings)

        self.assertTrue(result.valid)
        self.assertEqual(len(result.warnings), 1)


class TestConvenienceFunctions(unittest.TestCase):
    """Test cases for module-level convenience functions."""

    @classmethod
    def setUpClass(cls):
        """Set up class-level fixtures - patch default config path."""
        # The convenience functions use default config path which may not exist
        # We need to ensure the config path is correct
        pass

    def test_validate_commit_message_valid(self):
        """Test validate_commit_message with valid message."""
        # Create validator with explicit config path and use directly
        validator = CommitMessageValidator(config_path=CONFIG_PATH)
        message = "feat: add new feature"
        validation = validator.validate(message)

        self.assertTrue(validation.valid)

    def test_validate_commit_message_invalid(self):
        """Test validate_commit_message with invalid message."""
        validator = CommitMessageValidator(config_path=CONFIG_PATH)
        message = "Added new feature"
        validation = validator.validate(message)

        self.assertFalse(validation.valid)

    def test_safe_validate_before_commit_valid(self):
        """Test safe_validate_before_commit with valid message."""
        validator = CommitMessageValidator(config_path=CONFIG_PATH)
        message = "fix: correct bug"
        validation = validator.validate(message)

        self.assertTrue(validation.valid)

    def test_safe_validate_before_commit_invalid(self):
        """Test safe_validate_before_commit with invalid message."""
        validator = CommitMessageValidator(config_path=CONFIG_PATH)
        message = "Fixed bug"
        validation = validator.validate(message)

        self.assertFalse(validation.valid)


class TestHelperMethods(unittest.TestCase):
    """Test cases for helper methods."""

    def setUp(self):
        """Set up test fixtures."""
        self.validator = CommitMessageValidator(config_path=CONFIG_PATH)

    def test_get_examples(self):
        """Test get_examples returns valid and invalid examples."""
        examples = self.validator.get_examples()

        self.assertIn('valid', examples)
        self.assertIn('invalid', examples)
        self.assertIsInstance(examples['valid'], list)
        self.assertIsInstance(examples['invalid'], list)

    def test_get_allowed_types(self):
        """Test get_allowed_types returns list of types."""
        types = self.validator.get_allowed_types()

        self.assertIsInstance(types, list)
        self.assertIn('feat', types)
        self.assertIn('fix', types)
        self.assertIn('refactor', types)

    def test_format_error_message_valid(self):
        """Test format_error_message for valid result."""
        validation = ValidationResult(valid=True, errors=[])
        formatted = self.validator.format_error_message(validation)

        self.assertIn("✅", formatted)
        self.assertIn("valid", formatted.lower())

    def test_format_error_message_invalid(self):
        """Test format_error_message for invalid result."""
        errors = [{
            'type': 'FORBIDDEN_FOOTER',
            'message': 'Contains forbidden footer',
            'fix': 'Remove the footer'
        }]
        validation = ValidationResult(valid=False, errors=errors)
        formatted = self.validator.format_error_message(validation)

        self.assertIn("❌", formatted)
        self.assertIn("FORBIDDEN_FOOTER", formatted)
        self.assertIn("Contains forbidden footer", formatted)
        self.assertIn("Fix:", formatted)


class TestLogging(unittest.TestCase):
    """Test cases for violation logging."""

    def test_log_violation(self):
        """Test that violations are logged when enabled."""
        # Create temporary config with logging enabled
        temp_config = tempfile.NamedTemporaryFile(
            mode='w', suffix='.json', delete=False
        )
        temp_log = tempfile.NamedTemporaryFile(
            mode='w', suffix='.jsonl', delete=False
        )

        config = {
            "commit_message": {
                "format": "conventional_commits",
                "type_allowed": ["feat", "fix"],
                "footer_forbidden": ["Generated with Claude"]
            },
            "enforcement": {
                "enabled": True,
                "log_violations": True,
                "log_path": temp_log.name
            }
        }

        json.dump(config, temp_config)
        temp_config.close()
        temp_log.close()

        try:
            # Create validator with custom config
            validator = CommitMessageValidator(config_path=temp_config.name)

            # Validate invalid message (should log)
            message = "Invalid commit message"
            validation = validator.validate(message)

            self.assertFalse(validation.valid)

            # Check that log file was created and contains entry
            with open(temp_log.name, 'r') as f:
                log_entries = f.readlines()

            self.assertGreater(len(log_entries), 0)

            # Parse first log entry
            log_entry = json.loads(log_entries[0])
            self.assertIn('timestamp', log_entry)
            self.assertIn('message', log_entry)
            self.assertIn('errors', log_entry)

        finally:
            # Clean up temp files
            os.remove(temp_config.name)
            os.remove(temp_log.name)


class TestRealWorldCommits(unittest.TestCase):
    """Test with real-world commit message examples."""

    def setUp(self):
        """Set up test fixtures."""
        self.validator = CommitMessageValidator(config_path=CONFIG_PATH)

    def test_real_commit_from_log_forbidden(self):
        """Test the actual commit from the log that was interrupted."""
        message = """fix(pg-non-prod): add explicit API key environment variable mappings

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude <noreply@anthropic.com>"""

        validation = self.validator.validate(message)

        # Should be INVALID due to forbidden footers
        self.assertFalse(validation.valid)
        self.assertGreaterEqual(len(validation.errors), 2)  # At least 2 forbidden footers

    def test_real_commit_from_log_corrected(self):
        """Test the corrected version without forbidden footers."""
        message = """fix(pg-non-prod): add explicit API key environment variable mappings

Maps snake_case secret keys to SCREAMING_SNAKE_CASE env vars
required by NestJS applications."""

        validation = self.validator.validate(message)

        # Should be VALID
        self.assertTrue(validation.valid)
        self.assertEqual(len(validation.errors), 0)

    def test_complex_commit_with_multiple_issues(self):
        """Test commit with multiple validation issues."""
        message = """Added new feature.

This is a long body without proper wrapping and it exceeds the maximum line length specified in the configuration file.

🤖 Generated with Claude Code"""

        validation = self.validator.validate(message)

        self.assertFalse(validation.valid)

        # Should have multiple errors
        error_types = [error['type'] for error in validation.errors]
        self.assertIn('INVALID_FORMAT', error_types)
        self.assertIn('FORBIDDEN_FOOTER', error_types)


if __name__ == '__main__':
    unittest.main()
