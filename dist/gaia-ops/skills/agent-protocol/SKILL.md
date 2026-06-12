---
name: agent-protocol
description: Use when producing any agent response
metadata:
  user-invocable: false
  type: protocol
---

# Agent Protocol

## What is agent-protocol?

The producer's playbook for an agent turn: how to read where you stand in the increment, and how to emit your position back so the orchestrator can decide the next dispatch. The `agent_contract_handoff` fenced block you emit is the coordination tool, not an administrative form -- the orchestrator reads it to route the next turn and the runtime persists it as the outcome. This skill is produce-side judgment only. For the full field schema, conditional triggers, sub-field tables, the INPUT-vs-OUTPUT name collision, and the plan_status enum, see `agent-contract-handoff`. For the approval payload, see `agent-approval-protocol`; for orchestrator-side interpretation, see `agent-response`.

## The workflow as a map

```
INVESTIGATE -> PLAN -> EXECUTE -> VERIFY -> COMPLETE
   ^                                  |
   |                                  v
   +---------- (loop on fail) --------+

        |- BLOCKED          (escalate, cannot continue alone)
mid-loop |- NEEDS_INPUT     (need user decision)
        |- APPROVAL_REQUEST (T3 blocked, awaiting consent)
```

You receive a task (the injected INPUT envelope), work the increment, then emit one OUTPUT envelope. `state_tracker.py` enforces the legal transitions (`_LEGAL_TRANSITIONS`, `_MAX_IN_PROGRESS_RETRIES = 2`); this map tells you where you stand, not which transitions are allowed -- the runtime blocks the illegal ones for you.

## The minimal output envelope

Every response ends with one fenced `agent_contract_handoff` block. The common case is `agent_status` + `evidence_report`, with `consolidation_report` and `approval_request` null:

```json
{
  "agent_status": {
    "plan_status": "COMPLETE",
    "agent_id": "ab7e4d2",
    "pending_steps": [],
    "next_action": "done"
  },
  "evidence_report": {
    "patterns_checked": [], "files_checked": [], "commands_run": [],
    "key_outputs": [], "verbatim_outputs": [], "cross_layer_impacts": [],
    "open_gaps": [],
    "verification": { "method": "...", "checks": ["..."], "result": "pass", "details": "..." }
  },
  "consolidation_report": null,
  "approval_request": null
}
```

`evidence_report` carries 7 required keys + `verification` on COMPLETE (see the honesty rule below). The block body must be valid JSON: `parse_contract` runs `json.loads`, so YAML, comments, trailing commas, or unquoted keys make the parser return `None`, the runtime treats the block as missing, and you pay a forced reissue. Emit JSON, not the YAML it resembles. For every field, its required/conditional status, and its trigger, see `agent-contract-handoff`; a rendered example per `plan_status` is in `examples.md`.

## When to emit each `plan_status`

Choose by what is true of your position. The enum and meanings are owned by `agent-contract-handoff`; here is when to reach for each:

- **`COMPLETE`** -- you finished the increment AND verification genuinely passed. Reaching for it because the loop felt long is the failure mode below.
- **`APPROVAL_REQUEST`** -- a T3 command was blocked with an `approval_id`. Pass the id through verbatim; do not retry the command. Hand off to `agent-approval-protocol` for the payload.
- **`BLOCKED`** -- you hit something outside your authority (wrong surface, missing capability) or need information another surface owns. Name the gap in `open_gaps` and suggest the next agent.
- **`NEEDS_INPUT`** -- you need a user decision to continue. List the explicit options in `next_action`.
- **`IN_PROGRESS`** -- mid-loop during retry or verify-fail. Rarely terminal; the runtime caps consecutive `IN_PROGRESS` at 2, so do not park here to avoid a decision.

## The verification honesty rule

Report `verification.result = "pass"` only when it genuinely passed. `verification` is required only on `COMPLETE`, and on COMPLETE `result` must be `"pass"` -- a `COMPLETE` with `result = "fail"` is a contradiction the runtime rejects (`VERIFICATION_RESULT_MUST_BE_PASS`). The deeper failure mode is not the rejected contract; it is the habit of defining success by command exit code. A clean exit does not mean the change worked -- verification is the moment you confirm the change produced the intended outcome. Choose the method that fits the domain (infra: `dry-run`; code: `test`; skills: `self-review`; email: `metric`). When no automated check exists, `self-review` is the floor: state what you checked and what you observed.

## When to populate `update_contracts`

`update_contracts` is the optional array (`{contract, payload}` each) that writes your discoveries into shared project-context so the next agent does not start from zero on what only you saw. It is produce-side judgment: emit a delta when ANY is true -- a section you own is **empty**, discovered data **drifts** from what is indexed, you found **new resources** not listed, or you uncovered a **pattern/structure/config** not yet captured. Skip when findings match what is already indexed; a redundant write only adds noise to the audit trail. Emit as you discover, not at task end. For the field shape, merge semantics, and the index-not-snapshot boundary on payload contents, see `agent-contract-handoff`.

**Prioritize when a section is empty or sparse** -- capture the highest-value keys first:

| Priority | Capture | Why |
|----------|---------|-----|
| P0 | resource identifiers (names, IDs, paths) | direct targeting in future searches |
| P1 | structural relationships (what connects to what) | cross-agent reasoning |
| P2 | configuration values (versions, replicas, limits) | drift detection |
| P3 | behavioral patterns (conventions, naming schemes) | consistency enforcement |

Capture P0 on every investigation; P1-P3 only when naturally encountered -- do not investigate solely to populate context.

**Mutative triggers fire even without investigation.** An investigative trigger fires when you *discover* something that already existed; a mutative trigger fires when an action *creates or changes* workspace state. When tool output says *installed*, *added*, *configured*, *applied*, or *upgraded* for a named package or service (`npm install`, `pip install`, `kubectl apply`, `helm install/upgrade`, `brew install`, `auth configure`), record the new state in `update_contracts` before the turn ends. Read-only output is not a trigger: `kubectl get`, `helm list`, `npm list`, `terraform plan`, and `*describe*` index through scanners, not the contract.

## Anti-patterns

- **Defining success by exit code** -- `exit 0` is not verification. The change either produced the intended outcome or it did not; the honesty rule is the test.
- **Emitting `COMPLETE` without verification** -- the validator blocks it, but the worse cost is reaching for `COMPLETE` because the loop felt long. If unverified, you are still `IN_PROGRESS`.
- **Assuming the orchestrator remembers** -- every turn starts from the contract. If you saw something relevant and did not write it into `evidence_report`, it does not exist for the next agent.
- **Inventing a `plan_status`** -- only the five canonical values exist. A novel status is silently coerced or rejected; either way the signal is lost.
- **Caching live-state in `update_contracts`** -- writing runtime facts (pod counts, IPs, instance status) into project-context misleads the next agent the moment they change. Index what statically exists; fetch the live value on demand.

## Handoffs

- **`agent-contract-handoff`** -- the full field schema, conditional triggers, sub-field tables, INPUT-vs-OUTPUT distinction, and plan_status enum.
- **`agent-approval-protocol`** -- sealed_payload schema, `approval_id` format, full APPROVAL_REQUEST envelope.
- **`agent-response`** -- how the orchestrator parses and acts on your contract.
- **Domain skills** (`gaia-patterns`, ...) -- what counts as evidence and which verification method fits per surface.
