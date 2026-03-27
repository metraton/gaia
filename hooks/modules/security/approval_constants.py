"""Approval token patterns and deprecated approval phrases for T3 operation resumes.

The APPROVE: prefix is a legacy path (SendMessage-based nonce relay). The primary
approval flow now uses ElicitationResult (AskUserQuestion -> user clicks Approve).
"""

import re

NONCE_APPROVAL_PREFIX = "APPROVE:"
NONCE_APPROVAL_PATTERN = re.compile(r"\bAPPROVE:([a-f0-9]{32})\b")

# Deprecated approval phrases that agents should not use.
# Moved here from pre_tool_use.py so all approval-related constants live together.
DEPRECATED_APPROVAL_PHRASES = (
    "user approved:",
    "user approval received",
    "approved by user",
    "approval confirmed",
    "approved. execute",
    "approved, execute",
    "proceed with execution",
    "confirmed. proceed",
)
