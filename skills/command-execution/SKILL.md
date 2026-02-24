---
name: command-execution
description: Defensive command execution - timeout protection, pipe avoidance, and safe shell patterns
user-invocable: false
---

# Command Execution

## Timeouts First

External systems may hang. Apply before any network/cloud command:

| Operation | Timeout |
|-----------|---------|
| Read / query | 30s |
| Validation (lint, fmt) | 30s |
| Simulation (plan, diff) | 300s |
| Realization (apply, deploy) | 600s |
| Flux reconcile | 90s |

Use tool-native flag first (`kubectl get pods --request-timeout=30s`), fall back to `timeout 30s <cmd>`. If still unreachable → report and abort.

## Rule 1: No Pipes

Pipes hide exit codes and trigger extra permission prompts. Use native output flags.

```bash
# BAD
kubectl get pods -o json | jq '.items[0].metadata.name'
# GOOD
kubectl get pods -o jsonpath='{.items[0].metadata.name}'

# BAD
gcloud compute instances list | grep running | awk '{print $1}'
# GOOD
gcloud compute instances list --filter='status:RUNNING' --format='value(name)'
```

## Rule 2: One Command Per Step

Never chain with `&&` or `;`. Chained commands can trigger interactive prompts
(`Do you want to proceed? y/n`) mid-execution, blocking Claude Code.

```bash
# BAD — interactive prompt mid-chain blocks execution
terraform init && terraform validate && terraform plan
# GOOD
terraform init
terraform validate
terraform plan -out=/tmp/tfplan
```

## Rule 3: Use Claude Code Tools Over Bash

| Instead of | Use |
|------------|-----|
| `cat`, `head`, `tail` | Read tool |
| `echo >`, heredocs | Write tool |
| `sed -i`, `awk` | Edit tool |
| `grep -r`, `rg` | Grep tool |
| `find` | Glob tool |

**Never use heredocs** — they fail in batch contexts. Exception: `git commit -m "$(cat <<'EOF' ...)"`.

## Rule 4: Absolute Paths

Avoid `cd` and relative paths. Use absolute paths or tool-native chdir flags.
For unbounded outputs always set limits (`--limit=50 --freshness=1h`).

```bash
# BAD
cd ../../shared/vpc && terraform plan
# GOOD
terraform plan -chdir="/abs/path/to/terraform/shared/vpc"
```

## Rule 5: Validate Before Mutate

Always dry-run → diff → apply before any mutation.

```bash
kubectl apply -f manifest.yaml --dry-run=server
kubectl diff -f manifest.yaml
kubectl apply -f manifest.yaml
```

## Rule 6: Files Over Inline Data

Never embed JSON/YAML/HCL inline in commands — write to a temp file first.

```bash
# BAD
helm upgrade app chart --set "config={key: value, nested: {foo: bar}}"
# GOOD — use Write tool to create /tmp/values.yaml, then reference it
helm upgrade app chart -f /tmp/values.yaml
```

## Rule 7: Quote Variables

Always `"${VAR}"` to prevent word splitting: `kubectl get pods -n "${NAMESPACE}"`
