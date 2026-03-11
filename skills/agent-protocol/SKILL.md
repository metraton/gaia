---
name: agent-protocol
description: Use when starting any agent task, reporting status, or handling errors — defines json:contract format, state machine, and search protocol
user-invocable: false
---

# Agent Protocol

## Instantiation Model

You are instantiated with:
- **Identity** — your agent `.md`: who you are, domain, scope, output format
- **Skills** — injected procedural knowledge (the HOW)
- **Contracts** — project-context sections relevant to your domain: your trusted baseline
- **Request** — what the orchestrator needs you to do

Trust your contracts. Use them as search anchors — not documentation to read linearly.
When investigation reveals reality differs from contracts → emit `CONTEXT_UPDATE`.
If Project Context includes `surface_routing`, `investigation_brief`, or `context_update_contract`, treat them as orchestration guidance:
- `surface_routing` tells you which surfaces appear active
- `investigation_brief` tells you your role, ownership, and expected cross-surface evidence
- `context_update_contract` tells you which sections you may read and write

These are assignment constraints, not a closed checklist. They tell you what you
own and what you must account for. They do not supplant open investigation.

## Search Protocol

```
CONTRACTS → LOCAL → LIVE → REPORT
```

**CONTRACTS** — Go here first. If you have injected project-context, use its values as
search anchors: known path? target it. Known name/ID? search for it.
If no project-context was injected, proceed directly to LOCAL.
Broad globs are last resort.
Before running LIVE commands, check if contracts indicate resource accessibility
(e.g., `kubectl_accessible`, endpoint reachability). If a resource is marked
unreachable, skip live commands for it and note the limitation in your report.

**LOCAL** — Read code and config files. Compare against contracts.
- Match → confirmed, proceed.
- Mismatch → drift detected. Note for `CONTEXT_UPDATE`. May require LIVE verification.

**LIVE** — Only if drift is suspected or task explicitly requires live state.
If you have the `fast-queries` skill, run triage first (<15s). Otherwise, use
domain-appropriate read-only commands.
If your identity restricts you to local/code-only operations, skip LIVE entirely
— report what you found in LOCAL.
Mismatch found → update your map, continue investigating with the new reality.
Only escalate (`BLOCKED`/`NEEDS_INPUT`) if the mismatch is CRITICAL to completing the task.

**REPORT** — Every response ends with a `json:contract` block. When you investigated, validated, reviewed, or gathered live/local evidence, include the `evidence` object in that block.
Use `output-format` for the human-facing summary around the contract block; this skill
remains the authority for the block schema itself.

For investigation methodology and pattern hierarchy, follow the `investigation` skill.

## Unified Contract Block (MANDATORY)

Every response MUST end with a single `json:contract` fenced block. This block unifies evidence, consolidation, and status into one structured object.

The `evidence` object is mandatory whenever your response is based on code reading, config reading, command execution, review findings, or other concrete evidence. This applies to most `INVESTIGATING`, `PLANNING`, `BLOCKED`, `NEEDS_INPUT`, and evidence-backed `COMPLETE` responses. It is also required for `PENDING_APPROVAL` and `FIXING`, because those states still need to show the evidence that justified the plan or the fix attempt.

The `consolidation` object is mandatory when `investigation_brief.consolidation_required` is `true`. It exists so Gaia can consolidate multiple agent responses without guessing. Set it to `null` when not required.

If an evidence field does not apply, use an empty array `[]` or `"not run"` instead of omitting the key.

### Full Schema

```json:contract
{
  "plan_status": "COMPLETE",
  "agent_id": "a1f2c3",
  "pending_steps": [],
  "next_action": "done",
  "evidence": {
    "patterns_checked": ["existing pattern or convention you compared against"],
    "files_checked": ["file or path"],
    "commands_run": ["exact command -> concise result"],
    "key_outputs": ["short output excerpt or evidence summary"],
    "verbatim_outputs": ["command:\n```\nliteral output\n```"],
    "cross_layer_impacts": ["affected adjacent surface, contract, or subsystem"],
    "open_gaps": ["remaining unknown, validation gap"]
  },
  "consolidation": {
    "ownership_assessment": "owned_here|cross_surface_dependency|not_my_surface",
    "confirmed_findings": ["facts confirmed by code, config, commands, or docs"],
    "suspected_findings": ["plausible but not yet confirmed findings"],
    "conflicts": ["contradiction with prior findings"],
    "open_gaps": ["remaining unknowns, validation gaps"],
    "next_best_agent": "agent name to continue, or null"
  }
}
```

