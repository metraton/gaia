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

The harness names this tool ``Agent``; ``Task`` is its former name, still
honored as a hooks.json matcher but no longer what the payload carries. Both
names are accepted (``TASK_TOOL_NAMES``) so the dispatch does not depend on
which one a given harness version reports.

Gotchas:
- A Task the harness itself reports as errored, cancelled, or interrupted is
  NOT this signature -- that failure is already visible to the caller. Only the
  silent one is recorded here.
- Nor is a BACKGROUND dispatch: ``run_in_background`` returns immediately with
  ``status="async_launched"`` and an ``outputFile`` instead of any result text.
  It carries no fence because the turn has not ended, not because it was cut.
  Measured over 325 real Agent results, that form is 156 of them -- reading it
  as a cut would bury the true signal under ~48% false positives.
- Detection requires a RECOGNIZED result shape. A payload whose text cannot be
  located is reported as unprocessable (traced) rather than silently read as
  "no fence", so the two are never confused: absence of a fence is evidence of
  a cut only when the fence is something we could have found.
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

# Every name the subagent-dispatch tool has shipped under. Current harnesses
# report "Agent"; "Task" is the former name, kept because hooks.json still
# matches on it and older harnesses still send it.
TASK_TOOL_NAMES = frozenset({"Agent", "Task"})

# Statuses the harness uses for a turn whose failure is already visible to the
# caller. A cut is the opposite: it reports success.
_VISIBLE_FAILURE_STATUSES = frozenset(
    {"error", "errored", "failed", "failure", "cancelled", "canceled", "interrupted"}
)

# Statuses that close a turn. Only these can carry the cut signature.
_TERMINAL_STATUSES = frozenset({"completed", "complete", "success", "succeeded", "done"})

# Statuses for a turn that has not ended yet -- a background dispatch reports
# the launch, not a result, so the missing fence means nothing.
_PENDING_STATUSES = frozenset(
    {"async_launched", "pending", "running", "in_progress", "queued", "started"}
)

# Harness-reported metric keys carried on a Task tool_response.
_METRIC_KEYS = ("totalDurationMs", "totalTokens", "totalToolUseCount")

# Reason codes recorded on the event, in the order they are tested.
REASON_NO_FENCE = "no_contract_fence"
REASON_UNPARSEABLE_FENCE = "unparseable_contract_fence"

# Why a result was NOT recorded as a cut. The first three are ordinary
# outcomes; the last two mean the payload could not be read at all and are the
# ones surfaced to the hook trace.
SKIP_VISIBLE_FAILURE = "visible_failure"
SKIP_TURN_NOT_ENDED = "turn_not_ended"
SKIP_CONTRACT_PRESENT = "contract_fence_present"
SKIP_UNKNOWN_STATUS = "unknown_task_status"
SKIP_UNREADABLE_RESULT = "unreadable_result_shape"

# Skip codes that mean "the observer could not process this payload". They are
# traced so a harness shape change surfaces instead of silently suppressing
# every future detection.
UNPROCESSABLE_SKIPS = frozenset({SKIP_UNKNOWN_STATUS, SKIP_UNREADABLE_RESULT})

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
    # The harness-minted id of the cut run. It is the handle that locates the
    # orphaned contract draft the subagent left behind (``gaia contract view``),
    # which is the only surviving record of work the cut turn actually did.
    agent_run_id: str = ""

    def to_meta(self) -> Dict[str, Any]:
        """Structured payload for the harness_events row."""
        meta: Dict[str, Any] = {
            "agent": self.agent,
            "reason": self.reason,
            "task_status": self.status,
            "detected_by": "post_tool_use.Agent",
        }
        if self.session_id:
            meta["session_id"] = self.session_id
        if self.agent_run_id:
            meta["agent_run_id"] = self.agent_run_id
        if self.result_preview:
            meta["result_preview"] = self.result_preview
        meta.update(self.metrics)
        return meta


