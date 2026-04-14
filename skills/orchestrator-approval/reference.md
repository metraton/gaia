# Orchestrator Approval -- Reference

Detailed templates, examples, and batch flow. Read on-demand when presenting approvals.

## GOOD vs BAD Examples

### Example 1: Git push

**BAD -- vague label, missing fields:**
```
AskUserQuestion(
  question="Shall I push the changes?",
  options=["Approve", "Reject"]
)
```
Missing: OPERATION, COMMAND, SCOPE, RISK, ROLLBACK. Label "Approve" does not name the action.

**BAD -- paraphrased command, generic label:**
```
AskUserQuestion(
  question="APPROVAL REQUIRED\n\nOPERACION: Push changes\nCOMANDO: push the 2 commits\nSCOPE: main branch\nRIESGO: MEDIUM\nROLLBACK: git revert",
  options=["Approve -- aplicar cambios", "Reject"]
)
```
COMMAND is paraphrased ("push the 2 commits" instead of the literal `git push origin main`). Label is vague Spanish.

**GOOD -- verbatim command, specific label:**
```
AskUserQuestion(
  question=(
    "APPROVAL REQUIRED\n\n"
    "OPERACION: Push 2 commits to origin/main\n"
    "COMANDO:   git push origin main\n"
    "SCOPE:     remote origin, branch main -- 2 commits (a1b2c3, d4e5f6)\n"
    "RIESGO:    MEDIUM -- modifies shared branch history\n"
    "ROLLBACK:  git revert a1b2c3..d4e5f6"
  ),
  options=["Approve -- push 2 commits to origin/main [P-a1b2c3d4]", "Modify", "Reject"]
)
```

### Example 2: Terraform apply

**BAD:**
```
options=["Approve -- los 3 recursos", "Reject"]
```
"los 3 recursos" -- what 3? The user cannot tell from the label alone.

**GOOD:**
```
AskUserQuestion(
  question=(
    "APPROVAL REQUIRED\n\n"
    "OPERACION: Apply Terraform changes to dev VPC\n"
    "COMANDO:   terraform -chdir=/infra/dev apply -auto-approve\n"
    "SCOPE:     3 resources: google_compute_network.dev, google_compute_subnetwork.dev-a, google_compute_subnetwork.dev-b\n"
    "RIESGO:    MEDIUM -- creates new cloud resources in dev\n"
    "ROLLBACK:  terraform -chdir=/infra/dev destroy -auto-approve"
  ),
  options=["Approve -- terraform apply (3 resources in dev) [P-9c4e1f2a]", "Modify", "Reject"]
)
```

### Example 3: Multiple file edits

**BAD:**
```
options=["Approve -- aplicar cambios", "Reject"]
question="Can I make the changes we discussed?"
```

**GOOD:**
```
AskUserQuestion(
  question=(
    "APPROVAL REQUIRED\n\n"
    "OPERACION: Edit 3 config files to update API endpoint\n"
    "COMANDO:\n"
    "  1. Edit /app/config/prod.yaml -- api_url: https://old.api.com -> https://new.api.com\n"
    "  2. Edit /app/config/staging.yaml -- api_url: https://old.api.com -> https://new.api.com\n"
    "  3. Edit /app/.env.production -- API_BASE=https://old.api.com -> API_BASE=https://new.api.com\n"
    "SCOPE:     3 config files in /app/config/ and /app/.env.production\n"
    "RIESGO:    HIGH -- production config, affects live API routing\n"
    "ROLLBACK:  git checkout HEAD -- /app/config/prod.yaml /app/config/staging.yaml /app/.env.production"
  ),
  options=["Approve -- update API endpoint in 3 config files [P-d7f3a09b]", "Modify", "Reject"]
)
```

## Option Label Patterns

| Pattern | Verdict | Why |
|---------|---------|-----|
| `"Approve -- push 2 commits to origin/main [P-a1b2c3d4]"` | GOOD | Names exact action, includes nonce suffix |
| `"Approve -- terraform apply (3 resources in dev) [P-9c4e1f2a]"` | GOOD | Names tool, count, environment, includes nonce suffix |
| `"Approve -- delete branch feature/old-login [P-f5b0e871]"` | GOOD | Names the destructive action and target, includes nonce suffix |
| `"Approve -- push 2 commits to origin/main"` | BAD | Missing `[P-{8hex}]` suffix -- hook cannot do targeted activation |
| `"Approve"` | BAD | No action description |
| `"Approve -- aplicar cambios"` | BAD | Vague paraphrase |
| `"Approve -- los 3"` | BAD | What 3? |
| `"Approve -- proceed"` | BAD | "proceed" adds no information |
| `"Approve -- the plan above"` | BAD | References context, not action |
| `"Si, ejecutar"` | BROKEN | Missing "Approve" -- hook will not activate grant |

