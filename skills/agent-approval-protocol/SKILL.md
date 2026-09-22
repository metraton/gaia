---
name: agent-approval-protocol
description: Use for approval_request, COMMAND_SET, approval identifiers, fingerprints, and progress data
---

# Agent Approval Protocol

This is the approval data reference. Producer, presenter, and executor workflows
live in their branch skills. Never author or derive security data that the
runtime returns.

## Identity and placement

- Contract `agent_id`: `^a[0-9a-f]{16,}$`.
- Approval id: **legacy spellings circulate, but only one is current.** The
  canonical `P-` plus 32 lowercase hexadecimal characters is the fully-qualified
  id the runtime seals, current refusal surfaces emit, and the only form to
  relay. Bare 32-hex nonces and `APPROVE:<32 hex>` belong only to the legacy
  compatibility parser in
  `hooks/modules/security/approval_constants.py::NONCE_APPROVAL_PATTERN`.
  `P-` plus the first 8 hex may be displayed for readability -- a label, never
  an address. Relay
  the canonical form exactly as received; agents never mint one, never pad a
  displayed truncation back to full length, and never reconstruct one from a
  prefix.
- **If the validator rejects the relayed id, that is a runtime seam, not your
  error.** Do not re-spell it, do not retry variants, and above all never invent
  an id that merely looks resolvable -- a plausible 32-hex string is
  indistinguishable from a real one at the point it is read, so inventing one
  turns a visible seam into a silent false grant. Close `NEEDS_VERIFICATION`
  with the id verbatim in `verbatim_outputs` and name the rejection in
  `open_gaps`, so the seam is routed to whoever owns it instead of being
  absorbed as a failure of yours.
- All consent data belongs inside top-level `approval_request`, not beside it or
  duplicated in `evidence_report`.
- The persisted contract row is the delivery and the DB is the durable source
  for lookup and reconciliation; no response fence is emitted.

## Single command

The runtime-sealed request carries `operation`, verbatim `exact_content`,
`scope`, `risk_level`, `rollback_hint`, and `rationale`. The contract maps
`rollback_hint` to `rollback` and adds the verification method and returned
`approval_id`.

## Plan-first COMMAND_SET

`gaia approvals request-set` accepts one or more ordered exact T3 commands --
including a single command requested proactively, before any attempt reaches
the pre-execution policy gate. Its `approval_request` carries:

```json
{
  "request_type": "COMMAND_SET",
  "operation": "Execute an ordered T3 command set",
  "exact_content": "<all exact commands in order>",
  "commands": ["<exact command 0>", "<exact command 1>"],
  "command_set": [
    {"command": "<exact command 0>", "fingerprint": "<sha256>", "rationale": ""},
    {"command": "<exact command 1>", "fingerprint": "<sha256>", "rationale": ""}
  ],
  "request_fingerprint": "<order-sensitive sha256>",
  "scope": "COMMAND_SET",
  "risk_level": "high",
  "rollback": null,
  "rationale": "<bounded goal>",
  "verification": "<desired-state check>",
  "approval_id": "P-<32 hex>"
}
```

The request's stored schema includes the order-sensitive fingerprint shown
above. The request CLI directly returns only `status`, canonical `approval_id`,
and `command_set` with per-command fingerprints; use the producer skill's
trusted read-back when the aggregate fingerprint is needed. Activation verifies
the stored REQUESTED fingerprint before forming the grant.

## Progress and status

The grant store exposes ordered progress: `next_index`,
`consumed_indexes_json`, `failed_index`, `failure_reason`, plus status
`PENDING`, `CONSUMED`, `FAILED`, `REVOKED`, or `EXPIRED`. A reservation binds
the exact next command to one session/tool call. Success advances it. Failure
sets terminal/frozen `FAILED`: completed indexes remain consumed, the failed
index and reason remain evidence, and every later index remains unconsumed but
unusable under this grant. Grouped consent is not transactional execution.

The approvals table uses lowercase `pending`, `approved`, `rejected`,
`revoked`, `expired`. Event types are `REQUESTED`, `SHOWN`, `APPROVED`,
`REJECTED`, `EXECUTED`, `FAILED`, `NOOP`, `REVOKED`, and `REVERTED`.

## Discovery rule

Pending approvals are not automatically resurfaced into a later conversation.
Cross-session recovery is explicit: the user names an id or asks to list/search
pending approvals, and `pending-approvals` performs the DB-first lookup. Legacy
filesystem fallback, where still supported, is documented in `reference.md`.

See `reference.md` for store fields, legacy compatibility, event-chain details,
and exact activation/reconciliation seams.
