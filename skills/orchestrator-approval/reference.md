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
  options=["Approve -- push 2 commits to origin/main", "Modify", "Reject"]
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
  options=["Approve -- terraform apply (3 resources in dev)", "Modify", "Reject"]
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
  options=["Approve -- update API endpoint in 3 config files", "Modify", "Reject"]
)
```

## Option Label Patterns

| Pattern | Verdict | Why |
|---------|---------|-----|
| `"Approve -- push 2 commits to origin/main"` | GOOD | Names the exact action |
| `"Approve -- terraform apply (3 resources in dev)"` | GOOD | Names tool, count, environment |
| `"Approve -- delete branch feature/old-login"` | GOOD | Names the destructive action and target |
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

**Options:** `["Approve batch -- modify 500 Gmail messages", "Approve single", "Modify", "Reject"]`
- "Approve batch" creates a verb-family grant (multi-use, 10-minute TTL)
- "Approve single" creates a normal single-use grant for only the first blocked command

**Resume:** After batch approval, resume via SendMessage with: "Batch approved. Proceed with all [verb] operations."

## Grant Activation Mechanics

When a hook blocks a T3 command, it writes a pending approval and returns an `approval_id` in the deny response. The subagent includes this `approval_id` in its `approval_request`. The orchestrator presents the plan via AskUserQuestion with structured options. When the user selects an "Approve" option, the PostToolUse hook for AskUserQuestion fires and activates the pending grant. No nonce or approval_id is relayed through SendMessage -- grant activation is handled entirely by the hook.
