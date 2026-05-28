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

## Step 0 -- Fingerprint validation (mandatory before SHOWN)

Before AskUserQuestion, call `verify_fingerprint(approval_id, payload_json, con) -> bool` from `gaia/approvals/chain.py`. It raises `ChainTamperError` if the payload was modified between subagent emission and your relay (security boundary, do not present), and `ValueError` if no REQUESTED event exists for this `approval_id`. Either case: **do not present**, report the failure, stop.

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

3. **No batch grant.** Legacy `verb_family` was removed; the `COMMAND_SET`
   replacement has only the CHECK side wired (`match_command_set_grant` is
   called by `bash_validator`, but `create_command_set_grant` has no production
   caller). `batch_scope` is ignored. For N commands, expect N approvals.
   See `reference.md` -> "On batch intents".

4. **Re-dispatch, do not resume.** `mode` does not survive a SendMessage resume:
   the resume runs in `default` and re-blocks the next protected operation even
   after the Gaia grant activated. Prefer a fresh re-dispatch with the same
   `mode` and the verbatim `exact_content`; the DB grant lives in the session
   and is found by the re-dispatched subagent. See `security-tiers` R3 for the
   underlying mechanism (mode is per-dispatch).

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
| "I'll set batch_scope to approve many at once" | No batch activation exists in current code -- the field is ignored. Each blocked command needs its own approval. |
| "I can paraphrase a field before relaying" | The fingerprint covers all 7 sealed fields; any modification raises `ChainTamperError` in Step 0 and the presentation is refused. |