@dataclass(frozen=True)
class TaskResultVerdict:
    """The outcome of inspecting one Task result: a cut, or why it is not one."""

    cut: Optional[TaskCut] = None
    skip_reason: str = ""
    status: str = ""
    detail: str = ""

    @property
    def unprocessable(self) -> bool:
        """Whether the payload defeated the observer rather than simply passing."""
        return self.skip_reason in UNPROCESSABLE_SKIPS


def _extract_result_text(tool_response: Any) -> Optional[str]:
    """Flatten a Task tool_response into its result text, or None if unreadable.

    The measured shape (325 real Agent results) is a dict whose ``content`` is
    a list of typed blocks; ``result``/``output``/``stdout``/``text`` are
    accepted as flat alternatives other harness versions have used.

    Returns None -- NOT the empty string -- when no known key yields text. The
    distinction is load-bearing: "" means the turn genuinely ended with nothing
    to say, while None means the observer does not understand this payload and
    must not infer a missing fence from a shape it never read.
    """
    if not isinstance(tool_response, Mapping):
        return None

    content = tool_response.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        # An EMPTY list is a read, not a miss: the harness offered its result
        # channel and it held no text. That is a turn which ended saying
        # nothing -- the most severe cut, and two were measured (68 and 80 tool
        # calls, no final message at all). Returning "" keeps it a cut; falling
        # through to None would have downgraded it to "shape not understood".
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, Mapping):
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)

    for key in ("result", "output", "stdout", "text"):
        value = tool_response.get(key)
        if isinstance(value, str) and value:
            return value
    return None


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
    if isinstance(tool_response, str):
        # The harness swaps the result dict for bare error text on a failure it
        # already reports to the caller (``User rejected tool use``, a hook
        # error). Both measured instances were exactly that.
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


def _extract_agent(tool_input: Any, tool_response: Any) -> str:
    """Name the dispatched subagent, from the request or the result."""
    if isinstance(tool_input, Mapping):
        named = tool_input.get("subagent_type") or tool_input.get("agent")
        if named:
            return str(named)
    if isinstance(tool_response, Mapping) and tool_response.get("agentType"):
        return str(tool_response["agentType"])
    return "unknown"


def inspect_task_result(
    tool_input: Any,
    tool_response: Any,
    *,
    session_id: str = "",
) -> TaskResultVerdict:
    """Classify a Task result as a cut, or say why it is not one.

    The cut signature is: the harness reports the turn as ENDED and not failed,
    AND the result it surfaces carries no parseable ``agent_contract_handoff``
    fence. Every other outcome returns a skip code instead of a bare None, so
    the caller can tell an ordinary clean turn apart from a payload the
    observer could not read.

    Args:
        tool_input:    The Task ``tool_input`` dict (source of ``subagent_type``).
        tool_response: The Task ``tool_response``, in any of its harness shapes.
        session_id:    Session the Task ran under, recorded on the event.
    """
    status = _extract_status(tool_response)
    normalized = status.strip().lower()

    if _reports_visible_failure(tool_response, status):
        return TaskResultVerdict(skip_reason=SKIP_VISIBLE_FAILURE, status=status)
    if normalized in _PENDING_STATUSES:
        return TaskResultVerdict(skip_reason=SKIP_TURN_NOT_ENDED, status=status)
    if normalized not in _TERMINAL_STATUSES:
        return TaskResultVerdict(
            skip_reason=SKIP_UNKNOWN_STATUS,
            status=status,
            detail=f"status={status!r} is neither terminal nor pending",
        )

    result_text = _extract_result_text(tool_response)
    if result_text is None:
        keys = sorted(tool_response) if isinstance(tool_response, Mapping) else []
        return TaskResultVerdict(
            skip_reason=SKIP_UNREADABLE_RESULT,
            status=status,
            detail=f"no result text in {type(tool_response).__name__} keys={keys[:12]}",
        )

    if _parse_contract(result_text) is not None:
        return TaskResultVerdict(skip_reason=SKIP_CONTRACT_PRESENT, status=status)

    reason = REASON_UNPARSEABLE_FENCE if _CONTRACT_TAG in result_text else REASON_NO_FENCE
    return TaskResultVerdict(
        cut=TaskCut(
            agent=_extract_agent(tool_input, tool_response),
            status=status,
            reason=reason,
            metrics=_extract_metrics(tool_response),
            result_preview=result_text[-_PREVIEW_CHARS:].strip(),
            session_id=session_id,
            agent_run_id=str(tool_response.get("agentId") or "")
            if isinstance(tool_response, Mapping)
            else "",
        ),
        status=status,
    )


