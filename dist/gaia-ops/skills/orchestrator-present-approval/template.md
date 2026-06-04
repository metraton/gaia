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

## No batch template

There is no batch/multi-use approval in the current code. The `verb_family` grant
was removed (see the module docstring of
`hooks/modules/security/approval_grants.py`) and the `COMMAND_SET` replacement
has no production activation path (`create_command_set_grant` has no production
caller). The word "batch" in a label and a `batch_scope` field are both ignored.
For a sweep of N commands, present each command with its own single-command
approval (the template above), once per `approval_id`. See `reference.md` ->
"On batch intents".

## Field Extraction Reference

| Presentation field | Source |
|--------------------|--------|
| OPERACION | `sealed_payload.operation` |
| COMANDO | `sealed_payload.exact_content` (verbatim) |
| SCOPE | `sealed_payload.scope` |
| RIESGO | `sealed_payload.risk_level` + `sealed_payload.rationale` |
| ROLLBACK | `sealed_payload.rollback_hint` (null -> "NOT REVERSIBLE") |
| Option nonce suffix | `approval_request.approval_id` first 8 chars after `P-` |
