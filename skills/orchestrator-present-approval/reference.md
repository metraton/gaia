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

## Approve label format is the activation surface, not decoration

Grant activation is driven entirely by the AskUserQuestion option label the
user selects -- there is no separate confirmation step. `extract_nonce_from_label`
in `hooks/modules/security/approval_grants.py` applies `_APPROVE_NONCE_RE`,
`^Approve\b.*\[P-([a-f0-9]+)\]`, to the chosen label: it must start with the
literal English word `Approve` and contain a bracketed `[P-{nonce8}]` tag,
`nonce8` being the first 8 hex characters of the approval id after `P-`.
`activate_db_pending_by_prefix` then matches that captured prefix against
pending rows whose `id` starts with `P-{prefix}`.

A label that fails the regex -- a translated verb ("Aprobar"/"Rechazar"), a
paraphrase, or the bare/full approval id with no brackets -- extracts no nonce.
`activate_db_pending_by_prefix` is never called, no grant is inserted, and the
user's decision has no effect on the ledger: the pending stays `PENDING` and
every retry of the originally blocked command re-blocks on the same
`approval_id`, indistinguishable from a decision never having been made.
Getting the label right is therefore not a formatting nicety -- it is the only
thing that turns the user's consent into a grant the hook layer will honor.

Example GOOD label: `Approve -- push branch flux-system and open PR [P-4bd2b170]`.
Example BROKEN labels: `Aprobar`, `Approve P-4bd2b170` (no brackets),
`Approve <full approval_id>` (no `[P-...]` tag), `Si, ejecutar`.

`gaia approvals approve <approval_id>` is a separate, CLI-only admin verb: it
writes `APPROVED` directly to the `approvals` table but does **not** call
`activate_db_pending_by_prefix` and does **not** create a hook-side grant. It
is not an alternate activation path -- running it does not make the blocked
command executable, and it does not trigger the automatic re-dispatch that
follows a genuine AskUserQuestion approval. See `pending-approvals` for when
that admin verb is the right tool.
