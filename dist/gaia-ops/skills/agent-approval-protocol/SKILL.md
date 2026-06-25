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
never by dispatching a subagent to verify or derive it (it has no shell). The
primary source is the per-turn `[PENDING-APPROVALS-VERIFIED]` block injected at
`UserPromptSubmit` (`build_verified_pending_approvals` in
`hooks/modules/session/session_manifest.py`), which carries every pending that
has survived >= 1 turn, each already DB-read and fingerprint-verified
(`verified: true`). For a pending emitted in the current turn -- not yet in the
block -- the fallback is the subagent's relayed `approval_request`. The
**integrity boundary is grant activation**, not presentation:
`verify_fingerprint` (`gaia/approvals/chain.py`) runs when the user selects the
Approve label, so a tampered payload fails to form a grant regardless of how it
was presented. See `Skill('orchestrator-present-approval')` for the presentation
discipline.

For the universal response envelope (`plan_status` states, `evidence_report`),
see `agent-protocol`. For the deep mechanics -- fingerprint canonicalization,
the hash chain, grant activation, reading a granted approval from Python -- see
`reference.md`.

## approval_id format

For a **singular** T3 approval (the hook-block path),
`store._generate_approval_id()` returns `P-{uuid4().hex}` (e.g.
`P-b1bdfbb0b9474bf5b3f86b1f6a213f7a`) -- a random, unique id the subagent relays
verbatim. For a **plan-first `COMMAND_SET`** the id is instead **content-derived**
by `store.derive_command_set_id()`: `P-<first 32 hex of
sha256(canonical(command strings))>`. The two share the `P-` prefix and 32-hex
length but differ in origin -- the command_set id is deterministic (minted at
SubagentStop intake), and once the pending has survived a turn the orchestrator
reads that id directly from the injected `[PENDING-APPROVALS-VERIFIED]` block
(no derive-dispatch, no DB search); the singular id is random and the subagent
relays it directly for the same-turn case. The `P-` prefix is mandatory in both
cases: without it the PostToolUse
hook cannot do targeted grant activation. The first 8 hex chars after `P-` are
the nonce prefix shown in option labels: `[P-b1bdfbb0]`.

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
grant. For a batch of >= 2 T3 commands known up-front, emit a `command_set`
list and **no** `approval_id` -- the SubagentStop intake mints a single
`COMMAND_SET` grant (one consent covers all). See
`Skill('orchestrator-present-approval')` for the orchestrator side.

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
  `ChainTamperError` so the grant never forms. The orchestrator presents from a
  trusted source (the injected `[PENDING-APPROVALS-VERIFIED]` block, already
  fingerprint-verified by the hook; or a same-turn relayed `approval_request`)
  and never dispatches a subagent to verify or derive the approval.

For the grant activation walk-through, fingerprint internals, reading a granted
approval from Python, and the retry-blocked-again diagnosis, see `reference.md`.
