"""
Git Commit Message Validator

Validates the format of a commit message (Conventional Commits type, subject
length, trailing period, emoji, body line length) before the commit runs.
Footer detection is not done here; it lives in bash_validator.

The runtime caller is ``bash_validator._validate_commit_message``, which uses
``validate_commit_message``.
"""

import json
import os
import re
from typing import Dict, List, Optional
from datetime import datetime
from dataclasses import dataclass


FORMAT = "conventional_commits"

TYPE_ALLOWED = (
    "feat",
    "fix",
    "refactor",
    "docs",
    "test",
    "chore",
    "ci",
    "perf",
    "style",
    "build",
)

SCOPE_REQUIRED = False
SCOPE_EXAMPLES = ("helmrelease", "terraform", "pg-non-prod", "infrastructure")

SUBJECT_MAX_LENGTH = 72
SUBJECT_RULES = {
    "capitalize_first_letter": False,
    "no_period_at_end": True,
    "imperative_mood": True,
    "no_emoji": True,
}

BODY_MAX_LINE_LENGTH = 72
BODY_REQUIRED = False

EXAMPLES_VALID = (
    "feat(helmrelease): add Phase 3.3 services",
    "fix(pg-non-prod): correct API key environment variable mappings",
    "refactor: simplify context provider logic",
    "docs: update README with new workflow",
    "chore(deps): update terraform to v1.6.0",
)

EXAMPLES_INVALID = (
    "Added new feature",
    "Fixed bugs",
    "Updates",
    "feat: add feature\n\n🤖 Generated with Claude Code",
    "feat: add new feature 🚀",
    "fix: 🐛 correct bug",
)

ENFORCEMENT = {
    "enabled": True,
    "block_on_failure": True,
    "log_violations": True,
    "log_path": ".claude/logs/commit-violations.jsonl",
}


@dataclass
class ValidationResult:
    """Result of commit message validation."""
    valid: bool
    errors: List[Dict[str, str]]
    warnings: List[Dict[str, str]] = None

    def __post_init__(self):
        if self.warnings is None:
            self.warnings = []


