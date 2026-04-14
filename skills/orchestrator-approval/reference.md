# Orchestrator Approval -- Reference

Detailed templates and batch flow. Read on-demand when presenting approvals.

## AskUserQuestion Template

Map the 5 mandatory fields into AskUserQuestion parameters.

**question** (multi-line string with all fields visible before the user chooses):
```
APPROVAL REQUIRED

OPERATION: {approval_request.operation}
CONTENT: {approval_request.exact_content}
SCOPE: {approval_request.scope}
RISK: {approval_request.risk_level}
ROLLBACK: {approval_request.rollback}
```

**options** (depends on batch_scope):
- Default: `["Approve", "Modify", "Reject"]`
- When `batch_scope` present: `["Approve batch", "Approve single", "Modify", "Reject"]`

## Approval Option Label Convention

The PostToolUse hook activates grants by checking for `"approve"` in the answer value.
Labels that do not contain "approve" will not activate the grant, even if the user's
intent was to proceed.

Valid examples:
- "Approve" -- activates grant
- "Approve batch" -- activates grant (batch)
- "Approve -- merge + borrar branch" -- activates grant
- "Merge + borrar branch" -- WILL NOT activate grant (missing "Approve" prefix)

## Batch Approval Flow

When `approval_request` contains `batch_scope: "verb_family"`, the agent requests a
multi-use grant covering many commands with the same base CLI and verb but different arguments.

**Presentation:** Use the same template above, but frame the scope as a batch:
- OPERATION describes the batch (e.g., "Modify 500 Gmail messages")
- CONTENT shows the command pattern (e.g., "`gws gmail users messages modify`")
- SCOPE states the TTL (e.g., "All modify operations for the next 10 minutes")

**Options:** `["Approve batch", "Approve single", "Modify", "Reject"]`
- "Approve batch" creates a verb-family grant (multi-use, 10-minute TTL). The PostToolUse hook detects "batch" in the answer.
- "Approve single" creates a normal single-use grant for only the first blocked command.

**Resume:** After batch approval, resume via SendMessage with: "Batch approved. Proceed with all [verb] operations."

## Grant Activation Mechanics

When a hook blocks a T3 command, it writes a pending approval and returns an `approval_id` in the deny response. The subagent includes this `approval_id` in its `approval_request`. The orchestrator presents the plan via AskUserQuestion with structured options. When the user selects an "Approve" option, the PostToolUse hook for AskUserQuestion fires and activates the pending grant. No nonce or approval_id is relayed through SendMessage -- grant activation is handled entirely by the hook.
