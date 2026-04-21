"""Graders for the eval framework (T1 scaffold).

Three graders are planned:

- :func:`code_grader` -- v1-style keyword match (``expect_present`` /
  ``expect_absent``). Implemented here for T1 so downstream tasks
  (T3a tests) can exercise it immediately.
- :func:`contract_grader` -- parses the fenced ``json:contract`` block
  and validates shape. Implemented in T3b.
- :func:`tool_trace_grader` -- inspects session JSONL and audit slices
  for tool-call ordering / presence / absence. **Stub** in T1;
  implemented in T3c.

All graders return a :class:`GradeResult`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional


@dataclass(frozen=True)
class GradeResult:
    """Outcome of grading a single case.

    Attributes:
        passed: Overall pass/fail flag. For semantic graders this means
            ``score >= threshold``; for binary graders it is ``score == 1.0``.
        score: Normalized score in ``[0.0, 1.0]``.
        reasons: Human-readable justifications -- one per check -- that
            explain why the result was pass or fail. Downstream reporters
            render this list verbatim.
    """

    passed: bool
    score: float
    reasons: list[str] = field(default_factory=list)


def code_grader(
    response: str,
    expect_present: Optional[Iterable[str]] = None,
    expect_absent: Optional[Iterable[str]] = None,
) -> GradeResult:
    """Keyword-match grader.

    Scoring: ``score = matched / total`` where ``total`` is the count of
    non-empty keyword lists considered. Passes when every keyword in
    ``expect_present`` appears in ``response`` and no keyword in
    ``expect_absent`` appears. Matching is case-sensitive and substring-based
    (no regex, no tokenization) -- callers should pass the exact string they
    expect.

    Args:
        response: Captured agent response (stdout).
        expect_present: Keywords that must appear in ``response``.
        expect_absent: Keywords that must NOT appear in ``response``.

    Returns:
        :class:`GradeResult` with per-keyword reasons.
    """
    present = list(expect_present or [])
    absent = list(expect_absent or [])

    reasons: list[str] = []
    matched = 0
    total = 0

    if present:
        total += 1
        missing = [kw for kw in present if kw not in response]
        if missing:
            reasons.append(f"expect_present missing: {missing}")
        else:
            reasons.append(f"expect_present all found ({len(present)} keyword(s))")
            matched += 1

    if absent:
        total += 1
        leaked = [kw for kw in absent if kw in response]
        if leaked:
            reasons.append(f"expect_absent leaked: {leaked}")
        else:
            reasons.append(f"expect_absent none found ({len(absent)} keyword(s))")
            matched += 1

    if total == 0:
        # No constraints requested -- treat as trivially passing.
        return GradeResult(passed=True, score=1.0, reasons=["no keyword constraints"])

    score = matched / total
    return GradeResult(passed=matched == total, score=score, reasons=reasons)


# ---------------------------------------------------------------------------
# contract_grader (T3b)
# ---------------------------------------------------------------------------

# Fenced block matching the agent-protocol spec. We match the LAST such block
# in the response (agents sometimes show example contracts earlier in their
# narrative; the operative one is always at the tail).
_CONTRACT_BLOCK_RE = re.compile(
    r"```json:contract\s*\n(.*?)```",
    re.DOTALL,
)

_REQUIRED_TOP_KEYS = (
    "agent_status",
    "evidence_report",
    "consolidation_report",
    "approval_request",
)

_VALID_PLAN_STATUSES = frozenset(
    {"IN_PROGRESS", "APPROVAL_REQUEST", "COMPLETE", "BLOCKED", "NEEDS_INPUT"}
)

# When plan_status == APPROVAL_REQUEST, approval_request must carry at least
# these fields (subset of the full protocol list that we treat as load-bearing
# for the grader -- the runtime hook is the real gate for the remaining ones).
_APPROVAL_REQUEST_REQUIRED_FIELDS = ("operation", "exact_content", "risk_level")


def _extract_last_contract_block(response: str) -> Optional[str]:
    """Return the raw JSON text of the LAST ```json:contract fenced block.

    Returns ``None`` when no fenced block is present. The returned string is
    the payload between the opening fence and the closing ``` (trailing
    whitespace preserved -- ``json.loads`` tolerates it).
    """
    matches = _CONTRACT_BLOCK_RE.findall(response)
    if not matches:
        return None
    return matches[-1]


def contract_grader(
    response: str,
    contract_expect: Optional[dict] = None,
) -> GradeResult:
    """Validate the fenced ``json:contract`` block shape.

    Binary grader -- every check must pass. Checks performed in order:

    1. A fenced ``json:contract`` block exists in ``response``.
    2. Its body parses as JSON.
    3. All four required top-level keys are present: ``agent_status``,
       ``evidence_report``, ``consolidation_report``, ``approval_request``
       (values may be ``null`` where the protocol allows it, but the keys
       themselves must be declared).
    4. ``agent_status.plan_status`` is one of the five canonical states:
       ``IN_PROGRESS``, ``APPROVAL_REQUEST``, ``COMPLETE``, ``BLOCKED``,
       ``NEEDS_INPUT``.
    5. When ``plan_status == "APPROVAL_REQUEST"``, the ``approval_request``
       object must be a dict carrying at minimum ``operation``,
       ``exact_content``, and ``risk_level``.
    6. When ``contract_expect["plan_status"]`` is set, the observed
       ``plan_status`` must equal that value. Use the catalog's
       ``contract_expect`` to pin S6 to ``APPROVAL_REQUEST``; leave it
       absent (or ``None``) for "any valid status" scenarios like S5.

    Args:
        response: Captured agent response (stdout / final message).
        contract_expect: Optional per-case expectations. Supported key:
            ``plan_status`` -- expected plan_status string.

    Returns:
        :class:`GradeResult` with ``passed`` True only when every check
        succeeds, ``score`` in ``{0.0, 1.0}``, and one reason per check.
    """
    expect = contract_expect or {}
    reasons: list[str] = []

    raw = _extract_last_contract_block(response)
    if raw is None:
        return GradeResult(
            passed=False,
            score=0.0,
            reasons=["no json:contract fenced block found"],
        )
    reasons.append("json:contract fenced block found")

    try:
        contract = json.loads(raw)
    except json.JSONDecodeError as exc:
        return GradeResult(
            passed=False,
            score=0.0,
            reasons=reasons + [f"json:contract body is not valid JSON: {exc.msg}"],
        )
    reasons.append("json:contract body parses as JSON")

    if not isinstance(contract, dict):
        return GradeResult(
            passed=False,
            score=0.0,
            reasons=reasons + [f"json:contract body is not an object (got {type(contract).__name__})"],
        )

    missing_top = [k for k in _REQUIRED_TOP_KEYS if k not in contract]
    if missing_top:
        return GradeResult(
            passed=False,
            score=0.0,
            reasons=reasons + [f"missing required top-level keys: {missing_top}"],
        )
    reasons.append(f"all required top-level keys present ({len(_REQUIRED_TOP_KEYS)})")

    agent_status = contract.get("agent_status")
    if not isinstance(agent_status, dict):
        return GradeResult(
            passed=False,
            score=0.0,
            reasons=reasons + ["agent_status must be an object"],
        )

    plan_status = agent_status.get("plan_status")
    if not isinstance(plan_status, str) or plan_status not in _VALID_PLAN_STATUSES:
        return GradeResult(
            passed=False,
            score=0.0,
            reasons=reasons + [
                f"plan_status {plan_status!r} not in {sorted(_VALID_PLAN_STATUSES)}"
            ],
        )
    reasons.append(f"plan_status={plan_status} is valid")

    if plan_status == "APPROVAL_REQUEST":
        approval = contract.get("approval_request")
        if not isinstance(approval, dict):
            return GradeResult(
                passed=False,
                score=0.0,
                reasons=reasons + [
                    "plan_status=APPROVAL_REQUEST but approval_request is not an object"
                ],
            )
        missing_approval = [
            f for f in _APPROVAL_REQUEST_REQUIRED_FIELDS if not approval.get(f)
        ]
        if missing_approval:
            return GradeResult(
                passed=False,
                score=0.0,
                reasons=reasons + [
                    f"approval_request missing required fields: {missing_approval}"
                ],
            )
        reasons.append("approval_request carries operation, exact_content, risk_level")

    expected_status = expect.get("plan_status")
    if expected_status is not None and plan_status != expected_status:
        return GradeResult(
            passed=False,
            score=0.0,
            reasons=reasons + [
                f"plan_status mismatch: expected {expected_status!r}, got {plan_status!r}"
            ],
        )
    if expected_status is not None:
        reasons.append(f"plan_status matches contract_expect ({expected_status})")

    return GradeResult(passed=True, score=1.0, reasons=reasons)


# ---------------------------------------------------------------------------
# tool_trace_grader (T3c)
# ---------------------------------------------------------------------------

# DSL keys understood by :func:`tool_trace_grader`. Unknown keys raise so
# catalog typos fail loudly instead of silently passing.
_TRACE_EXPECT_KEYS = frozenset({
    "must_contain",
    "must_not_contain",
    "ordering",
    "delegated_to",
})

# Parameter name carrying the file path for path-bearing tools. Mirrors
# Claude Code's tool_input schema for Read / Edit / Write / Glob.
_PATH_PARAM_KEYS = ("file_path", "path", "notebook_path")


@dataclass(frozen=True)
class _TraceCall:
    """Normalised view of one tool invocation drawn from audit or session.

    ``tool`` is the tool name (``"Read"``, ``"Edit"``, ``"Bash"``,
    ``"Agent"``, ...). ``params`` is the raw tool_input dict as recorded
    by the source. ``timestamp`` is the ISO-ish string the source
    produced; ordering uses string compare which is correct for the
    ``YYYY-MM-DDTHH:MM:SS...`` shape both extractors emit.
    """

    tool: str
    params: dict
    timestamp: str


def _call_path(call: _TraceCall) -> str:
    """Return the file path bound to a call, or ``""`` when absent."""
    for key in _PATH_PARAM_KEYS:
        value = call.params.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _call_command(call: _TraceCall) -> str:
    """Return the command string for Bash calls, or ``""`` otherwise."""
    value = call.params.get("command")
    return value if isinstance(value, str) else ""


def _call_subagent(call: _TraceCall) -> str:
    """Return the ``subagent_type`` for Agent calls, or ``""`` otherwise."""
    value = call.params.get("subagent_type")
    return value if isinstance(value, str) else ""


def _load_audit_calls(audit_paths: Iterable[Path]) -> list[_TraceCall]:
    """Parse audit JSONL files via the shared extractor.

    Delegates to ``tools.gaia_simulator.extractor.LogExtractor`` so the
    grader stays consistent with replay tests. The extractor emits
    :class:`ReplayEvent` with ``tool_input`` nested inside
    ``stdin_payload``; we flatten that here into :class:`_TraceCall` for
    a smaller surface downstream.
    """
    # Lazy import: keeps graders importable in environments that only run
    # keyword-match graders (e.g. unit tests for ``code_grader`` on a CI
    # node without the ``tools/`` sibling tree).
    import sys

    tools_dir = Path(__file__).resolve().parents[2] / "tools"
    if str(tools_dir) not in sys.path:
        sys.path.insert(0, str(tools_dir))
    from gaia_simulator.extractor import LogExtractor  # noqa: E402

    extractor = LogExtractor()
    calls: list[_TraceCall] = []
    for path in audit_paths:
        for event in extractor.extract_from_audit_jsonl(path):
            tool_input = event.stdin_payload.get("tool_input") or {}
            if not isinstance(tool_input, dict):
                continue
            calls.append(_TraceCall(
                tool=event.tool_name,
                params=tool_input,
                timestamp=event.timestamp,
            ))
    calls.sort(key=lambda c: c.timestamp)
    return calls


def _load_session_calls(session_path: Optional[Path]) -> list[_TraceCall]:
    """Extract tool_use blocks from a Claude Code transcript JSONL.

    Transcript lines are assistant/user message records. Tool invocations
    appear as ``content`` blocks of type ``tool_use`` inside assistant
    messages; each block has ``name`` and ``input`` fields. We leave
    non-tool content alone.
    """
    if session_path is None or not session_path.exists():
        return []

    calls: list[_TraceCall] = []
    for raw in session_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        if record.get("type") != "assistant":
            continue
        message = record.get("message") or {}
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            continue
        timestamp = record.get("timestamp", "") or ""
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "tool_use":
                continue
            name = block.get("name") or ""
            params = block.get("input") or {}
            if not isinstance(name, str) or not isinstance(params, dict):
                continue
            calls.append(_TraceCall(tool=name, params=params, timestamp=timestamp))
    # Session transcripts are already temporally ordered by line, but we
    # sort defensively in case a consumer merges multiple sources.
    calls.sort(key=lambda c: c.timestamp)
    return calls


def _match_call(call: _TraceCall, spec: dict) -> bool:
    """Return True when ``call`` satisfies the DSL ``spec``.

    Spec keys:

    - ``tool`` -- exact tool name match.
    - ``path_matches`` -- regex applied to the call's file path.
    - ``command_matches`` -- regex applied to the call's Bash command.
    - ``subagent_type`` -- exact match on Agent ``subagent_type``.
    - ``subagent_type_in`` -- list; Agent ``subagent_type`` must be one.
    """
    want_tool = spec.get("tool")
    if want_tool is not None and call.tool != want_tool:
        return False

    path_rx = spec.get("path_matches")
    if path_rx is not None:
        if not re.search(path_rx, _call_path(call)):
            return False

    cmd_rx = spec.get("command_matches")
    if cmd_rx is not None:
        if not re.search(cmd_rx, _call_command(call)):
            return False

    want_subagent = spec.get("subagent_type")
    if want_subagent is not None and _call_subagent(call) != want_subagent:
        return False

    subagent_in = spec.get("subagent_type_in")
    if subagent_in is not None:
        if _call_subagent(call) not in list(subagent_in):
            return False

    return True


def _first_matching_index(calls: list[_TraceCall], spec: dict) -> int:
    """Return the index of the first call matching ``spec``, or -1."""
    for idx, call in enumerate(calls):
        if _match_call(call, spec):
            return idx
    return -1


def tool_trace_grader(
    session_path: Optional[Path],
    audit_paths: list[Path],
    trace_expect: dict,
) -> GradeResult:
    """Validate tool-call sequences against the trace DSL.

    Binary grader -- every assertion in ``trace_expect`` must hold.
    Input is drawn from both sources when available: audit JSONL (parsed
    via :mod:`tools.gaia_simulator.extractor`) captures post-tool-use
    records, while the optional session transcript captures tool_use
    blocks from the assistant messages. Both streams are merged and
    sorted by timestamp.

    Supported ``trace_expect`` keys:

    - ``must_contain`` -- list of call-specs; every spec must match at
      least one call in the trace.
    - ``must_not_contain`` -- list of call-specs; no spec may match any
      call in the trace. Used by S7 (no ``|`` in Bash commands) and S6
      (no ``Bash(git push)`` after APPROVAL_REQUEST).
    - ``ordering`` -- list of ``{before, after, ...extra}`` objects. The
      first match for ``before`` must come strictly before the first
      match for ``after``. ``extra`` keys apply to BOTH sides -- e.g.
      ``{"before": "Read", "after": "Edit", "path_matches": "foo\\.py$"}``
      means "a Read of foo.py precedes an Edit of foo.py". Used by S8
      (Read before Edit for same path).
    - ``delegated_to`` -- list of allowed ``subagent_type`` values. At
      least one ``Agent`` call must appear, and every observed
      ``subagent_type`` must be in this list. Used by S4-style traces
      where the orchestrator delegates.

    Call-spec shape (shared across ``must_contain`` / ``must_not_contain``
    / ``ordering``):

    - ``tool`` (str) -- exact tool name, or ``None`` to ignore.
    - ``path_matches`` (regex str) -- applied to the call's file_path.
    - ``command_matches`` (regex str) -- applied to the call's Bash
      command.
    - ``subagent_type`` (str) / ``subagent_type_in`` (list[str]) --
      Agent invocation filters.

    Args:
        session_path: Path to CC session transcript JSONL, or ``None``.
        audit_paths: List of ``audit-YYYY-MM-DD.jsonl`` files from
            ``DispatchResult.audit_paths``. May be empty.
        trace_expect: Per-case expectations dict from the catalog YAML.

    Returns:
        :class:`GradeResult` with ``passed=True`` only when every
        assertion holds, ``score`` in ``{0.0, 1.0}``, and one reason per
        check.
    """
    expect = trace_expect or {}
    reasons: list[str] = []

    unknown = [k for k in expect.keys() if k not in _TRACE_EXPECT_KEYS]
    if unknown:
        return GradeResult(
            passed=False,
            score=0.0,
            reasons=[
                f"trace_expect has unknown keys {unknown}; "
                f"valid set: {sorted(_TRACE_EXPECT_KEYS)}"
            ],
        )

    # Merge both streams: audit JSONL is the canonical source (structured,
    # hook-stamped), session transcript adds live tool_use blocks that
    # may not have been flushed to audit yet.
    calls = _load_audit_calls(audit_paths) + _load_session_calls(session_path)
    calls.sort(key=lambda c: c.timestamp)

    if not expect:
        return GradeResult(
            passed=True,
            score=1.0,
            reasons=[f"no trace constraints; observed {len(calls)} call(s)"],
        )

    # must_contain: every spec must match at least once.
    for spec in expect.get("must_contain", []) or []:
        if not isinstance(spec, dict):
            return GradeResult(
                passed=False,
                score=0.0,
                reasons=reasons + [f"must_contain entry is not a dict: {spec!r}"],
            )
        if _first_matching_index(calls, spec) < 0:
            return GradeResult(
                passed=False,
                score=0.0,
                reasons=reasons + [
                    f"must_contain spec {spec!r} did not match any of "
                    f"{len(calls)} call(s)"
                ],
            )
        reasons.append(f"must_contain spec {spec!r} satisfied")

    # must_not_contain: no spec may match any call.
    for spec in expect.get("must_not_contain", []) or []:
        if not isinstance(spec, dict):
            return GradeResult(
                passed=False,
                score=0.0,
                reasons=reasons + [
                    f"must_not_contain entry is not a dict: {spec!r}"
                ],
            )
        idx = _first_matching_index(calls, spec)
        if idx >= 0:
            offender = calls[idx]
            return GradeResult(
                passed=False,
                score=0.0,
                reasons=reasons + [
                    f"must_not_contain spec {spec!r} matched call "
                    f"{offender.tool}@{offender.timestamp} ({offender.params!r})"
                ],
            )
        reasons.append(f"must_not_contain spec {spec!r} held")

    # ordering: before/after must appear, before must precede after.
    for entry in expect.get("ordering", []) or []:
        if not isinstance(entry, dict):
            return GradeResult(
                passed=False,
                score=0.0,
                reasons=reasons + [f"ordering entry is not a dict: {entry!r}"],
            )
        if "before" not in entry or "after" not in entry:
            return GradeResult(
                passed=False,
                score=0.0,
                reasons=reasons + [
                    f"ordering entry missing 'before' or 'after': {entry!r}"
                ],
            )

        # Shared filters (path_matches, command_matches, subagent_type*)
        # are applied to BOTH sides. The ``tool`` field is split -- the
        # ``before`` call must use the ``before`` tool, the ``after``
        # call must use the ``after`` tool.
        shared = {
            k: v for k, v in entry.items()
            if k not in ("before", "after")
        }
        before_spec = dict(shared)
        before_spec["tool"] = entry["before"]
        after_spec = dict(shared)
        after_spec["tool"] = entry["after"]

        before_idx = _first_matching_index(calls, before_spec)
        after_idx = _first_matching_index(calls, after_spec)

        if before_idx < 0:
            return GradeResult(
                passed=False,
                score=0.0,
                reasons=reasons + [
                    f"ordering 'before' spec {before_spec!r} never matched"
                ],
            )
        if after_idx < 0:
            return GradeResult(
                passed=False,
                score=0.0,
                reasons=reasons + [
                    f"ordering 'after' spec {after_spec!r} never matched"
                ],
            )
        if before_idx >= after_idx:
            return GradeResult(
                passed=False,
                score=0.0,
                reasons=reasons + [
                    f"ordering violated: {entry['before']} at index "
                    f"{before_idx} does not precede {entry['after']} at "
                    f"index {after_idx} (shared filters: {shared!r})"
                ],
            )
        reasons.append(
            f"ordering {entry['before']} -> {entry['after']} holds "
            f"(indices {before_idx} < {after_idx})"
        )

    # delegated_to: at least one Agent call, every subagent_type in set.
    if "delegated_to" in expect:
        allowed = list(expect["delegated_to"] or [])
        agent_calls = [c for c in calls if c.tool == "Agent"]
        if not agent_calls:
            return GradeResult(
                passed=False,
                score=0.0,
                reasons=reasons + [
                    f"delegated_to requires at least one Agent call; "
                    f"none seen in {len(calls)} call(s)"
                ],
            )
        observed = [_call_subagent(c) for c in agent_calls]
        unexpected = [s for s in observed if s not in allowed]
        if unexpected:
            return GradeResult(
                passed=False,
                score=0.0,
                reasons=reasons + [
                    f"delegated_to: observed subagent_type(s) {unexpected} "
                    f"not in allowed set {allowed}"
                ],
            )
        reasons.append(
            f"delegated_to: {len(agent_calls)} Agent call(s) all in {allowed}"
        )

    return GradeResult(passed=True, score=1.0, reasons=reasons)


# ---------------------------------------------------------------------------
# routing_grader (T3d -- paired with runner.RoutingSimBackend)
# ---------------------------------------------------------------------------

# DSL keys understood by :func:`routing_grader`. Any extra keys in
# ``routing_expect`` raise -- this keeps catalog typos loud instead of
# silently passing.
_ROUTING_EXPECT_KEYS = frozenset({
    "primary_agent",
    "primary_agent_in",
    "primary_agent_not",
    "adjacent_contains",
    "adjacent_not",
    "surfaces_contains",
    "min_confidence",
})


def _as_list(value) -> list[str]:
    """Normalise ``value`` to a list of strings (None -> [], str -> [str])."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return list(value)


