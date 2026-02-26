---
name: command-execution
description: Use when executing any bash command, cloud CLI (gcloud, kubectl, terraform), or shell operation
user-invocable: false
---

# Command Execution

```
NO PIPES. NO CHAINS. NO REDIRECTS.
One command. One result. One exit code.
```

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

The CLI already has the flag you're looking for. Pipes hide exit codes, split the atomic contract, and trigger extra permission prompts for the second process. For unbounded outputs, use native limit flags (`--limit=50`, `--freshness=1h`) — never pipe to `head`.

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

## Rule 5: Validate Before Mutate

Mutations are irreversible. Always dry-run → diff → apply. Each step is a separate, atomic confirmation — skipping any one of them is a violation.

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

---

## Red Flags — Stop Before Executing

If you're forming any of these thoughts, stop. You're about to violate the atomic contract:

- *"This command is quick, it won't hang"* → Timeouts: apply it anyway — external systems hang for reasons unrelated to complexity
- *"I'll use `|` just to limit the output"* → Rule 1: use `--limit` or `--format` flag
- *"I'll pipe to `grep`/`awk`/`jq` to filter"* → Rule 1: use `--filter` and `--format`
- *"I'll chain with `&&` because these steps always go together"* → Rule 2: run separately, verify each exit code
- *"Let me save the output with `>`"* → Rule 3: use the Write tool
- *"Let me `cat` this file to check it quickly"* → Rule 3: use the Read tool
- *"Let me `cd` first, then run the command"* → Rule 4: use absolute path with `-chdir` or equivalent
- *"The dry-run passed, I can apply directly"* → Rule 5: dry-run → diff → apply are three required steps, not one
- *"It's a simple value, I'll put it inline"* → Rule 6: write to temp file first
- *"This variable won't have spaces"* → Rule 7: always quote — it will eventually, and it will break silently

## Rationalization Table

| Rationalization | Reality | Rule |
|----------------|---------|------|
| "This command is fast, no timeout needed" | External systems hang for reasons unrelated to command complexity | Timeouts |
| "It's just to filter output, not a real pipe" | Pipes hide exit codes and split the atomic contract regardless of intent | 1 |
| "I need `grep` to find what I'm looking for" | `gcloud`/`kubectl` `--filter` finds it natively, without a subprocess | 1 |
| "These steps always run together, chaining is fine" | Each command needs its own exit code verification — chaining loses that | 2 |
| "I need to persist the output for later analysis" | Use the Write tool — redirects in bash break the hook's structured output | 3 |
| "It's faster to use `cat` than the Read tool" | Bash subprocesses lose structured output and create unnecessary permission prompts | 3 |
| "The relative path should work here" | Working directory is not reliable across tool calls — it will break | 4 |
| "Dry-run passed so apply is safe" | dry-run and diff are separate validations — skip either and you miss drift | 5 |
| "The inline value is simple enough" | Shell quoting breaks at spaces, special chars, and nested quotes — always | 6 |
| "This variable definitely won't have spaces" | It will, eventually — and when it does, it breaks silently and is hard to debug | 7 |

## Anti-Patterns

Pipe as shortcut · Chain as convenience · Redirect as persistence · `cd` before command · Inline complex data

All covered in Red Flags and Rationalization Table above.
