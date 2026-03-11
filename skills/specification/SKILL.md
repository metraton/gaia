---
name: specification
description: Use when the user describes a feature, problem, or need and a structured spec does not yet exist
user-invocable: false
type: technique
---

# Specification

Conversational skill for the orchestrator. The orchestrator drives spec creation
through dialogue -- it never delegates spec authoring to an agent.

A spec captures WHAT to build and WHY.

**The line between spec and plan:**
- Technologies as problem context = OK in the spec. "The service needs a Cloud SQL
  database for transaction storage" describes WHAT.
- Implementation details = NOT OK. "Create a Cloud SQL instance with db-f1-micro
  tier in us-central1 using Terraform module google_sql_database_instance" is HOW.

The user will naturally mention technologies -- "deploy to GKE", "use Flux CD",
"set up a Cloud SQL database." That is part of the problem domain. Accept it.
What does NOT belong is file paths, class names, config values, or step-by-step
instructions.

## When to Activate

Activate when ANY of these are true:
- User describes a feature, capability, or change they want
- User says "I want to build..." / "we need..." / "what if we..."
- User pastes a ticket, issue, or informal requirements
- The `planning_specs` surface is active and no `spec.md` exists yet

Do NOT activate when:
- A spec.md already exists and the user wants to plan or implement
- The user is asking about live infrastructure or runtime state

## Step 1: Load Context and Constraints

Before asking the first question, delegate to an agent to read:
- `governance.md` from the speckit root -- architectural principles, stack constraints
- `project-context.json` -- existing services, infrastructure, paths

Extract:
- Mandatory architectural principles (GitOps, Workload Identity, etc.)
- Technology stack constraints (cloud provider, orchestration, IaC tool)
- Existing services and infrastructure that may overlap with the request

These feed your critical thinking. Do not dump them to the user -- use them
to ask better questions and catch problems early.

## Step 2: Capture the Raw Idea

Let the user talk. This is brainstorming -- messy and nonlinear is fine.
Collect everything they say without restructuring yet.

Identify from their words:
- **Who benefits** -- the actor or stakeholder
- **What problem** they face today
- **What outcome** they want
- **What triggers** the need (event, pain point, dependency)

If any of these four are missing after the user's initial description,
ask ONE focused question to fill the biggest gap. Do not interrogate.
Prefer "What problem does this solve?" over a list of five questions.

## Step 3: Challenge and Clarify

This is where the orchestrator earns its keep. Do not just transcribe --
think critically about what the user is asking.

**Proactive checks (run these against loaded context):**
- Does an existing service already cover this? Alert: "The project already
  has a payment service at services/payments/ -- is this replacing it or
  something separate?"
- Does the request conflict with governance? Alert: "Governance mandates
  ArgoCD but you mentioned Flux -- is there a reason to diverge?"
- Is there unnecessary complexity? Push back: "Do you really need a separate
  microservice, or could this be a module in the existing order service?"
- Do stated requirements match the acceptance criteria? Challenge: "You said
  real-time notifications, but the acceptance criteria suggest hourly batches
  would satisfy the users. Which is it?"

**Say NO when something does not make sense.** If the request contradicts
governance, duplicates existing work, or introduces unjustified complexity,
surface that clearly. The user can override, but they should do it consciously.

**Clarifying questions** -- ask at most 3 rounds. Each round: one question,
wait for the answer, integrate it. Stop asking when:
- You can write a problem statement without guessing
- You can list at least 2 acceptance scenarios
- Scope boundaries are clear (what is IN, what is OUT)

Good clarifying questions:
- "What should happen when [edge case]?"
- "Is [adjacent feature] in scope or separate?"
- "Who else uses this besides [primary actor]?"
- "What does success look like from the user's perspective?"

Bad clarifying questions (never ask these):
- "How should the database schema look?" (implementation detail)
- "What API endpoints do you need?" (implementation detail)
- "Which Terraform module should we use?" (implementation detail)

## Step 4: Draft the Spec

Organize findings into this structure. Use the spec template from
`speckit/templates/spec-template.md` as the canonical format.

**Mandatory sections:**

1. **Problem Statement** -- 2-3 sentences. The pain point and who feels it.

2. **User Stories** -- "As [actor], I want [goal], so that [benefit]."
   Minimum 1, maximum 5. Each must name a real actor, not "the system."

3. **Acceptance Criteria** -- Given/When/Then format. At least one per
   user story. These are the contract the implementation must satisfy.

4. **Scope Boundaries** -- Two columns: IN scope / OUT of scope.
   Explicit exclusions prevent scope creep during planning.

5. **Constraints** -- Pull from governance.md. Only list constraints
   that are relevant to this feature. Do not dump the entire governance.

6. **Key Entities** -- If the feature involves data, name the entities
   and their relationships in plain language. No field types, no schemas.

**Optional sections** (include only when relevant):

- **Edge Cases** -- What happens when [boundary condition]?
- **Security Considerations** -- If the feature handles auth, PII, or secrets
- **Performance Expectations** -- Only if the user stated targets

Mark anything uncertain with `[NEEDS CLARIFICATION: specific question]`.

## Step 5: Present for Review

Show the full draft spec to the user. Ask:
- "Does this capture what you want?"
- "Anything missing or wrong?"
- "Are the scope boundaries right?"

Iterate until the user approves. Each iteration: apply their feedback,
show the updated section (not the full spec again), confirm.

## Step 6: Save

When the user approves, delegate to an agent to save `spec.md` in the
feature directory: `{speckit_root}/{feature-name}/spec.md`.

The feature directory name: lowercase, hyphenated, descriptive.
Example: `payment-gateway`, `user-notifications`, `report-export`.

After saving, suggest: "The spec is ready. Want me to start planning?"
This transitions to the `speckit-planner` agent via `/speckit.plan`.

## Quality Checks

Before presenting the draft, verify:
- [ ] Every user story names a real actor (not "the system")
- [ ] Every acceptance criterion is testable without knowing the implementation
- [ ] Technologies appear only as problem context, never as implementation instructions
- [ ] Scope boundaries explicitly exclude at least one adjacent concern
- [ ] Constraints come from governance, not from assumptions
- [ ] Problem statement explains WHY, not WHAT to build

## Anti-Patterns

- **Agreeable transcriber** -- just writing down what the user says without questioning it. The orchestrator guides, challenges, and shapes.
- **Interrogation** -- asking 10 questions before writing anything. Capture first, clarify gaps.
- **Premature structure** -- forcing the user into Given/When/Then before they have finished describing the idea.
- **Technology ban** -- rejecting all technology mentions. "Deploy to GKE with Flux CD" is problem context, not implementation detail.
- **Completionism** -- trying to spec every edge case. The plan phase handles depth.
- **Skipping governance** -- writing a spec that violates known constraints, wasting plan time.
- **Ignoring existing state** -- not checking project-context for services or infrastructure that overlap with the request.
