---
name: agent-creation
description: Use when creating a new specialist agent for Gaia, or reviewing whether an existing agent follows the correct structure, tone, and component inventory
metadata:
  user-invocable: false
  type: technique
---

# Agent Creation

## What is an agent?

A specialist agent is a persistent identity with its own tool set, scope, and output contract. The identity is what separates it from a skill or inline behavior: an agent acts from a role, not just follows a process. If the component you are building has no distinct output type, no delegation surface, and could work as injected text, it is a skill, not an agent. That decision belongs upstream -- this skill assumes it has already been made.

## Step 1:  Answer the 3 bifurcating dimensions

Answer these before writing a single line. They determine which components are required, what the tool set looks like, and what the failure model must handle.

**D1: Does the agent mutate system state?**
A "yes" means: Write/Edit in tools, `permissionMode: acceptEdits` in frontmatter, T3 approval flow in failure handling, and an output type that says "Realization Package" rather than "Diagnostic Report."
A "no" means: `disallowedTools` should explicitly exclude Write/Edit/NotebookEdit, no T3 surface in failure handling, and output is always read-only.

**D2: Does the agent delegate to other agents?**
Almost always "no" for specialists -- they are terminal nodes. A "yes" adds a delegation table to the body. A "no" still needs a CANNOT DO -> DELEGATE table for the orchestrator's benefit, but the agent itself never dispatches.

**D3: Does the agent enter the orchestrator's automatic routing?**
Almost always "yes." A "yes" means the description field must be written as triggering conditions (not a role summary), and surface signals should be proposed for `surface-routing.json`. Those signals are proposals -- gaia-system applies them; this skill only guides what to propose.

## Step 2: Apply the component inventory

**Obligatory in every specialist:**

1. **Frontmatter**: `name`, `description` (triggering conditions only), `model`, `tools`, `color`. Add `permissionMode: acceptEdits` if D1=yes. Add `disallowedTools` if D1=no and enforcement matters. Add `maxTurns` for long-running agents.
2. **Identity** (1-2 paragraphs): what this agent *is*, not what it does. Carries enough weight that removing it would change behavior -- see Step 4.
3. **Workflow** (numbered steps): the operational sequence for this agent's domain. Put this before Identity when the workflow is complex enough to be the agent's primary reference.
4. **Scope -- CAN DO / CANNOT DO -> DELEGATE**: boundaries with reasons. Every entry in CANNOT DO must name a concrete delegate agent.
5. **Failure handling / Domain Errors**: a table of concrete errors with concrete actions. "Report the error" is not an action.
6. **Response protocol**: the agent must load `agent-protocol`. Reference it in the skills list; do not replicate its content.

**Optional by dimension:**
- **Delegation table** (D2=yes): which agents this specialist can dispatch, under what conditions.
- **Surface signals** (D3=yes): proposed keyword patterns for the orchestrator's `surface-routing.json`. Write them as a proposal block -- gaia-system applies them.
- **Domain reference inline**: domain-specific lookup tables or decision logic that applies only to this agent and does not warrant a skill.

## Step 3: Write for judgment, not compliance

Each obligatory component must carry enough weight to actually change behavior. The test: if the section were removed, would the agent behave differently? If not, the section is decorative.

**Identity:** "You are a specialist in X" is baseline -- the LLM already knows what X specialists do. The identity must justify *why this agent exists as a distinct entity* -- what it sees that a generic assistant would miss, what constraint it operates under that shapes every decision. If the identity section were removed and the agent still behaved identically, it needs more weight.

**Scope boundaries:** Each CANNOT DO entry needs enough specificity that the agent declines at the right moment, not one step too late. A boundary described only as a category ("cloud infrastructure") is weaker than one that names the decision point ("if the resource type is managed by IaC, creating it belongs to terraform-architect even if you need it as a prerequisite").

**Failure handling:** A Domain Errors table that says "check logs" or "report the error" does nothing the agent would not do by default. Each row should describe what a naive agent would do wrong, and redirect to the correct action.

**Output type declaration:** Specialists declare their output type explicitly ("Your output is always a Realization Package" / "Your output is always a Diagnostic Report"). This is not cosmetic -- it prevents the agent from producing hybrid outputs that neither commit nor diagnose.

## Step 4: Write the description field as triggering conditions

The description is what the orchestrator reads to decide when to dispatch. It must describe *when to use this agent*, not *what this agent is*. Summarizing the role in the description causes the orchestrator to satisfy itself with the summary and never dispatch.

```yaml
# Wrong -- describes the role
description: Senior Terraform architect that manages cloud infrastructure lifecycle

# Right -- triggering conditions
description: Use when creating, modifying, or validating Terraform/Terragrunt configurations, or managing the infrastructure lifecycle via IaC
```

## Step 5: Evaluate the skills catalog and propose applicable skills

Do not hardcode a mapping of tool to skill. Instead, evaluate the current catalog at `.claude/skills/` and propose which skills apply to this agent's tool set and domain. The catalog changes; a hardcoded mapping goes stale silently.

The evaluation should ask: which skills address a recurring risk or discipline gap for this agent's tool set? `agent-protocol` and `security-tiers` are non-negotiable for every agent. Beyond those, let the tool set and domain guide the selection.

## Step 6: Propose surface signals (if D3=yes)

For agents entering automatic routing, propose signal patterns for `surface-routing.json` -- high-confidence and medium-confidence keyword clusters that would reliably indicate this agent should handle the request. These are proposals: write them as a block the invoking agent (gaia-system) can apply directly. Do not apply them yourself.

## Anti-patterns

- **Treating this as a form**: filling in sections without testing whether each one carries enough weight to change behavior produces a well-structured agent that the LLM ignores and acts from baseline.
- **Skipping the weight test**: an identity section that says "You are a specialist in X" is decorative. Test every section: if it were removed, would behavior change?
- **Creating a new archetype**: the 3 bifurcating dimensions cover the full specialist space. Adding a new archetype ("Readonly Specialist", "Executor") when the dimensions already capture the distinction adds taxonomy without adding precision.
- **Hardcoding the tool-to-skill mapping**: the skills catalog changes; a fixed mapping produces agents that reference non-existent skills or miss new ones that would help.
- **Writing the description as a role summary**: the orchestrator reads the description to decide when to dispatch. A role summary satisfies the read without triggering the dispatch.
- **Skipping disallowedTools for read-only agents**: not listing Write/Edit in `tools` is weaker than explicitly disallowing them. A future edit that adds a tool could silently give a read-only agent write access.
- **Domain Errors that only say "report"**: every error row should redirect to a concrete action that a naive agent would not take by default.