### Evidence Rules

- Keep each field to 1-3 items unless the task genuinely needs more.
- `commands_run` must contain the exact command when a command was executed.
- `key_outputs` must summarize what mattered, not paste walls of output.
- `verbatim_outputs` must contain the literal command output when it is evidence for your conclusion. Truncate outputs longer than ~100 lines with `[truncated]`. Use `[]` when no commands were executed.
- `cross_layer_impacts` is required for review, debugging, or multi-surface tasks.
- `open_gaps` must be explicit. Do not imply certainty when uncertainty remains.

### Consolidation Rules

- `ownership_assessment` is required and must use one of: `owned_here`, `cross_surface_dependency`, `not_my_surface`.
- `confirmed_findings` should contain only evidence-backed facts.
- `suspected_findings` is where hypotheses belong. Do not hide uncertainty.
- `conflicts` must explicitly call out disagreements with prior agent findings when they exist.
- `next_best_agent` must name the next owner if the fix or validation clearly belongs elsewhere. Use `null` if none.
- Reuse `open_gaps` consistently with the one in `evidence`; they can overlap, but they must not contradict.

### Status-Specific Examples

**COMPLETE** (task finished, evidence-backed):
```json:contract
{
  "plan_status": "COMPLETE",
  "agent_id": "b7e4d2",
  "pending_steps": [],
  "next_action": "done",
  "evidence": {
    "patterns_checked": ["existing HelmRelease naming convention in flux/apps/"],
    "files_checked": ["flux/apps/qxo-api/helmrelease.yaml"],
    "commands_run": ["kubectl get hr -n qxo -> all reconciled"],
    "key_outputs": ["All 12 HelmReleases healthy, no drift detected"],
    "verbatim_outputs": [],
    "cross_layer_impacts": [],
    "open_gaps": []
  },
  "consolidation": null
}
```

**BLOCKED** (cannot proceed):
```json:contract
{
  "plan_status": "BLOCKED",
  "agent_id": "c3a1f9",
  "pending_steps": ["validate IAM binding", "apply terraform change"],
  "next_action": "User must grant roles/container.admin to SA",
  "evidence": {
    "patterns_checked": ["SA binding pattern in terraform/iam/"],
    "files_checked": ["terraform/iam/main.tf", "terraform/iam/variables.tf"],
    "commands_run": ["gcloud iam service-accounts get-iam-policy sa@proj.iam -> missing binding"],
    "key_outputs": ["SA lacks roles/container.admin required for node pool ops"],
    "verbatim_outputs": ["gcloud iam service-accounts get-iam-policy sa@proj.iam:\n```\nbindings: []\n```"],
    "cross_layer_impacts": ["GKE node pool scaling depends on this SA"],
    "open_gaps": ["Whether SA should get role directly or via workload identity"]
  },
  "consolidation": null
}
```

**NEEDS_INPUT** (missing information):
```json:contract
{
  "plan_status": "NEEDS_INPUT",
  "agent_id": "d9f2b1",
  "pending_steps": ["create namespace manifest", "configure HelmRelease"],
  "next_action": "User must choose: Option A (shared namespace) or Option B (dedicated namespace)",
  "evidence": {
    "patterns_checked": ["namespace conventions in flux/clusters/"],
    "files_checked": ["flux/clusters/dev/namespaces/"],
    "commands_run": [],
    "key_outputs": ["Both patterns exist in codebase -- no single convention"],
    "verbatim_outputs": [],
    "cross_layer_impacts": ["Network policies differ per pattern"],
    "open_gaps": ["User preference for namespace isolation"]
  },
  "consolidation": null
}
```

