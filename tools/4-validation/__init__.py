"""
Validation Module: Approval gates and commit validation

This module provides T3 approval gate enforcement and conventional commit validation.
Ensures infrastructure changes and critical operations follow proper governance.
"""

from .approval_gate import ApprovalGate, request_approval, process_approval_response
from .commit_validator import (
    CommitMessageValidator,
    ValidationResult,
    validate_commit_message,
    safe_validate_before_commit,
)

__all__ = [
    "ApprovalGate",
    "request_approval",
    "process_approval_response",
    "CommitMessageValidator",
    "ValidationResult",
    "validate_commit_message",
    "safe_validate_before_commit",
]
