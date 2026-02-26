---
name: fast-queries
description: Use when diagnosing an issue, checking system health, or validating infrastructure state before starting a task
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

- `0` = All healthy — proceed
- `1` = Warnings found — review before proceeding, not necessarily blocking
- `2` = Errors found — stop and investigate before continuing
- `3` = Script error (missing tools, permissions) — check setup

## Usage Pattern

```
1. User reports issue or asks for status
2. Run fast-queries for relevant domain
3. Interpret by exit code:
   - 0 ✅ All green → proceed with task
   - 1 ⚠️ Warnings → review each warning, decide if blocking before continuing
   - 2 ❌ Errors → report to user, do not proceed, investigate flagged issues
   - 3 💥 Script error → check tool availability and permissions
4. Deep-dive only on flagged issues (exit 1 or 2)
```

Use domain-specific scripts when you know the area. Use `all` only for general status checks.
