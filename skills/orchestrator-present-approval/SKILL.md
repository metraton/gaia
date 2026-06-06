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
a subagent emits `APPROVAL_REQUEST` with an `approval_id`: relay the
`sealed_payload` into AskUserQuestion -- fingerprint check, mandatory fields in
the question, mandatory nonce in the option label. For the subagent side that
produced the payload see `subagent-request-approval`; for the data contract
itself see `agent-approval-protocol`.

## Mental Model

The orchestrator sits between the subagent and the user. The user cannot make
an informed decision on data they have not seen -- a summary, a reference to
"the plan above", or an offer to show details on request all push the decision
without the data needed to decide. The job is **verbatim relay, not
re-authoring**: rewriting any of the 7 sealed fields breaks the fingerprint and
`verify_fingerprint` (`gaia/approvals/chain.py`) raises `ChainTamperError`.

## Step 0 -- Verify the approval against the DB (mandatory before SHOWN)

A subagent's reported `approval_id` is an unverified claim, not a fact. The agent runs in its own context and can relay an id that is stale, from another session, or simply wrong -- and a stale id presented as a fresh block walks the user into consenting to nothing real (or to a grant that no longer exists). The DB is the source of truth; the agent's report is a pointer into it that you must resolve, never the authority itself.

So before AskUserQuestion, two checks against the DB, in order:

1. **The approval exists, is fresh, and is from the current session.** Query `gaia approvals pending --session "$CLAUDE_SESSION_ID"` (or `--json` for parsing). The reported `approval_id` MUST appear in that result. If it appears only under `--all-sessions` but not the current session, it is leakage from another session (a test session such as `e2e-sim`, a prior run) -- **do not present**. If it does not appear at all, it does not exist or was already consumed/rejected -- **do not present**. Freshness is the `created_at` of the pending row plus its presence as still-`pending`; an id the agent reports that is not currently pending in *this* session is not a fresh block, whatever the agent says.
2. **The payload is untampered.** Call `verify_fingerprint(approval_id, payload_json, con) -> bool` from `gaia/approvals/chain.py`. It raises `ChainTamperError` if the payload was modified between subagent emission and your relay (security boundary, do not present), and `ValueError` if no REQUESTED event exists for this `approval_id`. Either case: **do not present**, report the failure, stop.

**For a `command_set` (plan-first batch) the agent does not know the id at all.** The hook mints the `approval_id` at SubagentStop (`_intake_command_set_pending` -- see Rule 3); the subagent emits the `command_set` with **no** `approval_id`. So you do not have an agent-reported id to trust even if you wanted to -- you ALWAYS recover the freshly minted id from `gaia approvals pending` for the current session. This is the general shape made unavoidable: the DB mints, the orchestrator recovers, the agent never owns the id.

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

Fields above are extracted from the DB-stored canonical payload (`payload_json` on the REQUESTED row), not from the subagent's relayed `approval_request` — that's why `rollback_hint` is the field name here while the subagent contract uses `rollback`.

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
   `COMMAND_SET` with one `approval_id`. You present that single approval: list
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
| "I can paraphrase a field before relaying" | The fingerprint covers all 7 sealed fields; any modification raises `ChainTamperError` in Step 0 and the presentation is refused. |
| **"The agent reported an `approval_id`, so it's a real fresh block"** -- trusting a nonce relayed by the subagent | The agent's reported id is an unverified pointer, not a fact. It can be stale or belong to another session -- subagents have presented a STALE nonce from a test session (`e2e-sim`) as if it were a fresh block. Resolve every reported id against `gaia approvals pending --session "$CLAUDE_SESSION_ID"` (Step 0): it must be currently pending in *this* session. Visible only under `--all-sessions`, or absent entirely, means do not present. For `command_set` the hook mints the id and the agent never has one -- you always recover it from the DB. |
