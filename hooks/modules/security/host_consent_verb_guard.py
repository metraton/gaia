"""
host_consent_verb_guard.py -- the host consent verbs belong to the plugin alone.

``gaia approvals opencode-present`` records that a signature was SHOWN and
``gaia approvals opencode-decide`` applies the user's reply to it. The CLI can
check only that the caller names the requesting session and agent; the call id
and the token are whatever the caller passes, and the plugin's own spawn
carries nothing a shell could not copy. So a requester reaching these verbs
from a shell could present its own request to itself and approve it with no
user in the loop. The OpenCode plugin runs them through its own process spawn,
which no tool hook sees, so refusing them on every model-issued shell call, in
both hosts and for every role, leaves the plugin as their only caller.

Categorical, not approvable: a signature over "record that the user answered"
would be the user's answer to a different question.

Detection reads the command text rather than its tokens, so the verbs are
found inside a shell wrapper (``bash -c '...'``) or an argument list
(``['approvals', 'opencode-decide']``) as well as in the plain forms. A
spelling assembled at run time is not found; reaching the verbs that way is
the elusion ``security-tiers`` forbids.
"""

from __future__ import annotations

import re
from typing import Optional, Tuple

_HOST_CONSENT_VERB = re.compile(
    r"\bapprovals(?:[\s'\",\]]+-[^\s'\",\]]*)*[\s'\",\]]+opencode-(?:present|decide)\b"
)

REJECTION_MESSAGE = (
    "`gaia approvals opencode-present` and `opencode-decide` record a "
    "presentation and apply the user's reply; only the OpenCode plugin runs "
    "them, from its own process, never a model's shell. Ask for the signature "
    "the usual way (`gaia approvals request-set`, then close APPROVAL_REQUEST). "
    "Do NOT retry -- this is not approvable."
)


def check(command: str) -> Tuple[bool, Optional[str]]:
    """Refuse a shell command that invokes a host consent verb; allow anything else."""
    if command and _HOST_CONSENT_VERB.search(command):
        return False, REJECTION_MESSAGE
    return True, None
