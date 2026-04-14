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

3. AskUserQuestion options: `["Approve -- {specific_action} [P-{nonce_prefix8}]", "Reject"]`
   - Label MUST start with "Approve" (PostToolUse grant activation checks for "approve")
   - Label MUST end with `[P-{nonce_prefix8}]` (PostToolUse hook extracts nonce from label for targeted activation)
   - Label MUST name the specific action (e.g., "Approve -- kubectl apply -f manifest.yaml [P-8072af80]")
   - NEVER use vague labels like "Approve -- aplicar cambios" or "Approve -- proceed"
4a. Cross-session check: if `pending.session_id` != current `CLAUDE_SESSION_ID`:
    - The nonce is stale (from a prior session) -- do NOT pass it to the agent
    - The PostToolUse hook will have already activated the grant under the current session
    - Dispatch a one-shot agent using the dispatch template from `reference.md` (command + cwd + preflight + recovery instructions, no nonce)
    - The hook will find the pre-activated grant and allow the T3 operation through
4b. Same-session: dispatch a one-shot agent using the dispatch template from `reference.md` (command + cwd + nonce + preflight + recovery instructions)
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
- Omitting the `[P-{nonce_prefix8}]` suffix from the Approve label — the hook cannot do targeted activation without it
- Fire-and-forget dispatch -- omitting preflight checks and recovery instructions from the dispatch prompt

For JSON schema, format templates, flow example, and dispatch template: read `reference.md`.
