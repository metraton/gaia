# Agent Protocol -- Status-Specific Examples

Read on-demand when constructing a `json:contract` block.
See `SKILL.md` for the schema definition and field rules.

## COMPLETE (task finished, evidence-backed)

```json:contract
{
  "agent_status": {
    "plan_status": "COMPLETE",
    "agent_id": "ab7e4d2",
    "pending_steps": [],
    "next_action": "done"
  },
  "evidence_report": {
    "patterns_checked": ["existing HelmRelease naming convention in flux/apps/"],
    "files_checked": ["flux/apps/qxo-api/helmrelease.yaml"],
    "commands_run": ["kubectl get hr -n qxo -> all reconciled"],
    "key_outputs": ["All 12 HelmReleases healthy, no drift detected"],
    "verbatim_outputs": [],
    "cross_layer_impacts": [],
    "open_gaps": []
  },
  "consolidation_report": null
}
```

## BLOCKED (cannot proceed)

```json:contract
{
  "agent_status": {
    "plan_status": "BLOCKED",
    "agent_id": "ac3a1f9",
    "pending_steps": ["validate IAM binding", "apply terraform change"],
    "next_action": "User must grant roles/container.admin to SA"
  },
  "evidence_report": {
    "patterns_checked": ["SA binding pattern in terraform/iam/"],
    "files_checked": ["terraform/iam/main.tf", "terraform/iam/variables.tf"],
    "commands_run": ["gcloud iam service-accounts get-iam-policy sa@proj.iam -> missing binding"],
    "key_outputs": ["SA lacks roles/container.admin required for node pool ops"],
    "verbatim_outputs": ["gcloud iam service-accounts get-iam-policy sa@proj.iam:\n```\nbindings: []\n```"],
    "cross_layer_impacts": ["GKE node pool scaling depends on this SA"],
    "open_gaps": ["Whether SA should get role directly or via workload identity"]
  },
  "consolidation_report": null
}
```

## NEEDS_INPUT (missing information)

```json:contract
{
  "agent_status": {
    "plan_status": "NEEDS_INPUT",
    "agent_id": "ad9f2b1",
    "pending_steps": ["create namespace manifest", "configure HelmRelease"],
    "next_action": "User must choose: Option A (shared namespace) or Option B (dedicated namespace)"
  },
  "evidence_report": {
    "patterns_checked": ["namespace conventions in flux/clusters/"],
    "files_checked": ["flux/clusters/dev/namespaces/"],
    "commands_run": [],
    "key_outputs": ["Both patterns exist in codebase -- no single convention"],
    "verbatim_outputs": [],
    "cross_layer_impacts": ["Network policies differ per pattern"],
    "open_gaps": ["User preference for namespace isolation"]
  },
  "consolidation_report": null
}
```

## PENDING_APPROVAL (T3 plan ready)

```json:contract
{
  "agent_status": {
    "plan_status": "PENDING_APPROVAL",
    "agent_id": "ae5c8a3",
    "pending_steps": ["execute terraform apply", "verify state"],
    "next_action": "Awaiting user approval for terraform apply"
  },
  "evidence_report": {
    "patterns_checked": ["existing bucket naming in terraform/gcs/"],
    "files_checked": ["terraform/gcs/main.tf", "terraform/gcs/variables.tf"],
    "commands_run": ["terraform plan -out=tfplan -> 1 to add, 0 to change, 0 to destroy"],
    "key_outputs": ["Plan adds 1 GCS bucket with standard config"],
    "verbatim_outputs": ["terraform plan:\n```\n+ google_storage_bucket.events\n  name: qxo-events-dev\n  location: us-east4\n```"],
    "cross_layer_impacts": ["Flux ExternalSecret must reference new bucket"],
    "open_gaps": []
  },
  "consolidation_report": null
}
```

## With Consolidation (multi-surface task)

```json:contract
{
  "agent_status": {
    "plan_status": "COMPLETE",
    "agent_id": "af4b2e8",
    "pending_steps": [],
    "next_action": "done"
  },
  "evidence_report": {
    "patterns_checked": ["terraform module structure in terraform/modules/"],
    "files_checked": ["terraform/modules/gke/main.tf", "flux/clusters/dev/kustomization.yaml"],
    "commands_run": ["terragrunt plan -chdir=/abs/path -> no changes"],
    "key_outputs": ["Terraform state matches code; Flux kustomization references correct cluster"],
    "verbatim_outputs": [],
    "cross_layer_impacts": ["Flux depends on GKE node pool count from terraform output"],
    "open_gaps": ["HPA config in flux not verified"]
  },
  "consolidation_report": {
    "ownership_assessment": "cross_surface_dependency",
    "confirmed_findings": ["GKE cluster config matches terraform code", "Node pool count is 3 in both plan and live"],
    "suspected_findings": ["HPA max replicas may exceed node capacity"],
    "conflicts": [],
    "open_gaps": ["HPA config in flux not verified -- gitops-operator should check"],
    "next_best_agent": "gitops-operator"
  }
}
```