def routing_grader(
    response: str,
    routing_expect: Optional[dict] = None,
) -> GradeResult:
    """Grade a ``RoutingSimBackend`` response against the routing DSL.

    Binary grader (score in ``{0.0, 1.0}``) -- every assertion must
    hold. The paired backend (``runner.RoutingSimBackend``) writes a
    JSON-serialised :class:`RoutingResult` to ``stdout``; this grader
    parses that JSON and validates the recorded agent / surface /
    confidence against ``routing_expect``.

    Supported ``routing_expect`` keys:

    - ``primary_agent`` -- exact match (str). The agent the orchestrator
      must select.
    - ``primary_agent_in`` -- ``list[str]``. ``primary_agent`` must be
      one of these. Satisfies S4's "deflect to gitops-operator or
      cloud-troubleshooter" shape.
    - ``primary_agent_not`` -- ``list[str]``. ``primary_agent`` must NOT
      be any of these. Satisfies S4's "must NOT stay with
      gaia-orchestrator" shape.
    - ``adjacent_contains`` -- ``list[str]``. Every listed agent must
      appear in ``adjacent_agents``.
    - ``adjacent_not`` -- ``list[str]``. No listed agent may appear in
      ``adjacent_agents``.
    - ``surfaces_contains`` -- ``list[str]``. Every listed surface must
      appear in ``surfaces_active``.
    - ``min_confidence`` -- ``float``. The router's confidence must be
      ``>=`` this value.

    Args:
        response: ``DispatchResult.stdout`` produced by
            ``RoutingSimBackend``. Expected to be a JSON object
            containing ``primary_agent``, ``adjacent_agents``,
            ``surfaces_active``, and ``confidence``.
        routing_expect: Per-case expectations dict from the catalog
            YAML.

    Returns:
        :class:`GradeResult` with ``passed`` True only when every
        assertion in ``routing_expect`` holds.
    """

    expect = routing_expect or {}
    reasons: list[str] = []

    unknown_keys = [k for k in expect.keys() if k not in _ROUTING_EXPECT_KEYS]
    if unknown_keys:
        return GradeResult(
            passed=False,
            score=0.0,
            reasons=[
                f"routing_expect has unknown keys {unknown_keys}; "
                f"valid set: {sorted(_ROUTING_EXPECT_KEYS)}"
            ],
        )

    try:
        payload = json.loads(response) if response else {}
    except json.JSONDecodeError as exc:
        return GradeResult(
            passed=False,
            score=0.0,
            reasons=[f"routing response is not valid JSON: {exc.msg}"],
        )

    if not isinstance(payload, dict):
        return GradeResult(
            passed=False,
            score=0.0,
            reasons=[
                f"routing response must be a JSON object, got {type(payload).__name__}"
            ],
        )

    primary_agent = payload.get("primary_agent", "")
    adjacent_agents = payload.get("adjacent_agents") or []
    surfaces_active = payload.get("surfaces_active") or []
    confidence = payload.get("confidence", 0.0)

    if not expect:
        return GradeResult(
            passed=True,
            score=1.0,
            reasons=[f"no routing constraints; observed primary={primary_agent!r}"],
        )

    # primary_agent (exact)
    if "primary_agent" in expect:
        want = expect["primary_agent"]
        if primary_agent != want:
            return GradeResult(
                passed=False,
                score=0.0,
                reasons=reasons + [
                    f"primary_agent mismatch: expected {want!r}, got {primary_agent!r}"
                ],
            )
        reasons.append(f"primary_agent == {want!r}")

    # primary_agent_in
    if "primary_agent_in" in expect:
        allowed = _as_list(expect["primary_agent_in"])
        if primary_agent not in allowed:
            return GradeResult(
                passed=False,
                score=0.0,
                reasons=reasons + [
                    f"primary_agent {primary_agent!r} not in allowed set {allowed}"
                ],
            )
        reasons.append(f"primary_agent {primary_agent!r} in {allowed}")

    # primary_agent_not
    if "primary_agent_not" in expect:
        blocked = _as_list(expect["primary_agent_not"])
        if primary_agent in blocked:
            return GradeResult(
                passed=False,
                score=0.0,
                reasons=reasons + [
                    f"primary_agent {primary_agent!r} in forbidden set {blocked}"
                ],
            )
        reasons.append(f"primary_agent {primary_agent!r} not in {blocked}")

    # adjacent_contains
    if "adjacent_contains" in expect:
        required = _as_list(expect["adjacent_contains"])
        missing = [a for a in required if a not in adjacent_agents]
        if missing:
            return GradeResult(
                passed=False,
                score=0.0,
                reasons=reasons + [
                    f"adjacent_agents missing required entries: {missing} "
                    f"(observed: {list(adjacent_agents)})"
                ],
            )
        reasons.append(f"adjacent_agents contains all of {required}")

    # adjacent_not
    if "adjacent_not" in expect:
        forbidden = _as_list(expect["adjacent_not"])
        leaked = [a for a in forbidden if a in adjacent_agents]
        if leaked:
            return GradeResult(
                passed=False,
                score=0.0,
                reasons=reasons + [
                    f"adjacent_agents contains forbidden entries: {leaked}"
                ],
            )
        reasons.append(f"adjacent_agents excludes {forbidden}")

    # surfaces_contains
    if "surfaces_contains" in expect:
        required = _as_list(expect["surfaces_contains"])
        missing = [s for s in required if s not in surfaces_active]
        if missing:
            return GradeResult(
                passed=False,
                score=0.0,
                reasons=reasons + [
                    f"surfaces_active missing required entries: {missing} "
                    f"(observed: {list(surfaces_active)})"
                ],
            )
        reasons.append(f"surfaces_active contains all of {required}")

    # min_confidence
    if "min_confidence" in expect:
        threshold = expect["min_confidence"]
        if not isinstance(threshold, (int, float)):
            return GradeResult(
                passed=False,
                score=0.0,
                reasons=reasons + [
                    f"min_confidence must be numeric, got {type(threshold).__name__}"
                ],
            )
        try:
            observed = float(confidence)
        except (TypeError, ValueError):
            return GradeResult(
                passed=False,
                score=0.0,
                reasons=reasons + [
                    f"confidence {confidence!r} is not numeric"
                ],
            )
        if observed < float(threshold):
            return GradeResult(
                passed=False,
                score=0.0,
                reasons=reasons + [
                    f"confidence {observed} below min_confidence {threshold}"
                ],
            )
        reasons.append(f"confidence {observed} >= {threshold}")

    return GradeResult(passed=True, score=1.0, reasons=reasons)


