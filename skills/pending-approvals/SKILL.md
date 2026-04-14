---
name: pending-approvals
description: Use when there are pending approval requests to present — "aprobar", "ver pendientes", "approve P-", "reject P-"
metadata:
  user-invocable: true
  type: technique
---

# Pending Approvals

## When SessionStart injects pending approvals

1. Present the summary to the user (already formatted by the scanner)
2. Wait for user to say "ver P-XXXX" or "aprobar P-XXXX"

The scanner formats each entry as:
```
P-{nonce_prefix8}  {command}  [{danger_verb}]  {age}
```

## When user says "ver P-XXXX"

1. Find the pending file whose nonce starts with the given prefix
2. Present full details: operation, exact command (verbatim), context, risk, rollback
3. Ask: "aprobar" or "rechazar"

## When user says "aprobar P-XXXX"

1. Find the pending file whose nonce starts with the given prefix
2. Call AskUserQuestion with ALL mandatory fields visible:

```
APPROVAL REQUIRED

OPERATION: {danger_verb} on {base_cmd}
COMMAND:   {command}  ← verbatim, no paraphrase
SCOPE:     {scope from context field}
RISK:      {danger_category}
ROLLBACK:  {rollback from context field}
```

3. AskUserQuestion options: `["Approve -- {specific_action}", "Reject"]`
   - Label MUST start with "Approve" (PostToolUse grant activation checks for "approve")
   - Label MUST name the specific action (e.g., "Approve -- kubectl apply -f manifest.yaml")
   - NEVER use vague labels like "Approve -- aplicar cambios" or "Approve -- proceed"
4. On Approve: dispatch a one-shot agent to execute the command; pass the nonce
5. On Reject: delete the pending file; confirm deletion to user

## When user says "rechazar P-XXXX"

1. Delete the pending file at `.claude/cache/approvals/pending-{nonce}.json`
2. Confirm: "P-XXXX rechazado y eliminado"

## Anti-patterns

- Approving without showing the exact command — user needs to see verbatim, not a summary
- Summarizing command as "the deploy" or "the apply" instead of showing the literal string
- Asking for approval without AskUserQuestion — the PostToolUse grant hook will not activate
- Prefixing the approve option with anything other than "Approve" (e.g. "Sí, ejecutar")
- Dispatching execution before AskUserQuestion confirms approval

For JSON schema, format templates, flow example, and dispatch template: read `reference.md`.
