---
name: command-execution
description: Defensive command execution - timeout protection, pipe avoidance, and safe shell patterns
user-invocable: false
---

# Command Execution

## Mental Model

Every command you run is **atomic and self-contained**. It takes inputs via flags, produces output to stdout, and exits. That is the entire contract.

You never reach outside that boundary. You do not pipe output to another process to reshape it — you use the CLI's own `--format` and `--filter` flags to get exactly the output you need. You do not redirect to files — you use the Write tool. You do not chain commands — you run one, confirm it succeeded, then run the next.

This model is not a restriction. It is how cloud CLIs (`gcloud`, `kubectl`, `helm`) were designed. Every filtering, formatting, and output option you would reach for in a shell pipeline already exists as a native flag. When you reach for a pipe, it means you haven't looked for the flag yet.

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

The CLI already has the flag you're looking for. Pipes hide exit codes, split the atomic contract, and trigger extra permission prompts for the second process.

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

The atomic model requires one command, one result. Chaining with `&&` or `;` breaks atomicity: you lose exit-code isolation and risk interactive prompts mid-chain blocking Claude Code.

```bash
# BAD — interactive prompt mid-chain blocks execution
terraform init && terraform validate && terraform plan
# GOOD
terraform init
terraform validate
terraform plan -out=/tmp/tfplan
```

## Rule 3: Use Claude Code Tools Over Bash

File I/O is not a shell operation — it belongs to the tool layer. Using bash for file reads, writes, or searches creates unnecessary subprocesses and loses structured output.

| Instead of | Use |
|------------|-----|
| `cat`, `head`, `tail` | Read tool |
| `echo >`, heredocs | Write tool |
| `sed -i`, `awk` | Edit tool |
| `grep -r`, `rg` | Grep tool |
| `find` | Glob tool |

**Never use heredocs** — they fail in batch contexts. Exception: `git commit -m "$(cat <<'EOF' ...)"`.

## Rule 4: Absolute Paths

The working directory is not reliable across tool calls. Use absolute paths so each command is fully self-describing and reproducible regardless of context.

```bash
# BAD
cd ../../shared/vpc && terraform plan
# GOOD
terraform plan -chdir="/abs/path/to/terraform/shared/vpc"
```

For unbounded outputs always set limits (`--limit=50 --freshness=1h`).

## Rule 5: Validate Before Mutate

Mutations are irreversible. Always dry-run → diff → apply. Each step is a separate, atomic confirmation.

```bash
kubectl apply -f manifest.yaml --dry-run=server
kubectl diff -f manifest.yaml
kubectl apply -f manifest.yaml
```

## Rule 6: Files Over Inline Data

Inline JSON/YAML/HCL in command arguments is not self-contained — it creates shell quoting fragility and makes the command unparseable by the hook. Write to a temp file first, reference by path.

```bash
# BAD
helm upgrade app chart --set "config={key: value, nested: {foo: bar}}"
# GOOD — use Write tool to create /tmp/values.yaml, then reference it
helm upgrade app chart -f /tmp/values.yaml
```

## Rule 7: Quote Variables

Variable expansion without quotes breaks the atomic contract through word-splitting: a variable containing spaces becomes multiple arguments.

Always `"${VAR}"` to prevent word splitting: `kubectl get pods -n "${NAMESPACE}"`
