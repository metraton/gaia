# AskUserQuestion Template

Use this layout verbatim when presenting an approval to the user. Replace
`{...}` placeholders with values read from your trusted source -- the
subagent's same-turn relayed `approval_request`, or (for a user's explicit
later-turn ask) a `gaia approvals show P-XXXX` result. Approvals are in-loop
and single-session; there is no injected verified-pendings block to read from.
Never dispatch a subagent to derive or verify the approval. Do not paraphrase,
summarize, or omit any field.

**Pick the layout by command count, not by field.** `exact_content` is a
SINGULAR field: for a `COMMAND_SET` it holds command **[0]** only, so rendering
it alone asks for consent to one command while the grant covers N. Count the
commands the payload carries (`command_set`, else `commands`, else the single
`exact_content`) and use the singular layout only when that count is 1;
otherwise use the batch layout below, which lists every one of them.

## Standard Approval (single command -- count == 1)

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

## Batch template (COMMAND_SET -- count >= 2)

When a subagent chains >= 2 T3 sub-commands in one Bash call and the hook
classifies >= 2 of them as ungranted T3, it mints ONE pending `COMMAND_SET`
approval **at block time** and denies the call with the same `[T3_BLOCKED]`
shape as a singular block; the subagent relays that `approval_id` in its
`approval_request` exactly like a singular one. Present it as a single
approval: **all N commands in the question body**, one Approve label with one
`[P-{nonce8}]` suffix.

Use this layout verbatim. The `COMANDOS ({N})` header and the `[i]` index on
each line are what make an omission visible: a body showing 1 line where the
header says 3 is wrong on its face.

```
AskUserQuestion(
  question=(
    "APPROVAL REQUIRED\n\n"
    "OPERACION:  {sealed_payload.operation}\n"
    "COMANDOS ({N}):\n"
    "  [1] {command_1}\n"
    "  [2] {command_2}\n"
    "  ...\n"
    "  [N] {command_N}\n"
    "SCOPE:      {sealed_payload.scope}\n"
    "RIESGO:     {sealed_payload.risk_level} -- {sealed_payload.rationale}\n"
    "ROLLBACK:   {sealed_payload.rollback_hint or 'NOT REVERSIBLE'}\n"
  ),
  options=[
    "Approve -- {sealed_payload.operation} ({N} commands) [P-{approval_id_prefix8}]",
    "Reject"
  ]
)
```

`{N}` is the count of commands the payload carries and `{command_i}` is item
`i` of `sealed_payload.command_set` (verbatim, in order; each item's `command`
field). `{N}` is a literal count, not a placeholder to leave in the text, and
the body MUST contain exactly `{N}` indexed command lines -- never `...`, never
"and 2 more", never a single `exact_content`. Everything else -- the 5 labeled
fields, the one Approve label, the one nonce suffix -- is identical to the
singular layout. One consent covers the whole batch; do NOT issue N approvals.

The runtime renders this same layout from the sealed payload
(`render_consent_surface` in `hooks/modules/security/approval_grants.py`) and
records it in the `SHOWN` event, so the question you present and the audited
consent surface have one shape. A surface missing any covered command is
detectable there (`verify_consent_surface_completeness`,
`audit_consent_surface`).

A `batch_scope` field and the word "batch" in an option label are both
ignored -- the signal is the presence of `command_set` in the contract. See
`reference.md` -> "On batch intents" for the grant mechanics.

## Field Extraction Reference

| Presentation field | Source |
|--------------------|--------|
| OPERACION | `sealed_payload.operation` |
| COMANDO (count == 1) | `sealed_payload.exact_content` (verbatim) |
| COMANDOS (N) (count >= 2) | every `sealed_payload.command_set[i].command` (verbatim, indexed `[1]..[N]`) -- NOT `exact_content`, which holds only command [0] |
| SCOPE | `sealed_payload.scope` |
| RIESGO | `sealed_payload.risk_level` + `sealed_payload.rationale` |
| ROLLBACK | `sealed_payload.rollback_hint` (null -> "NOT REVERSIBLE") |
| Option nonce suffix | `approval_id` first 8 chars after `P-` (`approval_request.approval_id`, singular and `COMMAND_SET` alike -- both arrive in the same same-turn relay) |

The nonce suffix is unchanged by the batch layout: `extract_nonce_from_label`
matches `^Approve\b.*\[P-([a-f0-9]+)\]`, so the extra `({N} commands)` text
sits between `Approve` and the `[P-...]` tag and the nonce still activates.
