# Agent Creation -- Examples

Two real Gaia agents analyzed by component. The goal is not "correct vs incorrect" -- both work well -- but to show *why* each section was written the way it was, so the same reasoning transfers to a new agent. The first example is a builder, where identity is mostly shared essence and the design work is the contract + subset. The second is the read-only-into-prod case, where a hard tool constraint earns its keep.

---

## Example 1: `developer` -- a builder (D0 contract-first, D1=yes, D2=no, D3=yes)

**Dimensions:**
- D0: `read` = the slices an app engineer reasons over; `write` = `application_services`, the contract this domain owns
- D1=yes: writes files, runs tests, commits to VCS
- D2=no: terminal node; CANNOT DO table is for orchestrator routing, not for the agent to dispatch
- D3=yes: enters automatic routing for application-code requests

### The contract comes first

```yaml
project_context_contracts:
  read: [project_identity, stack, application_services, environment, architecture_overview, git]
  write: [application_services]
```

**Why this is the first decision, not the frontmatter or the identity:** the `read` list is the token lever. `developer` is injected exactly the slices an application engineer reasons over -- stack, the service inventory, environment, the architecture overview, git -- and nothing about cluster internals or IaC topology, because reading those would tax every call without informing a single code decision. The `write` list is the security lever: `write: [application_services]` is the *only* contract the runtime will let `developer` persist via an `update_contracts` clause. If `developer` discovers a new service it can index it; if it tried to write `infrastructure`, the runtime rejects it. Decide both lists before writing anything else -- the identity and skills derive from "this agent reasons over application code and owns the service inventory," which is exactly what the contract says.

### Frontmatter

```yaml
---
name: developer
description: Use when writing, modifying, debugging, or reviewing application code, CI/CD pipelines, or developer tooling — or when investigating an application-layer bug.
tools: Read, Edit, Write, Glob, Grep, Bash, Skill, WebSearch, WebFetch
model: inherit
maxTurns: 50
permissionMode: acceptEdits
skills:
  - agent-protocol
  - security-tiers
  - investigation
  - command-execution
  - git-conventions
---
```

**Why `permissionMode: acceptEdits`:** D1=yes. Without it, every Edit/Write would trigger a native permission prompt -- disruptive in headless sessions.

**Why no hard `disallowedTools`:** `developer` is a builder, governed softly by T3 consent, not by a tool denylist. It may need Write/Edit/Bash across its whole domain; fencing it with a hard denylist would block legitimate work. Compare `cloud-troubleshooter` below, where the read-only-into-prod constraint *is* the reason a hard denylist exists. The builder's safety lives in T3 plus the `write` contract, not in `disallowedTools`.

**Why `maxTurns: 50`:** investigation + code change + test run + a fix loop easily consumes 40 turns. 50 gives room to finish a debugging cycle without being cut off mid-execution.

