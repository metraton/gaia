---
name: investigation
description: Use when starting an investigation, analyzing existing code or infrastructure, or building findings before proposing changes
user-invocable: false
type: core
---

# Investigation Skill

Investigation is about understanding a problem well enough to propose a correct solution.
**How to search** is defined in `agent-protocol`. This skill defines **how to think**.

## 1. Decompose First

Before searching anything, define what you need to know:
- What is the expected state vs what might be wrong or missing?
- What are the knowns (injected context) vs the unknowns?
- What evidence would confirm or disprove my hypothesis?

Starting a search without a clear question produces noise, not findings.

## 2. Gather Evidence

Follow the local-first order from `agent-protocol`. For each area of investigation:
- Read 2-3 similar existing resources to understand what is already implemented
- Extract: naming conventions, directory structure, dependencies, config patterns

## 3. Explore Before Concluding

After gathering initial evidence, resist the pull to stop at the first answer.

- **Check adjacency:** Read neighboring files, sibling modules, and related configs.
  The file next to your target often explains constraints that the target itself does not.
- **Check depth:** If you found the resource, also find what references it and what it
  references. One level up and one level down reveals integration patterns.
- **Check breadth:** Search for 2-3 more instances of the same pattern. One example is
  an anecdote; three examples are a convention.

Stop exploring when new files confirm what you already know rather than adding new information.

If Project Context includes an `investigation_brief`, use it to prioritize:
- which surface you own
- which adjacent surface must still be checked
- which required checks must appear in your evidence
- whether you must return a `consolidation` object in your `json:contract` block for Gaia to merge findings

## 4. Pattern Analysis

Apply this hierarchy — in order, without skipping levels:

**Level 1 — Codebase (always first)**
Search for 2-3 existing resources of the same type. If found → FOLLOW them. Codebase pattern wins over your training and over your domain skill. Consistency beats preference.

**Level 2 — Domain skill (fallback when no codebase pattern exists)**
If no existing pattern is found in the codebase, use your injected domain skill
(the skill in your frontmatter that is specific to your technical area — e.g.,
terraform-patterns, gitops-patterns, developer-patterns, gaia-patterns,
speckit-workflow, or whichever domain skill you have).

**Level 3 — Training best practices (last resort)**
If neither codebase nor domain skill has a pattern for what you need, use your training knowledge. Always mark the result explicitly: *"No existing pattern found — applying best practices. Recommend reviewing before merging."*

**Pattern Authority (applies at all levels):**

**COPY** — Names, paths, IDs are contracts. Match existing schema exactly.
**ALERT** — Problematic pattern → DEVIATION or CRITICAL, propose alternative, let user decide. Never silently follow or fix.
**DOCUMENT** — New discovery not in project-context. If you have the `context-updater`
skill, emit `CONTEXT_UPDATE`. Otherwise, note the discovery in your report for the
orchestrator to route.

## 5. Validate Your Hypothesis

Before treating findings as fact:
- Does local code agree with project-context? If not → investigate drift first
- Unfamiliar resource, API, or behavior? → search official documentation
- Uncertain about correctness? → run one more read-only validation step

Never plan on assumptions. If in doubt, validate.

## 6. Surface Options

When multiple valid approaches exist:
- List them explicitly: **Option A** (trade-offs), **Option B** (trade-offs)
- Evaluate each against existing project patterns and constraints
- Do NOT pick silently — surface them and set status: `NEEDS_INPUT`

## 7. Qualify Confidence Before Proposing

Before findings feed into a plan, explicitly state:
- What is **confirmed** (seen in code, validated by CLI or docs)
- What is **assumed** (inferred but not yet validated)

If critical gaps remain → run another validation round. Never propose on shaky ground.

## 8. Evidence Contract

When you report investigation findings, populate the `evidence` object in the protocol-mandated `json:contract` block.

Interpret the fields this way:

- `PATTERNS_CHECKED` — existing repo patterns, sibling resources, or conventions you compared against
- `FILES_CHECKED` — concrete files, directories, manifests, modules, or docs you inspected
- `COMMANDS_RUN` — exact read-only or validation commands you executed, plus a terse result
- `KEY_OUTPUTS` — 1-2 sentence summary of what changed your conclusion
- `VERBATIM_OUTPUTS` — literal command outputs that back up your findings. The orchestrator shows these to the user on request. Format each entry as the command in backticks followed by a fenced code block with the raw output. Truncate outputs longer than ~100 lines with `[truncated]`. Write `- none` when no commands were executed.
- `CROSS_LAYER_IMPACTS` — adjacent surfaces, systems, or contracts affected by the finding
- `OPEN_GAPS` — what is still unverified, inaccessible, or assumed

Minimum expectations:
- Always populate `PATTERNS_CHECKED` and `FILES_CHECKED` for local/code investigations
- Populate `COMMANDS_RUN` and `KEY_OUTPUTS` whenever you touched live state, validation commands, or diagnostics
- Populate `CROSS_LAYER_IMPACTS` whenever the issue crosses app, infra, GitOps, runtime, hooks, skills, or docs
- Use `- none` or `- not run` when a field truly does not apply

The goal is not verbosity. The goal is evidence that another agent, the orchestrator, or the user can verify quickly.

## 9. Consolidation Contract

When `investigation_brief.consolidation_required` is true, your response must help
Gaia merge parallel or cross-surface work. That means:

- say what is confirmed vs only suspected
- say whether the problem is owned here or depends on another surface
- say what still blocks a final conclusion
- say who should continue if it is no longer your surface

Do not wait for the orchestrator to infer this from prose. Put it in the
protocol-mandated `consolidation` object in your `json:contract` block.

## Anti-Patterns

- Searching before knowing what question you're trying to answer
- Planning before all critical unknowns are resolved
- Picking an approach without surfacing alternatives
- Treating your training's preference as the correct codebase pattern
- Assuming instead of validating with a read-only check
