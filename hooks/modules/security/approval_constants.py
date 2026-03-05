"""
Canonical approval indicators for T3 operation gate.

Single source of truth imported by task_validator.py and pre_tool_use.py.
Any new approval phrase must be added here ONLY.
"""

import re

# Nonce-based approval pattern: APPROVE:<32-char hex>
# This is the primary approval mechanism. When an agent presents a
# PENDING_APPROVAL plan, the block response includes a nonce. The orchestrator
# resumes with "APPROVE:<nonce>" after the user approves.
NONCE_APPROVAL_PATTERN = re.compile(r"APPROVE:([a-f0-9]{32})")

# Legacy approval indicators — matched case-insensitively against the resume prompt.
# Kept for backward compatibility with old prompts. New flows should use nonces.
LEGACY_APPROVAL_INDICATORS = [
    "user approved:",        # Legacy scoped token: "User approved: terraform apply prod"
    "user approval received",
    "approved by user",
    "user approved",
    "approved. execute",
    "approved, execute",
    "approval confirmed",
    "proceed with execution",
    "go ahead",
    "confirmed. proceed",
]

# Combined list for backward-compatible code that checks both mechanisms.
# The nonce pattern is checked separately (regex), so this list is only
# for the legacy string-matching path.
APPROVAL_INDICATORS = LEGACY_APPROVAL_INDICATORS
