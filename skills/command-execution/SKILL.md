---
name: command-execution
description: Shell security rules, timeout guidelines, and execution best practices
user-invocable: false
---

# Command Execution Standards

## Core Execution Pillars

### 1. Simplicity First
Break complex operations into atomic steps.

```bash
# BAD: Chained commands hide failures
terraform init && terraform validate && terraform plan

# GOOD: Separate commands with verification
terraform init
terraform validate
terraform plan -out=/tmp/tfplan
```

### 2. Quote All Variables
Always use `"${VAR}"` syntax to prevent word splitting.

```bash
# BAD: Unquoted variables
kubectl get pods -n $NAMESPACE
terraform plan -target=$MODULE

# GOOD: Quoted variables
kubectl get pods -n "${NAMESPACE}"
terraform plan -target="${MODULE}"
```

### 3. Use Files for Complex Data
Never embed JSON/YAML/HCL inline in commands.

```bash
# BAD: Inline complex data
terraform apply -var 'tags={"Environment":"prod"}'

# GOOD: Write to file first, then reference
# Use Write tool to create /tmp/values.yaml
kubectl apply -f /tmp/values.yaml --dry-run=server
```

### 4. Log Each Step
Add echo statements to verify progress.

```bash
echo "Step 1: Validating configuration..."
terraform validate
if [ $? -eq 0 ]; then
  echo "Validation passed"
else
  echo "Validation failed"
  exit 1
fi
```

### 5. Respect Tool Timeouts
Keep operations under 120 seconds (Bash tool default).

- For long operations, use explicit `--timeout` flags
- For flux: always `--timeout=90s` or less
- For heavy deployments, extend Bash timeout parameter

### 6. Avoid Pipes in Critical Paths
Pipes hide exit codes and make debugging harder.

```bash
# BAD: Piped commands
kubectl get pods -o json | jq '.items[0]'
gcloud compute instances list | grep running

# GOOD: Native output formats or temp files
kubectl get pods -o jsonpath='{.items[0]}'
gcloud compute instances list --filter='status:RUNNING'
```

### 7. Use Native Tools Over Bash
Prefer Claude Code tools for file operations.

| Instead of... | Use... |
|---------------|--------|
| `cat file.tf` | `Read` tool |
| `echo "..." > file.yaml` | `Write` tool |
| `sed -i 's/old/new/' file` | `Edit` tool |
| `grep -r "pattern" dir/` | `Grep` tool |

### 8. Never Use Heredocs (Except Git Commits)
Heredocs fail in batch/CLI contexts. Use Write tool instead.

### 9. Explicit Error Handling
Verify success before continuing.

```bash
echo "Applying configuration..."
terraform apply -auto-approve
if [ $? -eq 0 ]; then
  echo "Apply successful"
else
  echo "Apply failed"
  exit 1
fi
```

## Path Handling

### Always Use Absolute Paths
```bash
# BAD: Relative paths depend on context
cd ../../shared/vpc && terraform plan

# GOOD: Absolute paths are explicit
terraform plan -chdir="/path/to/terraform/shared/vpc"
```

### Verify Location Before Operations
```bash
cd /path/to/terraform/module
pwd  # Verify location
terraform plan
```

## Timeout Guidelines

| Operation Type | Recommended Timeout |
|----------------|---------------------|
| Validation (lint, fmt) | 30s |
| Plan operations | 300s |
| Apply operations | 600s |
| Flux reconcile | 90s max |
| kubectl wait | 120s |
