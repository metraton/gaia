---
name: orchestrator-present-approval
description: Use when processing APPROVAL_REQUEST with approval_id from a subagent -- enforces showing values before asking for user consent
metadata:
  user-invocable: false
  type: discipline
---

# Orchestrator Present Approval

```
The user approves EXACT VALUES, not summaries.
Every AskUserQuestion shows the literal command, every option label
names the specific action. No exceptions. No brevity shortcuts.
```

`orchestrator-present-approval` is the discipline the orchestrator follows when
an approval needs the user's consent: relay the sealed fields into
AskUserQuestion -- mandatory fields in the question, mandatory nonce in the
option label. The orchestrator has no shell, so it never dispatches a subagent
to derive or verify an approval; it presents from a trusted source it already
holds. For the subagent side that produced the payload see
`subagent-request-approval`; for the data contract itself see
`agent-approval-protocol`.

## Mental Model

The orchestrator sits between the subagent and the user. The user cannot make
an informed decision on data they have not seen -- a summary, a reference to
"the plan above", or an offer to show details on request all push the decision
without the data needed to decide. The job is **verbatim relay, not
re-authoring**: rewriting any of the sealed fields would change the consent
surface from what was recorded. Integrity of the payload is enforced at grant
**activation** (`verify_fingerprint` in `gaia/approvals/chain.py`, called when
the user selects the Approve label), not at presentation -- so presentation
itself never needs a verify-dispatch.

## Step 0 -- Present from a trusted source; never dispatch to verify or derive

The orchestrator has no shell. It MUST NOT dispatch a subagent solely to derive
or verify an approval before presenting -- that dispatch is both unnecessary
(the integrity check runs at activation, below) and harmful (its SubagentStop
can sweep the very pending being verified). Approvals are **in-loop and
single-session**: there is no per-turn feed of previously-seen pendings
anymore. Present from one of two **trusted**, in-session sources:

1. **The subagent's same-turn relayed `approval_request`.** This is the normal
   case: the pending was freshly minted THIS turn by a trusted dispatch, and
   integrity is enforced at grant **activation** (`verify_fingerprint` fires
   when the user selects the Approve label), not at presentation -- so no
   pre-presentation verify dispatch is needed.
2. **An explicit, user-invoked lookup.** If the user asks again later in the
   same session ("ver P-XXXX", "aprobar P-XXXX" without a fresh relay in this
   turn), read the pending via `gaia approvals show P-XXXX` per
   `pending-approvals` -- a direct, user-driven query, not a proactive feed.

There is no automatic resurfacing of pendings across turns or sessions; do not
look for or expect an injected verified-pendings block.

**The singular in-loop path is the clean case.** When the hook blocks one T3
command, the subagent receives the `approval_id` in the `[T3_BLOCKED]` message
and relays it in its `approval_request`. You present directly from that relay --
the id is right there, no shell and no block needed.

**A `command_set` batch arrives the same way.** When a subagent chains >= 2 T3
sub-commands in one Bash call (e.g. `git add -A && git commit -m 'v1.2.0' &&
git push origin main`) and the hook classifies >= 2 of them as ungranted T3, it
mints ONE `COMMAND_SET` pending at block time
(`bash_validator._validate_compound_command`) with a content-derived
`approval_id` (`derive_command_set_id` in `gaia/approvals/store.py`,
`P-<first 32 hex of sha256(canonical(command list))>`), and denies the Bash
call with the same `[T3_BLOCKED]` shape as a singular block. The subagent
relays that `approval_id` in its `approval_request` -- exactly like the
singular path, no shell and no derive step needed on your side. There is no
plan-first declaration to wait for: the batch id is always already in the
relay you received this turn.

## Mandatory presentation -- 5 labeled fields + nonce-suffixed label

The AskUserQuestion `question` MUST contain these 5 labeled fields, extracted
verbatim from `sealed_payload`:

```
APPROVAL REQUIRED

OPERACION:  {sealed_payload.operation}
COMANDO:    {sealed_payload.exact_content}     <-- verbatim, never paraphrased
SCOPE:      {sealed_payload.scope}
RIESGO:     {sealed_payload.risk_level} -- {sealed_payload.rationale}
ROLLBACK:   {sealed_payload.rollback_hint or "NOT REVERSIBLE"}
```

The Approve option label MUST follow `"Approve -- {specific_action} [P-{nonce8}]"`,
where `nonce8` is the first 8 hex chars of `approval_id` after `P-`. The label
regex in `extract_nonce_from_label` (`hooks/modules/security/approval_grants.py`)
requires the leading `Approve` and the `[P-<hex>]` tag;
`activate_db_pending_by_prefix` matches the captured prefix against pending rows
whose `id` starts with `P-{prefix}`. Without the suffix no grant is created.

See `template.md` for the canonical layout and `reference.md` -> "GOOD vs BAD
Examples" for full presentations.

Fields above are extracted from your trusted source -- the subagent's relayed
`approval_request` (or, for a later-turn user query, the `gaia approvals show`
result). In the `approval_request` the rollback field arrives under the key
`rollback`; from `gaia approvals show` it arrives as `rollback_hint`. Map
either to ROLLBACK the same way. Either way you copy values verbatim; you do
not re-author them.

## Rules

1. **Copy `exact_content` byte-for-byte.** Grants match by statement signature.
   A redirect, a `cd` prefix, a `time` wrapper, or an unapproved flag is a
   different statement and an immediate re-block on the retry. The runtime grant match is semantic (see `execution`), but the discipline at presentation is verbatim — any drift you tolerate at relay can become a re-block at retry.

