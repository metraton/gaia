"""
Validation Module: Commit message validation for bash_validator

This module provides commit message validation that is exclusively used
by hooks/modules/tools/bash_validator.py to enforce git commit standards.

Note: This is an internal module. Do not import directly in agent code.
      Commit validation is automatically enforced via bash_validator.py.
"""

from .commit_validator import (
    CommitMessageValidator,
    ValidationResult,
    validate_commit_message,
    safe_validate_before_commit,
)

__all__ = [
    "CommitMessageValidator",
    "ValidationResult",
    "validate_commit_message",
    "safe_validate_before_commit",
]
