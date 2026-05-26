# AskUserQuestion Template

Use this layout verbatim when presenting an approval to the user. Replace
`{...}` placeholders with values extracted from the subagent's `sealed_payload`
and `approval_request`. Do not paraphrase, summarize, or omit any field.

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

Where `approval_id_prefix8` is the first 8 characters of the `approval_id`
field from the subagent's `approval_request` (after the `P-` prefix).

## Batch Approval (verb_family sweep)

When `approval_request.batch_scope == "verb_family"`, include both Approve
options. The word "batch" MUST appear in the first option label -- the
PostToolUse hook reads it to decide whether to create a verb-family (multi-use)
grant or a single-use grant.

```
AskUserQuestion(
  question=(
    "APPROVAL REQUIRED (BATCH)\n\n"
    "OPERACION:  {sealed_payload.operation}\n"
    "COMANDO:    {sealed_payload.exact_content}\n"
    "SCOPE:      {sealed_payload.scope}\n"
    "RIESGO:     {sealed_payload.risk_level} -- {sealed_payload.rationale}\n"
    "ROLLBACK:   {sealed_payload.rollback_hint or 'NOT REVERSIBLE'}\n"
  ),
  options=[
    "Approve batch -- {sealed_payload.operation} [P-{approval_id_prefix8}]",
    "Approve single -- {first_command} [P-{approval_id_prefix8}]",
    "Reject"
  ]
)
```

## Field Extraction Reference

| Presentation field | Source |
|--------------------|--------|
| OPERACION | `sealed_payload.operation` |
| COMANDO | `sealed_payload.exact_content` (verbatim) |
| SCOPE | `sealed_payload.scope` |
| RIESGO | `sealed_payload.risk_level` + `sealed_payload.rationale` |
| ROLLBACK | `sealed_payload.rollback_hint` (null -> "NOT REVERSIBLE") |
| Option nonce suffix | `approval_request.approval_id` first 8 chars after `P-` |
