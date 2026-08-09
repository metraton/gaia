# Agent Protocol -- Status-Specific Examples

Read on-demand when constructing an `agent_contract_handoff` envelope.
See `agent-contract-handoff` for the schema definition and field rules, and
`reference.md` for where each moment's answer lands.

Each example below is the envelope **as the row holds it** -- the shape your
`set`/`add`/`fill --json` calls build up one field at a time, and the shape
`gaia contract view` prints back. The row is the handoff, and whoever needs
this turn queries it (`SKILL.md`, principle 1). Read these as the target you
are filling toward.

The same envelope is also what the closing fence carries. The final message
still ends with the envelope in a fenced block tagged `agent_contract_handoff`
(not `json` -- the tag is how `parse_contract` finds it). The gate itself
never reads this copy, in any of its three cases (`reference.md`, "The gate
at the wall") -- only the turn's own persisted row decides the close. The
fence stays required because `parse_contract` still feeds it to the turn's
descriptive readers: episode metrics, `key_outputs`, `update_contracts`,
response-contract anomalies, and the T9 backstop. Emit it every time.

## 0. Building example 1 via the CLI, from first write to close

Every example below can be read as "the JSON shape", but example 1 is walked
through here as a CLI-built turn end-to-end, so the shape and the calls that
produce it are visibly the same contract:

```
# the row and its draft already exist; contract_id comes from `# Your Contract`
gaia contract set agent_status.agent_state IN_PROGRESS --draft-id <contract_id>
# ... work happens: kubectl get hr -n qxo, etc ...
gaia contract fill --json '{
  "evidence_report": {
    "patterns_checked": ["existing HelmRelease naming convention in flux/apps/"],
    "files_checked": ["flux/apps/qxo-api/helmrelease.yaml"],
    "commands_run": ["kubectl get hr -n qxo -> all reconciled"],
    "key_outputs": ["All 12 HelmReleases healthy, no drift detected"],
    "verbatim_outputs": [],
    "cross_layer_impacts": [],
    "open_gaps": []
  }
}' --draft-id <contract_id>
gaia contract fill --json '{"evidence_report": {"verification": {"type": "command", "command": "kubectl get hr -n qxo", "method": "listed every HelmRelease in the namespace and read its Ready and suspended columns", "checks": ["kubectl get hr -n qxo shows all reconciled", "no suspended or failed HelmReleases"], "result": "pass", "details": "12/12 HelmReleases Ready=True. Last reconciled within 5m."}}}' --draft-id <contract_id>
gaia contract set agent_status.agent_state COMPLETE --draft-id <contract_id>
gaia contract validate --draft-id <contract_id>   # confirm the verdict before finalizing
gaia contract finalize --draft-id <contract_id>   # writes the sole, idempotent agent_contract_handoffs row
```

No `--plan-task-id` here, and not by omission: `cmd_finalize` refuses a
`COMPLETE` whose turn is bound to a plan task (`reason:
blind_verification_required`), whether the binding arrives as that flag or is
recovered from the born row. A plan-task-bound turn walks these same calls but
sets `NEEDS_VERIFICATION` instead of `COMPLETE` (example 9), and passes
`--plan-task-id <id>` to finalize.

Order matters here: `verification` is filled in BEFORE `agent_state` is set to `COMPLETE` (`reference.md`, "Build order for a terminal state"). Reversing those two calls rejects with `VERIFICATION_RESULT` on the `set agent_state COMPLETE` step, because validate-on-write checks the FULL envelope at that point, not just the field being set.

The draft this produces is byte-for-byte the same envelope as example 1
below, and `finalize` writing that row is where the turn ends. The stop gate
resolves this turn's own persisted row and validates the envelope THAT row
holds. What the turn says in its final message is an account for whoever is
reading, closing with the fenced envelope -- the detail behind it is queried
from the row, at whatever granularity the question needs, whenever it is
asked.

## 1. COMPLETE (verified result, happy path)

Standard terminal envelope after a successful increment. `verification` is required and `result` must be `"pass"`.

The two verification fields are not synonyms and both earn their place. `type` is the classifier the validator reads, over an OPEN vocabulary: declaring one is a claim that a check ran, and the claim is priced in a companion field -- `command`/`code` owe `command`, `semantic` owes `requires_human`, `self_review` owes `reviewed`, any other word owes at least one of those three, and `none` (no oracle was required) owes nothing. `method` is free prose naming HOW you checked; nothing reads it as a classifier, so a block carrying only `method` declares no type and is asked for no evidence. See `agent-contract-handoff`.

```json
{
  "agent_status": {
    "agent_state": "COMPLETE",
    "agent_id": "ab7e4d2c9f10a3b5e",
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
      "type": "command",
      "command": "kubectl get hr -n qxo",
      "method": "listed every HelmRelease in the namespace and read its Ready and suspended columns",
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

```json
{
  "agent_status": {
    "agent_state": "BLOCKED",
    "agent_id": "ac3a1f906d2b4e871",
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

```json
{
  "agent_status": {
    "agent_state": "NEEDS_INPUT",
    "agent_id": "ad9f2b13c705e6a9f",
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

```json
{
  "agent_status": {
    "agent_state": "APPROVAL_REQUEST",
    "agent_id": "af1d9b72e4c806d13",
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

```json
{
  "agent_status": {
    "agent_state": "COMPLETE",
    "agent_id": "a2e8c1479b0d3f562",
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
      "type": "self_review",
      "reviewed": "publish.yml suffix parsing, read against the RC tag policy it implements",
      "method": "re-read the workflow by hand and matched its --tag branch to the -rc.N suffix; nothing was executed",
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

`verification.type` is `dry_run` here -- a word outside the names the validator knows. The vocabulary is open, so it is accepted as written, and it is priced exactly like a known type: it still owes at least one of `command`, `reviewed`, `requires_human`. Inventing a word never costs less than naming the evidence.

```json
{
  "agent_status": {
    "agent_state": "COMPLETE",
    "agent_id": "af4b2e805c19d7a34",
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
      "type": "dry_run",
      "command": "terragrunt plan -chdir=/abs/path",
      "method": "ran the plan against live state and read its change counts, then matched the kustomization sourceRef against the cluster name",
      "checks": ["terragrunt plan shows no changes", "kustomization references match cluster name"],
      "result": "pass",
      "details": "Plan: 0 to add, 0 to change, 0 to destroy. Kustomization sourceRef matches cluster dev-gke-01."
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

Agentic-loop agents carry a `loop_state` dict in the envelope. The runtime (`_check_loop_state_blocking` in `contract_validator.py`) blocks `COMPLETE` when `iteration < max_iterations AND metric < threshold` -- in that case another iteration is forced and the contract is rejected. When `metric >= threshold` (or iteration count is exhausted) the `COMPLETE` is accepted.

### 7a. Blocking case (metric below threshold, iteration remaining)

The runtime will reject this `COMPLETE` and force the agent to iterate again.

```json
{
  "agent_status": {
    "agent_state": "COMPLETE",
    "agent_id": "a19a3d76b28e40cf5",
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
      "type": "command",
      "command": "pytest tests/layer1_prompt_regression -q",
      "method": "ran the regression subset and read its summary line",
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

```json
{
  "agent_status": {
    "agent_state": "COMPLETE",
    "agent_id": "a4e8b21fd0356c7a9",
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
      "type": "command",
      "command": "pytest tests/layer1_prompt_regression -q",
      "method": "ran the regression subset and read its summary line",
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

```json
{
  "agent_status": {
    "agent_state": "COMPLETE",
    "agent_id": "a7c1d938e56420bf1",
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
      "type": "self_review",
      "reviewed": "port and namespace read from package.json and the manifest, and compared against each other",
      "method": "read both files directly; no command run and no live state queried",
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

## 9. NEEDS_VERIFICATION (producer hands off, does not self-complete)

Harness R2: the producer believes the increment is done and MAY propose `evidence_report.verification.result`, but this is a proposal, not a `COMPLETE` -- only a verifier-role agent transitions `NEEDS_VERIFICATION` to `COMPLETE` (a verifier rejecting it sends the increment back to `IN_PROGRESS`).

```json
{
  "agent_status": {
    "agent_state": "NEEDS_VERIFICATION",
    "agent_id": "a5f3c07a92d18b4e6",
    "pending_steps": ["verifier confirms HelmRelease reconciliation"],
    "next_action": "Hand off to verifier -- change believed complete, awaiting independent confirmation"
  },
  "evidence_report": {
    "patterns_checked": ["existing HelmRelease naming convention in flux/apps/"],
    "files_checked": ["flux/apps/qxo-api/helmrelease.yaml"],
    "commands_run": ["kubectl apply -f flux/apps/qxo-api/helmrelease.yaml -> configured"],
    "key_outputs": ["HelmRelease applied; reconciliation not yet independently confirmed"],
    "verbatim_outputs": [],
    "cross_layer_impacts": [],
    "open_gaps": ["Independent verifier confirmation of reconciled state"],
    "verification": {
      "type": "self_review",
      "reviewed": "the applied manifest diff, checked against the change the task asked for",
      "method": "read back the manifest after apply and diffed it against the intended change",
      "checks": ["kubectl apply exit code", "manifest diff matches intended change"],
      "result": "pass",
      "details": "Proposed by the producer, not a verifier -- offered for the verifier's reference, not a self-declared pass."
    }
  },
  "consolidation_report": null,
  "approval_request": null
}
```

## Notes on multi-command APPROVAL_REQUEST sweeps

**Per-command (default):** when T3 commands appear one at a time as the agent
works, each blocked command produces its own `APPROVAL_REQUEST` with an
`approval_id` (shape identical to example 4 above). Do not write `batch_scope`
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
