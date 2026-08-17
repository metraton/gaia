"""Detect a harness-truncated subagent turn from the ORCHESTRATOR side.

When the harness cuts a subagent mid-turn, nothing downstream of the subagent
runs: ``SubagentStop`` never fires, so no ``episodes`` row and no
``harness_events`` row is written, and the turn leaves no trace at all. The one
place the cut IS observable is where the parent receives the Task result: the
harness closes the Task reporting a non-error status, and surfaces as the
result a STALE text block -- the last text the model happened to emit before
the cut.

THE SIGNATURE IS THE PERSISTED ROW, NOT THE MESSAGE TEXT. It used to be the
absence of a fenced ``agent_contract_handoff`` block in that text; the fence is
being retired as a delivery channel, so keying on its absence would have
reclassified every successful turn as cut. What a turn that reached its own end
leaves behind is a contract row that EXISTS and is FINALIZED, addressed by the
harness-minted run id the result itself carries (``agentId``) -- the same
coordinate ``gaia contract view --harness-id`` reads by, resolved through the
one bridge that knows both identifier spaces
(``handoff_persister.dispatch_row_by_harness_id``).

Persistence is a separate, non-blocking step (``observe_task_result``). The
row read is injectable (``inspect_task_result(..., row_state=...)``) so the
signature stays testable in both directions without a database.

The harness names this tool ``Agent``; ``Task`` is its former name, still
honored as a hooks.json matcher but no longer what the payload carries. Both
names are accepted (``TASK_TOOL_NAMES``) so the dispatch does not depend on
which one a given harness version reports.

Gotchas:
- A Task the harness itself reports as errored, cancelled, or interrupted is
  NOT this signature -- that failure is already visible to the caller. Only the
  silent one is recorded here.
- Nor is a BACKGROUND dispatch: ``run_in_background`` returns immediately with
  ``status="async_launched"`` and an ``outputFile``. Measured over 325 real
  Agent results, that form is 156 of them -- reading it as a cut would bury the
  true signal under ~48% false positives.
- Detection requires the harness run id. A result that carries none, and a row
  lookup that cannot resolve to exactly one row, are both reported as
  unprocessable (traced) rather than read as "no row": a missing row is
  evidence of a cut only when the row is something we could have found.
- A finalize still in flight is tolerated rather than read as a cut -- see
  :func:`_await_finalization` for the measurement that sizes the window.
- The metrics (``totalDurationMs`` / ``totalTokens`` / ``totalToolUseCount``)
  are copied verbatim from the result. They are the only quantitative record
  the cut leaves behind, since the episode that would have carried them was
  never written.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

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
REASON_NO_CONTRACT_ROW = "no_contract_row"
REASON_ROW_NEVER_FINALIZED = "contract_row_never_finalized"

# Why a result was NOT recorded as a cut. The first three are ordinary
# outcomes; the last three mean the payload or the row could not be read at all
# and are the ones surfaced to the hook trace.
SKIP_VISIBLE_FAILURE = "visible_failure"
SKIP_TURN_NOT_ENDED = "turn_not_ended"
SKIP_CONTRACT_FINALIZED = "contract_row_finalized"
SKIP_UNKNOWN_STATUS = "unknown_task_status"
SKIP_NO_AGENT_RUN_ID = "no_agent_run_id"
SKIP_CONTRACT_ROW_UNRESOLVABLE = "contract_row_unresolvable"

# Skip codes that mean "the observer could not process this payload". They are
# traced so a harness shape change surfaces instead of silently suppressing
# every future detection.
UNPROCESSABLE_SKIPS = frozenset(
    {SKIP_UNKNOWN_STATUS, SKIP_NO_AGENT_RUN_ID, SKIP_CONTRACT_ROW_UNRESOLVABLE}
)

# What the row lookup can answer. Only MISSING and UNFINALIZED are cuts.
ROW_FINALIZED = "finalized"
ROW_UNFINALIZED = "unfinalized"
ROW_MISSING = "missing"
ROW_UNRESOLVABLE = "unresolvable"

# The agent_state a row carries between birth and the agent's own close
# (gaia.store.writer.insert_dispatched_handoff). Every other value is a
# terminal verdict, which is what agent_contract_handoff_finalized keys on too.
_DISPATCHED_STATE = "DISPATCHED"

# How much of the stale result text to keep on the event, in characters. Enough
# to recognize WHICH block the harness surfaced, not enough to bloat the row.
_PREVIEW_CHARS = 400

# Grace window for a finalize still in flight -- see _await_finalization.
#
# NOT calibrated on a fine measurement, and cannot be: both timestamp sources
# this signature has (harness_events, agent_contract_handoffs) record
# whole-second resolution with no sub-second component, coarser than any
# sub-second window -- so no measurement can validate a shorter number over a
# longer one here. Do not shorten this on the impression that it "looks long";
# that impression is not backed by data that could exist.
#
# Sized instead by cost asymmetry: the wait is paid only on the rare path about
# to record a cut (163 events in two months, never on an ordinary turn), so
# widening it is nearly free, while misreading a clean close as a cut
# contaminates exactly the cut data this signature exists to measure -- so
# under-sizing it is not free at all. That asymmetry, not precision, is why the
# total sits at 2.5s.
#
# The quarter-second interval is unchanged: it re-queries the row directly
# rather than reading a clock, so shortening it loses no information, and it is
# what lets the dominant race (p50 = 0s, see _await_finalization) resolve
# almost immediately. Only the retry count moved, 3 -> 11 (2 -> 10 retries),
# to raise the bound from ~0.5s to 2.5s without slowing the common, fast case.
_FINALIZE_GRACE_ATTEMPTS = 11
_FINALIZE_GRACE_SECONDS = 0.25


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


def _extract_result_text(tool_response: Any) -> str:
    """Flatten a Task tool_response into its result text, "" when unreadable.

    The measured shape (325 real Agent results) is a dict whose ``content`` is
    a list of typed blocks; ``result``/``output``/``stdout``/``text`` are
    accepted as flat alternatives other harness versions have used.

    Text no longer decides anything -- the row does. This feeds only
    ``TaskCut.result_preview``, so a shape yielding nothing costs a preview,
    never a verdict.
    """
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


def _extract_agent_run_id(tool_response: Any) -> str:
    """The harness-minted run id of the dispatch, or "" when absent."""
    if not isinstance(tool_response, Mapping):
        return ""
    return str(tool_response.get("agentId") or "")


# Evidence-report list fields, same canonical order as
# gaia.contract.drafts.initial_envelope, so the display order matches the
# order the schema itself declares the fields in.
_EVIDENCE_LIST_FIELDS: Tuple[str, ...] = (
    "patterns_checked", "files_checked", "commands_run", "key_outputs",
    "verbatim_outputs", "cross_layer_impacts", "open_gaps",
)

# Field-read priority for the ONE armed --field command the summary line
# hands back. open_gaps and cross_layer_impacts lead because agent-protocol
# names them as the highest-signal reads (a gap declared gets routed; a
# cross-layer impact is what no other field records); the rest follow in
# reverse canonical order.
_FIELD_READ_PRIORITY: Tuple[str, ...] = (
    "open_gaps", "cross_layer_impacts", "verbatim_outputs", "key_outputs",
    "files_checked", "commands_run", "patterns_checked",
)

# How many populated evidence fields the line names by count before folding
# the rest into a bare "+Nmore" tag. Bounds the line regardless of how many
# of the seven categories a real turn populates.
_MAX_FIELDS_NAMED = 3

# Above this length the field-count segment is dropped and the line falls
# back to state/verification + the bare command. The command is the part
# that must never be truncated -- a cut-off command is not copy-pasteable --
# so length pressure is absorbed by the descriptive segment, never by it.
_LINE_HARD_CEILING = 200


def _populated_evidence_fields(evidence: Mapping[str, Any]) -> List[Tuple[str, int]]:
    """[(field_name, count), ...] for every NON-EMPTY evidence_report list field.

    Canonical order (``_EVIDENCE_LIST_FIELDS``); a field absent, null, or an
    empty list is not named at all -- naming only what there IS to read is
    the whole value of the line (agent-protocol principle 5).
    """
    out: List[Tuple[str, int]] = []
    for name in _EVIDENCE_LIST_FIELDS:
        value = evidence.get(name)
        if isinstance(value, list) and value:
            out.append((name, len(value)))
    return out


def _choose_read_field(evidence: Mapping[str, Any], report_prose: str) -> Optional[str]:
    """The single dotted-path field the armed command reads.

    Walks ``_FIELD_READ_PRIORITY`` for the first populated evidence_report
    list field; falls back to the top-level ``report_prose`` (never nested
    under evidence_report -- see TOP_LEVEL_FIELD_TYPES in
    gaia.contract.validator) when no evidence list is populated; returns
    None when the row genuinely has neither, so the caller omits --field
    rather than name a field with nothing in it.
    """
    for name in _FIELD_READ_PRIORITY:
        value = evidence.get(name)
        if isinstance(value, list) and value:
            return f"evidence_report.{name}"
    if report_prose:
        return "report_prose"
    return None


def build_contract_summary_line(
    tool_response: Any,
    *,
    session_id: str = "",
    row_lookup: Optional[Callable[[dict, Optional[str]], Optional[Mapping[str, Any]]]] = None,
) -> Optional[str]:
    """One dense line naming a closed row's populated evidence + a ready read command.

    Returns None -- never a misleading line -- for every case where the row
    is not resolvable as a CLOSED contract: no agentId on the result, no row
    found, a row still DISPATCHED (never finalized -- the harness-cut
    signature this module already records separately), an unparseable
    envelope, or a lookup failure. Silence is the honest degrade: a line
    that guesses "nothing to read" when the row simply could not be read is
    worse than no line at all.

    Args:
        tool_response: The Task/Agent ``tool_response`` (source of ``agentId``).
        session_id:    Session the dispatch ran under, passed to the row bridge
            as a consistency check (see ``dispatch_row_by_harness_id``).
        row_lookup:    Row resolver, ``(task_info, session_id) -> row | None``.
            Defaults to ``handoff_persister.dispatch_row_by_harness_id`` --
            the SAME bridge ``contract_row_state`` uses, so this reads the
            identical row the cut-detection path already resolves. Tests
            substitute it to exercise both directions without a database.
    """
    agent_run_id = _extract_agent_run_id(tool_response)
    if not agent_run_id:
        return None

    lookup = row_lookup
    if lookup is None:
        from .handoff_persister import dispatch_row_by_harness_id

        lookup = dispatch_row_by_harness_id

    try:
        row = lookup({"agent_id": agent_run_id}, session_id or None)
    except Exception:
        return None
    if not row:
        return None

    state = str(row.get("agent_state") or "")
    if not state or state == _DISPATCHED_STATE:
        return None

    try:
        envelope = json.loads(row.get("raw_handoff_json") or "null")
    except (TypeError, ValueError):
        return None
    if not isinstance(envelope, dict):
        return None

    evidence = envelope.get("evidence_report")
    evidence = evidence if isinstance(evidence, dict) else {}
    report_prose = envelope.get("report_prose")
    report_prose = report_prose if isinstance(report_prose, str) else ""

    populated = _populated_evidence_fields(evidence)
    verification = evidence.get("verification")
    verification_result = (
        verification.get("result") if isinstance(verification, dict) else None
    )

    field_terms = [f"{name}({count})" for name, count in populated[:_MAX_FIELDS_NAMED]]
    if len(populated) > _MAX_FIELDS_NAMED:
        field_terms.append(f"+{len(populated) - _MAX_FIELDS_NAMED}more")
    fields_segment = ", ".join(field_terms)

    core = f"{agent_run_id}: state={state}"
    if isinstance(verification_result, str) and verification_result:
        core += f", verification={verification_result}"

    read_field = _choose_read_field(evidence, report_prose)
    cmd = (
        f"gaia contract view --harness-id {agent_run_id} --field {read_field}"
        if read_field
        else f"gaia contract view --harness-id {agent_run_id}"
    )

    line = f"{core}, {fields_segment} -- {cmd}" if fields_segment else f"{core} -- {cmd}"
    if fields_segment and len(line) > _LINE_HARD_CEILING:
        line = f"{core} -- {cmd}"
    return line


def contract_row_state(agent_run_id: str, session_id: str = "") -> str:
    """Classify the contract row this harness run id addresses.

    Resolution goes through ``handoff_persister.dispatch_row_by_harness_id``,
    the one bridge that joins the harness's per-run id to the minted contract
    identity, so a continuation chain collapses to its live tip instead of
    reading as several rival rows.

    That bridge answers None for two different situations, and only one of them
    is a cut: no row carries the id at all (the turn left nothing behind), or a
    row does but the bridge declined to pick between candidates. The second
    query separates them, and it runs only on the None path.

    Fails toward ROW_UNRESOLVABLE: an unavailable store must degrade to "cannot
    tell", never to a cut recorded against a turn that closed perfectly.
    """
    try:
        from .handoff_persister import dispatch_row_by_harness_id

        row = dispatch_row_by_harness_id(
            {"agent_id": agent_run_id}, session_id or None,
        )
        if row is not None:
            state = str(row.get("agent_state") or "")
            return ROW_UNFINALIZED if state == _DISPATCHED_STATE else ROW_FINALIZED

        from gaia.store.writer import list_agent_contract_handoffs

        rows = list_agent_contract_handoffs(harness_agent_id=agent_run_id, limit=2)
        return ROW_UNRESOLVABLE if rows else ROW_MISSING
    except Exception as exc:
        logger.debug("Contract-row lookup failed for run id %s: %s", agent_run_id, exc)
        return ROW_UNRESOLVABLE


def _await_finalization(
    lookup: Callable[[str, str], str], agent_run_id: str, session_id: str,
) -> str:
    """Read the row state, tolerating a finalize that has not landed yet.

    MEASURED over the 163 real ``agent.cut`` events, against the rows their
    ``agent_run_id`` addresses: of the 52 whose row had reached a terminal
    state, 47 carry ``cut_reason IS NULL`` -- the agent finalized ITSELF with
    the CLI, a tool call inside its own turn and therefore strictly before the
    Task result could reach the parent. That dominant path cannot race.

    The remaining 5 (3 reaped, 2 salvaged) were finalized by the SubagentStop
    machinery instead, and the ordering there is NOT established: comparing
    each cut event against the ``agent.complete`` of the same turn puts the two
    hooks in the SAME SECOND for 21 of 35 unambiguous pairs (p50 = 0s). So the
    race is real, confined to backstop-finalized turns, and answered by
    re-reading rather than by assuming an order the harness does not promise.
    """
    state = lookup(agent_run_id, session_id)
    for _ in range(_FINALIZE_GRACE_ATTEMPTS - 1):
        if state in (ROW_FINALIZED, ROW_UNRESOLVABLE):
            break
        time.sleep(_FINALIZE_GRACE_SECONDS)
        state = lookup(agent_run_id, session_id)
    return state


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
    row_state: Optional[Callable[[str, str], str]] = None,
) -> TaskResultVerdict:
    """Classify a Task result as a cut, or say why it is not one.

    The cut signature is: the harness reports the turn as ENDED and not failed,
    AND the contract row its ``agentId`` addresses either does not exist or
    never left ``DISPATCHED``. Every other outcome returns a skip code instead
    of a bare None, so the caller can tell an ordinary clean turn apart from a
    payload the observer could not read.

    Args:
        tool_input:    The Task ``tool_input`` dict (source of ``subagent_type``).
        tool_response: The Task ``tool_response``, in any of its harness shapes.
        session_id:    Session the Task ran under, recorded on the event.
        row_state:     Row lookup, ``(agent_run_id, session_id) -> ROW_*``.
            Defaults to :func:`contract_row_state`; tests substitute it to
            exercise both directions of the signature without a database.
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

    agent_run_id = _extract_agent_run_id(tool_response)
    if not agent_run_id:
        keys = sorted(tool_response) if isinstance(tool_response, Mapping) else []
        return TaskResultVerdict(
            skip_reason=SKIP_NO_AGENT_RUN_ID,
            status=status,
            detail=f"no agentId in {type(tool_response).__name__} keys={keys[:12]}",
        )

    state = _await_finalization(
        row_state or contract_row_state, agent_run_id, session_id,
    )
    if state == ROW_FINALIZED:
        return TaskResultVerdict(skip_reason=SKIP_CONTRACT_FINALIZED, status=status)
    if state == ROW_UNRESOLVABLE:
        return TaskResultVerdict(
            skip_reason=SKIP_CONTRACT_ROW_UNRESOLVABLE,
            status=status,
            detail=f"agentId={agent_run_id} resolves no single contract row",
        )

    result_text = _extract_result_text(tool_response)
    reason = (
        REASON_NO_CONTRACT_ROW if state == ROW_MISSING else REASON_ROW_NEVER_FINALIZED
    )
    return TaskResultVerdict(
        cut=TaskCut(
            agent=_extract_agent(tool_input, tool_response),
            status=status,
            reason=reason,
            metrics=_extract_metrics(tool_response),
            result_preview=result_text[-_PREVIEW_CHARS:].strip(),
            session_id=session_id,
            agent_run_id=agent_run_id,
        ),
        status=status,
    )


