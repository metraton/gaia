# Command Execution -- Reference

Read on-demand by infrastructure agents. Not injected automatically.

## Timeouts

| Operation | Timeout |
|-----------|---------|
| Read / query | 30s |
| Validation (lint, fmt) | 30s |
| Simulation (plan, diff) | 300s |
| Realization (apply, deploy) | 600s |
| Flux reconcile | 90s |

Use tool-native timeout flag first (`--request-timeout=30s`), fall back to `timeout 30s <cmd>`. Unreachable -- report and abort.

## Rule 5: Validate Before Mutate

Mutations are irreversible. Always dry-run, then diff, then apply -- each a separate, atomic confirmation.

```bash
kubectl apply -f manifest.yaml --dry-run=server
kubectl diff -f manifest.yaml
kubectl apply -f manifest.yaml
```

## Rule 6: Files Over Inline Data

Inline JSON/YAML/HCL creates shell quoting fragility. Write to a temp file, reference by path: `helm upgrade app chart -f /tmp/values.yaml` instead of `--set "config={key: value}"`.

## Cloud CLI Examples

### No Pipes (Rule 1)

```bash
# BAD:  kubectl get pods -o json | jq '.items[0].metadata.name'
# GOOD: kubectl get pods -o jsonpath='{.items[0].metadata.name}'
```

### One Command Per Step (Rule 2)

```bash
# BAD:  terraform init && terraform validate && terraform plan
# GOOD: run each separately, verify each exit code
terraform init
terraform validate
terraform plan -out=/tmp/tfplan
```

### Absolute Paths (Rule 4)

```bash
# BAD:  cd ../../shared/vpc && terraform plan
# GOOD: terraform plan -chdir="/abs/path/to/terraform/shared/vpc"
```

## Additional Red Flags (Mutation-Specific)

- *"It won't hang"* -- Timeouts: apply it anyway
- *"Dry-run passed, I can apply"* -- Rule 5: dry-run, then diff, then apply -- three required steps
- *"Simple value, I'll inline it"* -- Rule 6: write to temp file first

## Rationalization Table

Every excuse an agent makes for violating a rule, and why it is wrong.

| Rationalization | Reality | Rule |
|----------------|---------|------|
| "This command is fast, no timeout needed" | External systems hang for reasons unrelated to command complexity | Timeouts |
| "It's just to filter output, not a real pipe" | Pipes hide exit codes and split the atomic contract regardless of intent | 1 |
| "I need `grep` to find what I'm looking for" | `gcloud`/`kubectl` `--filter` finds it natively, without a subprocess | 1 |
| "These steps always run together, chaining is fine" | Each command needs its own exit code verification -- chaining loses that | 2 |
| "I need to persist the output for later analysis" | Use the Write tool -- redirects in bash break the hook's structured output | 3 |
| "It's faster to use `cat` than the Read tool" | Bash subprocesses lose structured output and create unnecessary permission prompts | 3 |
| "The relative path should work here" | Working directory is not reliable across tool calls -- it will break | 4 |
| "Dry-run passed so apply is safe" | dry-run and diff are separate validations -- skip either and you miss drift | 5 |
| "The inline value is simple enough" | Shell quoting breaks at spaces, special chars, and nested quotes -- always | 6 |
| "This variable definitely won't have spaces" | It will, eventually -- and when it does, it breaks silently and is hard to debug | 7 |

## Anti-Patterns

Pipe as shortcut. Chain as convenience. Redirect as persistence. `cd` before command. Inline complex data. Unquoted variables.