class CommitMessageValidator:
    """
    Validates git commit messages against project standards.

    Standards are inlined as module-level constants (TYPE_ALLOWED,
    SUBJECT_MAX_LENGTH, SUBJECT_RULES, etc.). Footer detection is not handled
    here -- that is bash_validator's responsibility.
    """

    def __init__(self, config_path: Optional[str] = None):
        """
        Initialize validator.

        Args:
            config_path: Accepted for backward compatibility only. When given,
                         it anchors base_path (used to resolve the relative
                         violation log path); the rules themselves come from
                         the module-level constants, not from any file.
        """
        if config_path is None:
            base_path = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        else:
            base_path = os.path.dirname(os.path.dirname(config_path))

        self.base_path = base_path
        self.enforcement = ENFORCEMENT

    def validate(self, message: str) -> ValidationResult:
        """
        Validate a commit message against all standards.

        Args:
            message: The commit message to validate

        Returns:
            ValidationResult with valid status and any errors/warnings
        """
        errors = []
        warnings = []

        format_errors = self._check_conventional_format(message)
        errors.extend(format_errors)

        subject_errors = self._check_subject_rules(message)
        errors.extend(subject_errors)

        body_warnings = self._check_body_rules(message)
        warnings.extend(body_warnings)

        if errors and self.enforcement.get('log_violations', False):
            self._log_violation(message, errors)

        return ValidationResult(
            valid=len(errors) == 0,
            errors=errors,
            warnings=warnings
        )

    def _check_conventional_format(self, message: str) -> List[Dict[str, str]]:
        """Check if message follows Conventional Commits format."""
        errors = []

        lines = message.split('\n')
        subject = lines[0].strip()

        allowed_types = '|'.join(TYPE_ALLOWED)
        pattern = rf'^({allowed_types})(\(.+?\))?: .+$'

        if not re.match(pattern, subject):
            errors.append({
                'type': 'INVALID_FORMAT',
                'message': 'Commit message does not follow Conventional Commits format',
                'fix': f"Use format: type(scope): description\nAllowed types: {', '.join(TYPE_ALLOWED)}",
                'severity': 'error',
                'examples': list(EXAMPLES_VALID)
            })

        return errors

    def _check_subject_rules(self, message: str) -> List[Dict[str, str]]:
        """Check subject line specific rules."""
        errors = []

        lines = message.split('\n')
        subject = lines[0].strip()

        match = re.match(r'^[a-z]+(\(.+?\))?: (.+)$', subject)
        if match:
            description = match.group(2)

            max_length = SUBJECT_MAX_LENGTH
            if len(subject) > max_length:
                errors.append({
                    'type': 'SUBJECT_TOO_LONG',
                    'message': f'Subject line exceeds {max_length} characters (current: {len(subject)})',
                    'fix': f'Shorten subject to {max_length} characters or less',
                    'severity': 'error'
                })

            rules = SUBJECT_RULES
            if rules.get('no_period_at_end', True) and description.endswith('.'):
                errors.append({
                    'type': 'SUBJECT_ENDS_WITH_PERIOD',
                    'message': 'Subject line should not end with a period',
                    'fix': 'Remove the period at the end of the subject',
                    'severity': 'error'
                })

            if rules.get('no_emoji', False):
                emoji_pattern = re.compile(
                    "["
                    "\U0001F600-\U0001F64F"
                    "\U0001F300-\U0001F5FF"
                    "\U0001F680-\U0001F6FF"
                    "\U0001F700-\U0001F77F"
                    "\U0001F780-\U0001F7FF"
                    "\U0001F800-\U0001F8FF"
                    "\U0001F900-\U0001F9FF"
                    "\U0001FA00-\U0001FA6F"
                    "\U0001FA70-\U0001FAFF"
                    "\U00002702-\U000027B0"
                    "\U000024C2-\U0001F251"
                    "]+", flags=re.UNICODE
                )

                if emoji_pattern.search(subject):
                    errors.append({
                        'type': 'SUBJECT_CONTAINS_EMOJI',
                        'message': 'Subject line contains emojis which are not allowed',
                        'fix': 'Remove all emojis from the subject line',
                        'severity': 'error'
                    })

        return errors

    def _check_body_rules(self, message: str) -> List[Dict[str, str]]:
        """Check body rules (returns warnings, not errors)."""
        warnings = []

        lines = message.split('\n')

        if len(lines) <= 1:
            return warnings

        if len(lines) > 1 and lines[1].strip() != '':
            warnings.append({
                'type': 'MISSING_BLANK_LINE',
                'message': 'Missing blank line between subject and body',
                'fix': 'Add a blank line after the subject line',
                'severity': 'warning'
            })

        max_length = BODY_MAX_LINE_LENGTH
        for i, line in enumerate(lines[2:], start=3):
            if len(line) > max_length and not line.startswith('http'):
                warnings.append({
                    'type': 'BODY_LINE_TOO_LONG',
                    'message': f'Body line {i} exceeds {max_length} characters',
                    'fix': f'Wrap line to {max_length} characters',
                    'severity': 'warning'
                })

        return warnings

    def _log_violation(self, message: str, errors: List[Dict[str, str]]):
        """Log commit message violation for audit trail."""
        log_path = self.enforcement.get('log_path', '.claude/logs/commit-violations.jsonl')

        if not os.path.isabs(log_path):
            # The default '.claude/logs/...' loses its './' and 'claude/' and
            # lands at '<base_path>/logs/...'.
            log_path = log_path.lstrip('./')
            if log_path.startswith('claude/'):
                log_path = log_path[7:]
            log_path = os.path.join(self.base_path, log_path)

        log_dir = os.path.dirname(log_path)
        if log_dir and not os.path.exists(log_dir):
            os.makedirs(log_dir, exist_ok=True)

        log_entry = {
            'timestamp': datetime.now().isoformat(),
            'message': message[:100] + ('...' if len(message) > 100 else ''),
            'errors': errors,
            'error_count': len(errors)
        }

        with open(log_path, 'a') as f:
            f.write(json.dumps(log_entry) + '\n')

    def get_examples(self) -> Dict[str, List[str]]:
        """Get example commit messages (valid and invalid)."""
        return {
            'valid': list(EXAMPLES_VALID),
            'invalid': list(EXAMPLES_INVALID)
        }

    def get_allowed_types(self) -> List[str]:
        """Get list of allowed commit types."""
        return list(TYPE_ALLOWED)

    def format_error_message(self, validation: ValidationResult) -> str:
        """
        Format validation errors into human-readable message.

        Args:
            validation: ValidationResult from validate()

        Returns:
            Formatted error message string
        """
        if validation.valid:
            return "[OK] Commit message is valid"

        lines = ["[ERROR] Commit message validation failed:\n"]

        for error in validation.errors:
            lines.append(f"  [{error['type']}]")
            lines.append(f"  {error['message']}")
            lines.append(f"  Fix: {error['fix']}")

            if 'examples' in error:
                lines.append(f"  Examples:")
                for example in error['examples'][:3]:
                    lines.append(f"    - {example}")

            lines.append("")

        if validation.warnings:
            lines.append("[WARNING] Warnings:")
            for warning in validation.warnings:
                lines.append(f"  - {warning['message']}")
            lines.append("")

        return "\n".join(lines)


def validate_commit_message(message: str) -> ValidationResult:
    """
    Quick validation function.

    Args:
        message: Commit message to validate

    Returns:
        ValidationResult
    """
    validator = CommitMessageValidator()
    return validator.validate(message)


def safe_validate_before_commit(message: str) -> bool:
    """
    Validate commit message and print the formatted errors if invalid.

    Args:
        message: Commit message to validate

    Returns:
        True if valid, False if invalid (with errors printed)
    """
    validator = CommitMessageValidator()
    validation = validator.validate(message)

    if not validation.valid:
        error_message = validator.format_error_message(validation)
        print(error_message)
        return False

    return True
