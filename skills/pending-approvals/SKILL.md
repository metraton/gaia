---
name: pending-approvals
description: Use when the user asks to list, inspect, approve, reject, or revoke pending approvals
---

# Pending Approvals

Pending approvals are not automatically resurfaced in later sessions. Recovery
is explicit: the user asks to list/search pendings or supplies an approval id.

Use the unified CLI and treat the DB as primary:

- `gaia approvals pending` -- the undecided pendings only (all sessions by
  default; `--session <id>` narrows to one orchestrator session)
- `gaia approvals list` -- the DB-backed grants table plus the undecided
  pendings beneath it; it takes `--session` / `--orphans-only` / `--json` and
  has NO `--status` filter. Filtering by decision lives on
  `gaia approvals history --status <pending|approved|rejected|revoked>`
  (`--limit N`, default 50).
- `gaia approvals show <approval_id>`
- `gaia approvals approve <approval_id>`
- `gaia approvals reject <approval_id>`
- `gaia approvals revoke <approval_id>`

Every lookup and single-item decision requires the complete canonical
`P-<32 lowercase hex>` id. A short display label or raw nonce is never a lookup
key. Full-id lookup may cross session boundaries. Always re-present exact content,
risk, rollback, and verification before an approve decision; for COMMAND_SET,
show the full indexed ordered set. Never infer approval from conversational
language alone or select a similarly prefixed id.

**These verbs are not all the orchestrator's to run.** The trusted-CLI
role guard (`hooks/modules/security/gaia_cli_only_guard.py`) splits them
along a read/write line, not a T3 line: `approvals list` / `show` / `pending`
/ `history` / `stats` are in `ALLOWED_READ_PHRASES` -- the orchestrator reads
these directly. `approvals approve` / `revoke` / `reject` / `reject-all` /
`clean` / `replay` are in `EXPLICITLY_DENIED_PHRASES` -- categorically denied
for the orchestrator role, not approvable, regardless of mode. **Reads are the
orchestrator's; decisions are not.** A decision is dispatched to a specialist
(or, for the CLI-only admin case below, made explicitly by the user) -- never
run bare by the orchestrator itself.

**For a SINGULAR approval, `gaia approvals approve <approval_id>` is an admin
decision-recording verb, not executable activation.** It writes `APPROVED`
directly to the `approvals` row, but creates **no executable grant** and triggers
**no automatic re-dispatch**. The originally blocked command therefore remains
unexecutable from that admin decision alone. When the blocked command still
needs to run, this verb **MUST NEVER** substitute for the structured decision
path owned by the active host adapter. Load the adapter skill declared for the
active host by
`hooks/adapters/registry.py::registered_adapter_skill_documents` for the
mechanism it owns; `orchestrator-present-approval` owns the neutral presentation
and activation contract. Use the bare `approve` CLI verb only for the
audit/CLI-only case -- for example, marking a row from a different session as
decided when the command it covers will not be re-run.

**COMMAND_SET now activates through the same writer from either entry point.**
For a plan-first `request_type: "COMMAND_SET"` pending (minted by `gaia
approvals request-set`, which carries a `request_fingerprint`),
`activate_db_pending_by_id` takes a dedicated branch that calls
`insert_plan_command_set` -- the identical call the CLI admin verb `gaia
approvals approve` makes, and the only shape the runtime's execution check
(`reserve_plan_command`, keyed on `source='plan-first'`) finds. The structured
decision path and the admin verb therefore activate a `request-set` pending
identically. The
legacy `create_command_set_grant()` route survives only for a multi-command
payload with NO `request_fingerprint`, a chain-intake shape production no longer
emits; it is kept as a defensive fallback, not as the plan-first path. The
earlier defect -- a correctly labeled Approve reporting success while the
retried commands re-blocked, because every multi-command payload went through
the legacy route -- is closed.

Legacy filesystem records may still be read as a compatibility fallback for
older singular approvals; new COMMAND_SET requests and grants are DB-backed.
See `reference.md` for current precedence, legacy locations, ambiguity handling,
expiry, and batch rejection rules. Never edit the DB or legacy files directly.

A COMMAND_SET grant in `FAILED` is not pending or resumable. Show its completed,
failed, and untouched indexes as history; require a new request and approval for
all retry/remainder work.
