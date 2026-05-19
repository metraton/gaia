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
5. On Reject: call `reject_pending(nonce_prefix)` to mark the pending as rejected; confirm to user

## When user says "rechazar P-XXXX"

1. The orchestrator dispatches an agent to edit the pending JSON file at `.claude/cache/approvals/pending-{nonce}.json`, setting `"status": "rejected"` and `"rejected_at"` to the current timestamp
2. Do NOT use `rm` to delete the file -- that triggers T3 approval. The `reject_pending()` function in `approval_grants.py` handles this via file I/O (read JSON, modify, write back)
3. The pending scanner will clean up rejected files on its next sweep
4. Confirm: "P-XXXX rechazado"

## Bulk cleanup

When to offer `gaia approvals reject-all`:
- The user says "limpia todos los pendings", "borra los pendientes", or similar
- The pending list contains entries from closed sessions (all have `cross_session: true`) and the user has not asked to review them individually
- Session-start injects 5 or more stale pendings and the user has not acted on any of them

How to invoke:

```
gaia approvals reject-all
```

After running, report the count of rejections and confirm: "X pendings rechazados." If the command returns 0, report "No había pendings activos."

Difference from individual rejection:
- `reject-all` marks every active pending as rejected in a single pass — no per-item confirmation is shown to the user
- Individual rejection (`rechazar P-XXXX`) is used when the user wants to review before discarding

Do NOT offer `reject-all` when there are active same-session pendings the user may still want to approve.

## Anti-patterns

- Approving without showing the exact command — user needs to see verbatim, not a summary
- Summarizing command as "the deploy" or "the apply" instead of showing the literal string
- Asking for approval without AskUserQuestion — the PostToolUse grant hook will not activate
- Prefixing the approve option with anything other than "Approve" (e.g. "Sí, ejecutar")
- Dispatching execution before AskUserQuestion confirms approval
- Omitting the `[P-{nonce_prefix8}]` suffix from the Approve label — the hook cannot do targeted activation without it
- Fire-and-forget dispatch -- omitting preflight checks and recovery instructions from the dispatch prompt

For JSON schema, format templates, flow example, and dispatch template: read `reference.md`.
