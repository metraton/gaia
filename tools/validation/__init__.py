"""
Validation Module: Approval gates for T3 operations

This module provides T3 approval gate enforcement.
Ensures infrastructure changes and critical operations follow proper governance.

Note: commit_validator.py has been moved to hooks/modules/validation/
      and is now only used by bash_validator.py
"""

from .approval_gate import ApprovalGate, request_approval, process_approval_response

__all__ = [
    "ApprovalGate",
    "request_approval",
    "process_approval_response",
]