2. **Single-use, consumed at match, 5-minute TTL.** Approval inserts one
   `SCOPE_SEMANTIC_SIGNATURE` grant that is consumed **at the moment the
   retried command matches it** -- before it executes, not after
   (`consume_db_semantic_grant` in `gaia/store/writer.py`) -- and lives for a
   5-minute TTL. A second invocation, or a retry after the command executed and
   failed, is a new APPROVAL_REQUEST. The one case the grant survives is a
   dispatch that dies before reaching the command: a re-dispatch within the
   5 minutes reuses the still-alive grant.

3. **Approving IS the order to execute.** When the user selects the Approve
   label, the ElicitationResult hook activates the grant and the orchestrator
   **immediately re-dispatches the verbatim command** -- there is no separate
   "should I run it now?" turn. Approve and execute are one coupled action.

4. **Batch grant is `COMMAND_SET` -- one consent, N commands, id arrives in the
   same relay.** Legacy `verb_family` was removed; its replacement,
   `COMMAND_SET`, is wired end-to-end in the hook layer (intake, activation,
   consume). When a subagent chains >= 2 T3 sub-commands in one Bash call and
   the hook classifies >= 2 of them as ungranted T3,
   `bash_validator._validate_compound_command` mints ONE pending `COMMAND_SET`
   **at block time**, with a content-derived `approval_id`, and denies the
   Bash call with the same `[T3_BLOCKED]` shape as a singular block. The
   subagent relays that `approval_id` -- together with the `commands` /
   `command_set` fields the hook built -- in the same `approval_request` it
   would use for one command; there is no separate no-`approval_id` shape to
   wait for. You present a single approval: list **all N commands** in the
   question body, but use **one** Approve label with **one** `[P-{nonce8}]`
   suffix -- one consent covers the whole batch. On approval,
   `activate_db_pending_by_prefix` Step 3b creates a single `COMMAND_SET` grant
   (5-minute TTL, aligned to the singular grant); each command is consumed
   byte-for-byte at its match, before it executes. `batch_scope` is still ignored
   (the signal is `command_set`). See `reference.md` -> "On batch intents".

   You present the batch the subagent's chained command produced; you do not
   steer it toward chaining. Whether grouping is warranted is the subagent's
   judgment made before it attempts the chain (see `subagent-request-approval`).
   A singular approval arriving where you imagined a batch is not a defect to
   correct: the default is just-in-time, and a batch a subagent would have
   manufactured by chaining unrelated commands asks the user to consent to
   work that does not need to run together.

5. **Re-dispatch, do not resume.** `mode` does not survive a SendMessage resume:
   the resume runs in `default` and re-blocks the next protected operation even
   after the Gaia grant activated. The automatic execute-on-approve of Rule 3 is
   therefore always a fresh re-dispatch with the same `mode` and the verbatim
   `exact_content`, never a SendMessage resume; the DB grant lives in the session
   and is found by the re-dispatched subagent. See `reference.md` ->
   "Re-dispatch instead of resume" for the underlying mechanism (mode is
   per-dispatch).

## Traps

Each row names a distinct way the consent surface goes false. For BAD-vs-GOOD
wording, see `reference.md` -> "GOOD vs BAD Examples", "Option Label Patterns",
"Cosmetic drift", and "Scope Mismatch".

| If you're thinking... | The reality is... |
|---|---|
| **Show specifics on both surfaces** -- "I can summarize / the label is enough" | The COMANDO field in the question body must be the verbatim command (not a summary, not "the above"); the option label must name the specific action (not just "Approve"). The user sees both surfaces; missing specificity on either is a blind-consent failure. |
| "I'll skip the [P-...] suffix, it's cosmetic" | The hook extracts the nonce from the label to find the right pending row; without it, targeted activation fails and no grant is created. |
| "Similar command, slightly different path -- I'll reuse / wrap it" | Grants match the statement signature byte-for-byte. Any wrapper, redirect, flag, or path drift is a different signature and a fresh re-block. |
| "The same command emitted a new approval_id" | Grants are single-use, consumed at match (before execution). A second run -- or a retry after the command executed and failed -- is a new APPROVAL_REQUEST. Approve again. |
| "After they approve, I'll ask whether to run it" | Approving IS the order to execute. On the Approve label the orchestrator immediately re-dispatches the verbatim command -- no intermediate confirmation turn. |
| "I'll set batch_scope to approve many at once" | `batch_scope` is ignored -- but a real batch path exists: a subagent chaining >= 2 T3 sub-commands in one Bash call gets blocked with ONE pending `COMMAND_SET` and one `approval_id`, same as a singular block. Present that single approval (N commands shown, one `[P-...]` nonce, one consent), not N separate approvals. |
| "I can paraphrase a field before relaying" | The fingerprint covers all sealed fields and is checked at grant **activation** (`verify_fingerprint`, when the user selects the Approve label); a paraphrase there raises `ChainTamperError` and the grant never forms. Relay verbatim so activation succeeds. |
| "I'll wait for the pending to resurface next turn / next session" | There is no cross-turn or cross-session resurfacing anymore -- no `[ACTIONABLE]` SessionStart block, no per-turn verified-pendings feed. Approvals are in-loop and single-session: present from the subagent's same-turn relayed `approval_request` (or a user's explicit `gaia approvals show`), and resolve within the session. |
| **"I'll dispatch a subagent to verify or derive the approval before presenting"** | The orchestrator must NEVER dispatch a subagent to verify or derive an approval, singular or `COMMAND_SET`. Present from the subagent's same-turn relayed `approval_request`; integrity is enforced at grant **activation** (`verify_fingerprint`), not at presentation, so a pre-presentation verify is unnecessary. A `COMMAND_SET`'s content-derived `approval_id` arrives in the same relay as a singular block -- there is nothing to derive. |
