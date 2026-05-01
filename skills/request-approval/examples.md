# Approval Request Examples

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
- `terragrunt plan` - No errors, 4 to add, 0 to change, 0 to destroy
- `terragrunt hclfmt --check` - No formatting issues
- `terraform validate` - Success

**Dependencies verified:**
- GCP project [project-id]: accessible
- No CIDR conflicts with existing networks

### Risk Assessment

**Risk Level:** MEDIUM

**Potential Risks:**
1. CIDR overlap with existing VPC networks
   - Mitigation: Verified no overlaps via `gcloud compute networks list`
2. Subnet creation timeout
   - Mitigation: Timeout set to 300s, idempotent -- safe to retry

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

- `gcloud compute networks describe prod-network --project=[project-id]` -> `status: ACTIVE`
- `gcloud compute networks subnets list --filter="network:prod-network" --project=[project-id]` -> 3 subnets listed

### Files Affected

**Git changes:**
- Added: `[terraform_vpc_path]/terragrunt.hcl`
- Added: `[terraform_vpc_path]/main.tf`
```

The corresponding `approval_request` object inside the agent's `json:contract`:

```json
"approval_request": {
  "operation": "Apply Terraform changes -- production VPC",
  "exact_content": "terragrunt apply -auto-approve --terragrunt-working-dir \"/abs/path/to/terraform/vpc\"",
  "scope": "google_compute_network.prod-network + 3 subnetworks in us-east4 (prod project)",
  "risk_level": "MEDIUM",
  "rollback": "terragrunt destroy --terragrunt-working-dir \"/abs/path/to/terraform/vpc\"",
  "verification": "gcloud compute networks describe prod-network -> status: ACTIVE; subnets list -> 3",
  "approval_id": "<hex from hook deny response, when blocked>"
}
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
  - Image: ghcr.io/vtr/graphql-server:v1.0.176 -> v1.0.180
  - No other changes

### Validation Results

**Dry-run status:**
- `kubectl apply --dry-run=client` - Valid manifest
- YAML syntax check - Passed
- Image exists in registry - Verified

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

- `kubectl get helmrelease graphql-server -n common --request-timeout=30s` -> `READY=True`, revision contains `v1.0.180`
- `kubectl get pods -n common -l app=graphql-server --request-timeout=30s` -> all pods `Running`

### Files Affected

**Git changes:**
- Modified: `gitops/clusters/prod-digital-eks/common/graphql-server.yaml`
```

The corresponding `approval_request` object inside the agent's `json:contract`:

```json
"approval_request": {
  "operation": "Push graphql-server v1.0.180 + Flux reconcile",
  "exact_content": "git push origin main",
  "scope": "gitops/clusters/prod-digital-eks/common/graphql-server.yaml -- prod cluster",
  "risk_level": "LOW",
  "rollback": "git revert HEAD && git push origin main",
  "verification": "kubectl get hr graphql-server -n common -- READY=True, revision contains v1.0.180",
  "approval_id": "<hex from hook deny response, when blocked>"
}
```

## Example 3: Batch (verb-family) approval -- many commands, one grant

Use this when one user intent expands into many commands sharing the same base
CLI and verb. Without `batch_scope`, every command after the first generates a
fresh nonce and is re-blocked.

```markdown
## Gmail Archive Batch Plan

### Summary
- Archive 500 messages currently in INBOX matching label "older-than-90d"
- Each call modifies one message; the verb is `modify` for all 500
- No deletion -- messages move to Archive, recoverable via removeLabelIds

### Changes Proposed
- 500 calls to `gws gmail users messages modify --addLabelIds Archive`
- Each call differs only by `messageId=<id>`

### Validation Results
- `gws gmail users messages list --query "label:older-than-90d"` -> 500 message IDs collected
- Dry-run on first message -> succeeds, label applied

### Risk Assessment
- Risk level: MEDIUM -- bulk label modification, reversible
- Rollback: re-run with `--removeLabelIds Archive` over the same 500 IDs
- Verification: `gws gmail users messages list --labelIds Archive | wc -l` increases by 500
```

The corresponding `approval_request` object inside the agent's `json:contract`:

```json
"approval_request": {
  "operation": "Archive 500 Gmail messages older than 90d -- add Archive label",
  "exact_content": "gws gmail users messages modify --addLabelIds Archive userId=me messageId=<each of 500>",
  "scope": "All `gws ... modify` calls for the next 10 minutes (verb-family grant)",
  "risk_level": "MEDIUM",
  "rollback": "gws gmail users messages modify --removeLabelIds Archive over the same 500 IDs",
  "verification": "gws gmail users messages list --labelIds Archive shows +500",
  "batch_scope": "verb_family",
  "approval_id": "<hex from hook deny response, when blocked>"
}
```

The orchestrator presents this with options including `"Approve batch -- archive
500 Gmail messages [P-{nonce_prefix8}]"` and `"Approve single -- {first_command}
[P-{nonce_prefix8}]"`. On batch approval, the runtime creates a multi-use grant
covering all `gws ... modify` calls for 10 minutes; the agent runs all 500
without re-blocking.

If the agent later needs a different verb on the same CLI (e.g., `gws ... delete`
to clean up matching threads), that is a different verb-family and requires its
own approval -- the modify batch grant does NOT cover it.