def detect_task_cut(
    tool_input: Any,
    tool_response: Any,
    *,
    session_id: str = "",
    row_state: Optional[Callable[[str, str], str]] = None,
) -> Optional[TaskCut]:
    """Return a :class:`TaskCut` when the payload matches the cut signature.

    Thin wrapper over :func:`inspect_task_result` for callers that only need
    the verdict, not the reason a result was skipped.
    """
    return inspect_task_result(
        tool_input, tool_response, session_id=session_id, row_state=row_state,
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
            f"subagent cut mid-turn ({cut.reason}); no finalized contract row "
            f"for its harness run id",
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
    "REASON_NO_CONTRACT_ROW",
    "REASON_ROW_NEVER_FINALIZED",
    "ROW_FINALIZED",
    "ROW_MISSING",
    "ROW_UNFINALIZED",
    "ROW_UNRESOLVABLE",
    "SKIP_CONTRACT_FINALIZED",
    "SKIP_CONTRACT_ROW_UNRESOLVABLE",
    "SKIP_NO_AGENT_RUN_ID",
    "SKIP_TURN_NOT_ENDED",
    "SKIP_UNKNOWN_STATUS",
    "SKIP_VISIBLE_FAILURE",
    "TASK_TOOL_NAMES",
    "UNPROCESSABLE_SKIPS",
    "TaskCut",
    "TaskResultVerdict",
    "build_contract_summary_line",
    "contract_row_state",
    "detect_task_cut",
    "inspect_task_result",
    "observe_task_result",
]
