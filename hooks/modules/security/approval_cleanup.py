"""
Approval file cleanup for the subagent stop hook.

Renamed from approval_consumer.py for clarity. Consumes (deletes) pending
approval files after an agent completes.

Provides:
    - cleanup(): Delete pending approval if it matches agent_type
    - consume_approval_file(): Backward-compatible alias for cleanup()
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def cleanup(agent_type: str) -> bool:
    """
    Delete .claude/approvals/pending.json if it exists and matches agent_type.

    The orchestrator writes this file when resuming an approved T3 operation.
    Consuming it here confirms the approval was used and prevents reuse.

    Returns:
        True if a matching approval file was consumed, False otherwise.
    """
    try:
        approval_path = Path(".claude/approvals/pending.json")
        if not approval_path.exists():
            return False

        data = json.loads(approval_path.read_text())
        if data.get("agent") == agent_type:
            approval_path.unlink()
            logger.info(
                "Consumed approval for agent '%s' (operation: %s)",
                agent_type,
                data.get("operation", "unknown"),
            )
            return True

        # File exists but for a different agent -- leave it alone
        logger.debug(
            "Approval file exists for agent '%s', not '%s' -- leaving intact",
            data.get("agent"),
            agent_type,
        )
        return False
    except Exception as e:
        logger.debug("Failed to consume approval file (non-fatal): %s", e)
        return False


# Backward-compatible alias
consume_approval_file = cleanup
