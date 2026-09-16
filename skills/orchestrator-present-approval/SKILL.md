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

## Two ways consent reaches Gaia

The rendered surface is host-neutral. How the user's answer becomes an
activated grant is not: there are two modalities, and the host you are running
in fixes which one applies. Selecting the wrong one produces a user who
believes they consented and a ledger that never heard it.

**Resolved by reply -- Claude Code.** Print the surface as text, then ask the
binary question. The selected control's text comes back through
`AskUserQuestion`, the resolver reads the `approval_id` out of it and activates
the grant in that same call. The harness needs exactly two things: print text,
and offer two controls whose chosen one returns its own text or a correlation
handle. Nothing renders the seven fields or parses the sealed payload host-side.

**Provoked by attempt -- OpenCode.** Printing the surface and asking the
question activates nothing there. On that host the reply crosses the bridge as
caller-supplied JSON, so the adapter strips the answer field from both tool
containers before the shared resolver reads them
(`hooks/adapters/opencode.py::_without_unverified_decision`) -- a forged
`tool.execute.after` would otherwise sign for the user. The only live path is
the host's native permission, and it is raised only when the specialist
ATTEMPTS the operation: the attempt is denied carrying the `approval_id` of the
set already pending (`hooks/modules/tools/bash_validator.py::_find_pending_plan_set_in_db`
matches the attempted command to it, so no second request is minted); the plugin
runs `gaia approvals opencode-present`, which records SHOWN and adopts the
session when the row was minted without one; the plugin opens a child control
session under the root and asks the binary question there; the reply runs
`gaia approvals opencode-decide`, which records the decision and arms the grant.
So on OpenCode the move after an APPROVAL_REQUEST is not to present anything:
dispatch the owning specialist with `execution` to attempt the first command of
the requested set, and let the host ask.

The attempt ENDS the specialist's turn. On OpenCode the block is a tool error,
and a tool error terminates the specialist's capacity to wait: it closes
`APPROVAL_REQUEST` and returns. "Stay in the turn until the user answers" is an
instruction no OpenCode specialist can follow, and a plan that relies on it
leaves an armed grant with nobody to use it (measured 2026-09-16: the user
approved 55 seconds before the orchestrator closed its own turn). The retry
therefore has a second dispatch, and its shape is fixed: once the user
activates the approval, the plugin prompts the ROOT session -- yours -- with
`Gaia: approval <id> activated by the user. Resume the specialist session
<sessionID> (task_id) so it retries command [<index>] now.`
(`opencode/plugin.ts::activationNotice`). Re-dispatch the specialist with
`task_id` set to that session id and `execution` as the instruction. The grant
is bound to that specialist session (`opencode/plugin.ts::consentRetry` requires
the same session, agent and command at the reserved index), so a fresh dispatch
without `task_id` falls back to the host-neutral pending-command match and
bypasses the plugin's retry accounting; the notice goes to the root rather than
to the specialist session because the specialist's turn is over -- a prompt
there would run it with no dispatch, no contract row and no coordinator reading
the result.

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

On OpenCode, the same picture -- one REQUESTED event, no SHOWN, status
`pending`, empty decision -- is what a text presentation produces every time,
however well-formed the control. Re-presenting is not a remedy there; it is a
loop that never terminates (measured live, 2026-09-16). The remedy is the other
modality: dispatch the specialist to attempt the first command. If that attempt
itself fails to raise the question, the error the specialist receives carries
the `approval_id` and the cause verbatim (`opencode/plugin.ts::requestApproval`
/ `openBinaryDecision`), and every point where the plugin gives up a control or
a decision leaves a row in `harness_events` (`gaia query --surface
harness_events`), written by `opencode/bridge.py` through
`gaia/approvals/decision_audit.py`. Read the cause there and act on it; do not
re-present. The records, by type:

- `consent.decision.not_activated` with `reason`: `presentation_failed` (`gaia
  approvals opencode-present` refused -- the session does not own the approval,
  or the surface could not be sealed), `control_plane_failed` (the host refused
  to create or prompt the control session), `decide_failed` (the user answered
  and `gaia approvals opencode-decide` refused the reply), `no_session_binding`
  (a host permission request matched no Gaia verdict).
- `consent.control.opened` (info): the question reached the host, in
  `control_session_id`. SHOWN alone does not say this.
- `consent.control.closed` with `reason`: `decided` (info; the answer reached
  Gaia) or one of the abandonments (warning): `question_mismatch`,
  `question_rejected`, `reply_unreadable`, `permission_reply_unusable`,
  `decision_duplicate`, `retry_conflict`, `prompt_rejected`, `session_ended`,
  `drifted_tool_call`, `drifted_tool_result`, `decide_failed`
  (`opencode/plugin.ts::ControlCloseReason`).
- `consent.decision.applied`: the grant was armed for `once`;
  `notified_session_id` is the root session that received the activation
  notice, empty (warning) with `notify_failure` when none could be told.
- `consent.retry.refused` (warning): after the yes, a tool call claimed the
  armed retry and was refused, with `expected` and `received` for the one
  comparison that failed. `lane` `opencode.plugin_gate` carries the plugin's
  `reason` (`opencode/plugin.ts::evaluateConsentRetry`): `replayed_call_id`,
  `session_mismatch`, `role_mismatch`, `out_of_order`, `fingerprint_mismatch`.
  `lane` `opencode.policy_gate` carries `proof_rejected` with the adapter's
  cause in `detail` (`hooks/adapters/opencode.py::_consent_retry_rejection`).
  The specialist's own error names the same approval, reason and pair, so a
  specialist reporting "Gaia refused consent retry for P-...: role_mismatch"
  is reporting this row, not asking for a new presentation.

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
