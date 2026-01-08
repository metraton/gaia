"""
Hook state management - Share state between pre and post hooks.

Uses a temporary file to pass information from pre_tool_use to post_tool_use,
since they run in separate processes.
"""

import os
import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional
from dataclasses import dataclass, asdict, field

from .paths import find_claude_dir

logger = logging.getLogger(__name__)

# State file location
STATE_FILE_NAME = ".hooks_state.json"


@dataclass
class HookState:
    """
    State passed from pre-hook to post-hook.

    Attributes:
        tool_name: Name of the tool being executed
        command: Command being executed (for Bash)
        tier: Security tier assigned by pre-hook
        start_time: ISO timestamp when pre-hook ran
        session_id: Current session identifier
        pre_hook_result: Result from pre-hook validation
        metadata: Additional context data
    """
    tool_name: str = ""
    command: str = ""
    tier: str = "unknown"
    start_time: str = ""
    session_id: str = ""
    pre_hook_result: str = "allowed"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "HookState":
        """Create from dictionary."""
        return cls(
            tool_name=data.get("tool_name", ""),
            command=data.get("command", ""),
            tier=data.get("tier", "unknown"),
            start_time=data.get("start_time", ""),
            session_id=data.get("session_id", ""),
            pre_hook_result=data.get("pre_hook_result", "allowed"),
            metadata=data.get("metadata", {}),
        )


def _get_state_file_path() -> Path:
    """Get path to state file."""
    claude_dir = find_claude_dir()
    return claude_dir / STATE_FILE_NAME


def save_hook_state(state: HookState) -> bool:
    """
    Save hook state for post-hook to read.

    Args:
        state: HookState to save

    Returns:
        True if saved successfully
    """
    try:
        state_file = _get_state_file_path()
        state_file.parent.mkdir(parents=True, exist_ok=True)

        with open(state_file, "w") as f:
            json.dump(state.to_dict(), f)

        logger.debug(f"Saved hook state: {state.tool_name} / {state.tier}")
        return True

    except Exception as e:
        logger.warning(f"Could not save hook state: {e}")
        return False


def get_hook_state() -> Optional[HookState]:
    """
    Get hook state saved by pre-hook.

    Returns:
        HookState if found, None otherwise
    """
    try:
        state_file = _get_state_file_path()

        if not state_file.exists():
            logger.debug("No hook state file found")
            return None

        with open(state_file, "r") as f:
            data = json.load(f)

        return HookState.from_dict(data)

    except Exception as e:
        logger.warning(f"Could not read hook state: {e}")
        return None


def clear_hook_state() -> bool:
    """
    Clear hook state after post-hook has processed it.

    Returns:
        True if cleared successfully
    """
    try:
        state_file = _get_state_file_path()

        if state_file.exists():
            state_file.unlink()
            logger.debug("Cleared hook state")

        return True

    except Exception as e:
        logger.warning(f"Could not clear hook state: {e}")
        return False


def create_pre_hook_state(
    tool_name: str,
    command: str = "",
    tier: str = "unknown",
    **metadata
) -> HookState:
    """
    Create a new hook state for pre-hook.

    Convenience function that sets common fields automatically.

    Args:
        tool_name: Name of the tool
        command: Command being executed
        tier: Security tier
        **metadata: Additional metadata

    Returns:
        New HookState instance
    """
    session_id = os.environ.get("CLAUDE_SESSION_ID", "default")

    return HookState(
        tool_name=tool_name,
        command=command,
        tier=tier,
        start_time=datetime.now().isoformat(),
        session_id=session_id,
        pre_hook_result="allowed",
        metadata=metadata,
    )
