# AskUserQuestion Template

Use this layout verbatim when presenting an approval to the user. Replace
`{...}` placeholders with values read from your trusted source -- the injected
`[PENDING-APPROVALS-VERIFIED]` block (primary; already DB-read and
fingerprint-verified by the per-turn hook) or, for a same-turn pending not yet
in the block, the subagent's relayed `approval_request` (fallback). Never
dispatch a subagent to derive or verify the approval. Do not paraphrase,
summarize, or omit any field.

## Standard Approval (single command)

```
AskUserQuestion(
  question=(
    "APPROVAL REQUIRED\n\n"
    "OPERACION:  {sealed_payload.operation}\n"
    "COMANDO:    {sealed_payload.exact_content}\n"
    "SCOPE:      {sealed_payload.scope}\n"
    "RIESGO:     {sealed_payload.risk_level} -- {sealed_payload.rationale}\n"
    "ROLLBACK:   {sealed_payload.rollback_hint or 'NOT REVERSIBLE'}\n"
  ),
  options=[
    "Approve -- {sealed_payload.operation} [P-{approval_id_prefix8}]",
    "Reject"
  ]
)
```

Where `approval_id_prefix8` is the first 8 characters (after the `P-` prefix) of
the `approval_id` read from the `[PENDING-APPROVALS-VERIFIED]` block, or from the
subagent's `approval_request` for a same-turn pending.

## Batch template (COMMAND_SET)

When the subagent emits a plan-first `APPROVAL_REQUEST` with a `command_set`
of >= 2 `{command, rationale}` items and **no** `approval_id`, the
SubagentStop intake mints ONE pending `COMMAND_SET` approval. Present it as
a single approval: list all N commands in the question body, one Approve
label with one `[P-{nonce8}]` suffix. See `reference.md` -> "On batch
intents" for the full layout.

A `batch_scope` field and the word "batch" in an option label are both
ignored -- the signal is the presence of `command_set` in the contract.

## Field Extraction Reference

| Presentation field | Source |
|--------------------|--------|
| OPERACION | `sealed_payload.operation` |
| COMANDO | `sealed_payload.exact_content` (verbatim) |
| SCOPE | `sealed_payload.scope` |
| RIESGO | `sealed_payload.risk_level` + `sealed_payload.rationale` |
| ROLLBACK | `sealed_payload.rollback_hint` (null -> "NOT REVERSIBLE") |
| Option nonce suffix | `approval_id` first 8 chars after `P-` (from the `[PENDING-APPROVALS-VERIFIED]` block, or `approval_request.approval_id` for a same-turn pending) |
