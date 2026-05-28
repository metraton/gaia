---
name: agent-protocol
description: Use when producing any agent response
metadata:
  user-invocable: false
  type: protocol
---

# Agent Protocol

## ¿Qué es agent-protocol?

The manual for the workflow every Gaia agent walks: how to know where you are in the increment, how to read what the runtime injected before your turn, and how to communicate position and outcome back to the orchestrator. The `agent_contract_handoff` fenced block is the coordination tool, not an administrative form -- the orchestrator reads it to decide the next dispatch and the runtime persists it as the turn's outcome. For the approval payload schema see `agent-approval-protocol`; for orchestrator-side interpretation see `agent-response`; for per-surface evidence and verification methods see the domain skills.

## El workflow como mapa

```
INVESTIGATE -> PLAN -> EXECUTE -> VERIFY -> COMPLETE
   ^                                  |
   |                                  v
   +---------- (loop on fail) --------+

        |- BLOCKED          (escalate, cannot continue alone)
mid-loop |- NEEDS_INPUT     (need user decision)
        |- APPROVAL_REQUEST (T3 blocked, awaiting consent)
```

`state_tracker.py` enforces the legal transitions (`_LEGAL_TRANSITIONS`, `_MAX_IN_PROGRESS_RETRIES = 2`); this map tells you where you stand, not which transitions are allowed -- the runtime blocks the illegal ones for you.

## Lo que recibís: input envelope

Before your turn starts, the runtime injects a context payload (`build_context_telemetry_snapshot` in `context_injector.py`). It has five named sections you can rely on -- name collision warning: the section `agent_contract_handoff` here is the INPUT contract for this dispatch; the OUTPUT fenced block tag is also `agent_contract_handoff`. Same name, two contexts.

- **`project_knowledge`** -- the indexed facts about the workspace your agent owns or can read; sourced from `project_context_contracts` in `gaia.db`. Read it before scanning the filesystem.
- **`surface_routing`** -- which surface(s) the orchestrator routed to, whether the task is multi-surface, recommended adjacent agents. Use it to know whether you are primary or assisting.
- **`agent_contract_handoff`** (input sub-block) -- the goal, acceptance criteria, and scope for THIS dispatch. Carries `consolidation_required` and `cross_check_required` flags that decide whether you must emit `consolidation_report` in your output.
- **`write_permissions`** -- exact list of `writable_sections` (project_context_contracts) you may emit `CONTEXT_UPDATE` for. Writing to a contract not in your list is rejected by the hook.
- **`metadata`** -- session id, cloud provider, contract version, rules count. Use for debugging and traceability, not control flow.

## La envelope mínima de salida

Every response ends with one fenced `agent_contract_handoff` block (parser: `parse_contract` and regex `_RE_HANDOFF` in `contract_validator.py`, tag constant `_TAG_HANDOFF`). The top-level structure is:

- **`agent_status`** -- your current position. The orchestrator reads `plan_status` first to decide what to do next. Fields: `plan_status`, `agent_id` (pattern `_AGENT_ID_PATTERN` in `response_contract.py`), `pending_steps`, `next_action`.
- **`evidence_report`** -- what you saw and did, so the orchestrator can corroborate without re-running your work. Always present; required fields per `_EVIDENCE_REQUIRED_FIELDS`: `patterns_checked`, `files_checked`, `commands_run`, `key_outputs`, `verbatim_outputs`, `cross_layer_impacts`, `open_gaps`, `verification`. Entries may be `[]`; `verification` is `null` except on `COMPLETE`.
- **`consolidation_report`** -- present only when the injected handoff asks for it. Fields per `_CONSOLIDATION_REQUIRED_FIELDS`: `ownership_assessment`, `confirmed_findings`, `suspected_findings`, `conflicts`, `next_best_agent`. Enum values per `VALID_OWNERSHIP_ASSESSMENTS`.
- **`approval_request`** -- present only when `plan_status` is `APPROVAL_REQUEST`. Carries the sealed payload the user will approve. Fields: `operation`, `exact_content`, `scope`, `risk_level`, `rollback`, `verification`, `approval_id` (when hook produced one).

Full schema validation lives in `_validate_from_handoff` (`contract_validator.py`) and `validate_response_contract` (`response_contract.py`). A rendered example per `plan_status` is in `examples.md`.

## Cuándo emitir cada `plan_status`

