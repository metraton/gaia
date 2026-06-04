"""
Core module - Shared utilities for all hook modules.

Provides:
- paths: Unified path resolution (find_claude_dir)
- plugin_mode: Plugin mode detection (security vs ops)
- state: Pre/post hook state sharing
- stdin: Stdin availability check (has_stdin_data)
"""

from .paths import find_claude_dir, get_plugin_data_dir, get_logs_dir, get_metrics_dir, get_memory_dir
from .plugin_mode import get_plugin_mode, is_ops_mode, is_security_mode, has_plugin, clear_mode_cache
from .state import HookState, get_hook_state, save_hook_state, clear_hook_state, get_session_id
from .stdin import has_stdin_data
from .hook_entry import run_hook

__all__ = [
    # Paths
    "find_claude_dir",
    "get_plugin_data_dir",
    "get_logs_dir",
    "get_metrics_dir",
    "get_memory_dir",
    # Plugin mode
    "get_plugin_mode",
    "is_ops_mode",
    "is_security_mode",
    "has_plugin",
    "clear_mode_cache",
    # State
    "HookState",
    "get_hook_state",
    "save_hook_state",
    "clear_hook_state",
    "get_session_id",
    # Stdin
    "has_stdin_data",
    # Hook entry
    "run_hook",
]