## Batch Approval Flow

When `approval_request` contains `batch_scope: "verb_family"`, the agent requests a
multi-use grant covering many commands with the same base CLI and verb but different arguments.

**Presentation:** Use the same mandatory format, but frame the scope as a batch:
- OPERACION describes the batch (e.g., "Modify 500 Gmail messages")
- COMANDO shows the command pattern (e.g., "`gws gmail users messages modify`")
- SCOPE states the TTL (e.g., "All modify operations for the next 10 minutes")

**Options:** `["Approve batch -- modify 500 Gmail messages [P-{nonce_prefix8}]", "Approve single -- {first_command} [P-{nonce_prefix8}]", "Modify", "Reject"]`
- "Approve batch" creates a verb-family grant (multi-use, 10-minute TTL)
- "Approve single" creates a normal single-use grant for only the first blocked command

**CRITICAL -- "batch" in the label:** The word "batch" MUST appear in the Approve option label for verb-family grants to activate. The PostToolUse hook checks the label text to decide whether to create a verb-family (multi-use) grant or a single-use grant. Without "batch" in the label, the hook creates a single-use grant and every command after the first one gets blocked again.

**BAD -- missing "batch" keyword:**
```
options=["Approve -- modify 500 Gmail messages [P-a1b2c3d4]", "Reject"]
```
Result: single-use grant created. First `gws gmail users messages modify` succeeds. Second one is blocked. Agent enters re-block loop for remaining 499 messages.

**GOOD -- "batch" keyword present:**
```
options=["Approve batch -- modify 500 Gmail messages [P-a1b2c3d4]", "Reject"]
```
Result: verb-family grant created (multi-use, 10-minute TTL). All 500 `gws gmail users messages modify` commands pass through.

**Resume:** After batch approval, resume via SendMessage with: "Batch approved. Proceed with all [verb] operations."

## Grant Activation Mechanics

When a hook blocks a T3 command, it writes a pending approval and returns an `approval_id` in the deny response. The subagent includes this `approval_id` in its `approval_request`. The orchestrator presents the plan via AskUserQuestion with structured options. When the user selects an "Approve" option, the PostToolUse hook for AskUserQuestion fires and activates the pending grant. No nonce or approval_id is relayed through SendMessage -- grant activation is handled entirely by the hook.

**Timing:** Grant activation is synchronous. The PostToolUse hook runs before AskUserQuestion returns to the orchestrator. By the time the orchestrator is ready to send SendMessage, the grant is already active. There is no race condition and no delay is needed.

## Scope Mismatch -- The Common Re-block Trap

Grants are matched by **semantic signature**: `base_cmd + verb + normalized arguments`. Two commands with the same verb but different path arguments are different signatures and do NOT share a grant.

**Example of the trap:**

1. Agent is blocked trying to run:
   `rm /path/to/approvals/grant-default-1776179289490.json`

2. Orchestrator approves it. The grant is scoped to that exact command.

3. Orchestrator sends resume: "Delete the stale grant file and then do the git operations"

4. Agent decides to run:
   `rm /path/to/approvals/grant-session-1776179452326.json`
   (different filename, same directory)

5. **Blocked again** -- the grant scope does not cover the new path.

**Why it happens:** The orchestrator paraphrased the operation ("delete the stale grant file") instead of quoting the approved command verbatim. The agent had latitude to choose a different target.

**Correct resume message:** Quote the exact approved command in the resume.

```
# BAD resume
"Proceed. Delete the stale grant file and then do the git operations."

# GOOD resume
"Proceed. Run exactly: rm /path/to/approvals/grant-default-1776179289490.json"
"Then continue with the git operations."
```

If the correct target has changed since the approval (e.g., the file that was blocked no longer exists and a different file needs to be deleted), present a new approval for the new command -- do not resume with modified instructions.