**PENDING_APPROVAL** (T3 plan ready):
```json:contract
{
  "plan_status": "PENDING_APPROVAL",
  "agent_id": "e5c8a3",
  "pending_steps": ["execute terraform apply", "verify state"],
  "next_action": "Awaiting user approval for terraform apply",
  "evidence": {
    "patterns_checked": ["existing bucket naming in terraform/gcs/"],
    "files_checked": ["terraform/gcs/main.tf", "terraform/gcs/variables.tf"],
    "commands_run": ["terraform plan -out=tfplan -> 1 to add, 0 to change, 0 to destroy"],
    "key_outputs": ["Plan adds 1 GCS bucket with standard config"],
    "verbatim_outputs": ["terraform plan:\n```\n+ google_storage_bucket.events\n  name: qxo-events-dev\n  location: us-east4\n```"],
    "cross_layer_impacts": ["Flux ExternalSecret must reference new bucket"],
    "open_gaps": []
  },
  "consolidation": null
}
```

### Valid States

| Status | Meaning |
|--------|---------|
| `INVESTIGATING` | Gathering information and evidence |
| `PLANNING` | Creating and validating the execution plan |
| `PENDING_APPROVAL` | T3 plan ready, awaiting user approval |
| `APPROVED_EXECUTING` | Running approved T3 actions |
| `FIXING` | Applying fixes after failed verification (max 2 cycles) |
| `COMPLETE` | Task finished, verification criteria passed |
| `BLOCKED` | Cannot proceed — escalated to user |
| `NEEDS_INPUT` | Missing information from user |

### State Flow

```
INVESTIGATING -> PLANNING -> PENDING_APPROVAL -> APPROVED_EXECUTING -> COMPLETE  (T3)
INVESTIGATING -> COMPLETE                                                        (T0/T1/T2)
APPROVED_EXECUTING -> FIXING (recoverable failure, max 2 cycles)
FIXING -> APPROVED_EXECUTING (retry after fix)
FIXING -> BLOCKED (after 2 cycles or non-recoverable error)
INVESTIGATING -> BLOCKED
INVESTIGATING -> NEEDS_INPUT
PLANNING -> NEEDS_INPUT
PENDING_APPROVAL -> PLANNING (user requests modifications)
```

## T3 Operations

**T3 only.** If your identity restricts you to read-only operations (T0/T1/T2),
skip this section entirely -- investigation leads directly to `COMPLETE`.

For T3 operations, read `.claude/skills/approval/SKILL.md` and follow the workflow there.
Post-approval execution protocol is in `.claude/skills/execution/SKILL.md`.

## Git Workflow

If you are preparing a git commit or PR-ready change summary, read
`.claude/skills/git-conventions/SKILL.md`.
Treat it as an on-demand workflow skill: load it when git realization is part of
the task, not as a substitution for your normal investigation or reporting flow.

## Self-Review Gate

Before setting `plan_status` to `COMPLETE`, verify your own output:

1. **Re-read the original request.** Does your output answer what was asked?
2. **Check completeness.** Are all requested items addressed? Any silent omissions?
3. **Validate accuracy.** Do file paths, resource names, and commands match what you found in code?
4. **Verify format.** Does your output follow the `output-format` skill? Is the `json:contract` block present and correct?

If any check fails, fix before emitting COMPLETE. Do not flag self-review to the user -- just do it.

## Contract Repair

If runtime resumes you with instructions to repair your previous response contract, treat that as a structural fix request:

- Reissue a complete response with the required `json:contract` block (including `evidence` and optional `consolidation`) and any optional `CONTEXT_UPDATE`
- Do not rerun the full investigation unless missing evidence truly requires one more command or file read
- Preserve the task's real status; contract repair is not a license to fabricate evidence or mark work complete prematurely

Runtime auto-repair retries are capped at 2. If your response is still structurally invalid after that, the orchestrator will escalate instead of silently retrying forever.

## Agent Handoff

When receiving context from another agent (team workflow): consume prior findings directly — no re-investigation of confirmed facts. If findings are incomplete or contradictory, investigate only the gap. Emit independent `json:contract` block.

## Error Handling

| Type | Action | Status |
|------|--------|--------|
| Recoverable | Fix and retry (FIXING state, max 2 cycles) | `FIXING` |
| Blocker | Log details, list solutions | `BLOCKED` |
| Ambiguous | List options (A, B, C) | `NEEDS_INPUT` |
