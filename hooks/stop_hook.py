#!/usr/bin/env python3
"""
Stop hook for Claude Code Agent System.

Fires when Claude finishes responding. It logs the adapter's quality result
(adapt_stop reports quality_sufficient=True unconditionally), records the
answer's form against the user's conversation preferences, and always exits 0.
"""

import os
import sys
import json
import logging
from pathlib import Path

_hooks_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(_hooks_dir))
_pkg_root = str(_hooks_dir.parent)
if _pkg_root not in sys.path:
    sys.path.insert(0, _pkg_root)

from adapters.registry import get_adapter
from modules.core.hook_entry import run_hook
from modules.core.logging_setup import configure_hook_logging

configure_hook_logging("stop_hook")
logger = logging.getLogger(__name__)


def _handle_stop(event) -> None:
    """Log the Stop event's quality result and allow the stop (exit 0).

    Args:
        event: Parsed HookEvent from the adapter layer.
    """
    from modules.tools.background_guard import close_refusal

    refusal = close_refusal(event.payload, event.payload.get("transcript_path") or "")
    if refusal:
        print(json.dumps({"decision": "block", "reason": refusal}))
        sys.exit(0)

    adapter = get_adapter()

    quality_result = adapter.adapt_stop(event.payload)
    stop_reason = event.payload.get("stop_reason", "unknown")

    logger.info(
        "Stop: reason=%s, quality_sufficient=%s, score=%.2f",
        stop_reason,
        quality_result.quality_sufficient,
        quality_result.score,
    )

    _measure_answer_form(event.payload)
    response = adapter.format_quality_response(quality_result)
    print(json.dumps(response.output))
    sys.exit(0)


def _measure_answer_form(payload: dict) -> None:
    """Record the form of the final answer against the user's conversation preferences; nothing reaches the user."""
    try:
        from gaia.store.reader import user_memory_rows
        from modules.session.answer_form import measure_final_answer

        answer = payload.get("last_assistant_message") or ""
        if answer:
            measure_final_answer(str(payload.get("session_id") or ""), answer, user_memory_rows())
    except Exception as exc:
        logger.debug("answer form measurement skipped (non-fatal): %s", exc)


if __name__ == "__main__":
    run_hook(_handle_stop, hook_name="stop_hook")
