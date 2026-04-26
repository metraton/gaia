# Agent Creation -- Reference

Detailed template, dimension guidance, and weight test per component. Read on-demand when drafting or reviewing an agent definition.

---

## Specialist Agent Template

The canonical structure for a Gaia specialist agent. Sections marked [REQUIRED] appear in every specialist. Sections marked [CONDITIONAL] appear when the corresponding dimension applies.

```markdown
---
name: <kebab-case-name>
description: <triggering conditions -- when the orchestrator should dispatch this agent>
tools: Read, Edit, Write, Glob, Grep, Bash, Task, Skill  # restrict to what the domain actually needs
model: inherit
maxTurns: 40                          # omit only for short-lived agents
permissionMode: acceptEdits           # required if D1=yes (agent mutates state)
disallowedTools: [Write, Edit, NotebookEdit]  # include if D1=no and enforcement matters
color: <optional -- hex or color name>
skills:
  - agent-protocol                    # always first
  - security-tiers                    # always second
  - command-execution                 # if agent runs Bash commands
  - investigation                     # if agent diagnoses complex state
  - <domain-skill>                    # agent's domain patterns
  - context-updater                   # if agent may discover new system state
  - fast-queries                      # if agent diagnoses cloud/system health
---

## Workflow                            [REQUIRED if domain has a non-obvious sequence]

1. **Step name**: Brief rationale for why this step comes first.
2. **Step name**: What to do and what to look at.
3. **Step name**: When to surface for approval vs proceed.

## Identity                            [REQUIRED]

You are a <role with stakes>. You <what you uniquely see or are constrained to do>.

**Your output is always a <Realization Package | Diagnostic Report | Findings Report>:**
- What the output contains
- What it never contains (hybrid outputs drift)

## <Domain-Specific Section>          [CONDITIONAL -- only if lookup logic is agent-specific]

Tables, decision trees, or classification logic that only applies to this agent.
If the same logic would help another agent, extract it to a skill instead.

## Scope                              [REQUIRED]

### CAN DO
- Concrete capability 1
- Concrete capability 2

### CANNOT DO -> DELEGATE             [REQUIRED]

| Need | Agent |
|------|-------|
| <boundary description that names the decision point> | `<agent-name>` |

## Domain Errors                      [REQUIRED]

| Error | Action |
|-------|--------|
| <specific error or condition> | <concrete action, not "report the error"> |

## Surface Signals (proposed)         [CONDITIONAL -- if D3=yes, remove after gaia-system applies]

```json
{
  "intent": "<what this agent handles>",
  "primary_agent": "<agent-name>",
  "signals": {
    "high_confidence": ["keyword1", "keyword2"],
    "medium_confidence": ["keyword3", "keyword4"]
  }
}
```
```

---

## The 3 Bifurcating Dimensions -- Detailed

### D1: Does the agent mutate system state?

"Mutate" means writing files, running commands that change resource state, or committing to VCS. The distinction matters because:

- Mutation requires T3 approval flow. An agent that can write but has no T3 handling in its failure model will either block silently or execute without user awareness.
- Read-only agents gain security value from `disallowedTools`, not just from not listing Write/Edit. A later frontmatter edit that adds a tool to a read-only agent should be caught by explicit disallow, not by convention.

**D1=yes implications:**
- `permissionMode: acceptEdits` in frontmatter
- `Write`, `Edit` listed in tools
- Failure handling covers T3 block + APPROVAL_REQUEST flow
- Output type is "Realization Package" (contains code/config changes)

**D1=no implications:**
- `disallowedTools: [Write, Edit, NotebookEdit]`
- No T3 surface in failure handling
- Output type is "Diagnostic Report" or "Findings Report"

### D2: Does the agent delegate to other agents?

Specialist agents are almost always terminal. "Delegation" here means the agent dispatches other agents (uses the Agent tool mid-task). Most specialists do not -- they surface CANNOT DO items back to the orchestrator, which handles routing.

**D2=yes implications (rare):**
- `Agent` in tools list
- A delegation table in the body describing which agents it dispatches and under what conditions

