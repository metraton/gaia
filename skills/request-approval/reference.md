# Request Approval -- Reference

Detailed semantics, plan template, and edge cases for the `approval_request`
object. Read on-demand when crafting your first plan, when the operation
expands into many commands, or when the standard fields are not enough to
present the change to the user.

## Approval Request Plan Template

Use this template when presenting a T3 plan; the labelled sections map
directly into the orchestrator's AskUserQuestion presentation.

```markdown
## Deployment Plan

### Summary (3-5 bullets)
- What will be changed
- Why this change is needed
- What the expected outcome is

### Changes Proposed

**Resources to CREATE:**
- [Resource]: [Description]

**Resources to MODIFY:**
- [Resource]: [What changes] (before -> after)

**Resources to DELETE:**
- [Resource]: [Why deletion]

### Validation Results

**Dry-run status:**
- `[simulation command]` - [result summary]

**Dependencies verified:**
- [Dependency]: Available

### approval_request fields

| Field | Example value |
|-------|---------------|
| `operation` | `"apply Terraform changes to dev VPC"` |
| `exact_content` | `"terraform -chdir=/infra/dev apply -auto-approve"` |
| `scope` | `"infra/dev/vpc.tf, infra/dev/subnets.tf -- dev environment only"` |
| `risk_level` | `"MEDIUM"` |
| `rollback` | `"terraform -chdir=/infra/dev apply -target=module.vpc -var='cidr=10.0.0.0/16'"` |
| `verification` | `"terraform -chdir=/infra/dev output vpc_id -- expect vpc-xxx"` |

When a hook blocked the command (attempt-first path), also include:

| Field | Example value |
|-------|---------------|
| `approval_id` | `"a1b2c3d4e5f6..."` (hex from hook deny response) |

When the operation is a sweep over many commands sharing the same base CLI
and verb, also include:

| Field | Example value |
|-------|---------------|
| `batch_scope` | `"verb_family"` -- requests a multi-use grant covering all `base_cmd + verb` commands for a 10-minute TTL. Omit for single commands. |

### Files Affected

**Git changes:**
- Modified: [files]
- Added: [files]
- Deleted: [files]
```

## Risk Levels

| Level | Criteria |
|-------|----------|
| LOW | Single resource, non-prod, no dependencies |
| MEDIUM | Multiple resources, non-prod, some dependencies |
| HIGH | Production, dependencies, potential downtime |
| CRITICAL | Irreversible, data loss possible |

The level is the agent's read on blast radius. The user always sees both the
literal command and the level -- they reinforce each other; one without the
other lets the user approve a `terraform destroy` because the level said LOW
or refuse a `kubectl get` because the level said CRITICAL.

## Batch Approval -- Full Semantics

When `batch_scope: "verb_family"` is present, the runtime creates a multi-use
grant on batch approval. The grant is matched by `base_cmd + verb` only --
different arguments are fine, but a different verb on the same CLI is outside
the grant and gets blocked.

```json
"approval_request": {
  "operation": "Modify 500 Gmail messages -- add Archive label",
  "exact_content": "gws gmail users messages modify --addLabelIds Archive userId=me messageId=<id>",
  "scope": "All gws ... modify operations for the next 10 minutes",
  "risk_level": "MEDIUM",
  "rollback": "gws gmail users messages modify --removeLabelIds Archive userId=me messageId=<id>",
  "verification": "gws gmail users messages list --labelIds Archive | wc -l matches",
  "batch_scope": "verb_family",
  "approval_id": "hex from hook deny response (when blocked)"
}
```

The `batch_scope` field is the **opt-in** signal. Without it, the orchestrator
only offers single-command approval, and the agent will be re-blocked after
the first command runs. The orchestrator's presentation must contain the word
"batch" in the Approve label for the runtime to create a verb-family grant
rather than a single-use one -- this is the runtime contract; the agent
controls only the request side.

### When NOT to use `batch_scope`

- **Single command** -- the standard fields handle it; batch presentation
  adds an "Approve single" option and a 10-minute TTL the user does not
  need.
- **Mixed verbs** -- one batch per verb in sequence (e.g., a `modify`
  batch then a `delete` batch), or a single approval if the count is small.
  A grant scoped to `modify` does not cover `delete` even on the same CLI.
- **Destructive irreversible operations** -- per-command audit trail is the
  safer default; reach for batch only when the user has clearly authorized
  the sweep.

For the orchestrator-side presentation rules (the literal "Approve batch"
label text the runtime greps for), see `orchestrator-approval/reference.md`.

## Status Semantics

The legacy plan_status `REVIEW` is gone from runtime. If a doc still
references `REVIEW` as a literal status, it is drift scheduled for cleanup.
Always emit `APPROVAL_REQUEST`; the presence or absence of `approval_id`
tells the orchestrator which path to take:

- **With `approval_id`** -- the hook blocked the command; the orchestrator
  presents the plan and the runtime activates the grant on approval.
- **Without `approval_id`** -- the agent is presenting a plan-first proposal;
  the orchestrator gates on user consent before any execution.

## Hook Deny Message Format

```
[T3_BLOCKED] This command requires user approval.
Do NOT retry this command. Report APPROVAL_REQUEST with this approval_id in your json:contract.
approval_id: <hex>
```

The first instruction is load-bearing: the agent that retries instead of
emitting APPROVAL_REQUEST locks itself out of the only doorway through which
the grant can activate.
