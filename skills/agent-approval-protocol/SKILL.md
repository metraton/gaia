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

The orchestrator presents this contract to the user from a **trusted source**,
never by dispatching a subagent to verify or derive it (it has no shell for
that). Approvals are **in-loop and single-session**: the source is the
subagent's same-turn relayed `approval_request` (there is no cross-turn or
cross-session resurfacing of pendings -- no injected verified-pendings block).
The **integrity boundary is grant activation**, not presentation:
`verify_fingerprint` (`gaia/approvals/chain.py`) runs when the user selects the
Approve label, so a tampered payload fails to form a grant regardless of how it
was presented. See `Skill('orchestrator-present-approval')` for the presentation
discipline.

For the universal response envelope (`plan_status` states, `evidence_report`),
see `agent-protocol`. For the deep mechanics -- fingerprint canonicalization,
the hash chain, grant activation, reading a granted approval from Python -- see
`reference.md`.

**Build this payload the same way as the rest of the contract: `gaia contract`
first, fence always.** Per `agent-protocol`, the primary path is `gaia contract
set approval_request.<field> <value>` (or `fill --json`) as each sealed_payload
field becomes known, then `finalize`. But the SubagentStop gate parses the
fenced `agent_contract_handoff` block out of your response text, not the
finalized DB row -- so the `approval_request` below is REQUIRED output in your
final fenced block regardless of whether you built it via the CLI or composed
it directly. An `APPROVAL_REQUEST` that only exists in a finalized draft, with
no matching fence in the response text, gives the orchestrator nothing to
parse and the turn is rejected.

## approval_id format

For a **singular** T3 approval (the hook-block path),
`store._generate_approval_id()` returns `P-{uuid4().hex}` (e.g.
`P-b1bdfbb0b9474bf5b3f86b1f6a213f7a`) -- a random, unique id the subagent relays
verbatim. For a **`COMMAND_SET`** (a chain of >= 2 T3 sub-commands blocked in one
Bash call) the id is instead **content-derived** by `store.derive_command_set_id()`:
`P-<first 32 hex of sha256(canonical(command strings))>`. The two share the `P-`
prefix and 32-hex length and, critically, share the same delivery path: both
arrive in the same `[T3_BLOCKED]` denial the hook returns at block time
(`bash_validator._validate_compound_command` mints the COMMAND_SET the moment it
classifies >= 2 chained sub-commands as ungranted T3), and the subagent relays
whichever `approval_id` it receives verbatim into its `approval_request` --
there is no separate derivation step for either shape. The `P-` prefix is
mandatory in both cases: without it the PostToolUse hook cannot do targeted
grant activation. The first 8 hex chars after `P-` are the nonce prefix shown in
option labels: `[P-b1bdfbb0]`.

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

There is no `batch_scope` field: the `verb_family` grant was removed. For a
single blocked command, each gets its own single-use `SCOPE_SEMANTIC_SIGNATURE`
grant. For a chain of >= 2 T3 sub-commands blocked in one Bash call, the hook
mints a single `COMMAND_SET` grant at block time and returns its
content-derived `approval_id` in the same `[T3_BLOCKED]` denial shape -- the
agent relays it exactly like a singular `approval_id`; it never emits a
`command_set` without one. See `Skill('orchestrator-present-approval')` for the
orchestrator side.

## Status vocabularies -- distinct columns, opposite casing, never collapse

| Table | Column | Values | Source |
|-------|--------|--------|--------|
| `approvals` | `status` | lowercase: `pending` `approved` `rejected` `revoked` `expired` | schema.sql `CREATE TABLE approvals` CHECK |
| `approval_grants` | `status` | UPPERCASE: `PENDING` `CONSUMED` `REVOKED` `EXPIRED` | schema.sql `CREATE TABLE approval_grants` |

## Event chain

The `approval_events.event_type` CHECK admits nine values: `REQUESTED` `SHOWN`
`APPROVED` `REJECTED` `EXECUTED` `FAILED` `NOOP` `REVOKED` `REVERTED`. These are
written by production code today:

| Event | Who writes it | When |
|-------|--------------|------|
| `REQUESTED` | `bash_validator` via `store.insert_requested()` | Hook intercepts a T3 Bash command in subagent context |
| `SHOWN` | ElicitationResult hook via `activate_db_pending_by_prefix()` | User selects an Approve `[P-xxx]` label |
| `APPROVED` | ElicitationResult hook (same call as `SHOWN`) | Immediately after `SHOWN` |
| `REJECTED` / `REVOKED` | `gaia approvals` CLI via `store.reject()` / `store.revoke()` | User rejects or admin cancels |
| `EXECUTED` / `FAILED` | PostToolUse adapter (`_record_t3_outcome_event`) via `store.record_event()` | An approved T3 command runs under a consumed grant -- `EXECUTED` on clean exit, `FAILED` otherwise |

The PostToolUse path closes the audit cycle: PreToolUse stashes the consumed
grant's `approval_id` in `HookState`, and PostToolUse appends `EXECUTED` or
`FAILED` for that approval, continuing the hash chain through `record_event()`.
`store.get_executed_payload()` and `gaia approvals replay` read the `EXECUTED`
payload to re-present the commands that ran. `NOOP` and `REVERTED` remain valid
in the CHECK but are **inert** -- no production code writes them (the revert
feature was removed). Do not assume an `EXECUTED` event exists for an approval
whose command never ran, or that ran through the redirect-sanitized path.

## Key invariants

- One `REQUESTED` event per `approval_id`; the hook never reuses one.
- `SHOWN` precedes `APPROVED`; the activation path writes them together.
- `approval_events` is append-only -- the `bu_approval_events_immutable` and
  `bd_approval_events_immutable` triggers `RAISE(ABORT)` on UPDATE/DELETE.
- The payload's integrity is enforced at grant **activation**, not at
  presentation: `chain.verify_fingerprint(approval_id, payload_json, con)` runs
  when the user selects the Approve label, and a mismatch raises
  `ChainTamperError` so the grant never forms. The orchestrator presents from
  the subagent's same-turn relayed `approval_request` (approvals are in-loop and
  single-session -- no injected verified-pendings block) and never dispatches a
  subagent to verify or derive the approval.

For the grant activation walk-through, fingerprint internals, reading a granted
approval from Python, and the retry-blocked-again diagnosis, see `reference.md`.
