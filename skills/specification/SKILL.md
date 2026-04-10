---
name: specification
description: Use when the user describes a feature, problem, or need and a structured spec does not yet exist
metadata:
  user-invocable: false
  type: technique
---

# Specification

**Orchestrator-only skill.** The orchestrator drives spec creation through
dialogue -- it never delegates spec authoring to an agent. Every file I/O
step must name the delegation target (agent) explicitly.

A spec captures WHAT to build and WHY. The user will naturally mention
technologies -- "deploy to GKE", "use Flux CD" -- and that is problem
context, not implementation detail. What does NOT belong is file paths,
class names, config values, or step-by-step instructions. When in doubt:
does this sentence describe the problem or a solution? Problem context stays.

## When to Activate

- User describes a feature, capability, or change they want
- User pastes a ticket, issue, or informal requirements
- The `planning_specs` surface is active and no `spec.md` exists yet

Skip when spec.md already exists, or the user asks about live state.

## Step 1: Load Context and Constraints

Before the first question, delegate to the `gaia` agent to read
`governance.md` and `project-context.json`. The `speckit_root` path comes
from `project-context.json` at `paths.speckit_root` (default: `specs/`).
Missing path -> BLOCKED.

Extract architectural principles, stack constraints, and existing services.
These feed your critical thinking -- do not dump them to the user.

## Step 2: Capture the Raw Idea

Let the user talk. Messy and nonlinear is fine. Identify from their words:
**who benefits**, **what problem** they face, **what outcome** they want,
and **what triggers** the need. If any of these four are missing, ask ONE
focused question to fill the biggest gap. Do not interrogate.

## Step 3: Challenge and Clarify

This is where the orchestrator earns its keep. Do not just transcribe --
think critically about what the user is asking.

**Proactive checks against loaded context:**
- Overlap: "The project already has a payment service -- replacing it or separate?"
- Governance conflict: "Governance mandates ArgoCD but you mentioned Flux."
- Complexity: "Do you really need a separate microservice, or a module in the existing service?"
- Consistency: "You said real-time, but the acceptance criteria suggest hourly batches."

**Say NO when something does not make sense.** The user can override, but
they should do it consciously -- not because you failed to flag the problem.

Ask at most 3 clarifying rounds. Stop when you can write a problem statement
without guessing, list 2+ acceptance scenarios, and scope boundaries are clear.

Good questions target gaps: "What happens when [edge case]?", "Is [adjacent
feature] in scope?", "What does success look like for the user?"
Never ask implementation questions (schema design, API endpoints, module choice).

## Step 4: Draft the Spec

Use `speckit/templates/spec-template.md` as the canonical format.

**Mandatory sections:**
1. **Problem Statement** -- 2-3 sentences. Why this matters and who feels it.
2. **User Stories** -- "As [actor], I want [goal], so that [benefit]." 1-5 stories; each names a real actor, not "the system."
3. **Acceptance Criteria** -- Given/When/Then. At least one per story. These are the contract -- if a criterion cannot be tested without knowing the implementation, rewrite it.
4. **Scope Boundaries** -- IN / OUT columns. Explicit exclusions prevent scope creep during planning.
5. **Constraints** -- From governance.md. Only constraints relevant to this feature.
6. **Key Entities** -- If data is involved. Plain language, no field types or schemas.

**Optional:** Edge Cases, Security Considerations, Performance Expectations.
Mark uncertainty with `[NEEDS CLARIFICATION: specific question]`.

Before presenting, verify: every story names a real actor, every criterion
is implementation-agnostic, technologies appear only as context, scope
excludes at least one adjacent concern, constraints come from governance.

## Step 5: Present and Iterate

Show the full draft. Ask: "Does this capture what you want? Anything
missing or wrong?" Each iteration: apply feedback, show the updated
section (not the full spec), confirm.

## Step 6: Save

Delegate to `developer` to save `{speckit_root}/{feature-name}/spec.md`.
Feature directory: lowercase, hyphenated (e.g., `payment-gateway`).
Suggest: "The spec is ready. Want me to start planning?" -- this triggers
`/speckit.plan` (a skill invocation, not a shell command).

## Anti-Patterns

- **Agreeable transcriber** -- transcribing without questioning produces specs that waste plan time when contradictions surface later.
- **Interrogation** -- ten questions before writing anything exhausts the user. Capture first, clarify gaps.
- **Premature structure** -- forcing Given/When/Then before the idea is clear kills brainstorming.
- **Completionism** -- speccing every edge case. The plan phase handles depth; over-specifying constrains design options unnecessarily.
- **Skipping governance** -- a spec that violates known constraints generates a plan that cannot be implemented, wasting everyone's time.
- **Ignoring existing state** -- not checking project-context for overlapping services leads to duplicate work or integration conflicts.