**D2=no implications (standard):**
- No `Agent` in tools (or `Agent` explicitly disallowed)
- CANNOT DO -> DELEGATE table is for the orchestrator's reference, not for the agent to act on directly

### D3: Does the agent enter the orchestrator's automatic routing?

Almost all specialists do. The exception would be a utility agent that is only dispatched explicitly, never via intent routing.

**D3=yes implications:**
- Description field written as triggering conditions (see Step 5 in SKILL.md)
- Surface signals proposed for `surface-routing.json`
- Description must not overlap with signals of existing agents (check `config/surface-routing.json` before finalizing)

---

## Weight Test per Component

Use this checklist when reviewing a drafted agent. For each component, the test question is: if this section were removed, would the agent behave differently in a realistic scenario?

### Identity weight test

| What you wrote | Does it pass? | Why |
|---|---|---|
| "You are a specialist in Terraform." | No | Baseline -- the LLM already knows what Terraform specialists do. |
| "You are a senior Terraform architect. You manage the lifecycle by working primarily with the declarative config in the repository. You never query live cloud resources directly." | Yes | The constraint ("never query live cloud") and the orientation ("declarative config, not live state") are not the LLM's default. They redirect behavior. |

**Fix:** Add the constraint or orientation that distinguishes this agent from a generic expert. The identity earns its place by narrowing the action space.

### Scope weight test

| What you wrote | Does it pass? | Why |
|---|---|---|
| "CANNOT DO: cloud infrastructure → terraform-architect" | Weak | Too broad. The agent will still touch cloud config when it seems "close enough." |
| "If the resource type is managed by IaC, creating it belongs to terraform-architect even if you need it as a prerequisite for your task." | Yes | Names the decision moment. Agent knows to stop at "I need this resource created" rather than proceeding. |

**Fix:** Identify the specific moment the agent would rationalize crossing the boundary, and make that moment explicit.

### Domain Errors weight test

| What you wrote | Does it pass? | Why |
|---|---|---|
| `terraform init fails → Check credentials` | Marginal | "Check credentials" is the default. Passes only if the agent would normally do something worse. |
| `Plan shows unexpected destroys → HALT -- report, require explicit confirmation` | Yes | "HALT" is not the default. The agent would normally continue to apply. |

**Fix:** For each error row, ask what a naive agent would do. If the row's action is identical to the default, it adds no weight.

### Output type weight test

An output type declaration passes if it excludes a hybrid the agent would otherwise produce. "Your output is code or a report -- never both" prevents the agent from writing files *and* returning a summary in the same turn -- a pattern that creates ambiguity about what was done. If the output type only names what the agent always produces anyway, it is decorative.

---

## Frontmatter Field Reference

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `name` | string | Yes | kebab-case; matches file name |
| `description` | string | Yes | Triggering conditions only -- what makes the orchestrator dispatch this agent |
| `tools` | list | Yes | Only what the domain actually uses; omitting is better than bloating |
| `model` | string | Yes | `inherit` for most; `sonnet` if the agent needs a specific model pinned |
| `permissionMode` | string | D1=yes | `acceptEdits` -- required for agents that write files |
| `disallowedTools` | list | D1=no | Include when read-only enforcement matters beyond convention |
| `maxTurns` | int | Recommended | 40 for most specialists; 50+ for agents with complex investigation phases |
| `skills` | list | Yes | `agent-protocol` always first, `security-tiers` always second |
| `color` | string | Optional | Hex or color name for visual routing distinction |

---

## Systemic Files the Agent Creation Touches

This skill guides thinking about each file, but gaia-system (the invoking agent) applies the writes.

| File | What changes | Who writes it |
|------|--------------|---------------|
| `.claude/agents/<name>.md` | New agent definition | gaia-system |
| `config/surface-routing.json` | New surface entry with signals | gaia-system |
| `.claude/skills/README.md` | Agent assignment matrix (if agent gets new skills) | gaia-system |
| `agents/README.md` | New agent in the roster | gaia-system |

The skill does not modify any of these files directly.
