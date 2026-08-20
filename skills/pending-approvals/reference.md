# Pending approvals reference

The primary store is `approvals` plus `approval_events` and
`approval_grants` in Gaia's shared database. `gaia approvals list/show` query
that store; full-id lookup is not constrained to the current session.

Legacy singular approvals may still exist in the historical filesystem store.
The CLI keeps a DB-first compatibility fallback for those records. Do not
inspect, edit, move, or delete either backing store directly; use the CLI so
status, fingerprints, audit events, expiry, and ambiguity checks remain intact.

There is no automatic pending-approval injection into a new conversation.
Discovery is an explicit user action: list/search pending approvals or provide a
full id. Before approval, re-present the exact payload. A prefix that matches
more than one row is ambiguous and must not be guessed.

COMMAND_SET progress is DB-only and may end partially completed. Display the
full ordered set plus `next_index`, consumed indexes, failed index/reason, and
status when present. `FAILED` is terminal/frozen: untouched remainder is audit
history, not pending authorization. Approving a pending request activates it;
it does not prove any command executed. `SKILL.md` carries the current
COMMAND_SET activation shape: a plan-first `request-set` pending -- the one
carrying a `request_fingerprint` -- now activates through the same writer from
either entry point, so the host path and the CLI `approve` path are no longer
split. Read it there rather than inferring either state from this line.

Nonce-prefix matching is not fixed-length: `_APPROVE_NONCE_RE` captures one or
more hex characters, and `activate_db_pending_by_prefix` activates the FIRST
(oldest) pending row whose id starts with that prefix, with no ambiguity check
the way `gaia approvals show`'s CLI-side prefix lookup has. A short prefix on
two pending approvals is a real, silent misactivation risk -- always use the
full 8-char nonce in a presented label.

Read verbs (`list`/`show`/`pending`/`history`/`stats`) are the orchestrator's
to run directly through its trusted-CLI lane. That lane is not a read-only
lane, and the split here is not an approvals rule: it also carries the
coordination writes the orchestrator owns -- brief, plan and task lifecycle,
`notifications ack`, memory curation, `scan`/`context scan`, `paths`. What
separates the two sides is ownership of the effect, not the read/write shape
of the verb. Approvals are where that ownership stops:
`approve`/`revoke`/`reject`/`reject-all`/`clean`/`replay` are categorically
denied to the orchestrator role
(`gaia_cli_only_guard.EXPLICITLY_DENIED_PHRASES`) because the consent record
is the user's to move -- granted, refused, or withdrawn -- never something a
coordinator issues to itself. Dispatch a specialist, or route through
AskUserQuestion, never a bare orchestrator CLI call.
