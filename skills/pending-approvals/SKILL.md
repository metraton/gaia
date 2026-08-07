---
name: pending-approvals
description: Use when the user asks to list, inspect, approve, reject, or revoke pending approvals
---

# Pending Approvals

Pending approvals are not automatically resurfaced in later sessions. Recovery
is explicit: the user asks to list/search pendings or supplies an approval id.

Use the unified CLI and treat the DB as primary:

- `gaia approvals list --status pending`
- `gaia approvals show <approval_id>`
- `gaia approvals approve <approval_id>`
- `gaia approvals reject <approval_id>`
- `gaia approvals revoke <approval_id>`

Lookup by full id may cross session boundaries. Always re-present exact content,
risk, rollback, and verification before an approve decision; for COMMAND_SET,
show the full indexed ordered set. Never infer approval from conversational
language alone or select a similarly prefixed id.

**These five verbs are not all the orchestrator's to run.** The trusted-CLI
role guard (`hooks/modules/security/gaia_cli_only_guard.py`) splits them
along a read/write line, not a T3 line: `approvals list` / `show` / `pending`
/ `history` / `stats` are in `ALLOWED_READ_PHRASES` -- the orchestrator reads
these directly. `approvals approve` / `revoke` / `reject` / `reject-all` /
`clean` / `replay` are in `EXPLICITLY_DENIED_PHRASES` -- categorically denied
for the orchestrator role, not approvable, regardless of mode. **Reads are the
orchestrator's; decisions are not.** A decision is dispatched to a specialist
(or, for the CLI-only admin case below, made explicitly by the user) -- never
run bare by the orchestrator itself.

**`gaia approvals approve <approval_id>` is an admin verb, not the AskUserQuestion
activation path -- for a SINGULAR approval.** It writes `APPROVED` directly to
the `approvals` row, but it does **not** call `activate_db_pending_by_prefix`
and does **not** create a hook-side grant -- so on its own it does not make
the originally blocked command executable, and it does not trigger the
automatic re-dispatch that follows a real approval. The path that creates the
grant is the AskUserQuestion flow in `orchestrator-present-approval`: the user
selects a label matching `Approve -- <action> [P-{nonce8}]`, which the hook's
nonce regex parses to activate the grant. Use the bare `approve` CLI verb only
for the audit/CLI-only case -- e.g. marking a row from a different session as
decided when the command it covers will not be re-run -- never as a substitute
for AskUserQuestion when the blocked command still needs to execute.

**COMMAND_SET is the opposite today, and this matters for any live
`request-set` approval.** For a `request_type: "COMMAND_SET"` pending (minted
by `gaia approvals request-set`), the AskUserQuestion path is currently
*broken*: `activate_db_pending_by_prefix` still routes every multi-command
payload through the legacy `create_command_set_grant()`, which the runtime's
actual execution check (`reserve_plan_command`, keyed on `source='plan-first'`)
never looks at -- so a correctly labeled Approve reports success and then the
retried commands re-block anyway. The only path currently wired end-to-end for
a COMMAND_SET is the CLI admin verb, `gaia approvals approve <approval_id>`
(its COMMAND_SET branch calls `insert_plan_command_set`, which the execution
check does find). See the COMMAND_SET audit for the full trace and the live
repro; do not treat this note as resolved just because the singular path is
fine.

Legacy filesystem records may still be read as a compatibility fallback for
older singular approvals; new COMMAND_SET requests and grants are DB-backed.
See `reference.md` for current precedence, legacy locations, ambiguity handling,
expiry, and batch rejection rules. Never edit the DB or legacy files directly.

A COMMAND_SET grant in `FAILED` is not pending or resumable. Show its completed,
failed, and untouched indexes as history; require a new request and approval for
all retry/remainder work.
