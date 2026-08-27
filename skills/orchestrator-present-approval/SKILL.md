---
name: orchestrator-present-approval
description: Use when presenting a returned APPROVAL_REQUEST for informed user consent
---

# Present Approval — Orchestrator Branch

Present trusted contract data exactly. Do not execute, derive, shorten, reorder,
or silently expand it.

## The surface is rendered, never composed

WHAT the user sees is produced Gaia-side from the sealed payload by one
function -- `hooks/adapters/consent_presentation.py::render_native_text`, over
that module's `VISIBLE_FIELDS` table -- and it comes out identical on every
host. Its exact shape, field set, render order and absence semantics are stated
in `template.md`. Show that text verbatim. Compose nothing, summarise nothing,
reorder nothing, translate no label.

## Present the surface as text, then ask a minimal decision

A presentation is two pieces. Print the rendered surface verbatim as console
text -- the MESSAGE. Then ask a binary decision -- the QUESTION -- carrying one
line of operation, the command count, and the approval id, and nothing else.
`template.md` states both shapes literally, together with the four rules that
make the split safe: the id on both ends, adjacency with a reprint duty, the
minimum the question carries, and a binary control set with no `always`.

That order is deliberate and the reason is narrow. When the surface travels
*inside* a host's decision payload, whether the host's renderer displays every
line of it is unverifiable from outside that host: nothing you can read back
confirms the user saw the `ROLLBACK` line, or the eighth command's fingerprint,
or anything past a truncation the renderer applied silently. Consent over a
truncated surface is exactly the failure this whole protocol exists to prevent,
and that exposure stands recorded as unmitigated -- closable only by real
end-to-end work against each host, never by a claim made here. Printed as
console text, completeness stops depending on a renderer nobody can inspect and
starts depending on the user's own terminal.

The choice has a real cost, stated here rather than buried. The binding between
what is shown and what is answered stops being CONTAINMENT and becomes
ADJACENCY WITH VERIFIABLE IDENTITY ON BOTH SIDES. That is weaker -- containment
could not fail to hold, adjacency can. What is bought for it is that truncation
stops being an uninspectable property of someone else's renderer. Rule 2 in
`template.md` -- reprint the surface if anything intervened before the question
-- is what keeps that trade honest; without it the design is not a weaker
binding, it is no binding at all.

## The reply must resolve to the approval id

The user's reply must be resolvable to the `approval_id`, because that
identifier is what the hook layer matches against the pending row before any
grant exists (`hooks/modules/security/approval_grants.py::extract_approval_id_from_label`
feeding `hooks/modules/security/approval_grants.py::activate_db_pending_by_id`).

How the identifier gets there turns on one condition:

- If the host's reply carries its own correlation handle back to the request
  that produced it, that handle suffices. The id does not need to be in the
  control at all.
- If the reply carries no correlation of its own -- and a host may return
  nothing but the text of the control the user selected -- then the id MUST
  travel in that text, because that text is the only thing that comes back.

For the second case, Gaia's resolver reads one form and only that form: the
selected control's text must begin with the literal English word `Approve` and
must end with the complete canonical id bracketed as `[P-<32 lowercase hex>]`.
A translated verb, any paraphrase of `Approve`, a short display label, a raw
nonce, or suffix text after the bracket all read as no identifier. Activation
then resolves that exact id; it never scans for a matching prefix.

## What a harness has to be able to do

Print text, and offer two controls where the chosen one returns either its own
text or a correlation handle back to the request. That is the entire
requirement. No harness renders the seven fields, none parses the sealed
payload, none needs to know the field set, and none needs a decision primitive
richer than two controls -- the split moved all of that Gaia-side, which is the
practical dividend of printing the surface as text.

Stated that way on purpose: this skill names no host. A harness either can do
those two things or cannot, and that is decidable without per-host prose here.
Host-specific instructions are the shape that went stale fastest -- the retired
adapter documentation asserted a host's semantics against code that had already
moved -- so what a particular host calls its controls belongs in that host's
adapter, never in this skill.

## What failure looks like

If the reply resolves to no pending row, nothing activates. No grant is
inserted, the ledger stays `PENDING`, and every retry of the blocked command
re-blocks on the same `approval_id`. The outcome is indistinguishable from a
decision never having been made -- while the user believes they consented. That
is the incident this skill exists to prevent, and it is exactly as reachable on
a single command as on a COMMAND_SET.

The residual risk has a fixed direction, which is the one reassurance
available: resolution is the sole predicate, and nothing else reads the reply,
so an unresolvable reply under-grants. No reply shape over-grants.

**A reply that resolves to nothing is a finding, not a no-op.** Silence is what
makes this failure dangerous, so a reply the resolver could not match is
surfaced and reported, never absorbed as if the user had simply declined.
Before dispatching execution, confirm with `gaia approvals show <approval_id>`
that the approval you intend to execute actually left `pending`. Presentation
and activation are separate events, and that read is the only thing that
distinguishes a grant that activated from one that is silently still pending.

## One decision per presentation

Present one approval at a time, so the user can read what they are signing.
Several exact commands folded into one decision is one signature over a surface
nobody consented to field by field. This is presentation hygiene, and it is
deliberately stated without a number: how many decisions a single interaction
can carry is a property of the host's primitive, and belongs in adapter code
rather than in this prose.

## Singular vs COMMAND_SET presentation

There is no layout to choose. A singular request and a COMMAND_SET are the same
rendered shape -- the same indexed `COMMANDS (N)` block carries one command or
many -- so there is no second form to select and no field to decide about.
`template.md` states that shape; show it verbatim.

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
commands itself.

The user's decision on the presented surface IS the activation, and it creates
the grant in the same call: `activate_db_pending_by_id` reads the pending
payload's scope and inserts the very row the guard will read -- `create_command_set_grant`
for a `COMMAND_SET`, `gaia.store.writer.insert_file_path_grant` for a
`SCOPE_FILE_PATH` protected-path Write/Edit, `gaia.store.writer.insert_semantic_grant`
for a single command. Nothing further has to be run to arm the approval, which
also means the grant's window opens at the DECISION, not at the retry: a
re-dispatched specialist is spending that window while it grounds itself. Read
the remaining window with `gaia approvals show <approval_id>` (`grant_state`,
`expires_at`) before dispatching, and re-present rather than dispatch into a
window that has closed.

`gaia approvals approve` is a separate, CLI-only admin verb
that writes the DB directly and does **not** create a hook-side grant -- it is
not the activation path, and it is not available to the orchestrator: the
trusted-CLI role guard (`hooks/modules/security/gaia_cli_only_guard.py`,
`EXPLICITLY_DENIED_PHRASES`) categorically denies `approvals approve` /
`revoke` / `reject` / `reject-all` / `clean` / `replay` for the orchestrator
role, non-approvable -- the orchestrator may only *read* approval state
(`approvals list` / `show` / `pending` / `history` / `stats`, in
`ALLOWED_READ_PHRASES`). Reads are the orchestrator's; approval decisions are
not -- they happen exclusively through the decision the user makes.
