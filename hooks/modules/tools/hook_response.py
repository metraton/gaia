"""
Shared builder for hookSpecificOutput responses.

Claude Code hooks communicate permission decisions via a standard JSON
structure.  This module provides a single builder so the three call sites
(bash_validator allow, bash_validator deny, cloud_pipe_validator deny)
share the same shape and field names.

Internally delegates to the adapter layer's ``format_validation_response``
for structural consistency, while preserving the original function signature
so callers (bash_validator.py, cloud_pipe_validator) require zero changes.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure the hooks directory is on sys.path so ``adapters`` resolves.
_hooks_dir = str(Path(__file__).resolve().parent.parent.parent)
if _hooks_dir not in sys.path:
    sys.path.insert(0, _hooks_dir)

from adapters.claude_code import ClaudeCodeAdapter
from adapters.types import ValidationResult

# Module-level singleton -- lightweight, no I/O in __init__.
_adapter = ClaudeCodeAdapter()


def build_hook_permission_response(decision: str, reason: str) -> dict:
    """Build a hookSpecificOutput dict for a PreToolUse permission decision.

    Args:
        decision: "allow" or "deny".
        reason: Human-readable explanation forwarded to the agent.

    Returns:
        Dict suitable for ``json.dumps()`` and ``print()`` in the hook
        entry point.
    """
    vr = ValidationResult(
        allowed=(decision == "allow"),
        reason=reason,
    )
    response = _adapter.format_validation_response(vr)
    return response.output
