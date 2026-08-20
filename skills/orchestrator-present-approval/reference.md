# Approval presentation reference

The presentation source is the producer contract reconciled with the DB-backed
approval. The integrity check runs at activation, where the canonical REQUESTED
payload fingerprint is verified. Exact human presentation is still mandatory
for informed consent.

For COMMAND_SET render an ordered table with index, exact command, scope/effect,
and item-specific risk when applicable. Follow it with total count, aggregate
risk, partial-completion warning, rollback for completed items, and the exact
post-execution desired-state verification. Approval and rejection controls bind
the full approval id.

Do not present one representative command, truncate the set, reorder it, call it
atomic, or claim verification before execution. A mismatch routes back to the
producer. An approval activates the grant; a fresh owning specialist dispatch
executes one index per call.

## The decision the user makes IS the activation surface, not decoration

Grant activation is driven entirely by the structured decision the user makes on
the host surface -- there is no separate confirmation step, and no free-text
answer activates anything. The decision must carry the approval id into the
channel the host adapter transports: `extract_nonce_from_label` in
`hooks/modules/security/approval_grants.py` recovers the identifier's leading hex
characters from it, and `activate_db_pending_by_prefix` matches that prefix
against pending rows whose `id` starts with `P-{prefix}`.

If the identifier does not survive into that channel, nothing activates:
`activate_db_pending_by_prefix` is never called, no grant is inserted, and the
user's decision has no effect on the ledger -- the pending stays `PENDING` and
every retry of the originally blocked command re-blocks on the same
`approval_id`, indistinguishable from a decision never having been made. Carrying
the identifier correctly is therefore not a formatting nicety; it is the only
thing that turns the user's consent into a grant the hook layer will honor.

The exact form that carrying takes -- the control's required text, the
identifier's placement within it, and the host-specific spellings that silently
fail -- is owned by the adapter skill for the running host,
`<host>-consent-adapter`. Read it there: any concrete example written here would
be a mechanism from one host presented as if it were the protocol.

`gaia approvals approve <approval_id>` is a separate, CLI-only admin verb: it
writes `APPROVED` directly to the `approvals` table but does **not** call
`activate_db_pending_by_prefix` and does **not** create a hook-side grant. It
is not an alternate activation path -- running it does not make the blocked
command executable, and it does not trigger the automatic re-dispatch that
follows a genuine approval on the host surface. See `pending-approvals` for when
that admin verb is the right tool.
