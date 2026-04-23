# Agent Protocol -- Status-Specific Examples

Read on-demand when constructing a `json:contract` block.
See `SKILL.md` for the schema definition and field rules.

## COMPLETE (verified result)

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
    "open_gaps": [],
    "verification": {
      "method": "test",
      "checks": ["kubectl get hr -n qxo shows all reconciled", "no suspended or failed HelmReleases"],
      "result": "pass",
      "details": "12/12 HelmReleases Ready=True. Last reconciled within 5m."
    }
  },
  "consolidation_report": null,
  "approval_request": null
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
    "open_gaps": ["Whether SA should get role directly or via workload identity"],
    "verification": null
  },
  "consolidation_report": null,
  "approval_request": null
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
    "open_gaps": ["User preference for namespace isolation"],
    "verification": null
  },
  "consolidation_report": null,
  "approval_request": null
}
```

## APPROVAL_REQUEST (hook blocked T3 command or plan ready for user feedback)

```json:contract
{
  "agent_status": {
    "plan_status": "APPROVAL_REQUEST",
    "agent_id": "ae5c8a3",
    "pending_steps": ["execute terraform apply", "verify state"],
    "next_action": "Awaiting user feedback on terraform apply plan"
  },
  "evidence_report": {
    "patterns_checked": ["existing bucket naming in terraform/gcs/"],
    "files_checked": ["terraform/gcs/main.tf", "terraform/gcs/variables.tf"],
    "commands_run": ["terraform plan -out=tfplan -> 1 to add, 0 to change, 0 to destroy"],
    "key_outputs": ["Plan adds 1 GCS bucket with standard config"],
    "verbatim_outputs": ["terraform plan:\n```\n+ google_storage_bucket.events\n  name: qxo-events-dev\n  location: us-east4\n```"],
    "cross_layer_impacts": ["Flux ExternalSecret must reference new bucket"],
    "open_gaps": [],
    "verification": null
  },
  "consolidation_report": null,
  "approval_request": {
    "operation": "Create GCS bucket qxo-events-dev",
    "exact_content": "terraform apply -auto-approve",
    "scope": "terraform/gcs/main.tf, GCS bucket in us-east4",
    "risk_level": "MEDIUM",
    "rollback": "terraform destroy -target=google_storage_bucket.events",
    "verification": "gcloud storage buckets describe gs://qxo-events-dev"
  }
}
```

## APPROVAL_REQUEST with approval_id (hook blocked T3 command)

```json:contract
{
  "agent_status": {
    "plan_status": "APPROVAL_REQUEST",
    "agent_id": "af1d9b7",
    "pending_steps": ["execute git push", "verify Flux reconciliation"],
    "next_action": "Hook blocked git push -- awaiting user approval"
  },
  "evidence_report": {
    "patterns_checked": ["git branch naming in flux/clusters/"],
    "files_checked": ["flux/apps/qxo-api/helmrelease.yaml"],
    "commands_run": ["git diff HEAD -> 1 file changed", "git push origin main -> BLOCKED by hook"],
    "key_outputs": ["Push blocked by security hook, approval_id issued"],
    "verbatim_outputs": ["[T3_BLOCKED] MUTATIVE operation requires user approval. approval_id: a1b2c3..."],
    "cross_layer_impacts": ["Flux will reconcile HelmRelease on push"],
    "open_gaps": [],
    "verification": null
  },
  "consolidation_report": null,
  "approval_request": {
    "operation": "Push HelmRelease changes to main",
    "exact_content": "git push origin main",
    "scope": "flux/apps/qxo-api/helmrelease.yaml",
    "risk_level": "MEDIUM",
    "rollback": "git revert HEAD && git push",
    "verification": "flux get hr -n qxo -> reconciled",
    "approval_id": "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"
  }
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
    "open_gaps": ["HPA config in flux not verified"],
    "verification": {
      "method": "dry-run",
      "checks": ["terragrunt plan shows no changes", "kustomization references match cluster name"],
      "result": "pass",
      "details": "Plan: 0 to add, 0 to change, 0 to destroy. Kustomization sourceRef matches cluster af4b2e8."
    }
  },
  "consolidation_report": {
    "ownership_assessment": "cross_surface_dependency",
    "confirmed_findings": ["GKE cluster config matches terraform code", "Node pool count is 3 in both plan and live"],
    "suspected_findings": ["HPA max replicas may exceed node capacity"],
    "conflicts": [],
    "open_gaps": ["HPA config in flux not verified -- gitops-operator should check"],
    "next_best_agent": "gitops-operator"
  },
  "approval_request": null
}
```

## COMPLETE with task decomposition (multi-increment)

Shows a skill-creation task where each subtask was verified individually.

```json:contract
{
  "agent_status": {
    "plan_status": "COMPLETE",
    "agent_id": "a9c4f71",
    "pending_steps": [],
    "next_action": "done"
  },
  "evidence_report": {
    "patterns_checked": ["existing skill structure in skills/", "skill-creation standards"],
    "files_checked": ["skills/new-skill/SKILL.md", "skills/new-skill/reference.md"],
    "commands_run": [],
    "key_outputs": ["Created new-skill with SKILL.md (87 lines) and reference.md"],
    "verbatim_outputs": [],
    "cross_layer_impacts": ["Agents using this skill need frontmatter update"],
    "open_gaps": [],
    "verification": {
      "method": "self-review",
      "checks": [
        "SKILL.md line count: 87 (under 100 budget)",
        "Frontmatter has name, description, metadata fields",
        "Description contains triggering conditions only",
        "Type-appropriate structure (domain: conventions, examples, key rules)"
      ],
      "result": "pass",
      "details": "87 lines. Frontmatter valid. Description triggers on domain conditions. Structure matches domain type from skill-creation standards."
    }
  },
  "consolidation_report": null,
  "approval_request": null
}
```
