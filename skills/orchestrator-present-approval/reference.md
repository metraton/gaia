# Approval presentation reference

The presentation source is the producer contract reconciled with the DB-backed
approval. The integrity check runs at activation, where the canonical REQUESTED
payload fingerprint is verified. Exact human presentation is still mandatory
for informed consent.

A COMMAND_SET has no layout of its own, and nothing here is composed. The
renderer emits one shape for one command and for many -- the indexed
`COMMANDS (N)` block of `template.md` -- so there is no table to build, no
aggregate to derive, and no per-item field to supply: `SCOPE`, `IMPACT`, `RISK`,
`ROLLBACK` and `VERIFICATION` are sealed once for the whole set and render once.
Composing a richer table would put lines in front of the user that the sealed
payload never declared, which is the one thing a consent surface may not do.

The approve control binds the approval id through the 8-character nonce tag, not
through the full identifier -- the full `approval_id` substituted for that tag
resolves to nothing. The reject control binds nothing at all: a rejection has no
pending row to activate.

Do not present one representative command, truncate the set, reorder it, call it
atomic, or claim verification before execution. A mismatch routes back to the
producer. An approval activates the grant; a fresh owning specialist dispatch
executes one index per call.

## The decision the user makes IS the activation surface, not decoration

Grant activation is driven entirely by the decision the user makes -- there is no
separate confirmation step, and no reply that fails to resolve to the pending row
activates anything. The reply must be resolvable to the `approval_id`:
`hooks/modules/security/approval_grants.py::extract_nonce_from_label` recovers the
identifier's leading hex characters, and
`hooks/modules/security/approval_grants.py::activate_db_pending_by_prefix` matches
that prefix against pending rows whose `id` starts with `P-{prefix}`.

If the identifier does not survive into the reply, nothing activates:
`activate_db_pending_by_prefix` is never called, no grant is inserted, and the
user's decision has no effect on the ledger -- the pending stays `PENDING` and
every retry of the originally blocked command re-blocks on the same
`approval_id`, indistinguishable from a decision never having been made. Carrying
the identifier correctly is therefore not a formatting nicety; it is the only
thing that turns the user's consent into a grant the hook layer will honor. A
reply that resolves to nothing is reported as a finding, not absorbed as a
decline.

Whether the identifier has to be in the control's text at all is conditional. A
reply carrying its own correlation handle back to the request already resolves;
a reply carrying none resolves only through what it does return, which is the
text of the control the user selected. For that second case Gaia's resolver
reads one form: the text begins with the literal English word `Approve` and
carries the id's leading hex characters bracketed as `[P-<hex>]`, with the full
8-character nonce, since `activate_db_pending_by_prefix` takes the first
prefix-matching pending row and reports no ambiguity on a multi-row match.

`gaia approvals approve <approval_id>` is a separate, CLI-only admin verb: it
writes `APPROVED` directly to the `approvals` table but does **not** call
`activate_db_pending_by_prefix` and does **not** create a hook-side grant. It
is not an alternate activation path -- running it does not make the blocked
command executable, and it does not trigger the automatic re-dispatch that
follows a genuine approval decision by the user. See `pending-approvals` for when
that admin verb is the right tool.
