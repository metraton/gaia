---
name: agent-approval-protocol
description: Use when constructing or interpreting the approval handoff envelope between subagent and orchestrator -- sealed_payload schema, approval_id format, APPROVAL_REQUEST contract shape, and reading a granted approval from the DB
metadata:
  user-invocable: false
  type: reference
---

# Agent Approval Protocol

The approval handoff envelope is the data contract that flows from a subagent
through the hook layer to the orchestrator. This skill documents the exact shape
of that envelope: the `sealed_payload` fields, the `approval_id` format, the
`APPROVAL_REQUEST` contract, and how to confirm that a grant is active before
proceeding.

This skill is NOT `agent-protocol`. That skill documents the universal response
contract (`json:contract`, plan_status states, evidence_report). This skill
documents only the approval-specific handoff.

## sealed_payload — 7 Required Fields

```json
{
  "operation":     "string — human-readable action description",
  "exact_content": "string — verbatim command(s), newline-separated if multiple",
  "scope":         "string — resource path or identifier targeted",
  "risk_level":    "string — one of: low | medium | high | critical",
  "rollback_hint": "string | null — human-readable inverse; null if not reversible",
  "rationale":     "string — why this T3 is needed in this context",
  "commands":      ["array of strings — discrete command strings in execution order"]
}
```

**Canonicalization rule:** `json.dumps(payload, sort_keys=True, separators=(',', ':'))`.
The hook computes `SHA-256(canonical_bytes)` and stores the hex string as the
fingerprint. The orchestrator MUST re-compute this fingerprint via
`verify_fingerprint(approval_id, payload_json, con)` before presenting the
approval. Any field mutation between emission and relay changes the fingerprint
and causes `ChainTamperError` -- the approval is aborted.

## approval_id Format

The hook returns approval IDs in the format `P-{uuid4_hex}`. Example:
`P-b1bdfbb0b9474bf5b3f86b1f6a213f7a`.

The `P-` prefix is mandatory for the PostToolUse hook to perform targeted grant
activation. An `approval_id` without the `P-` prefix will not trigger grant
activation when the user selects "Approve".

The first 8 characters after `P-` are the nonce prefix used in option labels:
`[P-b1bdfbb0]`.

## APPROVAL_REQUEST Contract Shape

When the hook blocks a T3 command, the subagent emits this shape in its
`json:contract`:

```json
{
  "agent_status": {
    "plan_status": "APPROVAL_REQUEST",
    "agent_id": "<a + 5+ hex chars>",
    "pending_steps": ["<blocked command description>"],
    "next_action": "awaiting user approval"
  },
  "approval_request": {
    "operation":     "<from sealed_payload.operation>",
    "exact_content": "<from sealed_payload.exact_content>",
    "scope":         "<from sealed_payload.scope>",
    "risk_level":    "<from sealed_payload.risk_level>",
    "rollback":      "<from sealed_payload.rollback_hint>",
    "rationale":     "<from sealed_payload.rationale>",
    "verification":  "how to confirm success after execution",
    "approval_id":   "P-{uuid4_hex}",
    "batch_scope":   "verb_family (only for sweeps)"
  }
}
```

Fields in `approval_request` MUST be copied from `sealed_payload` without
modification. The orchestrator uses these to relay verbatim to the user via
AskUserQuestion after fingerprint validation. See
`Skill('orchestrator-present-approval')` for the orchestrator side.

## Reading a Granted Approval

After the user approves and the grant activates, the subagent receives a resume
from the orchestrator. The subagent MUST re-attempt the original command using
the exact `exact_content` string -- no modifications. The hook reads the grant
from the DB and allows the command through.

If the retry is blocked again (new `approval_id` issued), it means one of:

1. The grant was consumed by a prior attempt -- emit a new `APPROVAL_REQUEST`.
2. The command drifted from the approved `exact_content` -- use the literal.
3. The approval was rejected or revoked -- stop and report to the orchestrator.

To check grant status directly:

```python
from gaia.approvals.store import list_pending

# Returns approvals with status='pending' or 'approved' for this session
pending = list_pending(all_sessions=False)
```

## Event Chain

Each approval flows through these event types in sequence:

| Event | Who writes it | When |
|-------|--------------|------|
| `REQUESTED` | Hook (pre_tool_use) | Hook intercepts T3 command |
| `SHOWN` | Orchestrator | After fingerprint validation, before AskUserQuestion |
| `APPROVED` or `REJECTED` | Orchestrator | After user answers |
| `EXECUTED` or `FAILED` | Hook (post_tool_use) | After the approved command runs |

The hash chain links each event to the previous via `prev_hash` -> `this_hash`.
`verify_fingerprint` validates the chain integrity for the REQUESTED row.
`ChainTamperError` means any event in the chain was modified post-write.

## Key Invariants

- One `REQUESTED` event per `approval_id`. The hook never issues the same token twice.
- A `SHOWN` event MUST precede `APPROVED` or `REJECTED`. The orchestrator writes it.
- `EXECUTED` or `FAILED` require a prior `APPROVED` event. The hook enforces this.
- `REVERTED` events carry the original `event_id` in `metadata_json.original_event_id`.
