# Agent Protocol -- Status-Specific Examples

Read on-demand when constructing an `agent_contract_handoff` envelope.
See `SKILL.md` for the schema definition and field rules.

The envelope shape below is unchanged by the by-value CLI model (`SKILL.md`
"Building the contract"): building it with `gaia contract init`/`set`/`add`/
`fill --json`/`finalize` produces this exact JSON, one field at a time, and
`gaia contract view` prints it in this same shape. The fenced block tag is
`agent_contract_handoff` (single canonical format) for the fallback path;
each example below opens the block with that tag as the reference for what
either path -- CLI draft or fence -- must validate to.

## 1. COMPLETE (verified result, happy path)

Standard terminal envelope after a successful increment. `verification` is required and `result` must be `"pass"`.

```agent_contract_handoff
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

## 2. BLOCKED (cannot proceed alone)

Escalation envelope -- the agent identified a gap it cannot close on its own surface.

```agent_contract_handoff
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

## 3. NEEDS_INPUT (missing decision from user)

`next_action` lists the explicit choices.

```agent_contract_handoff
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

## 4. APPROVAL_REQUEST (hook blocked T3 command)

Hook produced `approval_id` -- pass it through verbatim. The orchestrator presents the operation to the user for explicit consent.

```agent_contract_handoff
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
    "verbatim_outputs": ["[T3_BLOCKED] This command requires user approval. ... approval_id: P-a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"],
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
    "approval_id": "P-a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"
  }
}
```

## 5. COMPLETE with `memorialize_suggestions` (curate gaia memory)

The agent uncovered a fact worth persisting (a decision, an anchor) and offers it as a memorialize entry. The orchestrator presents it to the user; the user decides whether it lands in gaia memory. Required fields per entry: `description`, `body`.

```agent_contract_handoff
{
  "agent_status": {
    "plan_status": "COMPLETE",
    "agent_id": "a2e8c14",
    "pending_steps": [],
    "next_action": "done"
  },
  "evidence_report": {
    "patterns_checked": ["release tag schema in package.json"],
    "files_checked": ["package.json", "scripts/build-plugin.py"],
    "commands_run": ["npm version --no-git-tag-version -> 1.4.0-rc.3"],
    "key_outputs": ["RC tag policy confirmed: -rc.N suffix routes to npm tag 'rc'"],
    "verbatim_outputs": [],
    "cross_layer_impacts": [],
    "open_gaps": [],
    "verification": {
      "method": "self-review",
      "checks": ["publish.yml auto-detect logic matches -rc.N suffix"],
      "result": "pass",
      "details": "Confirmed `.github/workflows/publish.yml` parses suffix to set --tag."
    }
  },
  "memorialize_suggestions": [
    {
      "type": "decision",
      "class": "anchor",
      "description": "RC versioning convention for @jaguilar87/gaia",
      "body": "Versions matching X.Y.Z-rc.N publish to npm tag 'rc'; X.Y.Z publishes to 'latest'. Auto-detected by publish.yml on the GitHub Release event. No manual --tag flag is supported."
    }
  ],
  "consolidation_report": null,
  "approval_request": null
}
```

## 6. COMPLETE with `consolidation_report.ownership_assessment` (multi-surface task)

The injected handoff carried `consolidation_required: true`; the agent reports ownership state and names the next agent if the task crosses surfaces. Enum values: `owned_here`, `cross_surface_dependency`, `not_my_surface`.

```agent_contract_handoff
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

## 7. `loop_state` -- blocking vs non-blocking

Agentic-loop agents emit a `loop_state` dict. The runtime (`_check_loop_state_blocking` in `contract_validator.py`) blocks `COMPLETE` when `iteration < max_iterations AND metric < threshold` -- in that case another iteration is forced and the contract is rejected. When `metric >= threshold` (or iteration count is exhausted) the `COMPLETE` is accepted.

### 7a. Blocking case (metric below threshold, iteration remaining)

The runtime will reject this `COMPLETE` and force the agent to iterate again.

