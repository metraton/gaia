# Approval Request Plan Template

Use this template when requesting user approval for a T3 operation.
The fields below map directly to the `approval_request` object in your `json:contract` block.

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

These 6 fields MUST appear in the `approval_request` object of your `json:contract`:

| Field | Example value |
|-------|---------------|
| `operation` | `"apply Terraform changes to dev VPC"` |
| `exact_content` | `"terraform -chdir=/infra/dev apply -auto-approve"` |
| `scope` | `"infra/dev/vpc.tf, infra/dev/subnets.tf -- dev environment only"` |
| `risk_level` | `"MEDIUM"` |
| `rollback` | `"terraform -chdir=/infra/dev apply -target=module.vpc -var='cidr=10.0.0.0/16'"` |
| `verification` | `"terraform -chdir=/infra/dev output vpc_id -- expect vpc-xxx"` |

When a hook blocked the command (attempt first path), also include:
| Field | Example value |
|-------|---------------|
| `approval_id` | `"a1b2c3d4e5f6..."` (hex from hook deny response) |

### Files Affected

**Git changes:**
- Modified: [files]
- Added: [files]
- Deleted: [files]
```
