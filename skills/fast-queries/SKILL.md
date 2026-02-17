---
name: fast-queries
description: Quick diagnostic scripts for instant health checks (<5 sec)
user-invocable: false
---

# Fast-Query Diagnostics

**Always run fast-queries FIRST** when investigating issues, checking status, or validating changes.

## Available Scripts

| Script | Command | Duration |
|--------|---------|----------|
| **All systems** | `bash .claude/tools/fast-queries/run_triage.sh [domain]` | 8-15s |
| **GitOps/K8s** | `bash .claude/tools/fast-queries/gitops/quicktriage_gitops_operator.sh [ns]` | 2-3s |
| **Terraform** | `bash .claude/tools/fast-queries/terraform/quicktriage_terraform_architect.sh [dir]` | 3-4s |
| **AWS** | `bash .claude/tools/fast-queries/cloud/aws/quicktriage_aws_troubleshooter.sh` | 4-5s |
| **GCP** | `bash .claude/tools/fast-queries/cloud/gcp/quicktriage_gcp_troubleshooter.sh [project]` | 4-5s |

**Domains for triage:** `all`, `gitops`, `terraform`, `cloud`, `appservices`

## Exit Codes

- `0` = All healthy
- `1` = Issues found (warnings or errors)
- `2` = Script error (missing tools, permissions)

## Usage Pattern

```
1. User reports issue or asks for status
2. Run fast-queries for relevant domain
3. Interpret results:
   - ✅ All green → proceed with task
   - ⚠️ Warnings → review, decide if blocking
   - ❌ Errors → explain to user, suggest investigation
4. Deep-dive only on flagged issues
```

Use domain-specific scripts when you know the area. Use `all` only for general status checks.
