---
name: agent-approval-protocol
description: Use when constructing or interpreting the approval handoff envelope between subagent and orchestrator -- sealed_payload schema, approval_id format, APPROVAL_REQUEST contract shape, and reading a granted approval from the DB
metadata:
  user-invocable: false
  type: reference
---

# Agent Approval Protocol

`agent-approval-protocol` is the data contract that flows from a subagent,
through the hook layer, to the orchestrator when a T3 command is blocked: the
`sealed_payload` fields, the `approval_id` format, the `APPROVAL_REQUEST` shape,
the status and event vocabularies, and how to confirm a grant is active. The
tables below are the canonical schema -- relay them verbatim, do not author them.

For the universal response envelope (`plan_status` states, `evidence_report`),
see `agent-protocol`. For the deep mechanics -- fingerprint canonicalization,
the hash chain, grant activation, reading a granted approval from Python -- see
`reference.md`.

## approval_id format

`store._generate_approval_id()` returns `P-{uuid4().hex}` (e.g.
`P-b1bdfbb0b9474bf5b3f86b1f6a213f7a`). The `P-` prefix is mandatory: without it
the PostToolUse hook cannot do targeted grant activation. The first 8 hex chars
after `P-` are the nonce prefix shown in option labels: `[P-b1bdfbb0]`.

## APPROVAL_REQUEST contract shape

`bash_validator._build_sealed_payload()` builds 7 fields and passes them to
`store.insert_requested()`. The agent relays them verbatim into `approval_request`
-- it never authors them. Note the key rename: `rollback_hint` in the hook
becomes `rollback` in the contract; `commands` (`[exact_content]`) and
`verification`/`approval_id` exist only at the contract level.

```json
{
  "agent_status": {
    "plan_status": "APPROVAL_REQUEST",
    "agent_id": "<a + 5+ hex chars>",
    "pending_steps": ["<blocked command description>"],
    "next_action": "awaiting user approval"
  },
  "approval_request": {
    "operation":     "string -- e.g. 'MUTATIVE command intercepted: push'",
    "exact_content": "string -- the verbatim blocked command",
    "scope":         "string -- command.split()[0]: the leading CLI/resource token",
    "risk_level":    "string -- 'high' (DESTRUCTIVE) or 'medium' -- never low/critical",
    "rollback":      "null -- hook hardcodes this; it computes no inverse (sealed_payload field: rollback_hint)",
    "rationale":     "string -- why this T3 needs approval (built from agent + verb)",
    "verification":  "how to confirm success after execution",
    "approval_id":   "P-{uuid4_hex}"
  }
}
```

There is no `batch_scope` field: the `verb_family` grant was removed, so each
blocked command gets its own single-use grant. See
`Skill('orchestrator-present-approval')` for the orchestrator side.

## Status vocabularies -- distinct columns, opposite casing, never collapse

| Table | Column | Values | Source |
|-------|--------|--------|--------|
| `approvals` | `status` | lowercase: `pending` `approved` `rejected` `revoked` `expired` | schema.sql `CREATE TABLE approvals` CHECK |
| `approval_grants` | `status` | UPPERCASE: `PENDING` `CONSUMED` `REVOKED` `EXPIRED` | schema.sql `CREATE TABLE approval_grants` |

## Event chain

The `approval_events.event_type` CHECK admits nine values: `REQUESTED` `SHOWN`
`APPROVED` `REJECTED` `EXECUTED` `FAILED` `NOOP` `REVOKED` `REVERTED`. Only these
are written by production code today:

| Event | Who writes it | When |
|-------|--------------|------|
| `REQUESTED` | `bash_validator` via `store.insert_requested()` | Hook intercepts a T3 Bash command in subagent context |
| `SHOWN` | ElicitationResult hook via `activate_db_pending_by_prefix()` | User selects an Approve `[P-xxx]` label |
| `APPROVED` | ElicitationResult hook (same call as `SHOWN`) | Immediately after `SHOWN` |
| `REJECTED` / `REVOKED` | `gaia approvals` CLI via `store.reject()` / `store.revoke()` | User rejects or admin cancels |

`EXECUTED` `FAILED` `NOOP` `REVERTED` are valid in the CHECK and are *read* by
`store.get_executed_payload()` and `revert.py`, but no production hook *writes*
them today -- treat them as a designed extension point, not a live invariant. Do
not assume an `EXECUTED` event exists after a command runs.

## Key invariants

- One `REQUESTED` event per `approval_id`; the hook never reuses one.
- `SHOWN` precedes `APPROVED`; the activation path writes them together.
- `approval_events` is append-only -- the `bu_approval_events_immutable` and
  `bd_approval_events_immutable` triggers `RAISE(ABORT)` on UPDATE/DELETE.
- The orchestrator MUST re-verify a relayed payload via
  `chain.verify_fingerprint(approval_id, payload_json, con)` before presenting;
  a mismatch raises `ChainTamperError` and the approval aborts.

For the grant activation walk-through, fingerprint internals, reading a granted
approval from Python, and the retry-blocked-again diagnosis, see `reference.md`.
