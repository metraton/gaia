---
name: pending-approvals
description: Use when the user asks to list, inspect, approve, reject, or revoke pending approvals
---

# Pending Approvals

Pending approvals are not resurfaced in later sessions on their own. Recovery
is explicit: the user asks to list pendings or supplies an approval id. Every
lookup and single-item decision takes the complete canonical
`P-<32 lowercase hex>` id; a short label is never a lookup key.

## Reading

- `gaia approvals pending` -- the undecided requests, all sessions by default
  (`--session <id>` narrows).
- `gaia approvals list` -- grants with STATUS, GRANT_STATE and OUTCOME
  columns, and the undecided requests beneath them with their STATE
  (`--session`, `--orphans-only` for the orphaned ones, `--json`).
- `gaia approvals show <approval_id>` -- one approval: State, Outcome, Reason,
  Requester, Window, Directory, the grant and the sealed payload.
- `gaia approvals history [<approval_id>]` -- recent approvals, or one
  approval's event chain.
- `gaia approvals stats` -- counts by state and outcome.

Every reader names an approval by one derived state; `reference.md` lists them.

## Deciding

The user decides on the signature Gaia shows (`orchestrator-present-approval`).
From a terminal, the user can also run `gaia approvals approve <approval_id>`:
it creates the grant exactly as Approve on the question does.

The orchestrator withdraws but never approves. It may run
`gaia approvals reject <approval_id>` (a pending request becomes `rejected`) and
`gaia approvals revoke <approval_id>` (an armed grant can no longer be used) --
for example to retire an orphaned request. Both accept `--reason <text>`, which
the withdrawal event records. Approving, replaying, and the bulk
verbs (`reject --all`, `reject-all`, `clean`) are the user's, or a specialist's
when the user asks for them.

A set that ended `failed` is frozen: show its completed, failed and untouched
commands as history; anything still needed is a new request.
