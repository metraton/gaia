# Agent Creation -- Reference

Detailed template, dimension guidance, and weight test per component. Read on-demand when drafting or reviewing an agent definition.

---

## Specialist Agent Template

The canonical structure for a Gaia specialist agent. Sections marked [REQUIRED] appear in every specialist. Sections marked [CONDITIONAL] appear when the corresponding dimension applies.

```markdown
---
name: <kebab-case-name>
description: <triggering conditions -- when the orchestrator should dispatch this agent>
# DECIDE THIS BLOCK FIRST (D0): the read list is the token lever, the write list is the security lever
project_context_contracts:            # per-agent project-context access; write list gates update_contracts
  read: [project_identity, stack]     # only the slices this domain actually reasons over -- each extra slice taxes every call
  write: []                           # only the contracts this domain owns; absence here means the runtime rejects the write
tools: Read, Edit, Write, Glob, Grep, Bash, Skill  # restrict to what the domain actually needs
model: inherit
maxTurns: 40                          # omit only for short-lived agents
effort: high                          # optional -- raises reasoning effort for complex agents
permissionMode: acceptEdits           # required if D1=yes (agent mutates state)
disallowedTools: [Write, Edit, NotebookEdit]  # ONLY for read-only-into-prod; builders carry no hard denylist (at most [NotebookEdit])
skills:
  - agent-protocol                    # always first
  - security-tiers                    # always second
  - command-execution                 # if agent runs Bash commands
  - investigation                     # if agent diagnoses complex state
  - <domain-skill>                    # agent's domain patterns
  - agent-contract-handoff            # if agent may discover new system state to persist
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

## The Bifurcating Dimensions -- Detailed

### D0 (decide first): What is the contract?

The `project_context_contracts` block is the first design decision because it sets two things nothing else can recover later: token cost and write safety.

- **`read` is the token lever.** It filters the project-context injection down to the slices this domain reasons over. Every slice in the list is injected on every call, read or not -- so a `read` list bloated with slices the agent never consults is a tax paid on every turn. Scope it to what the domain actually consults.
- **`write` is the security lever.** It is the allowlist the runtime checks before accepting any `update_contracts` clause in the agent's `agent_contract_handoff` envelope (see `agent-contract-handoff`). A contract absent from `write` cannot be persisted, regardless of what the agent emits. Scope it to the contracts the domain *owns* -- `developer` owns `application_services`, `platform-architect` owns `infrastructure`/`infrastructure_topology`, a read-only diagnostic agent owns nothing or only the single observation contract it curates.

The identity, the tool set, and the skills all derive from the contract: "this agent reasons over X and owns Y" is what the `read`/`write` lists already say. Decide the contract, then write the rest to match it.

### D1: Does the agent mutate system state?

"Mutate" means writing files, running commands that change resource state, or committing to VCS.

- Mutation requires the T3 approval flow. An agent that can write but has no T3 handling in its failure model will either block silently or execute without user awareness.
- **Hard `disallowedTools` is reserved for the read-only-into-prod case.** The `[Write, Edit, NotebookEdit]` denylist exists to make an agent that inspects live production state *incapable* of mutating it -- `cloud-troubleshooter` is the canonical instance. It is applied before `tools` is resolved, so it overrides even a future edit that accidentally re-adds Write. That second layer is worth its weight only when an accidental write is a real incident, i.e. against live cloud state.
- **Builders are governed softly by T3, not by a hard denylist.** A builder (`developer`, `platform-architect`, `gitops-operator`) may legitimately need Write/Edit/Bash across its whole domain; fencing it with a hard denylist would block real work. Its safety lives in T3 consent plus the `write` contract. At most a builder denies `[NotebookEdit]` -- the one surface no Gaia builder uses -- and that is a tidiness choice, not a security boundary. Do not reach for `disallowedTools` to "lock down" a builder.

**D1=yes (builder) implications:**
- `permissionMode: acceptEdits` in frontmatter
- `Write`, `Edit` listed in tools
- no hard `disallowedTools` (at most `[NotebookEdit]`); governance is T3 + the `write` contract
- Failure handling covers the T3 block + APPROVAL_REQUEST flow
- Output type is "Realization Package XOR Findings Report -- never a hybrid"

**D1=no (read-only-into-prod) implications:**
- `disallowedTools: [Write, Edit, NotebookEdit]` -- this is the case the hard denylist exists for
- No `permissionMode` (it would mislead for an agent that must never write)
- No T3 surface in failure handling
- Output type is "Diagnostic Report" or "Findings Report"

### D2: Does the agent delegate to other agents?

Specialist agents are terminal -- and the runtime forces this, not just convention. A Gaia specialist runs *as a subagent* under the orchestrator, and a subagent cannot spawn other subagents. The `Agent`/`Task` tools are inert in a subagent's frontmatter even when listed (per Anthropic's subagents doc), so a specialist cannot dispatch even if its tools say otherwise. Delegation is real only for an agent run as the *main thread* via `--agent` -- in Gaia, that is the orchestrator.

**D2=yes implications (orchestrator only, not specialists):**
- `Agent` in tools list (effective only when the agent is the main thread)
- A delegation table in the body describing which agents it dispatches and under what conditions

**D2=no implications (every specialist):**
- Do not list `Agent`/`Task` -- they add surface area with no effect in a subagent
- CANNOT DO -> DELEGATE table is for the orchestrator's reference, not for the agent to act on directly

### D3: Does the agent enter the orchestrator's automatic routing?

Almost all specialists do. The exception would be a utility agent that is only dispatched explicitly, never via intent routing.

**D3=yes implications:**
- Description field written as triggering conditions (see Step 5 in SKILL.md)
- Surface signals proposed for `surface-routing.json`
- Description must not overlap with signals of existing agents (check `config/surface-routing.json` before finalizing)

---

## The Shared Builder Essence

The builder agents -- `developer`, `platform-architect`, `gitops-operator` -- do not each carry a hand-authored personality. They share one essence, written the same way each time, and differ only in their contract, their skills, and a small subset naming what they build. Carry these five commitments verbatim in spirit when authoring a builder identity:

| Commitment | What it means in the identity |
|---|---|
| **defer to authority** | prefer what already exists -- the modules/versions/patterns in the codebase, and official documentation -- over a clean-slate design |
| **verify the outcome** | the change is not done at `exit 0`; it is done when re-running the check (plan shows no diff, tests pass, the resource is present as declared) confirms the intended outcome |
| **Realization Package XOR Findings** | emit a change package *or* a findings report, never a hybrid that both mutates and summarizes in one turn |
| **disciplined citizen** | flag what is out of lane rather than editing across boundaries; propose rather than persist beyond the `write` contract |
| **capability free under T3** | not fenced by a fixed toolbox -- it may run what its task requires; the mutations are gated by T3 consent and the contract, not by a hard tool denylist |

What is actually per-builder, and where the design effort goes:

- the **contract** (D0) -- the `read`/`write` lists
- the **skills** list -- which catalog skills address this domain's risks
- the **subset** -- one paragraph naming *what this agent builds* (application code / infrastructure-as-code / Kubernetes desired-state) and *which neighbors own the adjacent surfaces*

`platform-architect` is the reference instance: read its identity to see the essence and subset side by side. A non-builder (read-only-into-prod, e.g. `cloud-troubleshooter`) does not use this essence -- its identity is the *constraint* that fences it ("you never act"), not the builder's capability-under-consent.

---

## Weight Test per Component

Use this checklist when reviewing a drafted agent. For each component, the test question is: if this section were removed, would the agent behave differently in a realistic scenario?

### Identity weight test

For a builder, the shared essence is present by design -- its weight comes from being consistent, not novel -- so the weight test applies to the **subset**: does it narrow the action space enough that the agent stops at the right boundary?

| What you wrote | Does it pass? | Why |
|---|---|---|
| "You are an infrastructure specialist." | No | Baseline -- the LLM already knows what an infra specialist does, and it omits both the shared essence and the differentiating subset. |
| Shared essence (defer / verify / Package-XOR-Findings / disciplined citizen / capability-under-T3) + subset ("you build IaC; application code belongs to developer, Kubernetes desired-state to gitops-operator") | Yes | The essence makes it behave like a builder; the subset narrows what *this* builder owns and hands off, redirecting behavior at the boundary. |

**Fix:** Carry the shared essence consistently, then make the subset specific enough that the agent declines neighbor work at the right moment. The identity earns its place by narrowing the action space, not by inventing a new personality.

### Scope weight test

| What you wrote | Does it pass? | Why |
|---|---|---|
| "CANNOT DO: cloud infrastructure → platform-architect" | Weak | Too broad. The agent will still touch cloud config when it seems "close enough." |
| "If the resource type is managed by IaC, creating it belongs to platform-architect even if you need it as a prerequisite for your task." | Yes | Names the decision moment. Agent knows to stop at "I need this resource created" rather than proceeding. |

**Fix:** Identify the specific moment the agent would rationalize crossing the boundary, and make that moment explicit.

### Domain Errors weight test

| What you wrote | Does it pass? | Why |
|---|---|---|
| `terraform init fails → Check credentials` | Marginal | "Check credentials" is the default. Passes only if the agent would normally do something worse. |
| `Plan shows unexpected destroys → HALT -- report, require explicit confirmation` | Yes | "HALT" is not the default. The agent would normally continue to apply. |

**Fix:** For each error row, ask what a naive agent would do. If the row's action is identical to the default, it adds no weight.

### Output type weight test

An output type declaration passes if it excludes a hybrid the agent would otherwise produce. "Realization Package XOR Findings Report -- never both" prevents the agent from writing files *and* returning a summary in the same turn -- a pattern that creates ambiguity about what was done. If the output type only names what the agent always produces anyway, it is decorative.

---

## Frontmatter Field Reference

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `name` | string | Yes | kebab-case; matches file name |
| `description` | string | Yes | Triggering conditions only -- what makes the orchestrator dispatch this agent |
| `tools` | list | Yes | Only what the domain actually uses; omitting is better than bloating |
| `model` | string | Yes | `inherit` for most; `sonnet` if the agent needs a specific model pinned |
| `permissionMode` | string | D1=yes | `acceptEdits` -- required for agents that write files |
| `disallowedTools` | list | read-only-into-prod | Hard denylist `[Write, Edit, NotebookEdit]` reserved for agents that inspect live prod and must never mutate (e.g. `cloud-troubleshooter`). Builders carry no hard denylist (at most `[NotebookEdit]`); they are governed by T3 consent + the `write` contract |
| `project_context_contracts` | block | Yes | `read`/`write` contract lists; the `write` list gates what an agent may persist via an `update_contracts` clause |
| `maxTurns` | int | Recommended | 40 for most specialists; 50+ for agents with complex investigation phases |
| `effort` | string | Optional | `high` raises reasoning effort; used by `gaia-system` for architecture-level work |
| `skills` | list | Yes | `agent-protocol` always first, `security-tiers` always second |
| `color` | string | Optional | Display color in task list/transcript; unused by current Gaia agents |

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