def detect_task_cut(
    tool_input: Any,
    tool_response: Any,
    *,
    session_id: str = "",
) -> Optional[TaskCut]:
    """Return a :class:`TaskCut` when the payload matches the cut signature.

    Thin wrapper over :func:`inspect_task_result` for callers that only need
    the verdict, not the reason a result was skipped.
    """
    return inspect_task_result(
        tool_input, tool_response, session_id=session_id
    ).cut


def _trace(hook_data: Mapping[str, Any], **fields: Any) -> None:
    """Append one observer line to the hook trace. Never raises.

    The observer is non-blocking by contract, which previously meant a write
    that failed left no evidence anywhere -- indistinguishable from a cut that
    never happened. The trace is where that evidence goes now.
    """
    try:
        from ..core.hook_trace import record_hook_invocation

        record_hook_invocation(
            "task_result_observer",
            payload=hook_data,
            extra={k: v for k, v in fields.items() if v not in ("", None)},
        )
    except Exception:  # pragma: no cover - tracing must never disturb the hook
        pass


def observe_task_result(hook_data: Mapping[str, Any]) -> Optional[TaskCut]:
    """Detect a cut in a PostToolUse Task/Agent payload and record it.

    Non-blocking by contract: the event write is best-effort and any failure is
    swallowed. It is not, however, SILENT -- a payload the observer cannot
    process and a write that fails both leave a line in the hook trace.

    Returns:
        The detected TaskCut (whether or not the event write succeeded), or
        None when the payload does not match the signature.
    """
    verdict = inspect_task_result(
        hook_data.get("tool_input", {}),
        hook_data.get("tool_response", {}),
        session_id=str(hook_data.get("session_id", "") or ""),
    )

    if verdict.cut is None:
        if verdict.unprocessable:
            logger.warning(
                "Task result not processable: %s (%s)", verdict.skip_reason, verdict.detail,
            )
            _trace(
                hook_data,
                observer="unprocessable",
                skip=verdict.skip_reason,
                detail=verdict.detail,
            )
        return None

    cut = verdict.cut
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
    except Exception as exc:
        logger.warning("agent.cut event write failed (non-fatal): %s", exc)
        _trace(
            hook_data,
            observer="write_failed",
            reason=cut.reason,
            detail=f"{type(exc).__name__}: {exc}",
        )
        return cut

    _trace(hook_data, observer=AGENT_CUT_EVENT, reason=cut.reason, cut_agent=cut.agent)
    return cut


__all__ = [
    "AGENT_CUT_EVENT",
    "REASON_NO_FENCE",
    "REASON_UNPARSEABLE_FENCE",
    "SKIP_CONTRACT_PRESENT",
    "SKIP_TURN_NOT_ENDED",
    "SKIP_UNKNOWN_STATUS",
    "SKIP_UNREADABLE_RESULT",
    "SKIP_VISIBLE_FAILURE",
    "TASK_TOOL_NAMES",
    "UNPROCESSABLE_SKIPS",
    "TaskCut",
    "TaskResultVerdict",
    "detect_task_cut",
    "inspect_task_result",
    "observe_task_result",
]
