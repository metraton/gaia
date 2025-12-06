# Command Anti-Patterns

Common mistakes to avoid when executing infrastructure commands.

## kubectl Anti-Patterns

### Pipes with kubectl output
```bash
# BAD: Exit code only reflects jq, not kubectl
kubectl get pods -o json | jq '.items[0].metadata.name'

# GOOD: Use native jsonpath
kubectl get pods -o jsonpath='{.items[0].metadata.name}'
```

### Apply without validation
```bash
# BAD: Silent partial failures
kubectl apply -f manifest.yaml

# GOOD: Validate first
kubectl apply -f manifest.yaml --dry-run=server
kubectl diff -f manifest.yaml
kubectl apply -f manifest.yaml
```

### Chained kubectl commands
```bash
# BAD: Hard to verify each step
kubectl get service app && kubectl patch service app -p '...'

# GOOD: Separate with verification
kubectl get service app -o yaml > /tmp/service.yaml
kubectl patch -f /tmp/service.yaml -p '...'
```

## terraform/terragrunt Anti-Patterns

### Chained terraform commands
```bash
# BAD: Error buried if init succeeds but validate fails
terraform init && terraform validate && terraform plan

# GOOD: Separate commands
terraform init
terraform validate
terraform plan -out=/tmp/tfplan
```

### Relative working directories
```bash
# BAD: Relative paths depend on current location
terragrunt plan --terragrunt-working-dir=../../vpc

# GOOD: Absolute paths
terragrunt plan --terragrunt-working-dir=/path/to/terraform/vpc
```

### Skip init before plan
```bash
# BAD: Cryptic errors if modules not initialized
terragrunt plan

# GOOD: Always init first
terragrunt init
terragrunt plan
```

## gcloud Anti-Patterns

### Pipes with gcloud commands
```bash
# BAD: Can't tell if gcloud succeeded
gcloud compute instances list | grep running | awk '{print $1}'

# GOOD: Native filters
gcloud compute instances list --filter='status:RUNNING' --format='value(name)'
```

### Exploration commands (forbidden)
```bash
# BAD: Violates code-first protocol
find /terraform -name "*.tf" -exec grep -l "google_compute" {} \;

# GOOD: Use contract-provided paths directly
Read("/path/from/contract/main.tf")
```

### Logs without time range
```bash
# BAD: Returns massive result set, times out
gcloud logging read "resource.type=gke_cluster"

# GOOD: Always specify limits
gcloud logging read "resource.type=gke_cluster" --limit=50 --freshness=1h
```

## helm Anti-Patterns

### Complex inline values
```bash
# BAD: Shell escaping conflicts with YAML
helm upgrade app chart --set "config={key: value, nested: {foo: bar}}"

# GOOD: Use values file
# Write values to /tmp/values.yaml first
helm upgrade app chart -f /tmp/values.yaml
```

### Chained helm commands
```bash
# BAD: Hard to debug which step failed
helm lint chart && helm template app chart && helm upgrade app chart

# GOOD: Separate with verification
helm lint chart
helm template app chart > /tmp/manifest.yaml
kubectl apply -f /tmp/manifest.yaml --dry-run=server
helm upgrade app chart
```

## flux Anti-Patterns

### Reconcile without timeout
```bash
# BAD: Default 5min timeout exceeds Bash tool limit (2min)
flux reconcile helmrelease app -n namespace

# GOOD: Always set timeout
flux reconcile helmrelease app -n namespace --timeout=90s
```

### Ignore reconcile errors
```bash
# BAD: Silently ignores failures
flux reconcile helmrelease app || true

# GOOD: Verify success after reconciliation
flux reconcile helmrelease app --timeout=90s
kubectl wait --for=condition=Ready helmrelease/app --timeout=30s
```

## npm/pytest Anti-Patterns

### Chained test commands
```bash
# BAD: Status only reflects last command
npm install && npm run lint && npm run test && npm run build

# GOOD: Separate each step
npm install
npm run lint
npm run test
npm run build
```

### Global package installation
```bash
# BAD: Not reproducible across environments
npm install -g typescript

# GOOD: Local dev dependencies
npm install --save-dev typescript
npx tsc --version
```

## Docker Anti-Patterns

### Complex inline build args
```bash
# BAD: Escaping issues with special chars
docker build -t app --build-arg "config=$(cat config.json | tr '\n' ' ')" .

# GOOD: Use build context files
docker build -t app --build-arg CONFIG_FILE=config.json .
```

### Chain without verification
```bash
# BAD: If build fails, tag/push may use old image
docker build -t app . && docker tag app:v1 && docker push app:v1

# GOOD: Verify each step
docker build -t app .
docker images app:latest  # Verify created
docker tag app:latest app:v1.0.0
```
