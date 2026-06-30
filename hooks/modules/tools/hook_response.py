"""
Shared builder for PreToolUse permission responses.

Hooks communicate permission decisions via a host-specific JSON structure.
This module provides a single builder so the call sites (bash_validator allow,
bash_validator deny, cloud_pipe_validator deny) share one entry point.

The host-specific shape is assembled entirely inside the adapter layer: this
builder delegates to ``format_validation_response`` / ``format_ask_response``
and returns whatever the adapter produces, while preserving the original
function signature so callers require zero changes.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure the hooks directory is on sys.path so ``adapters`` resolves.
_hooks_dir = str(Path(__file__).resolve().parent.parent.parent)
if _hooks_dir not in sys.path:
    sys.path.insert(0, _hooks_dir)

from adapters.registry import get_adapter
from adapters.types import ValidationResult

# Single construction point: the shared, process-wide adapter from the registry
# (formerly a module-level ``ClaudeCodeAdapter()`` singleton). Resolved once at
# import; the registry caches the stateless instance.
_adapter = get_adapter()


def build_hook_permission_response(
    decision: str, reason: str, updated_input: dict | None = None
) -> dict:
    """Build a host permission-response dict for a PreToolUse decision.

    Args:
        decision: "allow", "deny", or "ask".
        reason: Human-readable explanation forwarded to the agent.
        updated_input: Optional modified tool input to pass through for
            "ask" decisions (e.g. footer-stripped command).

    Returns:
        Dict suitable for ``json.dumps()`` and ``print()`` in the hook
        entry point.
    """
    if decision == "ask":
        response = _adapter.format_ask_response(reason, updated_input=updated_input)
        return response.output

    vr = ValidationResult(
        allowed=(decision == "allow"),
        reason=reason,
    )
    response = _adapter.format_validation_response(vr)
    return response.output
