---
name: agent-creation
description: Use when creating a new specialist agent for Gaia, or reviewing whether an existing agent follows the correct structure, tone, and component inventory
metadata:
  user-invocable: false
  type: technique
---

# Agent Creation

## What is an agent?

A specialist agent is a contract over project-context plus a small identity that points at a domain. The contract is the load-bearing part: a `read` list that filters which slices of project-context get injected (the token-efficiency lever) and a `write` list that confines what the agent may persist back (the security lever). The identity that wraps the contract is mostly *shared* — for the builder agents (`developer`, `platform-architect`, `gitops-operator`) it is nearly the same essence — and the only genuinely per-agent pieces are the contract, the skills, and a small subset of "what this agent builds." If the component you are building has no distinct contract, no delegation surface, and could work as injected text, it is a skill, not an agent. That decision belongs upstream — this skill assumes it has been made.

## The contract-first model

Creating an agent is **contract + skills + a small domain subset**, not a personality authored from scratch. Three facts drive every decision below:

1. **The contract is the first design decision.** Before identity, before tools, before skills, decide the `project_context_contracts` block. The `read` list is what makes the agent token-efficient — it filters the project-context injection down to the slices this domain actually reasons over, so the agent is not paying for context it never reads. The `write` list is what makes the agent safe — it is the allowlist the runtime checks before accepting any `update_contracts` clause; a contract not in the `write` list cannot be persisted, no matter what the agent emits. Get this wrong and every other section is built on the wrong foundation.

2. **Builder identity is shared essence + a small subset.** The builder agents do not each invent a personality. They share one essence: defer to what already exists over a clean-slate design; verify that the change produced the intended outcome rather than trusting an exit code; emit a Realization Package XOR a Findings Report, never a hybrid; be a disciplined citizen of the system (flag what is out of lane, do not edit across boundaries; propose, do not persist beyond the contract); and operate with capability that is free under T3 consent rather than fenced by a fixed toolbox. What is actually per-builder is small: the contract, the skills list, and a one-paragraph subset naming *what this agent builds* (application code / infrastructure-as-code / Kubernetes desired-state) and which neighbors own the adjacent surfaces. Write the shared essence the same way each time; spend your design effort on the subset and the contract.

3. **Base skills come from `agent-protocol`; soft governance comes from T3.** `agent-protocol` and `security-tiers` are non-negotiable for every agent and carry the response contract and tier discipline — you do not re-teach them in the identity. The builders are governed *softly*: their mutations are gated by T3 consent and their contract, not by a hard tool denylist. Hard `disallowedTools` is reserved for one case (see Step 1, D1).

## Step 1: Answer the bifurcating dimensions

Answer these before writing a line. They determine the contract shape, the tool set, and the failure model.

**D0 (decide first): What is the contract?**
Name the `read` slices this domain reasons over and the `write` slices it owns. Scope `read` to what the agent actually consults — every extra slice is injected on every call and spent whether read or not. Scope `write` to the contracts the agent's domain *owns* — `developer` writes `application_services`, `platform-architect` writes `infrastructure` and `infrastructure_topology`, a read-only diagnostic agent writes nothing or only the one observation contract it curates. This is the answer that makes the agent efficient and safe; the rest of the inventory derives from it.

**D1: Does the agent mutate system state?**
A "yes" means: Write/Edit in tools, `permissionMode: acceptEdits` in frontmatter, the T3 approval flow in failure handling, and a "Realization Package" output type. A "no" means: no Write/Edit, no T3 surface, read-only output.
The hard `disallowedTools: [Write, Edit, NotebookEdit]` denylist is reserved for the **read-only-into-prod** case — an agent that inspects live production state and must be incapable of mutating it, e.g. `cloud-troubleshooter`. That is the one place a hard tool constraint earns its keep, because an accidental write to a live cloud resource is a real incident. The builder agents are *not* governed this way: they may need Write/Edit/Bash across their whole domain, so they carry no hard denylist (at most `[NotebookEdit]`, the surface no Gaia builder uses) and are governed softly by T3 consent. Do not reach for `disallowedTools` to "lock down" a builder — that is what T3 is for.

**D2: Does the agent delegate to other agents?**
Almost always "no" for specialists — and the runtime forces it. A Gaia specialist runs *as a subagent* under the orchestrator, and a subagent cannot spawn subagents: `Agent`/`Task` are inert in a subagent's frontmatter even if listed (per Anthropic's subagents doc). D2=yes applies only to an agent run as the main thread via `--agent` — in practice the orchestrator. A specialist surfaces work it cannot do through its CANNOT DO → DELEGATE table; the orchestrator routes. That table is required regardless of D2.

**D3: Does the agent enter the orchestrator's automatic routing?**
Almost always "yes." A "yes" means the description field is written as triggering conditions (not a role summary) and a `routing:` frontmatter block (surface, adjacent_surfaces, commands, artifacts, required_checks) is proposed for the agent. Those signals are proposals — gaia-system applies them to the agent's own frontmatter, from which `tools/scan/seed_surface_routing.py` seeds the `surface_routing` DB table at install time; `tools/context/surface_router.py` reads that table (not a JSON file) at runtime.

## Step 2: Apply the component inventory

**Obligatory in every specialist:**

