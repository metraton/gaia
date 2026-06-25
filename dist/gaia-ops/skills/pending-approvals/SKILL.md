---
name: pending-approvals
description: Use when the user invokes approvals directly -- "ver pendientes", "aprobar P-XXXX", "rechazar P-XXXX", "approve P-", "reject P-" -- or when SessionStart injected an [ACTIONABLE] pending approvals block
metadata:
  user-invocable: true
  type: technique
---

# Pending Approvals

`pending-approvals` is the workflow the orchestrator follows when the *user*
drives an approval -- "ver pendientes", "aprobar P-XXXX", "rechazar P-XXXX" --
or when `build_pending_approvals_block` (`hooks/modules/session/session_manifest.py`)
injects an `[ACTIONABLE] Pending approvals` block at SessionStart. The
orchestrator's role here is to translate user intent into the right `gaia
approvals` subcommand against the right store.

For the universal envelope of an approval payload see `agent-approval-protocol`;
for how the orchestrator relays a *subagent-initiated* APPROVAL_REQUEST into
AskUserQuestion see `orchestrator-present-approval` -- that skill owns the
verbatim-COMANDO, `[P-{nonce8}]` label, and single-use grant discipline; this
skill does not restate them.

## The DB / filesystem store gap

There are two stores for pending approvals, and the CLI subcommands do not
cover them symmetrically. Misreading this surface is what makes the orchestrator
report "rejected" when nothing actually changed.

| Subcommand | Store | Backed by |
|------------|-------|-----------|
| `gaia approvals pending` | DB only | `store.list_pending` |
| `gaia approvals history` | DB only | `store.list_all` |
| `gaia approvals approve P-xxx` | DB only | `store.approve` |
| `gaia approvals revoke P-xxx` | DB only | `store.revoke` (falls back to legacy) |
| `gaia approvals show P-xxx` | DB first, then filesystem | `cmd_show_v2` in `bin/cli/approvals.py` |
| `gaia approvals list` | DB grants + filesystem pendings | `cmd_list` (mixed) |
| `gaia approvals reject NONCE` | filesystem only | `reject_pending` in `hooks/modules/security/approval_grants.py` |
| `gaia approvals reject-all` | filesystem only | loops `reject_pending` |
| `gaia approvals clean` | DB (cross-session stale pendings) + filesystem | `cmd_clean` in `bin/cli/approvals.py`: calls `store.list_pending(all_sessions=True)`, transitions every pending older than `DEFAULT_PENDING_TTL_MINUTES` (24 h) to `revoked` via `store.revoke()`, then calls `cleanup_expired_grants` for filesystem files |

The practical consequence: `revoke` is the DB-aware single-id verb; `reject` and
`reject-all` only touch the legacy filesystem queue. If you need to mark a DB
row as terminated, use `revoke`. Bulk DB cleanup currently has no first-class
CLI -- it requires a Python loop over `store.revoke()`.

## When SessionStart injects the [ACTIONABLE] block

1. Present the summary to the user -- the scanner has already formatted each
   row as `P-{nonce_prefix8}  {command}  [{danger_verb}]  {age}`.
2. Wait for the user to choose: "ver P-XXXX", "aprobar P-XXXX", "rechazar P-XXXX",
   or a bulk operation.

Do not silently act on the block. The block is a prompt to the user, not an
instruction to the orchestrator.

## When user says "ver P-XXXX"

1. Run `gaia approvals show P-XXXX` -- `cmd_show_v2` checks the DB row first,
   then falls back to the filesystem pending. Either path returns the verbatim
   command and the context fields.
2. Relay the detail to the user. Do not paraphrase the command.
3. Ask whether to approve or reject.

## When user says "aprobar P-XXXX"

1. If you have not already, call `gaia approvals show P-XXXX` to load the
   verbatim command and context fields (`cmd_show_v2` checks the DB then the
   filesystem).
2. Present the approval via `AskUserQuestion` following
   `orchestrator-present-approval` -- 5 labeled fields and the `[P-{nonce8}]`
   option label. The same presentation works for both stores; the nonce-prefix
   matcher (`activate_db_pending_by_prefix` in
   `hooks/modules/security/approval_grants.py`) covers DB rows and the legacy
   path covers filesystem pendings.
3. On `"Approve"`, the ElicitationResult hook writes `SHOWN` + `APPROVED`
   events and the grant activates in the current session.
4. Dispatch a one-shot agent to execute the command using the dispatch template
   in `reference.md` (preflight + recovery, `mode` per target).

The CLI `gaia approvals approve P-XXXX` is the cross-session admin path: it
inserts `APPROVED` directly in the DB and does **not** create a hook-side grant.
Use it only when the user explicitly wants the CLI-only path (audit, marking a
row from a different session as decided). For any approval that needs to
execute the blocked command in this session, AskUserQuestion is the only path
that activates the grant.

## When user says "rechazar P-XXXX" / "reject P-XXXX"

Single rejection has two routes; pick by which store owns the row.

- **DB row**: `gaia approvals revoke P-XXXX --yes` -- `store.revoke` inserts a
  `REVOKED` event and updates `approvals.status` to `revoked`.
- **Filesystem pending**: `gaia approvals reject P-XXXX` -- `reject_pending`
  rewrites the JSON file with `status: "rejected"` (no `rm`, which would itself
  be T3).

Confirm to the user: "P-XXXX rechazado." If `revoke` returns `not_found`, fall
back to `reject` before declaring failure -- the row may have been a legacy
filesystem pending.

## Bulk cleanup

Offer bulk cleanup when the user says "limpia todos los pendings", "borra los
pendientes", or when SessionStart surfaces 5+ orphaned pendings the user has
not engaged with.

- `gaia approvals reject-all` -- bulk soft-reject across the **filesystem** queue.
  Returns "0 rejected" when the queue is empty. Does not touch DB rows.
- `gaia approvals clean` -- the first-class cross-session bulk drain for stale
  DB pendings: `cmd_clean` calls `store.list_pending(all_sessions=True)` and
  transitions every pending older than 24 h (`DEFAULT_PENDING_TTL_MINUTES`) to
  `revoked` via `store.revoke()`, then runs `cleanup_expired_grants` to clean
  expired filesystem grant files. Runs without a T3 prompt (consent-reducing,
  listed in `CONSENT_REDUCING_SUBCOMMAND_EXCEPTIONS`). Use this when
  `gaia approvals pending --all-sessions` shows a backlog of stale rows.

Do not report "bulk cleanup done" after `reject-all` alone -- it only clears
the filesystem queue. Run `gaia approvals clean` to drain the DB backlog, then
confirm with `gaia approvals pending --all-sessions`.

Do not offer `reject-all` when there are active same-session pendings the user
may still want to approve.

## Anti-patterns

- Approving without showing the exact COMANDO -- the user consents on the
  verbatim string, not a summary. The full presentation discipline lives in
  `orchestrator-present-approval`; this skill does not restate it.
- Treating `gaia approvals reject-all` as a full cleanup -- it operates on the
  filesystem queue only; DB rows survive the call. Use `gaia approvals clean`
  to drain the DB backlog.
- Reporting "rechazado" without verifying the store -- `revoke` returns
  `not_found` for filesystem-only pendings; the inverse happens for `reject` on
  DB rows. Pick the verb by store, or be ready to fall back.
- Dispatching execution before AskUserQuestion returns "Approve" -- the grant
  does not activate until the ElicitationResult hook fires.
- Using `rm` on a `pending-*.json` file -- deletion itself is T3 and blocks.
  The legacy soft-reject path rewrites the file in place; use it.

For the dispatch template (preflight, recovery, mode selection) and the
filesystem pending JSON schema, read `reference.md`.
