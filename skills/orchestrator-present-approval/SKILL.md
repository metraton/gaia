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

Print the rendered surface verbatim as console text. Then ask a binary
decision -- approve or reject -- whose approve control carries a one-line
summary of the operation and the approval id.

That order is deliberate and the reason is narrow. When the surface travels
*inside* a host's decision payload, whether the host's renderer displays every
line of it is unverifiable from outside that host: nothing you can read back
confirms the user saw the `ROLLBACK` line, or the eighth command's fingerprint,
or anything past a truncation the renderer applied silently. Consent over a
truncated surface is exactly the failure this whole protocol exists to prevent.
Presented as console text, completeness stops depending on a renderer nobody
can inspect.

The choice has a real cost, stated here rather than hidden: the text and the
decision become adjacent rather than nested, so the binding between them is no
longer structural. Two things preserve it. The approval id appears on BOTH --
the surface's header line carries it, and the approve control carries it -- so a
reader can verify that the thing they are approving is the thing they just read.
And the one-line summary on the control makes the decision identifiable without
scrolling back for it.

## The reply must resolve to the approval id

The user's reply must be resolvable to the `approval_id`, because that
identifier is what the hook layer matches against the pending row before any
grant exists (`hooks/modules/security/approval_grants.py::extract_nonce_from_label`
feeding `hooks/modules/security/approval_grants.py::activate_db_pending_by_prefix`).

How the identifier gets there turns on one condition:

- If the host's reply carries its own correlation handle back to the request
  that produced it, that handle suffices. The id does not need to be in the
  control at all.
- If the reply carries no correlation of its own -- and a host may return
  nothing but the text of the control the user selected -- then the id MUST
  travel in that text, because that text is the only thing that comes back.

For the second case, Gaia's resolver reads one form and only that form: the
selected control's text must begin with the literal English word `Approve` and
must carry the approval id's leading hex characters bracketed as `[P-<hex>]`. A
translated verb, any paraphrase of `Approve`, the hex without the brackets, or
the full `approval_id` substituted for the `[P-...]` tag all read as no
identifier at all.

Carry the full 8-character nonce.
`hooks/modules/security/approval_grants.py::activate_db_pending_by_prefix`
matches the FIRST pending row whose id starts with `P-<captured prefix>` and
neither detects nor rejects a multi-row match, so truncating further to save
space in the control is a real collision risk: two pendings sharing a short
prefix would silently activate the wrong one, with none of the ambiguity error
that `gaia approvals show` raises on the CLI side.

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
commands itself. `gaia approvals approve` is a separate, CLI-only admin verb
that writes the DB directly and does **not** create a hook-side grant -- it is
not the activation path, and it is not available to the orchestrator: the
trusted-CLI role guard (`hooks/modules/security/gaia_cli_only_guard.py`,
`EXPLICITLY_DENIED_PHRASES`) categorically denies `approvals approve` /
`revoke` / `reject` / `reject-all` / `clean` / `replay` for the orchestrator
role, non-approvable -- the orchestrator may only *read* approval state
(`approvals list` / `show` / `pending` / `history` / `stats`, in
`ALLOWED_READ_PHRASES`). Reads are the orchestrator's; approval decisions are
not -- they happen exclusively through the decision the user makes.
