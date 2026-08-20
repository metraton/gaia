---
name: orchestrator-present-approval
description: Use when presenting a returned APPROVAL_REQUEST for informed user consent
---

# Present Approval — Orchestrator Branch

Present trusted contract data exactly. Do not execute, derive, shorten, reorder,
or silently expand it.

## The surface is neutral; the mechanism that carries it is the adapter's

Two things are separate and must stay separate. WHAT the user sees is one
host-neutral surface: rendered from the sealed payload, identical on every host,
with its exact shape, field set, render order and absence semantics stated in
`template.md`. HOW that surface reaches the user -- the host's structured
decision primitive, the form of the control the user selects, and the channel by
which the selection becomes a grant -- belongs to the adapter for the host you
are running under, and is documented in a skill named
`<host>-consent-adapter`. Load that skill alongside this one before presenting;
this skill does not restate its mechanics, and a host mechanism named here would
be wrong on every other host.

## The activation channel is structured, and it must carry the approval id

**Consent activates through the host's structured decision surface, never
through free text.** The selection the user makes must carry the approval id --
its full value or its leading hex characters, depending on what the adapter's
channel transports -- because that identifier is what the hook layer resolves to
the pending row before creating the grant
(`extract_nonce_from_label` -> `activate_db_pending_by_prefix`,
`hooks/modules/security/approval_grants.py`).

If the identifier does not reach that channel, nothing activates: no grant is
inserted, the ledger stays `PENDING`, and every retry of the blocked command
re-blocks on the same `approval_id` -- indistinguishable from a decision never
having been made, while the user believes they consented. That is the incident
this skill exists to prevent, and it is exactly as reachable on a single command
as on a COMMAND_SET. The exact form the identifier must take, and the failure
modes specific to it, are the adapter skill's -- read it there rather than
guessing a shape here.

**One decision activates one approval.** Present one approval per decision. The
reason is presentation hygiene, not activation loss: a host event that answers
several signed labels now activates every one of them, so grouping no longer
drops a grant. What grouping still costs is the user's ability to read what they
are signing -- several exact commands folded into one decision is one signature
over a surface nobody consented to field by field. How many a single host
interaction can carry is an adapter property, stated by the adapter skill. Before dispatching
execution, confirm with `gaia approvals show <approval_id>` that the approval you
intend to execute actually left `pending`.

## Singular vs COMMAND_SET presentation

The question body is not composed here: it is the surface rendered from the
sealed payload, and `template.md` states its exact shape, field set, render
order and absence semantics. A singular request and a COMMAND_SET share that one
shape -- the same indexed `COMMANDS (N)` block carries one command or many -- so
there is no second layout to choose between and no field to decide about. Show
it verbatim.

One approval control covers the whole set, never one per command. Do not call a
COMMAND_SET atomic: consent is grouped, execution is separate, ordered, and
fail-fast. Do not claim verification has happened; this is the pre-execution
consent point, and the surface's `VERIFICATION` field states what to check
afterwards.

Approval activation verifies the REQUESTED fingerprint. Presentation must still
be exact because informed consent depends on what the human sees. If the
contract is incomplete, reordered, mismatched, or ambiguous, do not repair it;
route back to the producer.

## Who activates, who executes

If the user approves, the orchestrator dispatches a fresh owning specialist
with the grant context and `execution` skill; the orchestrator never runs the
commands itself. `gaia approvals approve` is a separate, CLI-only admin verb
that writes the DB directly and does **not** create a hook-side grant -- it is
not the activation path, and it is not available to the orchestrator: the
trusted-CLI role guard (`hooks/modules/security/gaia_cli_only_guard.py`,
`EXPLICITLY_DENIED_PHRASES`) categorically denies `approvals approve` /
`revoke` / `reject` / `reject-all` / `clean` / `replay` for the orchestrator
role, non-approvable -- the orchestrator may only *read* approval state
(`approvals list` / `show` / `pending` / `history` / `stats`, in
`ALLOWED_READ_PHRASES`). Reads are the orchestrator's; approval decisions are
not -- they happen exclusively through the decision the user makes on the host
surface.