# ---------------------------------------------------------------------------
# skill_injection_consumer (T4 -- S7 backend, gap G5)
# ---------------------------------------------------------------------------

# DSL keys understood by :func:`skill_injection_consumer`. Unknown keys raise
# so catalog typos fail loudly instead of silently passing.
_ANOMALY_EXPECT_KEYS = frozenset({
    "anomaly_type",
    "skill",
    "present",
})

# Anomaly ``type`` values that ``hooks/modules/agents/skill_injection_verifier``
# (and related validators) emit. The catalog uses ``skill_injection_anomaly``
# as an umbrella label; the verifier itself stamps ``skill_injection_gap``.
# Both are accepted so the consumer survives an eventual rename.
_SKILL_INJECTION_TYPE_ALIASES: dict[str, frozenset[str]] = {
    "skill_injection_anomaly": frozenset({
        "skill_injection_anomaly",
        "skill_injection_gap",
    }),
    "skill_injection_gap": frozenset({
        "skill_injection_gap",
        "skill_injection_anomaly",
    }),
}


def _iter_anomalies(audit_paths: Iterable[Path]) -> list[dict]:
    """Collect anomaly dicts from the JSONL audit stream.

    Each audit line may take one of two shapes emitted across the hook
    stack:

    1. The wrapper shape written by
       :func:`modules.audit.workflow_auditor.signal_gaia_analysis` --
       ``{"timestamp": ..., "anomalies": [{"type": ...}, ...], "metrics":
       {...}}``. In that case the inner list is unpacked.
    2. A flat per-line anomaly record -- ``{"type": "...", "severity":
       "...", ...}`` -- produced by direct appenders or fixtures. The
       line itself is the anomaly.

    Lines that are neither (e.g. the ``post_tool_use`` records the
    :mod:`tools.gaia_simulator.extractor` consumes, which carry
    ``tool_name`` / ``parameters`` but no ``type`` key) are skipped.
    """
    anomalies: list[dict] = []
    for path in audit_paths or []:
        if not path.exists():
            continue
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict):
                continue

            inner = record.get("anomalies")
            if isinstance(inner, list):
                for entry in inner:
                    if isinstance(entry, dict):
                        anomalies.append(entry)
                continue

            # Flat per-line record -- only treat as anomaly when it
            # actually carries a ``type`` field. Otherwise this is a
            # tool-use record or unrelated telemetry.
            if "type" in record:
                anomalies.append(record)
    return anomalies


