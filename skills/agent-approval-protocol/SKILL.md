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
- Approval id: `P-` plus 32 lowercase hexadecimal characters. Relay it exactly;
  agents never mint it.
- All consent data belongs inside top-level `approval_request`, not beside it or
  duplicated in `evidence_report`.
- The fenced contract remains compatibility output; the DB is the durable
  source for lookup and reconciliation.

## Single command

The runtime-sealed request carries `operation`, verbatim `exact_content`,
`scope`, `risk_level`, `rollback_hint`, and `rationale`. The contract maps
`rollback_hint` to `rollback` and adds the verification method and returned
`approval_id`.

## Plan-first COMMAND_SET

`gaia approvals request-set` accepts two or more ordered exact T3 commands. Its
`approval_request` carries:

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

Commands are atomic strings, never compound shell. The runtime validates that
each is exact T3, non-interactive, not permanently blocked, and outside
protected paths. Fingerprints bind exact bytes and order; activation verifies
the stored REQUESTED fingerprint before forming the grant. Presentation is not
the integrity boundary.

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
