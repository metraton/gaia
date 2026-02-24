# Approval Plan Examples

Reference examples for agents. Read on-demand when building your first plan or when unsure about format.

## Example 1: Terraform Apply (GCP)

```markdown
## Terraform Apply Plan

### Summary
- Creating GCP VPC network for production cluster
- Adds 3 subnetworks across us-east4-a, us-east4-b, us-east4-c
- No existing resources affected

### Changes Proposed

**Resources to CREATE:**
- `google_compute_network.prod-network`: VPC in auto-subnet mode disabled
- `google_compute_subnetwork.prod-subnet-a`: 10.0.1.0/24 in us-east4-a
- `google_compute_subnetwork.prod-subnet-b`: 10.0.2.0/24 in us-east4-b
- `google_compute_subnetwork.prod-subnet-c`: 10.0.3.0/24 in us-east4-c

**Resources to MODIFY:** None
**Resources to DELETE:** None

### Validation Results

**Dry-run status:**
- ✅ `terragrunt plan` - No errors, 4 to add, 0 to change, 0 to destroy
- ✅ `terragrunt hclfmt --check` - No formatting issues
- ✅ `terraform validate` - Success

**Dependencies verified:**
- GCP project [project-id]: accessible ✓
- No CIDR conflicts with existing networks ✓

### Risk Assessment

**Risk Level:** MEDIUM

**Potential Risks:**
1. CIDR overlap with existing VPC networks
   - Mitigation: Verified no overlaps via `gcloud compute networks list`
2. Subnet creation timeout
   - Mitigation: Timeout set to 300s, idempotent — safe to retry

**Rollback Plan:**
- If creation fails: `terragrunt destroy --terragrunt-working-dir "/abs/path/to/terraform/vpc"`
- Recovery time: ~5 minutes

### Execution Steps

When approved, will execute:
1. `git add [terraform_vpc_path]/`
2. `git commit -m "feat(infra): add production VPC network"`
3. `git push origin main`
4. `terragrunt apply -auto-approve --terragrunt-working-dir "/abs/path/to/terraform/vpc"`

### Verification Criteria

- `gcloud compute networks describe prod-network --project=[project-id]` → `status: ACTIVE`
- `gcloud compute networks subnets list --filter="network:prod-network" --project=[project-id]` → 3 subnets listed

### Files Affected

**Git changes:**
- Added: `[terraform_vpc_path]/terragrunt.hcl`
- Added: `[terraform_vpc_path]/main.tf`

## Approval Required

**Operation:** terragrunt apply
**Environment:** prod
**Risk Level:** MEDIUM
```

## Example 2: GitOps Deployment

```markdown
## GitOps Deployment Plan

### Summary
- Updating graphql-server image to v1.0.180
- No configuration changes
- Flux will auto-reconcile in ~1 minute

### Changes Proposed

**HelmRelease to MODIFY:**
- `graphql-server` in namespace `common`
  - Image: ghcr.io/vtr/graphql-server:v1.0.176 → v1.0.180
  - No other changes

### Validation Results

**Dry-run status:**
- ✅ `kubectl apply --dry-run=client` - Valid manifest
- ✅ YAML syntax check - Passed
- ✅ Image exists in registry - Verified

### Risk Assessment

**Risk Level:** LOW

**Potential Risks:**
1. New image might have bugs
   - Mitigation: Tested in dev cluster, all tests passed
2. Pod restart might cause brief downtime
   - Mitigation: RollingUpdate strategy, 2 replicas ensure availability

**Rollback Plan:**
- If deployment fails: `git revert` + `flux reconcile`
- Recovery time: ~2 minutes

### Execution Steps

When approved, will execute:
1. `git add gitops/clusters/prod-digital-eks/common/graphql-server.yaml`
2. `git commit -m "chore(graphql): update to v1.0.180"`
3. `git push origin main`
4. Flux auto-reconciles in ~1 minute (or force: `flux reconcile helmrelease graphql-server -n common --timeout=90s`)

### Verification Criteria

- `kubectl get helmrelease graphql-server -n common --request-timeout=30s` → `READY=True`, revision contains `v1.0.180`
- `kubectl get pods -n common -l app=graphql-server --request-timeout=30s` → all pods `Running`

### Files Affected

**Git changes:**
- Modified: `gitops/clusters/prod-digital-eks/common/graphql-server.yaml`

## Approval Required

**Operation:** git push + flux reconcile
**Environment:** prod
**Risk Level:** LOW
```