def _anomaly_mentions_skill(anomaly: dict, skill: str) -> bool:
    """Return True when ``skill`` appears in the anomaly payload.

    Accepts two shapes:

    - ``missing_skills`` list (what
      :func:`hooks.modules.agents.skill_injection_verifier.verify_skill_injection`
      emits) -- membership check.
    - flat ``skill`` string -- equality check. Reserved for future
      anomaly types that pin a single skill per record.
    """
    missing = anomaly.get("missing_skills")
    if isinstance(missing, list) and skill in missing:
        return True
    flat = anomaly.get("skill")
    if isinstance(flat, str) and flat == skill:
        return True
    return False


def skill_injection_consumer(
    audit_paths: list[Path],
    anomaly_expect: dict,
) -> GradeResult:
    """Verify the presence (or absence) of a skill-injection anomaly.

    Binary grader (score in ``{0.0, 1.0}``). S7 uses this to confirm the
    ``command-execution`` skill detected a pipe violation without
    re-implementing keyword matching here -- the hook is the source of
    truth. The consumer simply scans the audit stream for an anomaly
    matching ``anomaly_expect`` and returns pass/fail.

    ``anomaly_expect`` keys:

    - ``anomaly_type`` (str, required) -- the anomaly ``type`` to match.
      Catalog uses ``skill_injection_anomaly`` as the umbrella label;
      ``skill_injection_gap`` (what the verifier actually emits) is
      accepted as an alias and vice versa.
    - ``skill`` (str, required) -- the skill name the anomaly must
      reference (matched against ``missing_skills`` list, then against
      a flat ``skill`` field).
    - ``present`` (bool, required) -- ``True`` when the matching anomaly
      MUST appear at least once, ``False`` when it MUST NOT appear at
      all.

    Args:
        audit_paths: Audit JSONL files from
            ``DispatchResult.audit_paths``. May be empty; missing files
            are skipped silently.
        anomaly_expect: Per-case expectations dict from the catalog YAML.

    Returns:
        :class:`GradeResult` with ``passed=True`` iff the presence /
        absence assertion holds, ``score`` in ``{0.0, 1.0}``.
    """
    expect = anomaly_expect or {}

    unknown = [k for k in expect.keys() if k not in _ANOMALY_EXPECT_KEYS]
    if unknown:
        return GradeResult(
            passed=False,
            score=0.0,
            reasons=[
                f"anomaly_expect has unknown keys {unknown}; "
                f"valid set: {sorted(_ANOMALY_EXPECT_KEYS)}"
            ],
        )

    missing_required = [
        k for k in ("anomaly_type", "skill", "present") if k not in expect
    ]
    if missing_required:
        return GradeResult(
            passed=False,
            score=0.0,
            reasons=[
                f"anomaly_expect missing required keys: {missing_required}"
            ],
        )

    anomaly_type = expect["anomaly_type"]
    skill = expect["skill"]
    present_expected = expect["present"]

    if not isinstance(anomaly_type, str) or not anomaly_type:
        return GradeResult(
            passed=False,
            score=0.0,
            reasons=[f"anomaly_type must be a non-empty string, got {anomaly_type!r}"],
        )
    if not isinstance(skill, str) or not skill:
        return GradeResult(
            passed=False,
            score=0.0,
            reasons=[f"skill must be a non-empty string, got {skill!r}"],
        )
    if not isinstance(present_expected, bool):
        return GradeResult(
            passed=False,
            score=0.0,
            reasons=[f"present must be bool, got {type(present_expected).__name__}"],
        )

    type_aliases = _SKILL_INJECTION_TYPE_ALIASES.get(
        anomaly_type, frozenset({anomaly_type})
    )

    anomalies = _iter_anomalies(audit_paths)
    matches = [
        a for a in anomalies
        if a.get("type") in type_aliases and _anomaly_mentions_skill(a, skill)
    ]

    if present_expected:
        if not matches:
            return GradeResult(
                passed=False,
                score=0.0,
                reasons=[
                    f"expected anomaly type={anomaly_type!r} for skill "
                    f"{skill!r} but none found among {len(anomalies)} "
                    f"anomaly record(s)"
                ],
            )
        return GradeResult(
            passed=True,
            score=1.0,
            reasons=[
                f"found {len(matches)} matching anomaly/anomalies "
                f"(type={anomaly_type!r}, skill={skill!r})"
            ],
        )

    # present_expected is False -- the anomaly must NOT appear.
    if matches:
        offender = matches[0]
        return GradeResult(
            passed=False,
            score=0.0,
            reasons=[
                f"unexpected anomaly type={anomaly_type!r} for skill "
                f"{skill!r}: {offender!r}"
            ],
        )
    return GradeResult(
        passed=True,
        score=1.0,
        reasons=[
            f"no anomaly type={anomaly_type!r} for skill {skill!r} "
            f"among {len(anomalies)} record(s)"
        ],
    )
