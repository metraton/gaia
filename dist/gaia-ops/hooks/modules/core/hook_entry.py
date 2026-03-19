"""
Common hook entrypoint boilerplate.

Provides ``run_hook()`` which encapsulates the repeated pattern found in
every hook entrypoint:

1. Check ``has_stdin_data()``
2. Read stdin
3. Parse via the adapter
4. Call a caller-provided handler with the parsed ``HookEvent``
5. Catch and log exceptions, exiting appropriately

Usage in a hook entrypoint::

    from modules.core.hook_entry import run_hook

    def _handle(event: HookEvent) -> None:
        ...  # business logic, call sys.exit() / print as needed

    if __name__ == "__main__":
        run_hook(_handle, hook_name="stop_hook")
"""

import json
import logging
import sys
from typing import Callable

from .stdin import has_stdin_data

logger = logging.getLogger(__name__)


def run_hook(
    handler: Callable,
    *,
    hook_name: str = "hook",
    usage_message: str | None = None,
) -> None:
    """Read stdin, parse via ClaudeCodeAdapter, and delegate to *handler*.

    Args:
        handler: Callable that receives an ``adapters.types.HookEvent``.
            The handler is responsible for printing output and calling
            ``sys.exit()`` with the appropriate code.
        hook_name: Human-readable name used in log messages and the default
            usage string.
        usage_message: Custom usage text shown when stdin is absent. If
            *None*, a sensible default is generated from *hook_name*.
    """
    if not has_stdin_data():
        msg = usage_message or (
            f"Usage: echo '{{...}}' | python {hook_name}.py  (stdin mode)"
        )
        print(msg)
        sys.exit(1)

    try:
        # Deferred adapter import avoids circular dependencies at module level;
        # the adapter package is a sibling of modules/.
        from adapters.claude_code import ClaudeCodeAdapter

        adapter = ClaudeCodeAdapter()
        stdin_data = sys.stdin.read()
        event = adapter.parse_event(stdin_data)
        handler(event)
    except ValueError as e:
        logger.error("Adapter parse failed in %s: %s", hook_name, e)
        sys.exit(1)
    except json.JSONDecodeError as e:
        logger.error("Invalid JSON from stdin in %s: %s", hook_name, e)
        sys.exit(1)
    except SystemExit:
        raise  # Let explicit sys.exit() calls propagate
    except Exception as e:
        logger.error("Error processing %s hook: %s", hook_name, e)
        sys.exit(1)
