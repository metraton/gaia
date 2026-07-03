"""
Core module - Shared utilities for all hook modules.

Provides:
- paths: Unified path resolution (find_claude_dir)
- plugin_mode: Plugin registry membership check (has_plugin)
- state: Pre/post hook state sharing
- stdin: Stdin availability check (has_stdin_data)
- logging_setup: Shared hook logging config (configure_hook_logging)
"""

from .paths import find_claude_dir, get_plugin_data_dir, get_logs_dir, get_memory_dir
from .plugin_mode import has_plugin
from .state import HookState, get_hook_state, save_hook_state, clear_hook_state, get_session_id
from .stdin import has_stdin_data
from .hook_entry import run_hook
from .logging_setup import configure_hook_logging

__all__ = [
    # Paths
    "find_claude_dir",
    "get_plugin_data_dir",
    "get_logs_dir",
    "get_memory_dir",
    # Plugin registry
    "has_plugin",
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
    # Logging
    "configure_hook_logging",
]
