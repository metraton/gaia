"""Canonical nonce approval token for T3 operation resumes."""

import re

NONCE_APPROVAL_PREFIX = "APPROVE:"
NONCE_APPROVAL_PATTERN = re.compile(r"\bAPPROVE:([a-f0-9]{32})\b")
