---
name: command-execution
description: Defensive command execution - timeout protection, pipe avoidance, and safe shell patterns
user-invocable: false
---

# Command Execution

## Principle: Defend Against Hanging

External systems (clusters, APIs, cloud services) may be unreachable, slow, or unresponsive.
**Never assume a command will complete.** Before executing any network/cloud command, ask:

1. **Can this hang?** (network, I/O, external API) → Apply timeout protection
2. **Do I know the timeout mechanism?** → Use tool-native flag if available, otherwise wrap with `timeout`

### Timeout Hierarchy (always apply from top to bottom)

```
PREFERRED  →  Tool-native flag     →  kubectl get pods --request-timeout=30s
FALLBACK   →  Shell timeout wrapper →  timeout 30s terraform show
LAST       →  Report and abort     →  "Target unreachable, check connectivity"
```

You already know each tool's timeout flags from your training. Use them.
If unsure, check `<tool> --help | grep -i timeout` before running the command.

### Timeout by Operation Type

| Operation | Timeout | Rationale |
|-----------|---------|-----------|
| Read/query (get, list, describe) | 30s | Should respond in <5s; 30s catches network issues |
| Validation (validate, lint, fmt) | 30s | Local or fast remote operations |
| Simulation (plan, diff, dry-run) | 300s | May process large state files |
| Realization (apply, deploy) | 600s | Long-running but bounded |
| Reconciliation (flux) | 90s | Must stay under Bash tool 120s default |

## Rule 1: No Pipes — Use Native Output

Pipes trigger unnecessary permission prompts, hide exit codes, and break error detection.
**Always use the tool's native output/filter flags instead.**

```bash
# BAD — pipe hides kubectl failure, triggers extra permission check
kubectl get pods -o json | jq '.items[0].metadata.name'
gcloud compute instances list | grep running | awk '{print $1}'

# GOOD — native output, single command, clean exit code
kubectl get pods -o jsonpath='{.items[0].metadata.name}'
gcloud compute instances list --filter='status:RUNNING' --format='value(name)'
```

## Rule 2: One Command Per Step

Never chain with `&&` or `;`. Each command runs separately so failures are visible.

```bash
# BAD — if init fails silently, plan runs against stale state
terraform init && terraform validate && terraform plan

# GOOD — each step verified independently
terraform init
terraform validate
terraform plan -out=/tmp/tfplan
```

## Rule 3: Use Claude Code Tools Over Bash

| Instead of... | Use... |
|---------------|--------|
| `cat`, `head`, `tail` | Read tool |
| `echo "..." >`, heredocs | Write tool |
| `sed -i`, `awk` | Edit tool |
| `grep -r`, `rg` | Grep tool |
| `find` | Glob tool |

Heredocs fail in batch/CLI contexts. **Never use heredocs** (exception: git commit messages).

## Rule 4: Validate Before Mutate

Never apply without validating first. Always: dry-run → diff → apply.

```bash
# BAD — silent partial failures
kubectl apply -f manifest.yaml

# GOOD — validate, then apply
kubectl apply -f manifest.yaml --dry-run=server
kubectl diff -f manifest.yaml
kubectl apply -f manifest.yaml
```

## Rule 5: Absolute Paths and Explicit Scope

```bash
# BAD — relative paths depend on unknown current directory
cd ../../shared/vpc && terraform plan

# GOOD — absolute, self-contained
terraform plan -chdir="/path/to/terraform/shared/vpc"
```

For logs and large outputs, always set limits:
```bash
# BAD — unbounded, may return millions of lines
gcloud logging read "resource.type=gke_cluster"

# GOOD — scoped and limited
gcloud logging read "resource.type=gke_cluster" --limit=50 --freshness=1h
```

## Rule 6: Files Over Inline Data

Never embed JSON/YAML/HCL inline in shell commands. Write to file first.

```bash
# BAD — escaping conflicts with YAML
helm upgrade app chart --set "config={key: value, nested: {foo: bar}}"

# GOOD — Write tool creates file, then reference it
helm upgrade app chart -f /tmp/values.yaml
```

## Rule 7: Quote Variables

Always `"${VAR}"` to prevent word splitting.

```bash
# BAD
kubectl get pods -n $NAMESPACE

# GOOD
kubectl get pods -n "${NAMESPACE}"
```
