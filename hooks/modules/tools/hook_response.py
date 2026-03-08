"""
Shared builder for hookSpecificOutput responses.

Claude Code hooks communicate permission decisions via a standard JSON
structure.  This module provides a single builder so the three call sites
(bash_validator allow, bash_validator deny, cloud_pipe_validator deny)
share the same shape and field names.
"""


def build_hook_permission_response(decision: str, reason: str) -> dict:
    """Build a hookSpecificOutput dict for a PreToolUse permission decision.

    Args:
        decision: "allow" or "deny".
        reason: Human-readable explanation forwarded to the agent.

    Returns:
        Dict suitable for ``json.dumps()`` and ``print()`` in the hook
        entry point.
    """
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        }
    }
