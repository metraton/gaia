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
can sweep the very pending being verified). Instead, present from one of two
**trusted** sources:

1. **Primary -- the injected `[PENDING-APPROVALS-VERIFIED]` block.** A per-turn
   hook (`hooks/modules/session/session_manifest.py`) injects, on every
   `UserPromptSubmit`, every pending that has survived >= 1 turn. Each row in
   that block has already been DB-read and fingerprint-verified by the hook
   (`build_verified_pending_approvals` -- only rows whose payload re-canonicalizes
   to the fingerprint stored on their `REQUESTED` event appear, each marked
   `verified: true`). **Present directly from this block** -- the fields, the
   full `approval_id`, and (for batches) the whole `command_set` with its minted
   id are all there. No DB query, no `derive-id`, no dispatch.
2. **Fallback -- same-turn relay.** A pending a subagent emits during the
   CURRENT turn will not be in this turn's block yet: the block is built at
   `UserPromptSubmit`, before the subagent ran. For that case present from the
   subagent's relayed `approval_request`. This is justified because the pending
   was freshly minted in THIS session by a trusted dispatch, AND integrity is
   enforced at grant **activation** (`verify_fingerprint` fires when the user
   selects the Approve label), not at presentation. The old pre-presentation
   verify was redundant belt-and-suspenders; it is removed.

Once the pending survives a turn it appears in the injected block, so the relay
is only ever needed for the same-turn case.

**For a `command_set` (plan-first batch) you do not derive the id -- you read it
from the block.** The hook mints the `approval_id` at SubagentStop
(`_intake_command_set_pending` -- see Rule 3) from the **content** of the
command_set (`derive_command_set_id` in `gaia/approvals/store.py`,
`P-<first 32 hex of sha256(canonical(command list))>`). Once that pending has
survived a turn, the `[PENDING-APPROVALS-VERIFIED]` block carries it with its
minted `approval_id` and all N commands already attached -- so you read the id
and the commands straight from the block. **No `gaia approvals derive-id`
dispatch is needed.** For a command_set emitted in the CURRENT turn (not yet in
the block), present from the subagent's relayed `approval_request`, which carries
the same `command_set`; the content-derived id reaches you when the pending
appears in the next turn's block.

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

Fields above are extracted from your trusted source. From the injected
`[PENDING-APPROVALS-VERIFIED]` block (the primary path) they appear under the
canonical names shown here (`operation`, `exact_content`, `scope`, `risk_level`,
`rationale`, `rollback_hint`). From a same-turn relayed `approval_request` (the
fallback) the rollback field arrives under the key `rollback` -- map it to
ROLLBACK the same way. Either way you copy values verbatim; you do not re-author
them.

## Rules

1. **Copy `exact_content` byte-for-byte.** Grants match by statement signature.
   A redirect, a `cd` prefix, a `time` wrapper, or an unapproved flag is a
   different statement and an immediate re-block on the retry. The runtime grant match is semantic (see `execution`), but the discipline at presentation is verbatim — any drift you tolerate at relay can become a re-block at retry.

2. **Single-use, no carry-over.** Approval inserts one `SCOPE_SEMANTIC_SIGNATURE`
   grant consumed by the first retry (`consume_db_semantic_grant` in
   `gaia/store/writer.py`). A second invocation is a new APPROVAL_REQUEST.

3. **Batch grant is `COMMAND_SET` -- one consent, N commands.** Legacy
   `verb_family` was removed; its replacement, `COMMAND_SET`, is now wired
   end-to-end (intake, activation, consume). When a subagent emits a plan-first
   `APPROVAL_REQUEST` carrying a `command_set` of >= 2 `{command, rationale}`
   items and **no** `approval_id`, the SubagentStop processor
   (`handoff_persister._intake_command_set_pending`) mints ONE pending
   `COMMAND_SET` with one content-derived `approval_id`. Once that pending has
   survived a turn it appears in the injected `[PENDING-APPROVALS-VERIFIED]`
   block with its minted `approval_id` and all N commands -- **read the id and
   commands from the block; do not dispatch `gaia approvals derive-id`.** (A
   command_set emitted in the current turn is presented from the subagent's
   relayed `approval_request`.) You present that single approval: list
   **all N commands** in the question body, but use **one** Approve label with
   **one** `[P-{nonce8}]` suffix -- one consent covers the whole batch. On
   approval, `activate_db_pending_by_prefix` Step 3b creates a single
   `COMMAND_SET` grant (60-min TTL); each command is consumed byte-for-byte on
   its own retry. `batch_scope` is still ignored (the signal is `command_set`).
   See `reference.md` -> "On batch intents".

   You present the batch the subagent chose to send; you do not steer it toward
   batching. Whether grouping is warranted is the subagent's judgment (known
   batch, >= 2, friction reduced -- see `subagent-request-approval`). A singular
   approval arriving where you imagined a batch is not a defect to correct: the
   default is just-in-time, and a batch you would have manufactured asks the
   user to consent to commands that may never run.

4. **Re-dispatch, do not resume.** `mode` does not survive a SendMessage resume:
   the resume runs in `default` and re-blocks the next protected operation even
   after the Gaia grant activated. Prefer a fresh re-dispatch with the same
   `mode` and the verbatim `exact_content`; the DB grant lives in the session
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
| "The same command emitted a new approval_id" | Grants are single-use and consumed on the first retry. A second run is a new APPROVAL_REQUEST -- approve again. |
| "I'll set batch_scope to approve many at once" | `batch_scope` is ignored -- but a real batch path exists: a plan-first `command_set` (>= 2 items, no `approval_id`) is intaken into ONE pending `COMMAND_SET`. Present that single approval (N commands shown, one `[P-...]` nonce, one consent), not N separate approvals. |
| "I can paraphrase a field before relaying" | The fingerprint covers all sealed fields and is checked at grant **activation** (`verify_fingerprint`, when the user selects the Approve label); a paraphrase there raises `ChainTamperError` and the grant never forms. Relay verbatim so activation succeeds. |
| **"I'll dispatch a subagent to verify or derive the approval before presenting"** | The orchestrator has no shell and must NEVER dispatch to verify or derive an approval. The pending arrives **already verified** in the injected `[PENDING-APPROVALS-VERIFIED]` block (DB-read + fingerprint-checked by the per-turn hook, `verified: true`) -- present from it. For a same-turn pending not yet in the block, present from the subagent's relayed `approval_request`. A verify/derive dispatch is unnecessary (integrity is enforced at activation) and harmful (its SubagentStop can sweep the very pending). For `command_set`, read the minted `approval_id` and all commands from the block -- do not run `gaia approvals derive-id`. |
