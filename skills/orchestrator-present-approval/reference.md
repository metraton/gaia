# Approval presentation reference

The presentation source is the producer contract reconciled with the DB-backed
approval. The integrity check runs at activation, where the canonical REQUESTED
payload fingerprint is verified. Exact human presentation is still mandatory
for informed consent.

A COMMAND_SET has no layout of its own, and nothing here is composed. The
renderer emits one shape for one command and for many -- the indexed
`COMMANDS (N)` block of `template.md` -- so there is no table to build, no
aggregate to derive, and no per-item field to supply: `SCOPE`, `IMPACT`, `RISK`,
`ROLLBACK`, `VERIFICATION`, and `WINDOW` are sealed once for the whole set and
render once.
Composing a richer table would put lines in front of the user that the sealed
payload never declared, which is the one thing a consent surface may not do.

When a control label is the identity channel, the approve control binds only
through the complete canonical `[P-<32 lowercase hex>]` suffix. An 8-character
display label, a raw nonce, or a prefix does not resolve. The reject control
binds nothing because rejection creates no grant.

Do not present one representative command, truncate the set, reorder it, call it
atomic, or claim verification before execution. A mismatch routes back to the
producer. An approval activates the grant; a resumed owning specialist turn in
the already-known child executes one index per call.

## The decision the user makes IS the activation surface, not decoration

Grant activation is driven entirely by the decision the user makes -- there is no
separate confirmation step, and no reply that fails to resolve to the pending row
activates anything. The reply must be resolvable to the `approval_id`:
`hooks/modules/security/approval_grants.py::extract_approval_id_from_label`
recovers the complete canonical id, and
`hooks/modules/security/approval_grants.py::activate_db_pending_by_id` matches
that exact id against one pending row.

If the identifier does not survive into the reply, nothing activates:
`activate_db_pending_by_id` is never called, no grant is inserted, and the
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
ends with the complete canonical id bracketed as `[P-<32 lowercase hex>]`.
Compact display labels and raw nonces resolve to nothing.

`gaia approvals approve <approval_id>` is a separate, CLI-only admin verb. It
uses the shared typed activation service and therefore creates the matching
grant (`bin/cli/approvals.py::cmd_approve` ->
`gaia/approvals/store.py::activate_approval_atomically`). The orchestrator still
cannot use it: `hooks/modules/security/gaia_cli_only_guard.py::EXPLICITLY_DENIED_PHRASES`
categorically denies that authority boundary. The OpenCode host branch uses
`tool.execute.before` to provoke presentation in the bound specialist child and
resumes that same child by task id after the typed decision reaches safe session
idle; Claude Code uses `PreToolUse` and the question-result path.

OpenCode never creates a separate control child. `openBinaryDecision` sets the
control session to the approval's known specialist `sessionID`, and
`presentControl` calls `promptAsync` without replacing `agent` or `tools`, so
the specialist's host permissions survive. Controls append to that session's
FIFO; `presentNextControl` is their sole presentation scheduler and refuses to
advance while a retry is armed, a decision is deferred, or the session still
owes the post-decision idle transition.

The native question is binary and exactly correlated: one question, canonical
surface plus approval/correlation ids, exact ordered approve/reject labels,
emitted `custom: false` (or its host-normalized omission at pre-execution and
question-event boundaries), and one admitted decision lane. Any other `custom`
value fails closed. A chat response or free text
is not consent. The original attempted operation stays blocked, and an accepted
typed retry is not installed in `retryBySession` until `session.idle`; queued
approvals remain unpresented through that boundary and through retry settlement.

## Host vocabulary

OpenCode begins at `tool.execute.before`; its known specialist child observes
`question.asked`, `question.replied`, and host permission-compatibility replies. The bridge uses
`gaia approvals opencode-present` and `gaia approvals opencode-decide`, while
`opencode/plugin.ts::activationNotice` tells the orchestrator which already-known
`task_id` to resume; this is orchestration metadata, not a user instruction to
find or open a session.
Failures and lifecycle transitions are recorded as
`consent.decision.not_activated`, `consent.control.opened`,
`consent.control.closed`, `consent.decision.applied`, and
`consent.retry.refused` (`gaia/approvals/decision_audit.py`). Typed retry proof
is checked by `opencode/plugin.ts::evaluateConsentRetry` and
`hooks/adapters/opencode.py::OpenCodeAdapter::_consent_retry_rejection`.