1. **`project_context_contracts`** (frontmatter block): the per-agent `read`/`write` contract lists from D0. The `write` list is what the runtime checks before accepting an `update_contracts` entry in the agent's `agent_contract_handoff` envelope (see `agent-contract-handoff`) — a contract absent from `write` cannot be persisted. This is listed first because it is the first design decision, even though it sits in the frontmatter alongside the other fields.
2. **Frontmatter**: `name`, `description` (triggering conditions only), `model`, `tools`. Add `permissionMode: acceptEdits` if D1=yes. Add `disallowedTools` only for the read-only-into-prod case (`[Write, Edit, NotebookEdit]`); a builder needs no hard denylist beyond at most `[NotebookEdit]`. Add `maxTurns` for long-running agents.
3. **Identity** (1-2 paragraphs): for a builder, the *shared essence* plus the *small subset* — what this agent builds and which neighbors own the adjacent surfaces. Do not re-author the essence from scratch; carry the same five commitments (defer-to-authority, verify-the-outcome, Realization-Package-XOR-Findings, disciplined-citizen, capability-free-under-T3) and let the subset do the differentiating. For a non-builder (read-only diagnostic), the identity names the constraint that fences it instead.
4. **Workflow** (numbered steps): the operational sequence for this domain. Put it before Identity when the sequence is the agent's primary reference.
5. **Scope — CAN DO / CANNOT DO → DELEGATE**: boundaries with reasons. Every CANNOT DO entry names a concrete delegate agent and, ideally, the decision point where a naive agent would cross.
6. **Failure handling / Domain Errors**: concrete errors with concrete actions. "Report the error" is not an action.
7. **Response protocol**: the agent loads `agent-protocol`. Reference it in the skills list; do not replicate its content.

**Optional by dimension:**
- **Delegation table** (D2=yes): only meaningful for the main-thread orchestrator — a subagent specialist cannot dispatch, so this does not apply to specialists.
- **Surface signals** (D3=yes): a proposed `routing:` frontmatter block (surface, adjacent_surfaces, commands, artifacts, required_checks) for gaia-system to apply to the agent's own file — the source of truth `tools/scan/seed_surface_routing.py` seeds into the `surface_routing` DB table at install time.
- **Domain reference inline**: lookup tables or decision logic that apply only to this agent and do not warrant a skill.

## Step 3: Write for judgment, not compliance

Each obligatory component must carry enough weight to change behavior. The test: if the section were removed, would the agent behave differently? If not, it is decorative.

**Contract:** A `read` list bloated with slices the agent never consults silently taxes every call; a `write` list wider than the domain owns is a security hole the runtime will partially catch but should never have been asked to. The weight test for the contract is whether each `read` slice is actually consulted and each `write` slice is actually owned.

**Identity:** For a builder, the essence is shared on purpose — its weight comes from being *present and consistent*, not from being novel. The differentiating weight lives in the subset: "what this agent builds" must narrow the action space enough that the agent stops at the right boundary. If the subset were removed and the agent still behaved identically to a generic builder, the subset needs more weight.

**Scope boundaries:** A boundary stated as a category ("cloud infrastructure") is weaker than one that names the decision point ("if the resource type is managed by IaC, creating it belongs to platform-architect even if you need it as a prerequisite").

**Failure handling:** A row whose action equals the default does nothing. Each row should describe what a naive agent would do wrong and redirect.

**Output type declaration:** Builders declare "Realization Package XOR Findings Report — never a hybrid" so the agent reaches a clean state at completion instead of mutating files *and* returning a summary.

## Step 4: Write the description field as triggering conditions

The description is what the orchestrator reads to decide when to dispatch. It must describe *when to use this agent*, not *what it is*. A role summary satisfies the read without triggering the dispatch.

```yaml
# Wrong -- describes the role
description: Senior infrastructure architect that manages the cloud lifecycle

# Right -- triggering conditions
description: Use when provisioning, modifying, or validating infrastructure-as-code (Terraform, Pulumi, CloudFormation, OpenTofu), or managing the infrastructure lifecycle
```

## Step 5: Evaluate the skills catalog and propose applicable skills

Do not hardcode a tool-to-skill mapping — the catalog changes and a fixed mapping goes stale silently. Evaluate the current catalog at `.claude/skills/` and propose which skills address a recurring risk or discipline gap for this agent's tool set and domain. `agent-protocol` and `security-tiers` are non-negotiable for every agent; beyond those, let the tool set and domain guide selection (e.g. `command-execution` if it runs Bash, `investigation` if it diagnoses complex state).

## Step 6: Propose surface signals (if D3=yes)

For agents in automatic routing, propose a `routing:` frontmatter block (surface, adjacent_surfaces, commands, artifacts, required_checks — `keywords` is retired, the matcher scores from `commands`/`artifacts` only) written for gaia-system to apply directly to the agent's own file. Do not apply it yourself, and check existing agents' `routing:` blocks so the new agent's surface and signals do not overlap a sibling's.

## Anti-patterns

- **Designing identity before the contract**: the contract is the first decision because it sets token cost and write safety. Authoring a personality first and bolting a contract on after produces an agent that reads too much and may write where it should not.
- **Re-authoring the builder essence from scratch**: the builders share one essence by design. Inventing a fresh personality per builder drifts the fleet and wastes the differentiating effort that belongs in the contract and the subset.
- **Reaching for `disallowedTools` to govern a builder**: hard denylists are for the read-only-into-prod case. A builder is governed by T3 consent; a hard denylist on it either blocks legitimate work or signals a misunderstanding of where the security boundary lives.
- **Treating this as a form**: filling sections without the weight test produces a well-structured agent the LLM ignores in favor of baseline behavior.
- **Writing the description as a role summary**: the orchestrator reads it to decide *when* to dispatch; a summary satisfies the read without triggering the dispatch.
- **Domain Errors that only say "report"**: every row should redirect to a concrete action a naive agent would not take by default.
