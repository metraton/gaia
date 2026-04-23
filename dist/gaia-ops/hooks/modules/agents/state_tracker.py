"""
State transition tracking for agent contracts.

Tracks agent state across responses and validates that transitions
follow the state machine defined in agent-protocol. Uses a JSON file
in /tmp/ keyed by agent_id.

Legal transitions (from agent-protocol):
    IN_PROGRESS -> COMPLETE                (T0/T1/T2 only)
    IN_PROGRESS -> APPROVAL_REQUEST        (hook blocks a mutative command)
    APPROVAL_REQUEST -> IN_PROGRESS        (after approval granted)
    IN_PROGRESS -> BLOCKED                 (any point)
    IN_PROGRESS -> NEEDS_INPUT             (any point)
    IN_PROGRESS -> IN_PROGRESS             (retry, max 2)

Provides:
    - track_transition(): Record a state and validate the transition
    - get_agent_state(): Read current state for an agent
    - clear_agent_state(): Remove tracking for an agent
"""

import json
import logging
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

_STATE_FILE = Path("/tmp/gaia-agent-states.json")

# Maximum consecutive IN_PROGRESS transitions (retry cap)
_MAX_IN_PROGRESS_RETRIES = 2

# Legal transitions: from_status -> set of allowed to_statuses
# Note: COMPLETE, BLOCKED, NEEDS_INPUT are terminal for a given task cycle.
_LEGAL_TRANSITIONS: Dict[str, Set[str]] = {
    "IN_PROGRESS": {"COMPLETE", "APPROVAL_REQUEST", "BLOCKED", "NEEDS_INPUT", "IN_PROGRESS"},
    "APPROVAL_REQUEST": {"IN_PROGRESS"},
    # Terminal states -- new task cycles can start from scratch
    "COMPLETE": {"IN_PROGRESS"},
    "BLOCKED": {"IN_PROGRESS"},
    "NEEDS_INPUT": {"IN_PROGRESS"},
}

# Transitions that require T3 context (IN_PROGRESS -> COMPLETE skipping REVIEW)
# is only legal for T0/T1/T2. We can't enforce this without tier info,
# so we flag it as a warning rather than a hard rejection.
_REVIEW_REQUIRED_TRANSITION = ("IN_PROGRESS", "COMPLETE")


@dataclass
class TransitionResult:
    """Result of a state transition validation.

    Attributes:
        valid: True if the transition is legal.
        previous_state: The agent's state before this transition (empty if first).
        current_state: The new state being recorded.
        warning: Optional warning message (e.g., retry count approaching limit).
        error: Error message when valid is False.
        in_progress_count: How many consecutive IN_PROGRESS states.
    """
    valid: bool
    previous_state: str
    current_state: str
    warning: str
    error: str
    in_progress_count: int

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _read_state_file() -> Dict[str, Any]:
    """Read the state tracking file. Returns empty dict on any error."""
    try:
        if _STATE_FILE.exists():
            return json.loads(_STATE_FILE.read_text())
    except (json.JSONDecodeError, OSError) as e:
        logger.debug("Could not read state file: %s", e)
    return {}


def _write_state_file(data: Dict[str, Any]) -> bool:
    """Write the state tracking file. Returns True on success."""
    try:
        _STATE_FILE.write_text(json.dumps(data, indent=2))
        return True
    except OSError as e:
        logger.warning("Could not write state file: %s", e)
        return False


def get_agent_state(agent_id: str) -> Optional[Dict[str, Any]]:
    """Read current tracked state for an agent.

    Args:
        agent_id: The agent's unique identifier.

    Returns:
        Dict with 'state', 'in_progress_count', 'last_updated', or None if not tracked.
    """
    if not agent_id:
        return None
    data = _read_state_file()
    return data.get(agent_id)


def clear_agent_state(agent_id: str) -> bool:
    """Remove tracking state for an agent.

    Args:
        agent_id: The agent's unique identifier.

    Returns:
        True if cleared successfully.
    """
    if not agent_id:
        return False
    data = _read_state_file()
    if agent_id in data:
        del data[agent_id]
        return _write_state_file(data)
    return True