**Why no `Agent`/`Task`:** `developer` runs as a subagent and a subagent cannot spawn subagents -- those tools are inert in its frontmatter (per Anthropic's subagents doc). When it needs another surface, it surfaces a CANNOT DO item and the orchestrator dispatches.

**Why these skills, and no `*-patterns` skill in this list:** the base discipline -- response contract, tier classification, command execution, investigation -- is the *shared* spine every builder loads, anchored by `agent-protocol` and `security-tiers`. Domain conventions that are genuinely per-agent are small; whether they live in a dedicated skill or inline, they are the *subset*, not the bulk of the agent. The point of the example is that creating this agent was contract + skills + subset, not authoring a personality.

### Identity -- shared essence + a small subset

`developer` is a builder, so most of its identity is the essence every builder carries, written the same way each time:

- **defer to what exists** over a clean-slate design (match the codebase's patterns before introducing new ones)
- **verify the outcome**, not the exit code (lint + tests + build actually pass, the change does what it should)
- **Realization Package XOR Findings Report -- never a hybrid** (code changes, or analysis to stdout, not both in one turn)
- **disciplined citizen** (flag what is out of lane, do not edit across boundaries; propose, do not persist beyond the contract)
- **capability free under T3** (it may run what its task requires; the mutations are gated by consent, not a fixed toolbox)

The only genuinely per-`developer` part is the **subset**: *what it builds* -- application code, CI/CD pipelines, developer tooling across Node.js/TypeScript and Python -- and *which neighbors own the adjacent surfaces* -- IaC to `platform-architect`, Kubernetes desired-state to `gitops-operator`, live diagnosis to `cloud-troubleshooter`.

```markdown
## Identity

You are a full-stack software engineer. You build, debug, and improve application
code, CI/CD pipelines, and developer tooling across Node.js/TypeScript and Python.
You defer to the patterns already in the codebase; your work is not done until the
change is real and verified (lint + tests + build), not merely written.

**Your output is a Realization Package XOR a Findings Report -- never both:**
- **Realization Package:** new or modified code, validated (lint + tests + build)
- **Findings Report:** analysis and recommendations to stdout -- never standalone
  report files (.md, .txt, .json)
```

**Why the shared essence carries weight by being consistent, not novel:** the essence's job is to be present and identical across builders so the fleet behaves coherently. Its weight is in consistency, not originality.

**Weight test on the subset:** remove "application code / CI/CD / Node + Python" and the neighbor handoffs, and `developer` would behave like a generic builder -- attempting IaC or cluster changes that belong to `platform-architect` or `gitops-operator`. The subset narrows the action space; it passes.

### Scope boundary -- name the decision point

```markdown
During investigation, if you discover that a resource type is managed by Terraform,
Terragrunt, Pulumi, Helm, Flux, or any other IaC/GitOps tool, creating new instances
of that resource belongs to the agent that owns that tool -- even if you need the
resource as a prerequisite for your task.
```

**Why this paragraph exists:** "CANNOT DO: cloud infrastructure → platform-architect" is correct but weak. An agent on a Node.js service that needs a database rationalizes "I just need one instance, it's a prerequisite." The paragraph names that exact moment and forbids it -- the disciplined-citizen essence made concrete at the boundary a builder is most tempted to cross.

---

## Example 2: `cloud-troubleshooter` -- read-only-into-prod (D1=no, D2=no, D3=yes)

**Dimensions:**
- D0: `read` = the infrastructure/gitops slices it compares against; `write` = the single observation contract it curates
- D1=no: read-only, enforced at the frontmatter level by a hard denylist
- D2=no: never dispatches; surfaces recommendations back to the orchestrator
- D3=yes: enters automatic routing for live-state diagnosis

### Frontmatter

```yaml
---
name: cloud-troubleshooter
description: Use when inspecting, diagnosing, or validating the actual state of running systems — pods, services, logs, cloud resources — or comparing live state against IaC/desired-state (drift).
tools: Read, Glob, Grep, Bash, Skill, WebSearch, WebFetch
model: inherit
maxTurns: 40
disallowedTools: [Write, Edit, NotebookEdit]
skills:
  - agent-protocol
  - security-tiers
  - command-execution
  - investigation
  - fast-queries
---
```

**Why `disallowedTools` here and nowhere among the builders:** this is *the* case a hard tool constraint is reserved for -- read-only into production. Not listing Write/Edit is the first layer; the hard denylist is the second, overriding even a future edit that accidentally re-adds Write. For an agent operating on live cloud state, that second layer matters because an accidental write to a live resource is a real incident. The builders do not get this treatment: their mutations are legitimate and governed by T3 consent, so a hard denylist would only block real work. The distinction is the point -- hard constraints fence the agent that must never write; soft T3 governs the agent that writes deliberately.

**Why no `permissionMode`:** D1=no. `acceptEdits` would mislead for an agent that must never write files.

### Identity -- the constraint is the differentiator

Where a builder's identity is shared essence + subset, a read-only agent's identity is the *constraint* that fences it:

```markdown
## Identity

You are a **discrepancy detector**. You find differences between what the code says
and what exists in the live system. You operate in **strict read-only mode** -- you
never mutate, you never apply a fix.

**Your output is always a Diagnostic Report:**
- Intended vs actual state, categorized by severity
- Root-cause candidates
- Recommendations (you suggest, you never act):
  - **Option A:** Sync code to live → invoke `platform-architect` or `gitops-operator`
  - **Option B:** Sync live to code → invoke `platform-architect` or `gitops-operator`
  - **Option C:** Further investigation needed
```

**Why "discrepancy detector" and not "cloud infrastructure specialist":** the generic framing lets the agent drift toward fixing things. "Discrepancy detector" constrains the action space to finding differences. **Weight test:** remove "you never act" and the agent would attempt fixes when it spots an obvious misconfiguration. The constraint passes.

### Domain-specific section -- inline because single-agent

```markdown
## Cloud Provider Detection

| Indicator | Provider | CLI |
|-----------|----------|-----|
| `gcloud`, `gsutil`, `GKE`, `Cloud SQL` | GCP | `gcloud` |
| `aws`, `eksctl`, `EKS`, `RDS`, `EC2` | AWS | `aws` |

If unclear, ask before proceeding.
```

**Why inline rather than a skill:** this logic applies only to `cloud-troubleshooter`. `platform-architect` works from declarative config and never needs CLI detection. Single-agent-only logic stays inline; extract to a skill only when a second agent needs it.

---

## Pattern Summary

| Decision | `developer` (builder) | `cloud-troubleshooter` (read-only-into-prod) | Rule |
|---|---|---|---|
| Contract (D0) | read app slices; write `application_services` | read infra/gitops slices; write one observation contract | First decision -- sets token cost and write safety |
| Hard `disallowedTools` | none (soft T3 governs) | `[Write, Edit, NotebookEdit]` | Hard denylist reserved for read-only-into-prod |
| Identity | shared builder essence + small subset | the read-only constraint | Builders share essence; differentiation is contract + subset |
| D1 | yes | no | Determines permissionMode and the failure model |
| Output type | Realization Package XOR Findings | Diagnostic Report | Named explicitly in identity |
| Boundary precision | named decision point ("even if you need it as prerequisite") | named action prohibition ("you never act") | Generic categories are weaker than named moments |
| Domain logic inline | no (lives in shared skills + subset) | yes (cloud provider detection) | Inline only when single-agent-specific |
