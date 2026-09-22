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

For an existing pending id, read both presentation pieces with `gaia approvals
show <approval_id> --consent-surface`. The JSON `visible_text` field is that
rendered surface byte-for-byte; print it unchanged. Use the JSON
`approve_label` field unchanged for the approval control -- Gaia sources it from
`render_approve_label`, so the presenter must not rebuild it. This read records
no `SHOWN` event and makes no decision or grant.

## Select the host modality before presenting or asking

Consent data is host-neutral; control flow is not. Determine the active host
first, then follow its branch below. On Claude Code, presentation is a printed
MESSAGE followed by a binary QUESTION. On OpenCode, the owning specialist's
attempt provokes the host-managed question in the already-known specialist
child; that question is native and binary. Activation is followed by resuming that same specialist session by `task_id`.
Calling an ask tool before making this selection is therefore wrong:
it resolves Claude Code consent but activates nothing on OpenCode.

`template.md` documents both transports while keeping the Gaia-rendered surface
single-source. Its printed-message adjacency rules apply only to the Claude Code
branch; OpenCode carries the same rendered `visible_text` inside the native
question in the specialist child it already knows. It does not create or
discover a separate control child.

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

## Two ways consent reaches Gaia

The rendered surface is host-neutral. How the user's answer becomes an
activated grant is not: there are two modalities, and the host you are running
in fixes which one applies. Selecting the wrong one produces a user who
believes they consented and a ledger that never heard it.

**Resolved by reply -- Claude Code.** Print the surface as text, then ask the
binary question. The selected control's text comes back through the host's
question result, the resolver reads the `approval_id` out of it and activates
the grant in that same call. The harness needs exactly two things: print text,
and offer two controls whose chosen one returns its own text or a correlation
handle. Nothing renders the eight visible fields or parses the sealed payload
host-side.

**Provoked by attempt -- OpenCode.** Printing the surface and asking the
question activates nothing there. Dispatch the owning specialist with
`execution` to attempt the operation; the host matches the attempt to the
already-pending id and queues its control on that specialist's already-bound
child. The plugin prompts that same child with the canonical rendered surface
and the native binary question. Its `promptAsync` body does not replace the
child's agent or tool permissions. No second request is minted and no newly
uncached control child exists.

The attempt ENDS the specialist's turn. On OpenCode the block is a tool error,
and a tool error terminates the specialist's capacity to wait: it closes
`APPROVAL_REQUEST` and returns. "Stay in the turn until the user answers" is an
instruction no OpenCode specialist can follow, and a plan that relies on it
leaves an armed grant with nobody to use it (measured 2026-09-16: the user
approved 55 seconds before the orchestrator closed its own turn). The retry
therefore has a second dispatch, and its shape is fixed: once the user
activates the approval, the host notifies the ROOT session with the specialist
session id and command index. The orchestrator consumes that notice and
re-dispatches the specialist with `task_id` set to the already-known child and
`execution` as the instruction; the user is never asked to locate or open an
internal session. The typed grant is bound to that same session, agent, tool
family, and exact command or path. A dispatch without that binding is not the
designed retry.

The answer does not make the blocked operation executable immediately. The
original attempt remains blocked, and the plugin holds the typed retry
descriptor outside the executable retry map until the specialist session emits
`session.idle`. Only that safe idle transition arms the exact retry and notifies
the root. Chat text, custom answers, and approval prose cannot take that
transition: only one of the two labels from the exact correlated native
question is a decision.

In one line: on Claude Code consent is resolved by reply; on OpenCode it is
provoked by attempt and resumed by re-dispatch. This skill names the two hosts
because the selection is the orchestrator's decision, not an adapter detail;
what each host calls its controls still belongs in its adapter.

## What failure looks like

On Claude Code, the reply resolves to no pending row. Nothing activates, no
grant is inserted, the ledger stays `PENDING`, and every retry of the blocked
command re-blocks on the same `approval_id` -- while the user believes they
consented. That is the incident this skill exists to prevent, and it is exactly
as reachable on a single command as on a COMMAND_SET. The residual risk has a
fixed direction: resolution is the sole predicate and nothing else reads the
reply, so an unresolvable reply under-grants and no reply shape over-grants. A
reply that resolves to nothing is a finding, not a no-op: report it, then
re-present with a well-formed control.

On OpenCode, one REQUESTED event with no presentation and a still-pending
decision is what text-only presentation produces every time. Re-presenting is
not a remedy; dispatch the specialist to attempt the operation. If the host
cannot open, decide, notify, or bind the typed retry, read the exact cause from
the specialist error and harness events, then report it rather than presenting
again. Concrete event and tool spellings live in `reference.md`.

On either host, before dispatching execution confirm with `gaia approvals show
<approval_id>` that the approval actually left `pending`. Presentation and
activation are separate events, and that read is the only thing that
distinguishes a grant that activated from one that is silently still pending.

## One decision per presentation

Present one approval at a time, so the user can read what they are signing.
Several exact commands folded into one decision is one signature over a surface
nobody consented to field by field. This is presentation hygiene, and it is
deliberately stated without a number: how many decisions a single interaction
can carry is a property of the host's primitive, and belongs in adapter code
rather than in this prose.

OpenCode enforces the same rule when approvals overlap. Each specialist child
has one guarded FIFO queue, and only its head may be presented. A second
approval remains queued while the first question is active, while its accepted
decision is waiting for safe idle, and while its typed retry remains unsettled.
The scheduler advances only after those states clear; a later reply cannot
activate an unpresented entry or overwrite the first retry.

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

If the user approves, the orchestrator resumes the already-known owning
specialist child in a new dispatched turn with the grant context and
`execution` skill; the orchestrator never runs the commands itself and never
asks the user to manage an internal session.

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

`gaia approvals approve` is a separate, CLI-only admin verb. Its shared typed
activation creates the matching grant, but it is not available to the
orchestrator: the
trusted-CLI role guard (`hooks/modules/security/gaia_cli_only_guard.py`,
`EXPLICITLY_DENIED_PHRASES`) categorically denies `approvals approve` /
`revoke` / `reject` / `reject-all` / `clean` / `replay` for the orchestrator
role, non-approvable -- the orchestrator may only *read* approval state
(`approvals list` / `show` / `pending` / `history` / `stats`, in
`ALLOWED_READ_PHRASES`). Reads are the orchestrator's; approval decisions are
not -- they happen exclusively through the decision the user makes.
