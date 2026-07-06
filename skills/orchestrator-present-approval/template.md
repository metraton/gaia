# AskUserQuestion Template

Use this layout verbatim when presenting an approval to the user. Replace
`{...}` placeholders with values read from your trusted source -- the
subagent's same-turn relayed `approval_request`, or (for a user's explicit
later-turn ask) a `gaia approvals show P-XXXX` result. Approvals are in-loop
and single-session; there is no injected verified-pendings block to read from.
Never dispatch a subagent to derive or verify the approval. Do not paraphrase,
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
the `approval_id` from the subagent's relayed `approval_request`. A `COMMAND_SET`
id arrives the same way -- see below.

## Batch template (COMMAND_SET)

When a subagent chains >= 2 T3 sub-commands in one Bash call and the hook
classifies >= 2 of them as ungranted T3, it mints ONE pending `COMMAND_SET`
approval **at block time** and denies the call with the same `[T3_BLOCKED]`
shape as a singular block; the subagent relays that `approval_id` in its
`approval_request` exactly like a singular one. Present it as a single
approval: list all N commands in the question body, one Approve label with
one `[P-{nonce8}]` suffix. See `reference.md` -> "On batch intents" for the
full layout.

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
| Option nonce suffix | `approval_id` first 8 chars after `P-` (`approval_request.approval_id`, singular and `COMMAND_SET` alike -- both arrive in the same same-turn relay) |
