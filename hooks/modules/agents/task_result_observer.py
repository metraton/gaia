"""Detect a harness-truncated subagent turn from the ORCHESTRATOR side.

When the harness cuts a subagent mid-turn, nothing downstream of the subagent
runs: ``SubagentStop`` never fires, so no ``episodes`` row and no
``harness_events`` row is written, and the turn leaves no trace at all. The one
place the cut IS observable is where the parent receives the Task result: the
harness closes the Task reporting a non-error status, and surfaces as the
result a STALE text block -- the last text the model happened to emit before
the cut, which carries no fenced ``agent_contract_handoff``.

That absence is the signature this module keys on. Every Gaia agent turn is
required to close with the fence (the SubagentStop full-verdict gate rejects a
turn without one), so a Task that came back reporting success yet carries no
parseable fence did not reach its own end.

Detection is a pure function of the PostToolUse payload (``detect_task_cut``);
persistence is a separate, non-blocking step (``observe_task_result``). The
split keeps the signature testable without a database.

Gotchas:
- A Task the harness itself reports as errored, cancelled, or interrupted is
  NOT this signature -- that failure is already visible to the caller. Only the
  silent one is recorded here.
- The metrics (``totalDurationMs`` / ``totalTokens`` / ``totalToolUseCount``)
  are copied verbatim from the result. They are the only quantitative record
  the cut leaves behind, since the episode that would have carried them was
  never written.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional

logger = logging.getLogger(__name__)

# Dotted event category written to harness_events when the signature matches.
AGENT_CUT_EVENT = "agent.cut"

# Statuses the harness uses for a turn whose failure is already visible to the
# caller. A cut is the opposite: it reports success.
_VISIBLE_FAILURE_STATUSES = frozenset(
    {"error", "errored", "failed", "failure", "cancelled", "canceled", "interrupted"}
)

# Harness-reported metric keys carried on a Task tool_response.
_METRIC_KEYS = ("totalDurationMs", "totalTokens", "totalToolUseCount")

# Reason codes recorded on the event, in the order they are tested.
REASON_NO_FENCE = "no_contract_fence"
REASON_UNPARSEABLE_FENCE = "unparseable_contract_fence"

# The fence tag every agent turn must close with.
_CONTRACT_TAG = "agent_contract_handoff"

# How much of the stale result text to keep on the event, in characters. Enough
# to recognize WHICH block the harness surfaced, not enough to bloat the row.
_PREVIEW_CHARS = 400


@dataclass(frozen=True)
class TaskCut:
    """A Task result whose shape matches the harness-cut signature."""

    agent: str
    status: str
    reason: str
    metrics: Dict[str, Any] = field(default_factory=dict)
    result_preview: str = ""
    session_id: str = ""

    def to_meta(self) -> Dict[str, Any]:
        """Structured payload for the harness_events row."""
        meta: Dict[str, Any] = {
            "agent": self.agent,
            "reason": self.reason,
            "task_status": self.status,
            "detected_by": "post_tool_use.Task",
        }
        if self.session_id:
            meta["session_id"] = self.session_id
        if self.result_preview:
            meta["result_preview"] = self.result_preview
        meta.update(self.metrics)
        return meta


def _extract_result_text(tool_response: Any) -> str:
    """Best-effort flattening of a Task tool_response into its text result.

    The harness has shipped several shapes for this field across versions: a
    bare string, a dict with a ``content`` list of typed blocks, or a dict with
    a flat ``result``/``output``/``stdout`` string. All are accepted; anything
    unrecognized flattens to the empty string, which reads as "no fence".
    """
    if isinstance(tool_response, str):
        return tool_response
    if not isinstance(tool_response, Mapping):
        return ""

    content = tool_response.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, Mapping):
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        if parts:
            return "\n".join(parts)

    for key in ("result", "output", "stdout", "text"):
        value = tool_response.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _extract_metrics(tool_response: Any) -> Dict[str, Any]:
    """Copy the harness-reported Task metrics, omitting absent keys."""
    if not isinstance(tool_response, Mapping):
        return {}
    return {k: tool_response[k] for k in _METRIC_KEYS if tool_response.get(k) is not None}


def _extract_status(tool_response: Any) -> str:
    """Read the harness-reported Task status, defaulting to "completed".

    A payload with no ``status`` key is treated as completed: the harness
    reports failure explicitly, so silence means the Task closed normally --
    which is precisely the half of the signature this module needs.
    """
    if isinstance(tool_response, Mapping):
        status = tool_response.get("status")
        if isinstance(status, str) and status:
            return status
    return "completed"


def _reports_visible_failure(tool_response: Any, status: str) -> bool:
    """Whether the harness already surfaced this turn as failed to the caller."""
    if status.strip().lower() in _VISIBLE_FAILURE_STATUSES:
        return True
    if isinstance(tool_response, Mapping):
        if tool_response.get("wasInterrupted") or tool_response.get("interrupted"):
            return True
        if tool_response.get("is_error") or tool_response.get("isError"):
            return True
    return False


def _parse_contract(result_text: str) -> Optional[dict]:
    """Parse the handoff fence via the canonical parser, never raising."""
    try:
        from .contract_validator import parse_contract
    except ImportError:  # pragma: no cover - defensive, module is a sibling
        return None
    try:
        return parse_contract(result_text)
    except Exception:  # pragma: no cover - parser is defensive already
        return None


def detect_task_cut(
    tool_input: Any,
    tool_response: Any,
    *,
    session_id: str = "",
) -> Optional[TaskCut]:
    """Return a :class:`TaskCut` when the payload matches the cut signature.

    The signature is: the harness reports no failure for the Task, AND the
    result it surfaces carries no parseable ``agent_contract_handoff`` fence.

    Args:
        tool_input:    The Task ``tool_input`` dict (source of ``subagent_type``).
        tool_response: The Task ``tool_response``, in any of its harness shapes.
        session_id:    Session the Task ran under, recorded on the event.

    Returns:
        A TaskCut describing the cut, or None when the turn closed normally or
        failed visibly.
    """
    status = _extract_status(tool_response)
    if _reports_visible_failure(tool_response, status):
        return None

    result_text = _extract_result_text(tool_response)
    if _parse_contract(result_text) is not None:
        return None

    reason = REASON_UNPARSEABLE_FENCE if _CONTRACT_TAG in result_text else REASON_NO_FENCE

    agent = "unknown"
    if isinstance(tool_input, Mapping):
        agent = tool_input.get("subagent_type") or tool_input.get("agent") or "unknown"

    return TaskCut(
        agent=str(agent),
        status=status,
        reason=reason,
        metrics=_extract_metrics(tool_response),
        result_preview=result_text[-_PREVIEW_CHARS:].strip(),
        session_id=session_id,
    )


def observe_task_result(hook_data: Mapping[str, Any]) -> Optional[TaskCut]:
    """Detect a cut in a PostToolUse Task payload and record it.

    Non-blocking by contract: the event write is best-effort and any failure is
    swallowed, so observability never breaks the orchestrator's turn.

    Returns:
        The detected TaskCut (whether or not the event write succeeded), or
        None when the payload does not match the signature.
    """
    cut = detect_task_cut(
        hook_data.get("tool_input", {}),
        hook_data.get("tool_response", {}),
        session_id=str(hook_data.get("session_id", "") or ""),
    )
    if cut is None:
        return None

    try:
        from ..events.event_writer import EventWriter

        EventWriter().write_event(
            AGENT_CUT_EVENT,
            "hook",
            cut.agent,
            f"subagent cut mid-turn ({cut.reason}); no contract fence in Task result",
            severity="warning",
            meta=cut.to_meta(),
        )
    except Exception as exc:  # pragma: no cover - write path is silent by design
        logger.debug("agent.cut event write failed (non-fatal): %s", exc)

    return cut


__all__ = [
    "AGENT_CUT_EVENT",
    "REASON_NO_FENCE",
    "REASON_UNPARSEABLE_FENCE",
    "TaskCut",
    "detect_task_cut",
    "observe_task_result",
]