def track_transition(
    agent_id: str,
    new_state: str,
    *,
    has_review_phase: bool = False,
) -> TransitionResult:
    """Record a new state for an agent and validate the transition.

    Args:
        agent_id: The agent's unique identifier.
        new_state: The new plan_status being reported.
        has_review_phase: Hint that this task involves T3 / plan-first flow.
            When True, IN_PROGRESS -> COMPLETE without an intervening REVIEW
            is flagged as a warning.

    Returns:
        TransitionResult with validation outcome.
    """
    new_state = new_state.upper().strip()

    if not agent_id:
        return TransitionResult(
            valid=True,
            previous_state="",
            current_state=new_state,
            warning="No agent_id provided; transition tracking skipped.",
            error="",
            in_progress_count=0,
        )

    data = _read_state_file()
    agent_record = data.get(agent_id)

    # First state for this agent -- always valid
    if agent_record is None:
        data[agent_id] = {
            "state": new_state,
            "in_progress_count": 1 if new_state == "IN_PROGRESS" else 0,
            "saw_review": False,
            "last_updated": time.time(),
        }
        _write_state_file(data)
        return TransitionResult(
            valid=True,
            previous_state="",
            current_state=new_state,
            warning="",
            error="",
            in_progress_count=data[agent_id]["in_progress_count"],
        )

    previous_state = agent_record.get("state", "")
    in_progress_count = agent_record.get("in_progress_count", 0)
    saw_review = agent_record.get("saw_review", False)

    # Check if transition is legal
    allowed = _LEGAL_TRANSITIONS.get(previous_state, set())
    if new_state not in allowed:
        return TransitionResult(
            valid=False,
            previous_state=previous_state,
            current_state=new_state,
            warning="",
            error=(
                f"Illegal state transition: {previous_state} -> {new_state}. "
                f"Allowed from {previous_state}: {', '.join(sorted(allowed)) if allowed else 'none'}."
            ),
            in_progress_count=in_progress_count,
        )

    warning = ""

    # Track IN_PROGRESS retry count
    if new_state == "IN_PROGRESS" and previous_state == "IN_PROGRESS":
        in_progress_count += 1
        if in_progress_count > _MAX_IN_PROGRESS_RETRIES:
            return TransitionResult(
                valid=False,
                previous_state=previous_state,
                current_state=new_state,
                warning="",
                error=(
                    f"IN_PROGRESS retry limit exceeded: {in_progress_count} consecutive "
                    f"IN_PROGRESS states (max {_MAX_IN_PROGRESS_RETRIES}). "
                    f"Agent should transition to APPROVAL_REQUEST, COMPLETE, BLOCKED, or NEEDS_INPUT."
                ),
                in_progress_count=in_progress_count,
            )
    elif new_state == "IN_PROGRESS":
        # Reset count when coming from a non-IN_PROGRESS state (e.g., REVIEW -> IN_PROGRESS)
        in_progress_count = 1
    else:
        in_progress_count = 0

    # Track APPROVAL_REQUEST visibility
    if new_state == "APPROVAL_REQUEST":
        saw_review = True

    # Check: IN_PROGRESS -> COMPLETE without REVIEW when review was expected
    if (previous_state, new_state) == _REVIEW_REQUIRED_TRANSITION:
        if has_review_phase and not saw_review:
            warning = (
                "IN_PROGRESS -> COMPLETE without an intervening APPROVAL_REQUEST phase. "
                "If this task involves T3 operations, an APPROVAL_REQUEST step is expected."
            )

    # Approaching retry limit
    if new_state == "IN_PROGRESS" and in_progress_count == _MAX_IN_PROGRESS_RETRIES:
        retry_warning = (
            f"IN_PROGRESS retry count at {in_progress_count}/{_MAX_IN_PROGRESS_RETRIES}. "
            f"Next IN_PROGRESS will be rejected."
        )
        warning = f"{warning} {retry_warning}".strip() if warning else retry_warning

    # Update record
    data[agent_id] = {
        "state": new_state,
        "in_progress_count": in_progress_count,
        "saw_review": saw_review,
        "last_updated": time.time(),
    }
    _write_state_file(data)

    return TransitionResult(
        valid=True,
        previous_state=previous_state,
        current_state=new_state,
        warning=warning,
        error="",
        in_progress_count=in_progress_count,
    )


__all__ = [
    "TransitionResult",
    "track_transition",
    "get_agent_state",
    "clear_agent_state",
]
