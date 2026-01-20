"""
Git Commit Message Validator

Validates commit messages against project standards before execution.
This prevents commits with forbidden footers or incorrect format from
being pushed to the repository.

Usage:
    from commit_validator import CommitMessageValidator

    validator = CommitMessageValidator()
    validation = validator.validate(commit_message)

    if not validation.valid:
        for error in validation.errors:
            print(f"Error: {error['message']}")
        # Do not proceed with commit
    else:
        # Safe to commit
        git commit -m "$commit_message"
"""

import json
import os
import re
from typing import Dict, List, Any, Optional
from datetime import datetime
from dataclasses import dataclass


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

    Standards are defined in .claude/config/git_standards.json
    """

    def __init__(self, config_path: Optional[str] = None):
        """
        Initialize validator with configuration.

        Args:
            config_path: Optional path to git_standards.json
                        If None, uses default location
        """
        if config_path is None:
            # Default path relative to this file
            # From .claude/tools/validation/ go up to .claude/
            base_path = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            config_path = os.path.join(base_path, 'config', 'git_standards.json')
        else:
            # If config_path provided, derive base_path from it
            base_path = os.path.dirname(os.path.dirname(config_path))

        self.base_path = base_path
        self.config_path = config_path
        self.config = self._load_config()
        self.standards = self.config.get('commit_message', {})
        self.enforcement = self.config.get('enforcement', {})

    def _load_config(self) -> Dict[str, Any]:
        """Load git standards configuration from JSON file."""
        if not os.path.exists(self.config_path):
            raise FileNotFoundError(
                f"Git standards configuration not found at: {self.config_path}"
            )

        with open(self.config_path, 'r') as f:
            return json.load(f)

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

        # 1. Check for forbidden footers (CRITICAL)
        footer_errors = self._check_forbidden_footers(message)
        errors.extend(footer_errors)

        # 2. Check conventional commits format
        format_errors = self._check_conventional_format(message)
        errors.extend(format_errors)

        # 3. Check subject line rules
        subject_errors = self._check_subject_rules(message)
        errors.extend(subject_errors)

        # 4. Check body rules (warnings only)
        body_warnings = self._check_body_rules(message)
        warnings.extend(body_warnings)

        # Log violations if configured
        if errors and self.enforcement.get('log_violations', False):
            self._log_violation(message, errors)

        return ValidationResult(
            valid=len(errors) == 0,
            errors=errors,
            warnings=warnings
        )

    def _check_forbidden_footers(self, message: str) -> List[Dict[str, str]]:
        """Check for forbidden footers in commit message."""
        errors = []
        forbidden = self.standards.get('footer_forbidden', [])

        for forbidden_text in forbidden:
            if forbidden_text.lower() in message.lower():
                errors.append({
                    'type': 'FORBIDDEN_FOOTER',
                    'message': f"Commit message contains forbidden footer: '{forbidden_text}'",
                    'fix': f"Remove all occurrences of '{forbidden_text}'",
                    'severity': 'error'
                })

        return errors

    def _check_conventional_format(self, message: str) -> List[Dict[str, str]]:
        """Check if message follows Conventional Commits format."""
        errors = []

        # Get first line (subject)
        lines = message.split('\n')
        subject = lines[0].strip()

        # Pattern: type(scope)?: description
        # Examples: feat: add feature, fix(api): correct bug
        allowed_types = '|'.join(self.standards.get('type_allowed', []))
        pattern = rf'^({allowed_types})(\(.+?\))?: .+$'

        if not re.match(pattern, subject):
            errors.append({
                'type': 'INVALID_FORMAT',
                'message': 'Commit message does not follow Conventional Commits format',
                'fix': f"Use format: type(scope): description\nAllowed types: {', '.join(self.standards.get('type_allowed', []))}",
                'severity': 'error',
                'examples': self.standards.get('examples_valid', [])
            })

        return errors

    def _check_subject_rules(self, message: str) -> List[Dict[str, str]]:
        """Check subject line specific rules."""
        errors = []

        lines = message.split('\n')
        subject = lines[0].strip()

        # Extract description part (after type and scope)
        # Example: "feat(scope): description" -> "description"
        match = re.match(r'^[a-z]+(\(.+?\))?: (.+)$', subject)
        if match:
            description = match.group(2)

            # Check max length
            max_length = self.standards.get('subject_max_length', 72)
            if len(subject) > max_length:
                errors.append({
                    'type': 'SUBJECT_TOO_LONG',
                    'message': f'Subject line exceeds {max_length} characters (current: {len(subject)})',
                    'fix': f'Shorten subject to {max_length} characters or less',
                    'severity': 'error'
                })

            # Check for period at end
            rules = self.standards.get('subject_rules', {})
            if rules.get('no_period_at_end', True) and description.endswith('.'):
                errors.append({
                    'type': 'SUBJECT_ENDS_WITH_PERIOD',
                    'message': 'Subject line should not end with a period',
                    'fix': 'Remove the period at the end of the subject',
                    'severity': 'error'
                })

            # Check for emojis in subject line
            if rules.get('no_emoji', False):
                emoji_pattern = re.compile(
                    "["
                    "\U0001F600-\U0001F64F"  # emoticons
                    "\U0001F300-\U0001F5FF"  # symbols & pictographs
                    "\U0001F680-\U0001F6FF"  # transport & map symbols
                    "\U0001F700-\U0001F77F"  # alchemical symbols
                    "\U0001F780-\U0001F7FF"  # Geometric Shapes Extended
                    "\U0001F800-\U0001F8FF"  # Supplemental Arrows-C
                    "\U0001F900-\U0001F9FF"  # Supplemental Symbols and Pictographs
                    "\U0001FA00-\U0001FA6F"  # Chess Symbols
                    "\U0001FA70-\U0001FAFF"  # Symbols and Pictographs Extended-A
                    "\U00002702-\U000027B0"  # Dingbats
                    "\U000024C2-\U0001F251"  # Enclosed characters
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

        # Check if there's a body (more than just subject)
        if len(lines) <= 1:
            return warnings

        # Check blank line after subject
        if len(lines) > 1 and lines[1].strip() != '':
            warnings.append({
                'type': 'MISSING_BLANK_LINE',
                'message': 'Missing blank line between subject and body',
                'fix': 'Add a blank line after the subject line',
                'severity': 'warning'
            })

        # Check body line length
        max_length = self.standards.get('body_max_line_length', 72)
        for i, line in enumerate(lines[2:], start=3):  # Skip subject and blank line
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

        # If log_path is relative, resolve from base_path (not cwd)
        if not os.path.isabs(log_path):
            # Remove leading ./ if present
            log_path = log_path.lstrip('./')
            # If starts with 'claude/', remove it since base_path already points to .claude/
            if log_path.startswith('claude/'):
                log_path = log_path[7:]  # Remove 'claude/' prefix
            log_path = os.path.join(self.base_path, log_path)

        # Ensure log directory exists
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
            'valid': self.standards.get('examples_valid', []),
            'invalid': self.standards.get('examples_invalid', [])
        }

    def get_allowed_types(self) -> List[str]:
        """Get list of allowed commit types."""
        return self.standards.get('type_allowed', [])

    def format_error_message(self, validation: ValidationResult) -> str:
        """
        Format validation errors into human-readable message.

        Args:
            validation: ValidationResult from validate()

        Returns:
            Formatted error message string
        """
        if validation.valid:
            return "✅ Commit message is valid"

        lines = ["❌ Commit message validation failed:\n"]

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
            lines.append("⚠️  Warnings:")
            for warning in validation.warnings:
                lines.append(f"  - {warning['message']}")
            lines.append("")

        return "\n".join(lines)


# Convenience function for quick validation
def validate_commit_message(message: str) -> ValidationResult:
    """
    Quick validation function.

    Args:
        message: Commit message to validate

    Returns:
        ValidationResult

    Example:
        validation = validate_commit_message("feat: add new feature")
        if not validation.valid:
            print("Invalid commit message")
    """
    validator = CommitMessageValidator()
    return validator.validate(message)


# Function for use in git commit workflow
def safe_validate_before_commit(message: str) -> bool:
    """
    Validate commit message and print errors if invalid.

    This is the primary function that agents should call before git commit.

    Args:
        message: Commit message to validate

    Returns:
        True if valid, False if invalid (with errors printed)

    Example:
        if not safe_validate_before_commit(commit_message):
            return {"status": "failed", "reason": "commit_validation_failed"}

        # Safe to commit
        Bash(f'git commit -m "{commit_message}"')
    """
    validator = CommitMessageValidator()
    validation = validator.validate(message)

    if not validation.valid:
        error_message = validator.format_error_message(validation)
        print(error_message)
        return False

    return True
