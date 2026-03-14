---
name: fast-queries
description: Use when diagnosing an issue, checking system health, or validating infrastructure state before starting a task
metadata:
  user-invocable: false
  type: reference
---

# Fast-Query Diagnostics

**Always run fast-queries FIRST** when investigating issues, checking status, or validating changes.

## Available Scripts

Run from project root. Use absolute path if calling from a different directory.

| Script | Command | Duration |
|--------|---------|----------|
| **All systems** | `bash .claude/tools/fast-queries/run_triage.sh [domain]` | 8-15s |
| **GitOps/K8s** | `bash .claude/tools/fast-queries/gitops/quicktriage_gitops_operator.sh [ns]` | 2-3s |
| **Terraform** | `bash .claude/tools/fast-queries/terraform/quicktriage_terraform_architect.sh [dir]` | 3-4s |
| **AWS** | `bash .claude/tools/fast-queries/cloud/aws/quicktriage_aws_troubleshooter.sh` | 4-5s |
| **GCP** | `bash .claude/tools/fast-queries/cloud/gcp/quicktriage_gcp_troubleshooter.sh [project]` | 4-5s |

**Domains for triage:** `all`, `gitops`, `terraform`, `cloud`, `appservices`

## Exit Codes

- `0` OK = All healthy — proceed
- `1` WARNING = Warnings found — review before proceeding, not necessarily blocking
- `2` ERROR = Errors found — stop and investigate before continuing
- `3` SCRIPT_ERROR = Script error (missing tools, permissions) — check setup

## Usage Pattern

```
1. User reports issue or asks for status
2. Run fast-queries for relevant domain
3. Interpret by exit code:
   - 0 OK → proceed with task
   - 1 WARNING → review each warning, decide if blocking before continuing
   - 2 ERROR → report to user, do not proceed, investigate flagged issues
   - 3 SCRIPT_ERROR → check tool availability and permissions
4. Deep-dive only on flagged issues (exit 1 or 2)
```

Use domain-specific scripts when you know the area. Use `all` only for general status checks.
