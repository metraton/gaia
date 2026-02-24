"""
Core module - Shared utilities for all hook modules.

Provides:
- paths: Unified path resolution (find_claude_dir)
- state: Pre/post hook state sharing
"""

from .paths import find_claude_dir, get_logs_dir, get_metrics_dir, get_memory_dir
from .state import HookState, get_hook_state, save_hook_state, clear_hook_state

__all__ = [
    # Paths
    "find_claude_dir",
    "get_logs_dir",
    "get_metrics_dir",
    "get_memory_dir",
    # State
    "HookState",
    "get_hook_state",
    "save_hook_state",
    "clear_hook_state",
]
