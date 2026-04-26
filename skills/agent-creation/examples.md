# Agent Creation -- Examples

Two real Gaia agents analyzed by component. The goal is not to show "correct" vs "incorrect" -- both agents work well -- but to explain *why* each section was written the way it was, so you can apply the same reasoning to a new agent.

---

## Example 1: `developer` (D1=yes, D2=no, D3=yes)

**Dimensions:**
- D1=yes: writes files, runs tests, commits to VCS
- D2=no: terminal node; CANNOT DO table is for orchestrator routing, not for the agent to dispatch
- D3=yes: enters automatic routing for application code requests

### Frontmatter

```yaml
---
name: developer
description: Full-stack software engineer for application code, CI/CD, and developer tooling across Node.js/TypeScript and Python stacks.
tools: Read, Edit, Write, Agent, Glob, Grep, Bash, Task, Skill, WebSearch, WebFetch
model: inherit
maxTurns: 50
permissionMode: acceptEdits
skills:
  - agent-protocol
  - security-tiers
  - investigation
  - command-execution
  - developer-patterns
  - context-updater
  - fast-queries
---
```

**Why `permissionMode: acceptEdits`:** D1=yes. Without this, every Edit/Write call inside `.claude/` would trigger a CC native permission prompt -- disruptive in headless sessions.

**Why `maxTurns: 50` (not 40):** Investigation + code change + test run + potential fix loop can easily consume 40 turns. 50 gives the agent room to complete a debugging cycle without getting cut off mid-execution.

**Why `Agent` in tools:** `developer` can spawn sub-investigations when the codebase is large enough to warrant parallel reads. Most specialists do not have this -- it adds surface area.

**Why `investigation` and `fast-queries`:** Application bugs often require root cause analysis before a fix is possible. `investigation` provides the methodology; `fast-queries` provides the triage scripts. A specialist that writes code without these would often produce code that fixes the symptom rather than the cause.

### Workflow

```markdown
## Workflow

1. **Triage first**: When diagnosing build, test, or runtime issues, run the fast-queries triage script before diving into code.
2. **Deep analysis**: When investigating complex bugs or architectural questions, follow the investigation phases.
3. **Update context**: Before completing, if you discovered new services, dependencies, or architecture patterns not in Project Context, emit a CONTEXT_UPDATE block.
```

**Why workflow appears before identity:** The developer's primary risk is diving into code without understanding the problem first. Workflow at the top of the body is the first thing the agent reads -- it front-loads the "investigate before fixing" discipline where it needs to be, before the agent's momentum carries it toward implementation.

### Identity

```markdown
## Identity

You are a full-stack software engineer. You build, debug, and improve application code, CI/CD pipelines, and developer tooling across Node.js/TypeScript and Python stacks.

**Your output is code or a report -- never both:**
- **Realization Package:** new or modified code files, validated (lint + tests + build)
- **Findings Report:** analysis and recommendations to stdout only -- never
  create standalone report files (.md, .txt, .json)
```

**Why the output type declaration matters:** Without it, the agent commonly produces a hybrid: modifies files *and* writes a summary report. That ambiguity makes it unclear to the orchestrator whether the task is complete or still in analysis. The "never both" rule forces a clean state at completion. The "never create standalone report files" clause is specific enough to prevent the most common manifestation of hybrid output.

**Weight test:** Remove the output type declaration. Does behavior change? Yes -- the agent would write summary files and return code changes in the same turn. The declaration passes the weight test.

### Scope boundaries

```markdown
During investigation, if you discover that a resource type is managed by Terraform,
Terragrunt, Helm, Flux, or any other IaC/GitOps tool, creating new instances of
that resource belongs to the agent that owns that tool -- even if you need the
resource as a prerequisite for your task.
```

**Why this paragraph exists:** The boundary "CANNOT DO: Terraform / cloud infrastructure → terraform-architect" is correct but weak. An agent working on a Node.js service that needs a database will rationalize "I just need one RDS instance, it's a prerequisite, I'll handle it." The paragraph names that exact decision point and explicitly forbids it. Without the paragraph, the boundary would be crossed at least some of the time.

---

## Example 2: `cloud-troubleshooter` (D1=no, D2=no, D3=yes)

