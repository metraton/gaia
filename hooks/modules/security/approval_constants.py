"""Canonical nonce approval token and deprecated approval phrases for T3 operation resumes."""

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
