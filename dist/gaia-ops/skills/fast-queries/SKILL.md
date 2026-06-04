---
name: fast-queries
description: Use when diagnosing an issue, checking system health, or validating infrastructure state before starting a task
metadata:
  user-invocable: false
  type: reference
---

# Fast-Query Diagnostics

A 10-second triage run surfaces 80% of issues that would otherwise take
minutes of manual commands to discover. Running triage first means your
investigation starts from known state, not assumptions about what is healthy.

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

Use domain-specific scripts when you know the area. Use `all` only for
general status checks -- it runs every domain and takes longer.

## Exit Codes

| Code | Meaning | Action |
|------|---------|--------|
| `0` OK | All healthy | Proceed with task |
| `1` WARNING | Warnings found | Review each; not necessarily blocking |
| `2` ERROR | Errors found | Report to user, investigate flagged issues before continuing |
| `3` SCRIPT_ERROR | Script failure | Check tool availability and permissions |

Deep-dive only on flagged issues (exit 1 or 2). Exit 0 means the
environment is healthy -- spending time re-verifying what triage already
confirmed wastes investigation budget on non-problems.