```agent_contract_handoff
{
  "agent_status": {
    "plan_status": "COMPLETE",
    "agent_id": "a19a3d7",
    "pending_steps": [],
    "next_action": "done"
  },
  "evidence_report": {
    "patterns_checked": ["test selection in CI"],
    "files_checked": ["tests/layer1_prompt_regression/"],
    "commands_run": ["pytest tests/layer1_prompt_regression -q -> 42 passed, 3 failed"],
    "key_outputs": ["3 prompt regressions remain"],
    "verbatim_outputs": [],
    "cross_layer_impacts": [],
    "open_gaps": ["3 failures need investigation"],
    "verification": {
      "method": "test",
      "checks": ["pytest exit code"],
      "result": "pass",
      "details": "42/45 passed"
    }
  },
  "loop_state": {
    "iteration": 2,
    "max_iterations": 5,
    "metric": 0.93,
    "threshold": 0.98
  },
  "consolidation_report": null,
  "approval_request": null
}
```

### 7b. Non-blocking case (metric meets threshold)

`metric >= threshold` -- the `COMPLETE` lands as terminal.

```agent_contract_handoff
{
  "agent_status": {
    "plan_status": "COMPLETE",
    "agent_id": "a4e8b21",
    "pending_steps": [],
    "next_action": "done"
  },
  "evidence_report": {
    "patterns_checked": ["test selection in CI"],
    "files_checked": ["tests/layer1_prompt_regression/"],
    "commands_run": ["pytest tests/layer1_prompt_regression -q -> 45 passed"],
    "key_outputs": ["All prompt regressions pass"],
    "verbatim_outputs": [],
    "cross_layer_impacts": [],
    "open_gaps": [],
    "verification": {
      "method": "test",
      "checks": ["pytest exit code"],
      "result": "pass",
      "details": "45/45 passed"
    }
  },
  "loop_state": {
    "iteration": 4,
    "max_iterations": 5,
    "metric": 1.0,
    "threshold": 0.98
  },
  "consolidation_report": null,
  "approval_request": null
}
```

## 8. COMPLETE with `update_contracts` (index a discovery into project-context)

The agent discovered a project fact a section it owns did not yet hold, and writes it back so the next agent does not re-derive it. `update_contracts` is an array of `{contract, payload}`; `contract` must be a name from the INPUT `write_permissions.writable_sections`, and `payload` carries only the keys to add or update (index, not live-state). See `agent-contract-handoff` for merge semantics.

```agent_contract_handoff
{
  "agent_status": {
    "plan_status": "COMPLETE",
    "agent_id": "a7c1d93",
    "pending_steps": [],
    "next_action": "done"
  },
  "evidence_report": {
    "patterns_checked": ["service entrypoints under services/"],
    "files_checked": ["services/graphql-server/package.json"],
    "commands_run": [],
    "key_outputs": ["graphql-server runs on port 3000 in namespace common"],
    "verbatim_outputs": [],
    "cross_layer_impacts": [],
    "open_gaps": [],
    "verification": {
      "method": "self-review",
      "checks": ["port and namespace confirmed against package.json and manifest"],
      "result": "pass",
      "details": "Service identifiers read directly from source; no live-state cached."
    }
  },
  "update_contracts": [
    {
      "contract": "application_services",
      "payload": {
        "services": [
          {"name": "graphql-server", "port": 3000, "namespace": "common"}
        ]
      }
    }
  ],
  "consolidation_report": null,
  "approval_request": null
}
```

## Notes on multi-command APPROVAL_REQUEST sweeps

**Per-command (default):** when T3 commands appear one at a time as the agent
works, each blocked command produces its own `APPROVAL_REQUEST` with an
`approval_id` (shape identical to example 4 above). Do not emit `batch_scope`
-- it is ignored.

**Compound-command batch (hook-minted, not agent-declared):** there is no
plan-first step and no `gaia approvals derive-id` call -- you never construct
or request a batch id yourself. When the agent runs a single Bash call that
chains >= 2 T3 sub-commands it already knows belong together (e.g. `git add
-A && git commit -m 'v1.2.0' && git push origin main`), and the hook's
compound-command classifier (`bash_validator._validate_compound_command`)
finds >= 2 of those sub-commands ungranted, it blocks the whole call and mints
ONE `COMMAND_SET` pending covering the chain (`decide_t3_outcome(command_set=
...)`), with a single content-derived `approval_id`
(`derive_command_set_id`). The block's denial message ends in that
`approval_id`, exactly like a singular block -- relay it verbatim into
`approval_request` the same way as example 4; you do not author the
`command_set` field. TTL is 5 minutes, same as the singular grant. Each
sub-command is then consumed byte-for-byte on its own retry, before it
executes, until the whole set is `CONSUMED`.