The five values are canonical in `VALID_PLAN_STATUSES` (`gaia/state/__init__.py`). Choose by what is true of your position:

- **`COMPLETE`** -- you finished the increment and verification genuinely passed. Carries the `verification` dict with `result: "pass"`.
- **`APPROVAL_REQUEST`** -- a T3 command was blocked by the hook. Emit the envelope with `approval_request` populated; hand off to `agent-approval-protocol` for the sealed payload schema.
- **`BLOCKED`** -- you identified something outside your authority (wrong surface, missing capability), or you need information another surface owns. Name the gap in `open_gaps` and the suggested next agent.
- **`NEEDS_INPUT`** -- you need a decision from the user to continue. List the options in `next_action`.
- **`IN_PROGRESS`** -- the default mid-loop state during retry or verify-fail. Rarely a terminal value for a turn; the runtime caps consecutive `IN_PROGRESS` at 2.

## Capabilities del envelope que vale la pena conocer

These are not validator-enforced fields you must fill -- they are levers you can pull when the situation calls for them. Skipping them is silent loss of signal:

- **`memorialize_suggestions`** (top-level array) -- structured entries the orchestrator presents to the user so they can curate gaia memory. Parser `parse_memorialize_suggestions` in `response_contract.py`; required entry fields `MEMORIALIZE_REQUIRED_FIELDS = ("description", "body")`; valid types `{atom, decision, negative}`, valid classes `{anchor, thread, log}`. You do not write memory directly; the orchestrator does after the user approves.
- **`memory_suggestions`** (top-level array of strings) -- advisory text-only notes; distinct from `memorialize_suggestions`. Parsed by `parse_memory_suggestions`. Use when you want to flag something worth noting that does not warrant a structured memory entry.
- **`consolidation_report.ownership_assessment`** -- one of `owned_here | cross_surface_dependency | not_my_surface` (`VALID_OWNERSHIP_ASSESSMENTS`). Tells the orchestrator whether your output is authoritative, partial, or misrouted.
- **`loop_state`** -- top-level dict with `iteration`, `max_iterations`, `metric`, `threshold`. Used by agentic-loop agents. `_check_loop_state_blocking` (`contract_validator.py`) BLOCKS a `COMPLETE` when `iteration < max_iterations AND metric < threshold` -- the runtime forces another iteration. Skip the dict (or leave metric/threshold null) when you are not in an agentic loop.
- **`evidence_report.cross_layer_impacts`** -- adjacent components (docs, configs, sister agents) your change invalidates. Flag, do not silently edit.

## Handoffs

- **`agent-approval-protocol`** -- sealed_payload schema, `approval_id` format, full APPROVAL_REQUEST envelope when T3 fires.
- **`agent-response`** -- how the orchestrator parses and acts on your contract.
- **Domain skills** (`terraform-patterns`, `gitops-patterns`, `developer-patterns`, ...) -- what counts as evidence and which verification method fits per surface.

## La regla de honestidad del verification

Report `verification.result = "pass"` only when it genuinely passed. Emitting `COMPLETE` with `result = "fail"` is a contradiction the runtime detects (`VERIFICATION_RESULT_MUST_BE_PASS`). The deeper failure mode is not the rejected contract -- it is the habit of defining success by command exit code. A clean exit does not mean the change worked; verification is the moment you confirm the change produced the intended outcome.

```json
"verification": {
  "method": "test | dry-run | metric | self-review",
  "checks": ["what was checked"],
  "result": "pass | fail",
  "details": "concrete evidence"
}
```

Choose the method that fits the domain (infra: `dry-run`; code: `test`; skills: `self-review`; email: `metric`). When no automated check exists, `self-review` is the floor -- state what you checked and what you observed.

## Anti-patterns

- **Defining success by exit code** -- `exit 0` is not verification. The change either produced the intended outcome or it did not; the rule above is the test.
- **Emitting `COMPLETE` without verification** -- the validator blocks it, but the worse cost is reaching for `COMPLETE` because the loop felt long. If unverified, you are still `IN_PROGRESS`.
- **Assuming the orchestrator remembers** -- every turn starts from the contract. If you saw something relevant and did not write it into `evidence_report`, it does not exist for the next agent.
- **Inventing a `plan_status`** -- only the five in `VALID_PLAN_STATUSES` exist. A novel status is silently coerced or rejected; either way the signal is lost.
