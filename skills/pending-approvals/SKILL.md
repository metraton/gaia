---
name: pending-approvals
description: Use when the user asks to list, inspect, approve, reject, or revoke pending approvals -- "qué aprobaciones tengo pendientes", "aprueba esto", "rechaza esa aprobación", "revoca el permiso", "show pending approvals", "approve this".
---

# Pending Approvals

Pending approvals are not resurfaced in later sessions on their own. Recovery
is explicit: the user asks to list pendings or supplies an approval id. Every
lookup and single-item decision takes the complete canonical
`P-<32 lowercase hex>` id; a short label is never a lookup key.

## Reading

- `gaia approvals pending` -- this session's live undecided requests, those
  its dispatched agents made included, and a trailer counting what that scope
  hides. `--all-sessions` adds other sessions' requests, `--include-stale` the
  orphaned and expired ones, `--session <id>` scopes to another session; a
  shell outside any host session sees every session's
  (`bin/cli/approvals.py::cmd_pending`).
- `gaia approvals list` -- grants with STATUS, GRANT_STATE and OUTCOME
  columns, and the undecided requests beneath them with their STATE, under
  the same session scope (`--session`, `--all-sessions`, `--include-stale`,
  `--orphans-only` for the orphaned ones in every session, `--json`).
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
the withdrawal event records. Approving, replaying and `clean` are the user's.
Bulk rejection is the user's and the orchestrator's: the user runs
`reject --all` or `reject-all`, and the orchestrator, whose CLI lane admits only
the single verbs (`hooks/modules/security/gaia_cli_only_guard.py`), rejects a
batch one `reject <approval_id>` at a time. A dispatched specialist withdraws only the
requests its own turn made: the approvals core checks the turn its shell carries
(`GAIA_DISPATCH_TURN`, exported by the hook on Claude Code and by the plugin on
OpenCode; `gaia/approvals/core.py::require_own_turn`), so from a
specialist `reject <id>` and `revoke <id>` on another turn's request fail
naming whose it is, and the bulk verbs reject its own and say how many they
left and whose. A request sealed without a turn stays for the user or the
orchestrator.

A set that ended `failed` is frozen: show its completed, failed and untouched
commands as history; anything still needed is a new request.