**Dimensions:**
- D1=no: read-only enforced at frontmatter level via `disallowedTools`
- D2=no: never dispatches other agents; surfaces recommendations back to orchestrator
- D3=yes: enters automatic routing for cloud diagnostic requests

### Frontmatter

```yaml
---
name: cloud-troubleshooter
description: Diagnostic agent for cloud infrastructure (GCP and AWS). Compares intended state (IaC/GitOps) with actual state (live resources) to identify discrepancies.
tools: Read, Glob, Grep, Bash, Task, Skill
model: inherit
maxTurns: 40
disallowedTools: [Write, Edit, NotebookEdit]
skills:
  - agent-protocol
  - security-tiers
  - investigation
  - command-execution
  - context-updater
  - fast-queries
---
```

**Why `disallowedTools` instead of just not listing Write/Edit:** Two layers of enforcement. Not listing Write/Edit in `tools` is the first layer -- the agent nominally does not have those tools. But `disallowedTools` adds a second layer: even if a future edit accidentally re-adds Write to the tools list, the disallow overrides it. For a read-only diagnostic agent operating on live cloud state, this matters. An accidental write to a live cloud resource is a non-trivial incident.

**Why no `permissionMode`:** D1=no. `permissionMode: acceptEdits` would be misleading for an agent that must never write files.

**Why no `investigation` skill... wait, it does have it:** Read-only diagnostic agents need investigation methodology even without mutation capabilities. The `investigation` skill teaches how to diagnose -- tool-independent.

### Identity

```markdown
## Identity

You are a **discrepancy detector**. You find differences between what the code says
and what exists in the cloud. You operate in **strict read-only mode** -- T3 forbidden.

**Your output is always a Diagnostic Report:**
- Intended vs actual state, categorized by severity
- Root cause candidates
- Recommendations (you suggest, you never act):
  - **Option A:** Sync code to live → invoke `terraform-architect` or `gitops-operator`
  - **Option B:** Sync live to code → invoke `terraform-architect` or `gitops-operator`
  - **Option C:** Further investigation needed
```

**Why "discrepancy detector" and not "cloud infrastructure specialist":** The more generic framing lets the agent drift toward fixing things. "Discrepancy detector" constrains the action space: the agent finds differences, it does not resolve them. The constraint is load-bearing -- remove it and the agent would occasionally attempt fixes when it detects an obvious misconfiguration.

**Why the output options are named A/B/C:** The orchestrator reads this output and decides what to dispatch next. Consistent option labels (A/B/C) give the orchestrator a deterministic signal to act on. Without them, the recommendations would be prose that the orchestrator has to interpret differently each time.

**Weight test:** Remove the "you never act" constraint from the output section. Would behavior change? Yes -- the agent would attempt to apply fixes directly (invoking `terraform-architect` from within itself, or in extreme cases, running mutative commands). The constraint passes.

### Domain-specific section

```markdown
## Cloud Provider Detection

Detect which CLI to use from project-context:

| Indicator | Provider | CLI |
|-----------|----------|-----|
| `gcloud`, `gsutil`, `GKE`, `Cloud SQL` | GCP | `gcloud` |
| `aws`, `eksctl`, `EKS`, `RDS`, `EC2` | AWS | `aws` |

If unclear, ask before proceeding.
```

**Why this is inline rather than in a skill:** This logic only applies to `cloud-troubleshooter`. A terraform-architect does not need CLI detection -- it works from HCL files and always uses terragrunt. If the same detection logic applied to two or more agents, it would warrant a skill. Single-agent-only logic stays inline.

---

## Pattern Summary

| Decision | `developer` | `cloud-troubleshooter` | Rule |
|---|---|---|---|
| D1 | yes | no | Determines permissionMode and disallowedTools |
| D2 | no | no | Both are terminal nodes |
| D3 | yes | yes | Both need description as triggering conditions |
| Output type | Realization Package or Findings Report | Diagnostic Report | Named explicitly in identity |
| Workflow position | Before identity | Before identity | Both have complex sequences -- workflow first |
| Boundary precision | Named decision point ("even if you need it as prerequisite") | Named action prohibition ("you never act") | Generic categories are weaker than named moments |
| Domain logic inline | No (developer-patterns handles it) | Yes (cloud provider detection) | Inline only when single-agent-specific |
